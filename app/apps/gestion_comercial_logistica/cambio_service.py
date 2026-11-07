"""[EXAMEN-2] Devoluciones y cambios offline (CU13).

Reutiliza `SolicitudDevolucion` y `DetalleSolicitudDevolucion` de
`gestion_ventas`; no crea tablas paralelas (plan, 3.8 y 9.3.9). El ajuste de
inventario se hace en una sola transacción y el reembolso con Stripe pasa
siempre por `ReembolsoService` (RN-018).
"""
from __future__ import annotations

import io
import logging
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.exceptions import (
    ConflictException,
    NotFoundException,
    OperacionRechazada,
    ValidationException,
)
from app.apps.gestion_catalogo.models import (
    Inventario,
    Producto,
    Sucursal,
    Talla,
    VarianteProducto,
)
from app.apps.gestion_usuarios.models import Cliente, Usuario
from app.apps.gestion_ventas.models import (
    Comprobante,
    DetalleOrden,
    DetalleSolicitudDevolucion,
    EstadoOrden,
    EstadoSolicitudDevolucion,
    EstadoSincronizacion,
    MotivoDevolucion,
    Orden,
    SolicitudDevolucion,
    TipoDiferencia,
    TipoSolicitudDevolucion,
)
from app.apps.gestion_ventas.services import InventarioVentasService

logger = logging.getLogger(__name__)

CENTAVO = Decimal("0.01")
ESTADOS_ORDEN_ELEGIBLES = {
    EstadoOrden.PAGADO,
    EstadoOrden.EN_PROCESO,
    EstadoOrden.ENVIADO,
    EstadoOrden.ENTREGADO,
}
ESTADOS_SOLICITUD_ACTIVA = {
    EstadoSolicitudDevolucion.PENDIENTE,
    EstadoSolicitudDevolucion.EN_REVISION,
    EstadoSolicitudDevolucion.APROBADA,
    EstadoSolicitudDevolucion.PENDIENTE_REEMBOLSO,
}


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(valor) -> Decimal:
    return Decimal(str(valor if valor is not None else 0)).quantize(CENTAVO)


def _aware(valor: datetime) -> datetime:
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


class AlternativaTallaService:
    """Busca tallas mayor/menor con stock para una variante (RF-034).

    El orden de comparación es `Talla.orden` (Fase D); si la talla no tiene
    orden, la alternativa se busca únicamente por color (misma talla).
    """

    @staticmethod
    async def buscar(
        db: AsyncSession,
        tenant_id: int,
        variante_id: int,
        sucursal_id: int | None = None,
        direccion: str = "MAYOR",
        cantidad: int = 1,
    ) -> dict:
        variante = (await db.execute(
            select(VarianteProducto)
            .options(
                selectinload(VarianteProducto.talla),
                selectinload(VarianteProducto.color),
            )
            .where(
                VarianteProducto.id == variante_id,
                VarianteProducto.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if variante is None:
            raise NotFoundException("Variante no encontrada para el tenant")

        direccion = str(direccion or "MAYOR").upper()
        variantes = (await db.execute(
            select(VarianteProducto)
            .options(
                selectinload(VarianteProducto.talla),
                selectinload(VarianteProducto.color),
            )
            .where(
                VarianteProducto.producto_id == variante.producto_id,
                VarianteProducto.tenant_id == tenant_id,
                VarianteProducto.deleted_at.is_(None),
                VarianteProducto.id != variante.id,
            )
        )).scalars().all()

        inventarios = [] if not variantes else (await db.execute(
            select(Inventario).where(
                Inventario.tenant_id == tenant_id,
                Inventario.variante_producto_id.in_([v.id for v in variantes]),
            )
        )).scalars().all()
        stock: dict[int, dict[int, int]] = {}
        for inventario in inventarios:
            disponible = max(
                0, int(inventario.cantidad or 0) - int(inventario.cantidad_reservada or 0)
            )
            if disponible <= 0:
                continue
            stock.setdefault(inventario.variante_producto_id, {})[
                inventario.sucursal_id
            ] = disponible

        orden_original = variante.talla.orden if variante.talla else None
        candidatos = [
            v for v in variantes
            if v.talla is not None and v.talla.orden is not None
        ]
        if orden_original is not None:
            if direccion == "MENOR":
                candidatos = [v for v in candidatos if v.talla.orden < orden_original]
                candidatos.sort(key=lambda v: v.talla.orden, reverse=True)
            else:
                candidatos = [v for v in candidatos if v.talla.orden > orden_original]
                candidatos.sort(key=lambda v: v.talla.orden)
        else:
            candidatos = []

        # Misma talla en otro color (el cliente acepta cambio de color).
        mismo_orden = [
            v for v in variantes
            if v.talla is not None and orden_original is not None
            and v.talla.orden == orden_original
        ]

        def _item(v: VarianteProducto, criterio: str) -> dict:
            sucursales = stock.get(v.id, {})
            total = sum(sucursales.values())
            return {
                "variante_id": v.id,
                "talla": v.talla.valor if v.talla else None,
                "color": v.color.nombre if v.color else None,
                "color_id": v.color_id,
                "criterio": criterio,
                "stock_total": total,
                "sucursal_id": (
                    sucursal_id if sucursal_id in sucursales else (
                        max(sucursales, key=sucursales.get) if sucursales else None
                    )
                ),
                "stock": (
                    sucursales.get(sucursal_id)
                    if sucursal_id in sucursales
                    else (sucursales[max(sucursales, key=sucursales.get)] if sucursales else 0)
                ),
                "misma_sucursal": sucursal_id in sucursales,
            }

        alternativas = [
            _item(v, "TALLA_MAYOR" if direccion == "MAYOR" else "TALLA_MENOR")
            for v in candidatos if stock.get(v.id)
        ] + [
            _item(v, "MISMA_TALLA_OTRO_COLOR")
            for v in mismo_orden if stock.get(v.id)
        ]

        elegida = next((a for a in alternativas if a["misma_sucursal"]), None)
        if elegida is None:
            elegida = next((a for a in alternativas if a["stock"] >= cantidad), None)

        return {
            "variante_original_id": variante.id,
            "producto_id": variante.producto_id,
            "talla_original": variante.talla.valor if variante.talla else None,
            "direccion": direccion,
            "cantidad": cantidad,
            "encontrada": elegida is not None,
            "variante_id": elegida["variante_id"] if elegida else None,
            "talla": elegida["talla"] if elegida else None,
            "color": elegida["color"] if elegida else None,
            "sucursal_id": elegida["sucursal_id"] if elegida else None,
            "stock": elegida["stock"] if elegida else 0,
            "alternativas": alternativas,
        }


class CambioDevolucionService:
    """Flujo completo de devolución/cambio online y offline."""

    # ------------------------------------------------------------------
    # Búsqueda y validaciones
    # ------------------------------------------------------------------

    @classmethod
    async def buscar_venta(
        cls,
        db: AsyncSession,
        tenant_id: int,
        numero_comprobante: str | None = None,
        numero_orden: str | None = None,
        uuid_offline: str | UUID | None = None,
    ) -> Orden:
        if not any((numero_comprobante, numero_orden, uuid_offline)):
            raise ValidationException(
                "Indique número de comprobante, número de orden o UUID offline"
            )

        consulta = (
            select(Orden)
            .options(selectinload(Orden.detalles))
            .where(Orden.tenant_id == tenant_id)
        )
        if numero_comprobante:
            consulta = consulta.join(
                Comprobante, Comprobante.orden_id == Orden.id
            ).where(Comprobante.numero == numero_comprobante)
        elif numero_orden:
            consulta = consulta.where(Orden.numero_orden == numero_orden)
        else:
            try:
                uuid_valor = uuid_offline if isinstance(uuid_offline, UUID) else UUID(str(uuid_offline))
            except (TypeError, ValueError):
                raise ValidationException("El UUID offline no es válido")
            consulta = consulta.where(
                or_(
                    Orden.uuid_orden == uuid_valor,
                    Orden.id.in_(
                        select(Comprobante.orden_id).where(
                            Comprobante.uuid_offline == uuid_valor,
                            Comprobante.tenant_id == tenant_id,
                        )
                    ),
                )
            )

        orden = (await db.execute(consulta)).scalar_one_or_none()
        if orden is None:
            raise NotFoundException("Venta no encontrada para el tenant")
        if orden.tenant_id != tenant_id:
            raise NotFoundException("Venta no encontrada para el tenant")
        return orden

    @classmethod
    def calcular_fecha_limite_cambio(
        cls, fecha_venta: datetime, promocion: bool = False
    ) -> datetime:
        fecha = _aware(fecha_venta)
        if promocion:
            return fecha + timedelta(hours=settings.PLAZO_CAMBIO_PROMOCION_HORAS)
        return fecha + timedelta(days=settings.PLAZO_CAMBIO_DIAS)

    @classmethod
    def validar_plazo(cls, orden: Orden, fecha_actual: datetime | None = None) -> datetime:
        fecha_actual = _aware(fecha_actual or _ahora())
        limite = orden.fecha_limite_cambio or cls.calcular_fecha_limite_cambio(orden.fecha)
        if _aware(limite) < fecha_actual:
            raise ValidationException(
                "El plazo de cambio/devolución venció el "
                f"{_aware(limite).strftime('%d/%m/%Y %H:%M')} (RN-012)"
            )
        return _aware(limite)

    @classmethod
    async def validar_variante_en_orden(
        cls, db: AsyncSession, orden_id: int, variante_id: int, cantidad: int
    ) -> DetalleOrden:
        resultado = await db.execute(
            select(DetalleOrden).where(
                DetalleOrden.orden_id == orden_id,
                DetalleOrden.variante_producto_id == variante_id,
            )
        )
        detalle = resultado.scalars().first()
        if detalle is None:
            raise ValidationException(
                "La variante devuelta no pertenece a la orden original"
            )
        if int(cantidad) > int(detalle.cantidad):
            raise ValidationException(
                f"La cantidad a devolver ({cantidad}) supera la comprada ({detalle.cantidad})"
            )
        return detalle

    @classmethod
    def validar_motivo(cls, motivo: str | MotivoDevolucion, tipo: str, detalle: str | None = None) -> MotivoDevolucion:
        try:
            motivo_enum = motivo if isinstance(motivo, MotivoDevolucion) else MotivoDevolucion(str(motivo).upper())
        except ValueError:
            raise ValidationException(
                "Motivo inválido; use TALLA_INCORRECTA, COLOR_INCORRECTO, DEFECTO_FABRICA u OTRO"
            )
        tipo_enum = tipo if isinstance(tipo, TipoSolicitudDevolucion) else TipoSolicitudDevolucion(str(tipo).upper())
        if motivo_enum == MotivoDevolucion.OTRO and not (detalle or "").strip():
            raise ValidationException("El motivo OTRO exige una descripción en motivo_detalle")
        if tipo_enum not in (
            TipoSolicitudDevolucion.CAMBIO,
            TipoSolicitudDevolucion.DEVOLUCION,
            TipoSolicitudDevolucion.REPOSICION,
        ):
            raise ValidationException("Tipo inválido; use DEVOLUCION, REPOSICION o CAMBIO")
        return motivo_enum

    @classmethod
    async def reservar_variante_cambio(
        cls,
        db: AsyncSession,
        tenant_id: int,
        sucursal_id: int,
        variante_id: int,
        cantidad: int,
        usuario_id: int | None = None,
    ) -> dict:
        inventario = await InventarioVentasService.reservar_stock(
            db, variante_id, sucursal_id, cantidad, usuario_id
        )
        if inventario.tenant_id != tenant_id:
            raise NotFoundException("Inventario no encontrado para el tenant")
        return {
            "inventario_id": inventario.id,
            "variante_id": variante_id,
            "sucursal_id": sucursal_id,
            "cantidad": cantidad,
            "estado": "RESERVADO",
        }

    @classmethod
    async def liberar_reserva_cambio(
        cls,
        db: AsyncSession,
        tenant_id: int,
        sucursal_id: int,
        variante_id: int,
        cantidad: int,
        usuario_id: int | None = None,
    ) -> dict:
        inventario = await InventarioVentasService.liberar_stock_reserva(
            db, variante_id, sucursal_id, cantidad, usuario_id, comprar=False
        )
        return {"inventario_id": inventario.id, "liberado": True}

    # ------------------------------------------------------------------
    # Diferencia económica
    # ------------------------------------------------------------------

    @classmethod
    def calcular_diferencia(
        cls, precio_original, precio_nuevo, cantidad: int = 1
    ) -> dict:
        diferencia = (
            _decimal(precio_nuevo) - _decimal(precio_original)
        ) * int(cantidad)
        diferencia = diferencia.quantize(CENTAVO)
        if diferencia > CENTAVO:
            tipo = TipoDiferencia.COBRO
            monto = diferencia
        elif diferencia < -CENTAVO:
            tipo = TipoDiferencia.SALDO_FAVOR
            monto = abs(diferencia)
        else:
            tipo = TipoDiferencia.SIN_DIFERENCIA
            monto = Decimal("0.00")
        return {
            "tipo_diferencia": tipo,
            "diferencia": diferencia,
            "monto": monto,
        }

    @classmethod
    async def registrar_pago_diferencia(
        cls,
        db: AsyncSession,
        solicitud: SolicitudDevolucion,
        monto,
        metodo: str = "EFECTIVO",
    ) -> dict:
        monto = _decimal(monto)
        if monto <= 0:
            raise ValidationException("El monto a cobrar debe ser mayor que cero")
        solicitud.monto_pagado_diferencia = monto
        solicitud.observaciones_staff = (
            f"{solicitud.observaciones_staff or ''} Pago de diferencia ({metodo}): {monto}".strip()
        )
        await db.flush()
        return {"solicitud_id": solicitud.id, "monto_pagado": float(monto), "metodo": metodo}

    @classmethod
    async def registrar_saldo_favor(
        cls, db: AsyncSession, solicitud: SolicitudDevolucion, monto
    ) -> dict:
        monto = _decimal(monto)
        if monto <= 0:
            raise ValidationException("El saldo a favor debe ser mayor que cero")
        solicitud.saldo_favor = monto
        await db.flush()
        return {"solicitud_id": solicitud.id, "saldo_favor": float(monto)}

    # ------------------------------------------------------------------
    # Creación de la solicitud
    # ------------------------------------------------------------------

    @classmethod
    def _generar_numero_solicitud(cls) -> str:
        fecha = datetime.now().strftime("%Y%m%d")
        return f"DEV-{fecha}-{secrets.token_hex(2).upper()}"

    @classmethod
    async def crear_solicitud_cambio(
        cls,
        db: AsyncSession,
        tenant_id: int,
        orden: Orden,
        motivo: MotivoDevolucion,
        usuario_id: int | None,
        *,
        tipo: TipoSolicitudDevolucion = TipoSolicitudDevolucion.CAMBIO,
        motivo_detalle: str | None = None,
        sucursal_id: int | None = None,
        es_offline: bool = False,
        uuid_offline: UUID | str | None = None,
        observaciones: str | None = None,
    ) -> SolicitudDevolucion:
        if orden.tenant_id != tenant_id:
            raise NotFoundException("Venta no encontrada para el tenant")

        limite = await cls.validar_plazo_orden(orden)
        solicitud = SolicitudDevolucion(
            numero_solicitud=cls._generar_numero_solicitud(),
            # [EXAMEN-2] Fase C: numeración propia de la reposición (CU18).
            numero_reposicion=(
                cls._generar_numero_reposicion()
                if tipo == TipoSolicitudDevolucion.REPOSICION
                else None
            ),
            cliente_id=orden.cliente_id,
            orden_id=orden.id,
            sucursal_id=sucursal_id or orden.sucursal_id,
            tipo=tipo,
            motivo=motivo,
            motivo_detalle=motivo_detalle,
            estado=EstadoSolicitudDevolucion.PENDIENTE,
            monto_reembolso=Decimal("0.00"),
            observaciones_staff=observaciones,
            tenant_id=tenant_id,
            uuid_offline=(
                uuid_offline if isinstance(uuid_offline, UUID) else (
                    UUID(str(uuid_offline)) if uuid_offline else None
                )
            ),
            es_offline=bool(es_offline),
            estado_sincronizacion=(
                EstadoSincronizacion.PENDIENTE
                if es_offline
                else EstadoSincronizacion.NO_APLICA
            ),
            fecha_limite_cambio=limite,
            usuario_procesa_id=usuario_id,
        )
        db.add(solicitud)
        await db.flush()
        return solicitud

    @classmethod
    async def validar_plazo_orden(cls, orden: Orden) -> datetime:
        return cls.validar_plazo(orden)

    @classmethod
    async def crear_detalle_cambio(
        cls,
        db: AsyncSession,
        solicitud: SolicitudDevolucion,
        detalle_orden: DetalleOrden,
        cantidad: int,
        variante_nueva: VarianteProducto | None = None,
        precio_nuevo: Decimal | None = None,
    ) -> DetalleSolicitudDevolucion:
        if cantidad <= 0:
            raise ValidationException("La cantidad del detalle debe ser mayor que cero")
        precio_original = _decimal(detalle_orden.precio_unitario)
        precio_variante_nueva = (
            _decimal(precio_nuevo)
            if precio_nuevo is not None
            else (_decimal(variante_nueva.precio_variante) if variante_nueva and variante_nueva.precio_variante else None)
        )
        diferencia = (
            ((precio_variante_nueva - precio_original) * cantidad).quantize(CENTAVO)
            if precio_variante_nueva is not None
            else Decimal("0.00")
        )
        detalle = DetalleSolicitudDevolucion(
            solicitud_id=solicitud.id,
            detalle_orden_id=detalle_orden.id,
            variante_producto_id=detalle_orden.variante_producto_id,
            cantidad=cantidad,
            variante_cambio_id=variante_nueva.id if variante_nueva else None,
            precio_unitario=precio_original,
            tenant_id=solicitud.tenant_id,
            precio_variante_nueva=precio_variante_nueva,
            subtotal_diferencia=diferencia,
            estado_item="PENDIENTE",
        )
        db.add(detalle)
        await db.flush()
        return detalle

    # ------------------------------------------------------------------
    # Inventario
    # ------------------------------------------------------------------

    @classmethod
    async def actualizar_inventario_cambio(
        cls,
        db: AsyncSession,
        tenant_id: int,
        sucursal_id: int,
        items_devueltos: Iterable[dict],
        items_nuevos: Iterable[dict],
        usuario_id: int | None = None,
        solicitud_id: int | None = None,
    ) -> dict:
        for item in items_devueltos:
            await InventarioVentasService.reintegrar_stock_devolucion(
                db,
                variante_id=item["variante_id"],
                sucursal_id=sucursal_id,
                cantidad=int(item["cantidad"]),
                usuario_id=usuario_id,
                motivo=f"Reintegro por cambio/devolución (solicitud {solicitud_id or '-'})",
            )
        for item in items_nuevos:
            await InventarioVentasService.liberar_stock_reserva(
                db,
                variante_id=item["variante_id"],
                sucursal_id=sucursal_id,
                cantidad=int(item["cantidad"]),
                usuario_id=usuario_id,
                comprar=True,
            )
        return {
            "devueltos": sum(int(item["cantidad"]) for item in items_devueltos),
            "entregados": sum(int(item["cantidad"]) for item in items_nuevos),
        }

    @classmethod
    async def registrar_movimientos_cambio(
        cls,
        db: AsyncSession,
        tenant_id: int,
        solicitud_id: int,
        items: Iterable[dict],
    ) -> dict:
        """Registra un movimiento informativo `CAMBIO` por cada línea."""
        from app.apps.gestion_catalogo.models import MovimientoInventario, TipoMovimiento

        registrados = 0
        for item in items:
            inventario = (await db.execute(
                select(Inventario).where(
                    Inventario.tenant_id == tenant_id,
                    Inventario.variante_producto_id == item["variante_id"],
                    Inventario.sucursal_id == item["sucursal_id"],
                )
            )).scalar_one_or_none()
            if inventario is None:
                continue
            db.add(MovimientoInventario(
                inventario_id=inventario.id,
                tipo=TipoMovimiento.CAMBIO,
                cantidad=0,
                motivo=f"Cambio de prenda (solicitud {solicitud_id})",
                usuario_id=item.get("usuario_id"),
                tenant_id=tenant_id,
            ))
            registrados += 1
        await db.flush()
        return {"movimientos": registrados}

    # ------------------------------------------------------------------
    # Flujo principal
    # ------------------------------------------------------------------

    @classmethod
    def _generar_numero_reposicion(cls) -> str:
        """[EXAMEN-2] Fase C: REP-AAAAMMDD-XXXXXX (único por tenant)."""
        return f"REP-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(3).upper()}"

    @classmethod
    async def procesar_cambio(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int | None,
        datos: dict,
        es_offline: bool = False,
    ) -> dict:
        """Despacha devolución y reposición sin duplicar la lógica común."""
        tipo = TipoSolicitudDevolucion(str(datos.get("tipo", "CAMBIO")).upper())
        if tipo == TipoSolicitudDevolucion.REPOSICION:
            return await cls._procesar_reposicion(
                db, tenant_id, usuario_id, datos, es_offline
            )
        return await cls._procesar_devolucion(
            db, tenant_id, usuario_id, datos, es_offline
        )

    @classmethod
    async def _procesar_devolucion(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int | None,
        datos: dict,
        es_offline: bool = False,
    ) -> dict:
        """Devolución pura y `CAMBIO` legacy (mismo comportamiento de antes)."""
        tipo = TipoSolicitudDevolucion(str(datos.get("tipo", "CAMBIO")).upper())
        motivo = cls.validar_motivo(
            datos.get("motivo", MotivoDevolucion.OTRO),
            tipo,
            datos.get("motivo_detalle"),
        )

        uuid_offline = datos.get("uuid_offline")
        if uuid_offline:
            existente = (await db.execute(
                select(SolicitudDevolucion).where(
                    SolicitudDevolucion.tenant_id == tenant_id,
                    SolicitudDevolucion.uuid_offline == (
                        uuid_offline if isinstance(uuid_offline, UUID) else UUID(str(uuid_offline))
                    ),
                )
            )).scalar_one_or_none()
            if existente is not None:
                return await cls.serializar(db, existente, idempotente=True)

        orden = await cls.buscar_venta(
            db,
            tenant_id,
            numero_orden=datos.get("numero_orden"),
            numero_comprobante=datos.get("numero_comprobante"),
            uuid_offline=datos.get("uuid_venta_offline"),
        ) if not datos.get("orden_id") else await cls._obtener_orden(db, datos["orden_id"], tenant_id)

        if orden.estado not in ESTADOS_ORDEN_ELEGIBLES:
            raise ValidationException(
                f"No se puede cambiar/devolver una orden en estado {orden.estado.value}"
            )
        cls.validar_plazo(orden)

        items = datos.get("items") or []
        if not items:
            raise ValidationException("Debe indicar al menos una línea para el cambio")

        sucursal_id = datos.get("sucursal_id") or orden.sucursal_id
        if sucursal_id is None:
            raise ValidationException("La venta no tiene sucursal para ajustar inventario")

        detalles_orden = {detalle.id: detalle for detalle in orden.detalles}
        preparados: list[dict] = []
        for item in items:
            detalle_id = int(item["detalle_orden_id"])
            detalle = detalles_orden.get(detalle_id)
            if detalle is None:
                raise ValidationException(
                    f"El detalle {detalle_id} no pertenece a la orden {orden.numero_orden}"
                )
            cantidad = int(item.get("cantidad", 1))
            if cantidad <= 0 or cantidad > int(detalle.cantidad):
                raise ValidationException(
                    f"Cantidad inválida para el detalle {detalle_id}"
                )

            variante_nueva = None
            precio_nuevo = None
            if tipo == TipoSolicitudDevolucion.CAMBIO:
                variante_id = item.get("variante_cambio_id")
                if not variante_id:
                    raise ValidationException(
                        "Todo cambio debe indicar la variante nueva (variante_cambio_id)"
                    )
                variante_nueva = (await db.execute(
                    select(VarianteProducto).where(
                        VarianteProducto.id == int(variante_id),
                        VarianteProducto.tenant_id == tenant_id,
                    )
                )).scalar_one_or_none()
                if variante_nueva is None:
                    raise ValidationException("La variante nueva no pertenece al tenant")
                disponible = await InventarioVentasService.validar_stock_disponible(
                    db, variante_nueva.id, sucursal_id, cantidad
                )
                if not disponible:
                    raise ValidationException(
                        "No hay stock suficiente de la variante nueva en la sucursal"
                    )
                precio_nuevo = _decimal(
                    variante_nueva.precio_variante
                    or (await cls._precio_producto(db, variante_nueva.producto_id, tenant_id))
                )
            preparados.append({
                "detalle": detalle,
                "cantidad": cantidad,
                "variante_nueva": variante_nueva,
                "precio_nuevo": precio_nuevo,
            })

        solicitud = await cls.crear_solicitud_cambio(
            db,
            tenant_id,
            orden,
            motivo,
            usuario_id,
            tipo=tipo,
            motivo_detalle=datos.get("motivo_detalle"),
            sucursal_id=sucursal_id,
            es_offline=es_offline,
            uuid_offline=uuid_offline,
            observaciones=datos.get("observaciones"),
        )

        diferencia_total = Decimal("0.00")
        items_devueltos: list[dict] = []
        items_nuevos: list[dict] = []
        detalles_solicitud: list[DetalleSolicitudDevolucion] = []
        monto_reembolso = Decimal("0.00")

        for preparado in preparados:
            detalle = preparado["detalle"]
            variante_nueva = preparado["variante_nueva"]
            detalle_solicitud = await cls.crear_detalle_cambio(
                db,
                solicitud,
                detalle,
                preparado["cantidad"],
                variante_nueva=variante_nueva,
                precio_nuevo=preparado["precio_nuevo"],
            )
            detalles_solicitud.append(detalle_solicitud)
            items_devueltos.append({
                "variante_id": detalle.variante_producto_id,
                "sucursal_id": sucursal_id,
                "cantidad": preparado["cantidad"],
            })
            if tipo == TipoSolicitudDevolucion.DEVOLUCION:
                monto_reembolso += _decimal(detalle.precio_unitario) * preparado["cantidad"]
            if variante_nueva is not None:
                diferencia_total += _decimal(detalle_solicitud.subtotal_diferencia or 0)
                items_nuevos.append({
                    "variante_id": variante_nueva.id,
                    "sucursal_id": sucursal_id,
                    "cantidad": preparado["cantidad"],
                    "usuario_id": usuario_id,
                })

        # Reserva provisional de las prendas nuevas antes de tocar stock.
        for item in items_nuevos:
            await cls.reservar_variante_cambio(
                db, tenant_id, sucursal_id, item["variante_id"], item["cantidad"], usuario_id
            )

        solicitud.monto_reembolso = monto_reembolso.quantize(CENTAVO)
        solicitud.diferencia_precio = diferencia_total.quantize(CENTAVO)
        if diferencia_total > CENTAVO:
            solicitud.tipo_diferencia = TipoDiferencia.COBRO
        elif diferencia_total < -CENTAVO:
            solicitud.tipo_diferencia = TipoDiferencia.SALDO_FAVOR
        else:
            solicitud.tipo_diferencia = TipoDiferencia.SIN_DIFERENCIA
        await db.flush()

        await cls.actualizar_inventario_cambio(
            db,
            tenant_id,
            sucursal_id,
            items_devueltos,
            items_nuevos,
            usuario_id,
            solicitud.id,
        )
        await cls.registrar_movimientos_cambio(db, tenant_id, solicitud.id, items_nuevos)

        for detalle in detalles_solicitud:
            detalle.estado_item = "ENTREGADO"
        await db.flush()

        if diferencia_total > CENTAVO:
            await cls.registrar_pago_diferencia(
                db,
                solicitud,
                diferencia_total,
                datos.get("metodo_pago_diferencia", "EFECTIVO"),
            )
        elif diferencia_total < -CENTAVO:
            await cls.registrar_saldo_favor(db, solicitud, abs(diferencia_total))

        estado = EstadoSolicitudDevolucion.COMPLETADA
        if tipo == TipoSolicitudDevolucion.DEVOLUCION and monto_reembolso > CENTAVO:
            estado = await cls._procesar_reembolso(db, solicitud, monto_reembolso, usuario_id)

        solicitud.estado = estado
        solicitud.fecha_resolucion = _ahora()
        if es_offline:
            solicitud.estado_sincronizacion = EstadoSincronizacion.CONFIRMADA
        solicitud.nota_devolucion_url = cls.nota_devolucion_url(solicitud.id)
        await db.flush()

        await cls.notificar_devolucion(db, solicitud, "CAMBIO_REGISTRADO")
        return await cls.serializar(db, solicitud)

    # ------------------------------------------------------------------
    # Reposición (CU18)
    # ------------------------------------------------------------------

    @classmethod
    async def _procesar_reposicion(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int | None,
        datos: dict,
        es_offline: bool = False,
    ) -> dict:
        """Reposición: reintegra la prenda devuelta y entrega una nueva.

        Todo el movimiento de inventario y la diferencia económica ocurren en
        la misma transacción (RNF-21); el orden es reservar → descontar →
        sumar → registrar el pago, para que un fallo de stock no deje stock
        sumado por error.
        """
        motivo = cls.validar_motivo(
            datos.get("motivo", MotivoDevolucion.TALLA_INCORRECTA),
            TipoSolicitudDevolucion.REPOSICION,
            datos.get("motivo_detalle"),
        )

        uuid_offline = datos.get("uuid_offline")
        if uuid_offline:
            uuid_valor = (
                uuid_offline if isinstance(uuid_offline, UUID) else UUID(str(uuid_offline))
            )
            existente = (await db.execute(
                select(SolicitudDevolucion).where(
                    SolicitudDevolucion.tenant_id == tenant_id,
                    SolicitudDevolucion.uuid_offline == uuid_valor,
                )
            )).scalar_one_or_none()
            if existente is not None:
                return await cls.serializar(db, existente, idempotente=True)

        orden = (
            await cls._obtener_orden(db, datos["orden_id"], tenant_id)
            if datos.get("orden_id")
            else await cls.buscar_venta(
                db,
                tenant_id,
                numero_orden=datos.get("numero_orden"),
                numero_comprobante=datos.get("numero_comprobante"),
                uuid_offline=datos.get("uuid_venta_offline"),
            )
        )
        if orden.estado not in ESTADOS_ORDEN_ELEGIBLES:
            raise ValidationException(
                f"No se puede reponer una orden en estado {orden.estado.value}"
            )
        cls.validar_plazo(orden)

        items = datos.get("items") or []
        if not items:
            raise ValidationException("Debe indicar al menos una línea para la reposición")

        sucursal_id = datos.get("sucursal_id") or orden.sucursal_id
        if sucursal_id is None:
            raise ValidationException("La venta no tiene sucursal para ajustar inventario")

        detalles_orden = {detalle.id: detalle for detalle in orden.detalles}
        preparados: list[dict] = []
        for item in items:
            detalle_id = int(item["detalle_orden_id"])
            detalle = detalles_orden.get(detalle_id)
            if detalle is None:
                raise ValidationException(
                    f"El detalle {detalle_id} no pertenece a la orden {orden.numero_orden}"
                )
            cantidad = int(item.get("cantidad", 1))
            if cantidad <= 0 or cantidad > int(detalle.cantidad):
                raise ValidationException(f"Cantidad inválida para el detalle {detalle_id}")

            variante_id = item.get("variante_cambio_id")
            if not variante_id:
                raise ValidationException(
                    "Toda reposición debe indicar la variante nueva (variante_cambio_id)"
                )
            variante_nueva = (await db.execute(
                select(VarianteProducto).where(
                    VarianteProducto.id == int(variante_id),
                    VarianteProducto.tenant_id == tenant_id,
                    VarianteProducto.deleted_at.is_(None),
                )
            )).scalar_one_or_none()
            if variante_nueva is None:
                raise ValidationException("La variante nueva no pertenece al tenant")

            # La reposición cambia talla/color, nunca el producto.
            variante_original = await db.get(VarianteProducto, detalle.variante_producto_id)
            if (
                variante_original is not None
                and variante_nueva.producto_id != variante_original.producto_id
            ):
                raise ValidationException(
                    "La reposición debe ser del mismo producto (otra talla o color)"
                )

            disponible = await InventarioVentasService.validar_stock_disponible(
                db, variante_nueva.id, sucursal_id, cantidad
            )
            if not disponible:
                direccion = str(datos.get("direccion_talla") or "MAYOR").upper()
                alternativas = await AlternativaTallaService.buscar(
                    db, tenant_id, variante_nueva.id, sucursal_id, direccion, cantidad
                )
                if datos.get("aceptar_sin_alternativa"):
                    return await cls._registrar_sin_alternativa(
                        db, tenant_id, orden, motivo, usuario_id, datos,
                        sucursal_id, alternativas, es_offline,
                    )
                raise ValidationException(
                    "No hay stock suficiente de la variante nueva en la sucursal"
                    + (
                        f"; alternativas disponibles: {alternativas['alternativas']}"
                        if alternativas.get("alternativas") else ""
                    )
                )

            precio_nuevo = _decimal(
                variante_nueva.precio_variante
                or (await cls._precio_producto(db, variante_nueva.producto_id, tenant_id))
            )
            preparados.append({
                "detalle": detalle,
                "cantidad": cantidad,
                "variante_nueva": variante_nueva,
                "precio_nuevo": precio_nuevo,
            })

        # 1. Reserva provisional de las prendas nuevas.
        for preparado in preparados:
            await cls.reservar_variante_cambio(
                db, tenant_id, sucursal_id,
                preparado["variante_nueva"].id, preparado["cantidad"], usuario_id,
            )

        # 2. Cabecera de la solicitud con numeración de reposición.
        solicitud = await cls.crear_solicitud_cambio(
            db, tenant_id, orden, motivo, usuario_id,
            tipo=TipoSolicitudDevolucion.REPOSICION,
            motivo_detalle=datos.get("motivo_detalle"),
            sucursal_id=sucursal_id,
            es_offline=es_offline,
            uuid_offline=uuid_offline,
            observaciones=datos.get("observaciones"),
        )

        diferencia_total = Decimal("0.00")
        items_devueltos: list[dict] = []
        items_nuevos: list[dict] = []
        detalles_solicitud: list[DetalleSolicitudDevolucion] = []
        for preparado in preparados:
            detalle = preparado["detalle"]
            detalle_solicitud = await cls.crear_detalle_cambio(
                db, solicitud, detalle, preparado["cantidad"],
                variante_nueva=preparado["variante_nueva"],
                precio_nuevo=preparado["precio_nuevo"],
            )
            detalle_solicitud.estado_item = "RESERVADO"
            detalles_solicitud.append(detalle_solicitud)
            items_devueltos.append({
                "variante_id": detalle.variante_producto_id,
                "sucursal_id": sucursal_id,
                "cantidad": preparado["cantidad"],
            })
            items_nuevos.append({
                "variante_id": preparado["variante_nueva"].id,
                "sucursal_id": sucursal_id,
                "cantidad": preparado["cantidad"],
                "usuario_id": usuario_id,
            })
            diferencia_total += _decimal(detalle_solicitud.subtotal_diferencia or 0)

        solicitud.monto_reembolso = Decimal("0.00")
        solicitud.diferencia_precio = diferencia_total.quantize(CENTAVO)
        if diferencia_total > CENTAVO:
            solicitud.tipo_diferencia = TipoDiferencia.COBRO
        elif diferencia_total < -CENTAVO:
            solicitud.tipo_diferencia = TipoDiferencia.SALDO_FAVOR
        else:
            solicitud.tipo_diferencia = TipoDiferencia.SIN_DIFERENCIA
        await db.flush()

        # 3. Descuenta la nueva y suma la devuelta en la misma transacción.
        await cls.actualizar_inventario_cambio(
            db, tenant_id, sucursal_id, items_devueltos, items_nuevos,
            usuario_id, solicitud.id,
        )
        await cls._registrar_movimientos_reposicion(
            db, tenant_id, solicitud.id, items_devueltos, items_nuevos, usuario_id
        )

        for detalle in detalles_solicitud:
            detalle.estado_item = "ENTREGADO"
        await db.flush()

        # 4. Diferencia económica: cobro o saldo a favor.
        if diferencia_total > CENTAVO:
            await cls.registrar_pago_diferencia(
                db, solicitud, diferencia_total,
                datos.get("metodo_pago_diferencia", "EFECTIVO"),
            )
        elif diferencia_total < -CENTAVO:
            await cls.registrar_saldo_favor(db, solicitud, abs(diferencia_total))

        solicitud.estado = EstadoSolicitudDevolucion.COMPLETADA
        solicitud.fecha_resolucion = _ahora()
        if es_offline:
            solicitud.estado_sincronizacion = EstadoSincronizacion.CONFIRMADA
        solicitud.nota_reposicion_url = cls.nota_reposicion_url(solicitud.id)
        await db.flush()

        await cls.notificar_devolucion(db, solicitud, "REPOSICION_REGISTRADA")
        return await cls.serializar(db, solicitud)

    @classmethod
    async def _registrar_sin_alternativa(
        cls,
        db: AsyncSession,
        tenant_id: int,
        orden: Orden,
        motivo: MotivoDevolucion,
        usuario_id: int | None,
        datos: dict,
        sucursal_id: int,
        alternativas: dict,
        es_offline: bool = False,
    ) -> dict:
        """Registra la solicitud en `SIN_ALTERNATIVA` y avisa al cliente."""
        solicitud = await cls.crear_solicitud_cambio(
            db, tenant_id, orden, motivo, usuario_id,
            tipo=TipoSolicitudDevolucion.REPOSICION,
            motivo_detalle=(
                datos.get("motivo_detalle")
                or "Sin talla alternativa con stock; el cliente será notificado"
            ),
            sucursal_id=sucursal_id,
            es_offline=es_offline,
            uuid_offline=datos.get("uuid_offline"),
            observaciones=datos.get("observaciones"),
        )
        solicitud.estado = EstadoSolicitudDevolucion.SIN_ALTERNATIVA
        solicitud.monto_reembolso = Decimal("0.00")
        await db.flush()

        try:
            from app.services.notificacion_service import NotificacionService

            await NotificacionService.crear(
                db,
                tenant_id=tenant_id,
                cliente_id=orden.cliente_id,
                usuario_id=usuario_id,
                tipo="SIN_ALTERNATIVA",
                asunto="No encontramos tu talla",
                mensaje=(
                    "No hay stock de la talla solicitada. Puedes solicitar una "
                    "devolución o un traspaso desde otra sucursal."
                ),
                referencia=("solicitud_devolucion", solicitud.id),
            )
        except Exception as exc:  # pragma: no cover - notificación opcional
            logger.warning("No se pudo notificar SIN_ALTERNATIVA: %s", exc)

        return await cls.serializar(db, solicitud)

    @classmethod
    async def _registrar_movimientos_reposicion(
        cls,
        db: AsyncSession,
        tenant_id: int,
        solicitud_id: int,
        items_devueltos: Iterable[dict],
        items_nuevos: Iterable[dict],
        usuario_id: int | None,
    ) -> dict:
        """Movimientos informativos separados de entrada y salida (CU18)."""
        from app.apps.gestion_catalogo.models import MovimientoInventario, TipoMovimiento

        registrados = 0
        for items, tipo, etiqueta in (
            (items_devueltos, TipoMovimiento.REPOSICION_ENTRADA, "entrada"),
            (items_nuevos, TipoMovimiento.REPOSICION_SALIDA, "salida"),
        ):
            for item in items:
                inventario = (await db.execute(
                    select(Inventario).where(
                        Inventario.tenant_id == tenant_id,
                        Inventario.variante_producto_id == item["variante_id"],
                        Inventario.sucursal_id == item["sucursal_id"],
                    )
                )).scalar_one_or_none()
                if inventario is None:
                    continue
                db.add(MovimientoInventario(
                    inventario_id=inventario.id,
                    tipo=tipo,
                    cantidad=0,
                    motivo=(
                        f"Reposición {etiqueta} de prenda "
                        f"(solicitud {solicitud_id})"
                    ),
                    usuario_id=usuario_id,
                    tenant_id=tenant_id,
                ))
                registrados += 1
        await db.flush()
        return {"movimientos": registrados}

    @classmethod
    def nota_reposicion_url(cls, solicitud_id: int) -> str:
        return f"/api/v1/devoluciones/{solicitud_id}/nota-reposicion"

    @classmethod
    async def generar_nota_reposicion(
        cls, db: AsyncSession, tenant_id: int, solicitud_id: int
    ) -> tuple[bytes, str]:
        """Nota de Reposición en PDF (dos tablas + bloque de diferencia)."""
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        solicitud = await cls.obtener_solicitud(db, tenant_id, solicitud_id)
        if solicitud.tipo != TipoSolicitudDevolucion.REPOSICION:
            raise ValidationException("La solicitud no es una reposición")

        tenant = None
        try:
            from app.apps.gestion_saas.models import Tenant

            tenant = await db.get(Tenant, tenant_id)
        except Exception:  # pragma: no cover - defensivo
            tenant = None

        buffer = io.BytesIO()
        documento = SimpleDocTemplate(buffer, pagesize=letter, topMargin=36, bottomMargin=36)
        estilos = getSampleStyleSheet()
        moneda = getattr(tenant, "moneda", "BOB") or "BOB"
        elementos = [
            Paragraph(getattr(tenant, "razon_social", None) or "FashionStore", estilos["Title"]),
            Paragraph(f"NIT: {getattr(tenant, 'nit', None) or '-'}", estilos["Normal"]),
            Paragraph(
                f"Nota de reposición N.º {solicitud.numero_reposicion or solicitud.numero_solicitud}",
                estilos["Heading2"],
            ),
            Paragraph(
                f"Orden: {getattr(solicitud.orden, 'numero_orden', solicitud.orden_id)} · "
                f"Estado: {solicitud.estado.value}",
                estilos["Normal"],
            ),
            Paragraph(
                f"Fecha: {_aware(solicitud.fecha_solicitud).strftime('%d/%m/%Y %H:%M')} · "
                f"Moneda: {moneda}",
                estilos["Normal"],
            ),
            Paragraph(
                f"Motivo: {solicitud.motivo.value} - {solicitud.motivo_detalle or ''}",
                estilos["Normal"],
            ),
            Spacer(1, 12),
        ]

        def _tabla(titulo: str, campo_variante: str) -> Table:
            filas = [[titulo, "Cantidad", "Precio", "Subtotal"]]
            total = 0.0
            for detalle in solicitud.detalles or []:
                variante = getattr(detalle, campo_variante)
                precio = (
                    detalle.precio_unitario
                    if campo_variante == "variante_producto_id"
                    else detalle.precio_variante_nueva
                )
                cantidad = detalle.cantidad or 0
                subtotal = float(precio or 0) * cantidad
                total += subtotal
                filas.append([
                    f"Variante #{variante or '-'}", str(cantidad),
                    f"{float(precio or 0):.2f}", f"{subtotal:.2f}",
                ])
            filas.append(["Total", "", "", f"{total:.2f}"])
            tabla = Table(filas, repeatRows=1)
            tabla.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E11D48")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]))
            return tabla

        elementos.append(Paragraph("Prenda devuelta", estilos["Heading4"]))
        elementos.append(_tabla("Prenda devuelta", "variante_producto_id"))
        elementos.append(Spacer(1, 10))
        elementos.append(Paragraph("Prenda nueva", estilos["Heading4"]))
        elementos.append(_tabla("Prenda nueva", "variante_cambio_id"))
        elementos.extend([
            Spacer(1, 12),
            Paragraph(
                f"Diferencia: {float(solicitud.diferencia_precio or 0):.2f} "
                f"({solicitud.tipo_diferencia.value if solicitud.tipo_diferencia else 'SIN_DIFERENCIA'})",
                estilos["Normal"],
            ),
            Paragraph(
                f"Pagado por diferencia: {float(solicitud.monto_pagado_diferencia or 0):.2f} · "
                f"Saldo a favor: {float(solicitud.saldo_favor or 0):.2f}",
                estilos["Normal"],
            ),
            Paragraph(
                f"Atendido por usuario #{solicitud.usuario_procesa_id or '-'} · "
                "Firma: ______________________",
                estilos["Normal"],
            ),
        ])
        documento.build(elementos)
        nombre = f"nota_reposicion_{solicitud.numero_reposicion or solicitud.numero_solicitud}.pdf"
        return buffer.getvalue(), nombre

    @classmethod
    async def _procesar_reembolso(
        cls,
        db: AsyncSession,
        solicitud: SolicitudDevolucion,
        monto: Decimal,
        usuario_id: int | None,
    ) -> EstadoSolicitudDevolucion:
        """Reembolsa con `ReembolsoService`; si no aplica queda pendiente (RN-018)."""
        try:
            from app.apps.pagos_diferidos.models import PagoDiferido
            from app.apps.pagos_diferidos.reembolso_service import ReembolsoService

            pago = (await db.execute(
                select(PagoDiferido)
                .where(
                    PagoDiferido.orden_id == solicitud.orden_id,
                    PagoDiferido.tenant_id == solicitud.tenant_id,
                )
                .order_by(PagoDiferido.id.desc())
            )).scalars().first()
            if pago is None or not pago.stripe_payment_intent_id:
                return EstadoSolicitudDevolucion.PENDIENTE_REEMBOLSO
            resultado = await ReembolsoService.reembolsar(
                db,
                pago,
                monto=monto,
                motivo=f"Devolución {solicitud.numero_solicitud}",
                usuario_id=usuario_id,
            )
            solicitud.observaciones_staff = (
                f"{solicitud.observaciones_staff or ''} Reembolso {resultado.get('reembolso_id')}".strip()
            )
            return EstadoSolicitudDevolucion.COMPLETADA
        except Exception as exc:  # pragma: no cover - depende de Stripe
            logger.warning("Reembolso pendiente para %s: %s", solicitud.numero_solicitud, exc)
            solicitud.observaciones_staff = (
                f"{solicitud.observaciones_staff or ''} Reembolso pendiente: {exc}".strip()
            )
            return EstadoSolicitudDevolucion.PENDIENTE_REEMBOLSO

    @classmethod
    async def _precio_producto(
        cls, db: AsyncSession, producto_id: int, tenant_id: int
    ) -> Decimal:
        precio = (await db.execute(
            select(Producto.precio).where(
                Producto.id == producto_id, Producto.tenant_id == tenant_id
            )
        )).scalar_one_or_none()
        return _decimal(precio)

    @classmethod
    async def _obtener_orden(
        cls, db: AsyncSession, orden_id: int, tenant_id: int
    ) -> Orden:
        orden = (await db.execute(
            select(Orden)
            .options(selectinload(Orden.detalles))
            .where(Orden.id == int(orden_id), Orden.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if orden is None:
            raise NotFoundException("Venta no encontrada para el tenant")
        return orden

    # ------------------------------------------------------------------
    # Offline y sincronización
    # ------------------------------------------------------------------

    @classmethod
    async def validar_venta_origen_sincronizada(
        cls, db: AsyncSession, operacion
    ) -> Orden | None:
        payload = operacion.payload_json or {}
        tenant_id = operacion.tenant_id
        orden_id = payload.get("orden_id")
        if orden_id:
            orden = (await db.execute(
                select(Orden).where(
                    Orden.id == int(orden_id), Orden.tenant_id == tenant_id
                )
            )).scalar_one_or_none()
        else:
            try:
                orden = await cls.buscar_venta(
                    db,
                    tenant_id,
                    numero_orden=payload.get("numero_orden"),
                    numero_comprobante=payload.get("numero_comprobante"),
                    uuid_offline=payload.get("uuid_venta_offline"),
                )
            except NotFoundException:
                orden = None
        if orden is None or orden.estado == EstadoOrden.CANCELADO:
            return None
        return orden

    @classmethod
    async def marcar_error_venta_invalida(
        cls,
        db: AsyncSession,
        solicitud: SolicitudDevolucion,
        motivo: str,
    ) -> SolicitudDevolucion:
        solicitud.estado = EstadoSolicitudDevolucion.ERROR_VENTA_INVALIDA
        solicitud.estado_sincronizacion = EstadoSincronizacion.CONFLICTO
        solicitud.motivo_rechazo = motivo
        solicitud.observaciones_staff = (
            f"{solicitud.observaciones_staff or ''} {motivo}".strip()
        )
        solicitud.fecha_resolucion = _ahora()
        await db.flush()
        return solicitud

    @classmethod
    async def procesar_cambio_offline(
        cls, db: AsyncSession, operacion, contexto: dict | None = None
    ) -> dict:
        contexto = contexto or {}
        tenant_id = int(contexto.get("tenant_id") or operacion.tenant_id)
        usuario_id = contexto.get("usuario_id") or operacion.usuario_id
        payload = dict(operacion.payload_json or {})
        payload.setdefault("uuid_offline", str(operacion.uuid_operacion))

        # Idempotencia del reintento: un mismo UUID no debe duplicar la
        # solicitud aunque la venta de origen haya quedado inválida.
        uuid_valor = payload.get("uuid_offline")
        if uuid_valor:
            uuid_valor = (
                uuid_valor if isinstance(uuid_valor, UUID) else UUID(str(uuid_valor))
            )
            existente = (await db.execute(
                select(SolicitudDevolucion).where(
                    SolicitudDevolucion.tenant_id == tenant_id,
                    SolicitudDevolucion.uuid_offline == uuid_valor,
                )
            )).scalar_one_or_none()
            if existente is not None:
                if existente.estado == EstadoSolicitudDevolucion.ERROR_VENTA_INVALIDA:
                    return {
                        "estado": EstadoSolicitudDevolucion.ERROR_VENTA_INVALIDA.value,
                        "solicitud_id": existente.id,
                        "motivo": existente.motivo_rechazo,
                        "idempotente": True,
                    }
                return await cls.serializar(db, existente, idempotente=True)

        orden = await cls.validar_venta_origen_sincronizada(db, operacion)
        if orden is None:
            solicitud = await cls.crear_solicitud_cambio(
                db,
                tenant_id,
                await cls._orden_placeholder(db, tenant_id, payload),
                MotivoDevolucion.OTRO,
                usuario_id,
                tipo=TipoSolicitudDevolucion(str(payload.get("tipo", "DEVOLUCION")).upper()),
                motivo_detalle=payload.get("motivo_detalle") or "Venta de origen inválida",
                es_offline=True,
                uuid_offline=payload["uuid_offline"],
            )
            await cls.marcar_error_venta_invalida(
                db, solicitud, "La venta original no existe o está anulada"
            )
            return {
                "estado": EstadoSolicitudDevolucion.ERROR_VENTA_INVALIDA.value,
                "solicitud_id": solicitud.id,
                "motivo": "La venta original no existe o está anulada",
            }

        return await cls.procesar_cambio(
            db, tenant_id, usuario_id, payload, es_offline=True
        )

    @classmethod
    async def _orden_placeholder(
        cls, db: AsyncSession, tenant_id: int, payload: dict
    ) -> Orden:
        """Devuelve una orden cancelada del tenant para dejar trazabilidad del error."""
        orden = (await db.execute(
            select(Orden).where(
                Orden.tenant_id == tenant_id,
                Orden.estado == EstadoOrden.CANCELADO,
            ).limit(1)
        )).scalars().first()
        if orden is not None:
            return orden
        # Si no hay orden cancelada se usa la referenciada aunque no exista en BD.
        orden_id = payload.get("orden_id")
        if orden_id:
            return await cls._obtener_orden(db, orden_id, tenant_id)
        raise OperacionRechazada(
            "No se pudo registrar la devolución: venta de origen inválida",
            tipo_conflicto="DEVOLUCION_INVALIDA",
        )

    # ------------------------------------------------------------------
    # Aprobación, rechazo e historial
    # ------------------------------------------------------------------

    @classmethod
    async def aprobar_solicitud(
        cls, db: AsyncSession, tenant_id: int, solicitud_id: int, usuario_id: int | None
    ) -> dict:
        solicitud = await cls.obtener_solicitud(db, tenant_id, solicitud_id)
        if solicitud.estado not in (
            EstadoSolicitudDevolucion.PENDIENTE,
            EstadoSolicitudDevolucion.EN_REVISION,
        ):
            raise ConflictException("La solicitud no está pendiente de aprobación")
        solicitud.estado = EstadoSolicitudDevolucion.APROBADA
        solicitud.revisado_por_id = usuario_id
        solicitud.fecha_resolucion = _ahora()
        await db.flush()
        return await cls.serializar(db, solicitud)

    @classmethod
    async def rechazar_solicitud(
        cls,
        db: AsyncSession,
        tenant_id: int,
        solicitud_id: int,
        usuario_id: int | None,
        motivo: str,
    ) -> dict:
        solicitud = await cls.obtener_solicitud(db, tenant_id, solicitud_id)
        if solicitud.estado == EstadoSolicitudDevolucion.COMPLETADA:
            raise ConflictException("No se puede rechazar una solicitud completada")
        solicitud.estado = EstadoSolicitudDevolucion.RECHAZADA
        solicitud.revisado_por_id = usuario_id
        solicitud.motivo_rechazo = motivo
        solicitud.fecha_resolucion = _ahora()
        for detalle in solicitud.detalles or []:
            if detalle.estado_item == "RESERVADO" and detalle.variante_cambio_id and solicitud.sucursal_id:
                await cls.liberar_reserva_cambio(
                    db,
                    tenant_id,
                    solicitud.sucursal_id,
                    detalle.variante_cambio_id,
                    detalle.cantidad,
                    usuario_id,
                )
            detalle.estado_item = "RECHAZADO"
        await db.flush()
        return await cls.serializar(db, solicitud)

    @classmethod
    async def confirmar_cambio(
        cls,
        db: AsyncSession,
        tenant_id: int,
        solicitud_id: int,
        usuario_id: int | None = None,
    ) -> dict:
        """Cierra la operación online y actualiza el comprobante (plan, CU13).

        Se usa tras `aprobar_solicitud` cuando el ajuste de inventario ya se
        aplicó: deja la solicitud `COMPLETADA`, los ítems `ENTREGADO` y guarda
        la nota de devolución en el comprobante de la venta. Es idempotente:
        confirmar una solicitud ya completada solo vuelve a serializarla.
        """
        solicitud = await cls.obtener_solicitud(db, tenant_id, solicitud_id)
        if solicitud.estado in (
            EstadoSolicitudDevolucion.RECHAZADA,
            EstadoSolicitudDevolucion.ERROR_VENTA_INVALIDA,
        ):
            raise ConflictException("La solicitud rechazada no se puede confirmar")

        if solicitud.estado != EstadoSolicitudDevolucion.COMPLETADA:
            for detalle in solicitud.detalles or []:
                if detalle.estado_item in (None, "PENDIENTE", "RESERVADO"):
                    detalle.estado_item = "ENTREGADO"
            solicitud.estado = EstadoSolicitudDevolucion.COMPLETADA
            solicitud.usuario_procesa_id = usuario_id or solicitud.usuario_procesa_id
            solicitud.fecha_resolucion = solicitud.fecha_resolucion or _ahora()
            if solicitud.es_offline:
                solicitud.estado_sincronizacion = EstadoSincronizacion.CONFIRMADA

        # El comprobante de la venta queda enlazado a la nota, aunque la
        # solicitud ya estuviera completada (confirmación idempotente).
        solicitud.nota_devolucion_url = cls.nota_devolucion_url(solicitud.id)
        comprobante = (await db.execute(
            select(Comprobante).where(
                Comprobante.orden_id == solicitud.orden_id,
                Comprobante.tenant_id == tenant_id,
            )
        )).scalars().first()
        if comprobante is not None and not comprobante.archivo_pdf:
            comprobante.archivo_pdf = solicitud.nota_devolucion_url
        await db.flush()

        await cls.notificar_devolucion(db, solicitud, "CAMBIO_CONFIRMADO")
        return await cls.serializar(db, solicitud)

    @classmethod
    async def listar_pendientes_offline(
        cls, db: AsyncSession, tenant_id: int
    ) -> list[dict]:
        resultado = await db.execute(
            select(SolicitudDevolucion)
            .options(selectinload(SolicitudDevolucion.detalles))
            .where(
                SolicitudDevolucion.tenant_id == tenant_id,
                SolicitudDevolucion.es_offline.is_(True),
                SolicitudDevolucion.estado_sincronizacion.in_([
                    EstadoSincronizacion.PENDIENTE,
                    EstadoSincronizacion.ENVIADA,
                    EstadoSincronizacion.RECHAZADA,
                ]),
            )
            .order_by(SolicitudDevolucion.fecha_solicitud.asc())
        )
        return [cls._serializar_solicitud(solicitud) for solicitud in resultado.scalars().all()]

    @classmethod
    async def consultar_historial(
        cls,
        db: AsyncSession,
        tenant_id: int,
        tipo: str | None = None,
        estado: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        condiciones = [SolicitudDevolucion.tenant_id == tenant_id]
        if tipo:
            condiciones.append(SolicitudDevolucion.tipo == TipoSolicitudDevolucion(tipo.upper()))
        if estado:
            condiciones.append(
                SolicitudDevolucion.estado == EstadoSolicitudDevolucion(estado.upper())
            )
        if desde:
            condiciones.append(SolicitudDevolucion.fecha_solicitud >= desde)
        if hasta:
            condiciones.append(SolicitudDevolucion.fecha_solicitud <= hasta)
        resultado = await db.execute(
            select(SolicitudDevolucion)
            .options(selectinload(SolicitudDevolucion.detalles))
            .where(*condiciones)
            .order_by(SolicitudDevolucion.fecha_solicitud.desc())
            .offset(max(0, skip))
            .limit(min(max(1, limit), 100))
        )
        return [cls._serializar_solicitud(solicitud) for solicitud in resultado.scalars().all()]

    @classmethod
    async def obtener_solicitud(
        cls, db: AsyncSession, tenant_id: int, solicitud_id: int
    ) -> SolicitudDevolucion:
        solicitud = (await db.execute(
            select(SolicitudDevolucion)
            .options(
                selectinload(SolicitudDevolucion.detalles),
                selectinload(SolicitudDevolucion.orden),
            )
            .where(
                SolicitudDevolucion.id == solicitud_id,
                SolicitudDevolucion.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if solicitud is None:
            raise NotFoundException("Solicitud de devolución no encontrada")
        return solicitud

    # ------------------------------------------------------------------
    # Nota de devolución
    # ------------------------------------------------------------------

    @classmethod
    def nota_devolucion_url(cls, solicitud_id: int) -> str:
        return f"/api/v1/devoluciones/{solicitud_id}/nota"

    @classmethod
    async def generar_nota_devolucion(
        cls, db: AsyncSession, tenant_id: int, solicitud_id: int
    ) -> tuple[bytes, str]:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        solicitud = await cls.obtener_solicitud(db, tenant_id, solicitud_id)
        tenant = None
        try:
            from app.apps.gestion_saas.models import Tenant

            tenant = await db.get(Tenant, tenant_id)
        except Exception:  # pragma: no cover - defensivo
            tenant = None

        buffer = io.BytesIO()
        documento = SimpleDocTemplate(buffer, pagesize=letter, topMargin=36, bottomMargin=36)
        estilos = getSampleStyleSheet()
        branding = {
            "razon_social": getattr(tenant, "razon_social", None) or "FashionStore",
            "nit": getattr(tenant, "nit", None) or "-",
            "moneda": getattr(tenant, "moneda", "BOB") or "BOB",
        }
        elementos = [
            Paragraph(branding["razon_social"], estilos["Title"]),
            Paragraph(f"NIT: {branding['nit']}", estilos["Normal"]),
            Paragraph(
                f"Nota de devolución/cambio N.º {solicitud.numero_solicitud}",
                estilos["Heading2"],
            ),
            Paragraph(
                f"Orden: {getattr(solicitud.orden, 'numero_orden', solicitud.orden_id)} · "
                f"Tipo: {solicitud.tipo.value} · Estado: {solicitud.estado.value}",
                estilos["Normal"],
            ),
            Paragraph(
                f"Fecha: {_aware(solicitud.fecha_solicitud).strftime('%d/%m/%Y %H:%M')} · "
                f"Moneda: {branding['moneda']}",
                estilos["Normal"],
            ),
            Paragraph(
                f"Motivo: {solicitud.motivo.value} - {solicitud.motivo_detalle or ''}",
                estilos["Normal"],
            ),
            Spacer(1, 12),
        ]

        filas = [["Prenda devuelta", "Cantidad", "Precio", "Prenda nueva", "Diferencia"]]
        for detalle in solicitud.detalles or []:
            filas.append([
                str(detalle.variante_producto_id or "-"),
                str(detalle.cantidad),
                f"{float(detalle.precio_unitario):.2f}",
                str(detalle.variante_cambio_id or "-"),
                f"{float(detalle.subtotal_diferencia or 0):.2f}",
            ])
        tabla = Table(filas, repeatRows=1)
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E11D48")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        elementos.append(tabla)
        elementos.extend([
            Spacer(1, 12),
            Paragraph(
                f"Diferencia: {float(solicitud.diferencia_precio or 0):.2f} "
                f"({solicitud.tipo_diferencia.value if solicitud.tipo_diferencia else 'SIN_DIFERENCIA'})",
                estilos["Normal"],
            ),
            Paragraph(
                f"Reembolso: {float(solicitud.monto_reembolso or 0):.2f} · "
                f"Saldo a favor: {float(solicitud.saldo_favor or 0):.2f}",
                estilos["Normal"],
            ),
        ])
        documento.build(elementos)
        return buffer.getvalue(), f"nota_devolucion_{solicitud.numero_solicitud}.pdf"

    @classmethod
    async def notificar_devolucion(
        cls, db: AsyncSession, solicitud: SolicitudDevolucion, evento: str
    ) -> dict:
        logger.info(
            "devolucion_evento tenant_id=%s solicitud=%s evento=%s",
            solicitud.tenant_id,
            solicitud.numero_solicitud,
            evento,
        )
        try:
            from app.services.firebase_service import FirebaseService

            if solicitud.sucursal_id:
                await FirebaseService.enviar_notificacion_sucursal(
                    db=db,
                    sucursal_id=solicitud.sucursal_id,
                    titulo="Actualización de devolución/cambio",
                    cuerpo=f"Solicitud {solicitud.numero_solicitud}: {evento}",
                    data={"solicitud_id": str(solicitud.id), "evento": evento},
                )
        except Exception as exc:  # pragma: no cover - notificación opcional
            logger.warning("No se pudo notificar la devolución: %s", exc)
        return {"notificado":True, "evento": evento}

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    @classmethod
    async def serializar(
        cls, db: AsyncSession, solicitud: SolicitudDevolucion, idempotente: bool = False
    ) -> dict:
        completa = await cls.obtener_solicitud(db, solicitud.tenant_id, solicitud.id)
        datos = cls._serializar_solicitud(completa)
        datos["idempotente"] = idempotente
        return datos

    @staticmethod
    def _serializar_solicitud(solicitud: SolicitudDevolucion) -> dict:
        return {
            "id": solicitud.id,
            "numero_solicitud": solicitud.numero_solicitud,
            "orden_id": solicitud.orden_id,
            "cliente_id": solicitud.cliente_id,
            "sucursal_id": solicitud.sucursal_id,
            "tipo": solicitud.tipo.value if hasattr(solicitud.tipo, "value") else solicitud.tipo,
            "motivo": solicitud.motivo.value if hasattr(solicitud.motivo, "value") else solicitud.motivo,
            "motivo_detalle": solicitud.motivo_detalle,
            "estado": solicitud.estado.value if hasattr(solicitud.estado, "value") else solicitud.estado,
            "estado_sincronizacion": (
                solicitud.estado_sincronizacion.value
                if hasattr(solicitud.estado_sincronizacion, "value")
                else solicitud.estado_sincronizacion
            ),
            "es_offline": bool(solicitud.es_offline),
            "uuid_offline": str(solicitud.uuid_offline) if solicitud.uuid_offline else None,
            "fecha_solicitud": (
                solicitud.fecha_solicitud.isoformat() if solicitud.fecha_solicitud else None
            ),
            "fecha_limite_cambio": (
                solicitud.fecha_limite_cambio.isoformat() if solicitud.fecha_limite_cambio else None
            ),
            "monto_reembolso": float(solicitud.monto_reembolso or 0),
            "diferencia_precio": float(solicitud.diferencia_precio or 0),
            "tipo_diferencia": (
                solicitud.tipo_diferencia.value if solicitud.tipo_diferencia else None
            ),
            "monto_pagado_diferencia": float(solicitud.monto_pagado_diferencia or 0),
            "saldo_favor": float(solicitud.saldo_favor or 0),
            "motivo_rechazo": solicitud.motivo_rechazo,
            "nota_devolucion_url": solicitud.nota_devolucion_url,
            "numero_reposicion": solicitud.numero_reposicion,
            "nota_reposicion_url": solicitud.nota_reposicion_url,
            "detalles": [
                {
                    "id": detalle.id,
                    "detalle_orden_id": detalle.detalle_orden_id,
                    "variante_producto_id": detalle.variante_producto_id,
                    "variante_cambio_id": detalle.variante_cambio_id,
                    "cantidad": detalle.cantidad,
                    "precio_unitario": float(detalle.precio_unitario or 0),
                    "precio_variante_nueva": (
                        float(detalle.precio_variante_nueva)
                        if detalle.precio_variante_nueva is not None
                        else None
                    ),
                    "subtotal_diferencia": float(detalle.subtotal_diferencia or 0),
                    "estado_item": detalle.estado_item,
                }
                for detalle in (solicitud.detalles or [])
            ],
        }
