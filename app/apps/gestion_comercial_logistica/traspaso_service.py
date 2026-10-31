"""[EXAMEN-2] Traspasos de stock entre sucursales (CU14).

Máquina de estados del plan 9.3.8:

    EN_TRANSITO -> RECIBIDO | RECHAZADO_PARCIAL | CANCELADO

Reglas duras:
- origen y destino pertenecen al mismo tenant y son distintos;
- al crear se descuenta el origen y se registra `TRASPASO_SALIDA`;
- al recibir se suma el destino, se registra `TRASPASO_ENTRADA` y la merma;
- en cada línea `cantidad_recibida + cantidad_merma = cantidad` (RN-013);
- un traspaso nunca crea ni destruye stock: lo redistribuye o lo registra
  como merma.
"""
from __future__ import annotations

import io
import logging
import secrets
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.apps.gestion_catalogo.models import (
    EstadoStock,
    Inventario,
    MovimientoInventario,
    Producto,
    Sucursal,
    TipoMovimiento,
    VarianteProducto,
)
from app.apps.gestion_ventas.models import DetalleOrden, EstadoOrden, Orden
from app.apps.gestion_comercial_logistica.models import (
    DetalleTraspaso,
    EstadoTraspaso,
    Traspaso,
)

logger = logging.getLogger(__name__)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _campo(datos, clave: str, defecto=None):
    """Lee un campo de un dict o de un objeto tipo schema."""
    if datos is None:
        return defecto
    if isinstance(datos, dict):
        return datos.get(clave, defecto)
    return getattr(datos, clave, defecto)


class TraspasoService:
    """Creación, recepción, rechazo y cancelación de traspasos."""

    # ------------------------------------------------------------------
    # Validaciones
    # ------------------------------------------------------------------

    @classmethod
    async def validar_sucursales(
        cls, db: AsyncSession, tenant_id: int, origen_id: int, destino_id: int
    ) -> tuple[Sucursal, Sucursal]:
        if int(origen_id) == int(destino_id):
            raise ValidationException(
                "La sucursal de origen y la de destino deben ser distintas"
            )
        resultado = await db.execute(
            select(Sucursal).where(
                Sucursal.id.in_([int(origen_id), int(destino_id)]),
                Sucursal.tenant_id == tenant_id,
            )
        )
        sucursales = {sucursal.id: sucursal for sucursal in resultado.scalars().all()}
        if int(origen_id) not in sucursales:
            raise NotFoundException("Sucursal de origen no encontrada para el tenant")
        if int(destino_id) not in sucursales:
            raise NotFoundException("Sucursal de destino no encontrada para el tenant")
        origen = sucursales[int(origen_id)]
        destino = sucursales[int(destino_id)]
        if origen.deleted_at is not None or destino.deleted_at is not None:
            raise ValidationException("No se puede operar con una sucursal dada de baja")
        return origen, destino

    @classmethod
    async def validar_variantes(
        cls, db: AsyncSession, tenant_id: int, detalles: Iterable
    ) -> dict[int, VarianteProducto]:
        ids = {int(_campo(item, "variante_producto_id")) for item in detalles}
        if not ids:
            raise ValidationException("El traspaso debe tener al menos una línea")
        resultado = await db.execute(
            select(VarianteProducto).where(
                VarianteProducto.id.in_(ids),
                VarianteProducto.tenant_id == tenant_id,
            )
        )
        variantes = {variante.id: variante for variante in resultado.scalars().all()}
        faltantes = ids - set(variantes)
        if faltantes:
            raise NotFoundException(
                "Variantes no encontradas para el tenant: "
                + ", ".join(str(item) for item in sorted(faltantes))
            )
        return variantes

    @classmethod
    async def validar_stock_origen(
        cls, db: AsyncSession, tenant_id: int, variante_id: int, origen_id: int, cantidad: int
    ) -> Inventario:
        if int(cantidad) <= 0:
            raise ValidationException("La cantidad de cada línea debe ser mayor que cero")
        inventario = (await db.execute(
            select(Inventario).where(
                Inventario.tenant_id == tenant_id,
                Inventario.variante_producto_id == int(variante_id),
                Inventario.sucursal_id == int(origen_id),
            )
        )).scalar_one_or_none()
        if inventario is None:
            raise ValidationException(
                f"La variante {variante_id} no tiene inventario en la sucursal de origen"
            )
        disponible = max(0, int(inventario.cantidad or 0) - int(inventario.cantidad_reservada or 0))
        if disponible < int(cantidad):
            raise ValidationException(
                f"Stock insuficiente en origen para la variante {variante_id}: "
                f"disponible {disponible}, solicitado {cantidad}"
            )
        return inventario

    # ------------------------------------------------------------------
    # Inventario
    # ------------------------------------------------------------------

    @classmethod
    async def descontar_stock_origen(
        cls,
        db: AsyncSession,
        tenant_id: int,
        variante_id: int,
        origen_id: int,
        cantidad: int,
    ) -> Inventario:
        # UPDATE atómico: solo descuenta si el disponible alcanza.
        resultado = await db.execute(
            update(Inventario)
            .where(
                Inventario.tenant_id == tenant_id,
                Inventario.variante_producto_id == int(variante_id),
                Inventario.sucursal_id == int(origen_id),
                (Inventario.cantidad - Inventario.cantidad_reservada) >= int(cantidad),
            )
            .values(cantidad=Inventario.cantidad - int(cantidad))
        )
        if resultado.rowcount == 0:
            inventario = await cls.validar_stock_origen(
                db, tenant_id, variante_id, origen_id, cantidad
            )
            raise ValidationException(
                f"No se pudo descontar stock de la variante {variante_id} "
                f"(disponible {inventario.cantidad_disponible})"
            )
        inventario = (await db.execute(
            select(Inventario).where(
                Inventario.tenant_id == tenant_id,
                Inventario.variante_producto_id == int(variante_id),
                Inventario.sucursal_id == int(origen_id),
            )
        )).scalar_one()
        if int(inventario.cantidad or 0) == 0:
            inventario.estado = EstadoStock.AGOTADO
        await db.flush()
        return inventario

    @classmethod
    async def _inventario_destino(
        cls, db: AsyncSession, tenant_id: int, variante_id: int, destino_id: int
    ) -> Inventario:
        inventario = (await db.execute(
            select(Inventario).where(
                Inventario.tenant_id == tenant_id,
                Inventario.variante_producto_id == int(variante_id),
                Inventario.sucursal_id == int(destino_id),
            )
        )).scalar_one_or_none()
        if inventario is None:
            inventario = Inventario(
                variante_producto_id=int(variante_id),
                sucursal_id=int(destino_id),
                cantidad=0,
                cantidad_reservada=0,
                cantidad_vendida=0,
                estado=EstadoStock.AGOTADO,
                tenant_id=tenant_id,
            )
            db.add(inventario)
            await db.flush()
        return inventario

    @classmethod
    async def sumar_stock_destino(
        cls, db: AsyncSession, tenant_id: int, variante_id: int, destino_id: int, cantidad: int
    ) -> Inventario:
        inventario = await cls._inventario_destino(
            db, tenant_id, variante_id, destino_id
        )
        inventario.cantidad = int(inventario.cantidad or 0) + int(cantidad)
        if int(inventario.cantidad) > 0:
            inventario.estado = EstadoStock.DISPONIBLE
        await db.flush()
        return inventario

    @classmethod
    async def devolver_stock_origen(
        cls, db: AsyncSession, tenant_id: int, variante_id: int, origen_id: int, cantidad: int
    ) -> Inventario:
        inventario = (await db.execute(
            select(Inventario).where(
                Inventario.tenant_id == tenant_id,
                Inventario.variante_producto_id == int(variante_id),
                Inventario.sucursal_id == int(origen_id),
            )
        )).scalar_one_or_none()
        if inventario is None:
            inventario = Inventario(
                variante_producto_id=int(variante_id),
                sucursal_id=int(origen_id),
                cantidad=0,
                cantidad_reservada=0,
                cantidad_vendida=0,
                estado=EstadoStock.AGOTADO,
                tenant_id=tenant_id,
            )
            db.add(inventario)
            await db.flush()
        inventario.cantidad = int(inventario.cantidad or 0) + int(cantidad)
        if int(inventario.cantidad) > 0:
            inventario.estado = EstadoStock.DISPONIBLE
        await db.flush()
        return inventario

    # ------------------------------------------------------------------
    # Movimientos
    # ------------------------------------------------------------------

    @classmethod
    async def _registrar_movimiento(
        cls,
        db: AsyncSession,
        tenant_id: int,
        inventario_id: int,
        tipo: TipoMovimiento,
        cantidad: int,
        motivo: str,
        usuario_id: int | None = None,
        referencia_uuid=None,
    ) -> MovimientoInventario:
        movimiento = MovimientoInventario(
            inventario_id=int(inventario_id),
            tipo=tipo,
            cantidad=int(cantidad),
            motivo=motivo,
            usuario_id=usuario_id,
            tenant_id=tenant_id,
            referencia_uuid=referencia_uuid,
        )
        db.add(movimiento)
        await db.flush()
        return movimiento

    @classmethod
    async def registrar_movimiento_salida(
        cls, db: AsyncSession, tenant_id: int, traspaso: Traspaso, detalle: DetalleTraspaso,
        usuario_id: int | None = None,
    ) -> MovimientoInventario:
        inventario = (await db.execute(
            select(Inventario).where(
                Inventario.tenant_id == tenant_id,
                Inventario.variante_producto_id == detalle.variante_producto_id,
                Inventario.sucursal_id == traspaso.sucursal_origen_id,
            )
        )).scalar_one()
        return await cls._registrar_movimiento(
            db,
            tenant_id,
            inventario.id,
            TipoMovimiento.TRASPASO_SALIDA,
            -int(detalle.cantidad),
            f"Traspaso {traspaso.numero} hacia sucursal {traspaso.sucursal_destino_id}",
            usuario_id,
        )

    @classmethod
    async def registrar_movimiento_entrada(
        cls, db: AsyncSession, tenant_id: int, traspaso: Traspaso, detalle: DetalleTraspaso,
        cantidad: int, usuario_id: int | None = None,
    ) -> MovimientoInventario:
        inventario = await cls._inventario_destino(
            db, tenant_id, detalle.variante_producto_id, traspaso.sucursal_destino_id
        )
        return await cls._registrar_movimiento(
            db,
            tenant_id,
            inventario.id,
            TipoMovimiento.TRASPASO_ENTRADA,
            int(cantidad),
            f"Recepción del traspaso {traspaso.numero}",
            usuario_id,
        )

    @classmethod
    async def registrar_merma(
        cls,
        db: AsyncSession,
        tenant_id: int,
        traspaso: Traspaso,
        detalle: DetalleTraspaso,
        cantidad: int,
        motivo: str,
        usuario_id: int | None = None,
    ) -> MovimientoInventario:
        inventario = await cls._inventario_destino(
            db, tenant_id, detalle.variante_producto_id, traspaso.sucursal_destino_id
        )
        return await cls._registrar_movimiento(
            db,
            tenant_id,
            inventario.id,
            TipoMovimiento.MERMA,
            int(cantidad),
            f"Merma del traspaso {traspaso.numero}: {motivo}",
            usuario_id,
        )

    # ------------------------------------------------------------------
    # Creación
    # ------------------------------------------------------------------

    @classmethod
    async def marcar_en_transito(
        cls, db: AsyncSession, traspaso: Traspaso
    ) -> Traspaso:
        """Deja el traspaso `EN_TRANSITO` y registra `fecha_envio` (plan, CU14)."""
        traspaso.estado = EstadoTraspaso.EN_TRANSITO
        if traspaso.fecha_envio is None:
            traspaso.fecha_envio = _ahora()
        await db.flush()
        return traspaso

    @classmethod
    def _generar_numero(cls) -> str:
        return f"TRASP-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"

    @classmethod
    async def crear_traspaso(
        cls, db: AsyncSession, tenant_id: int, usuario_id: int | None, datos
    ) -> dict:
        origen_id = _campo(datos, "sucursal_origen_id")
        destino_id = _campo(datos, "sucursal_destino_id")
        if origen_id is None or destino_id is None:
            raise ValidationException("Debe indicar sucursal de origen y de destino")

        await cls.validar_sucursales(db, tenant_id, origen_id, destino_id)

        detalles = list(_campo(datos, "detalles", None) or [])
        if not detalles:
            raise ValidationException("El traspaso debe incluir al menos una línea")
        variantes = await cls.validar_variantes(db, tenant_id, detalles)

        # Validación previa de todas las líneas antes de tocar stock.
        vistos: set[int] = set()
        lineas: list[dict] = []
        for item in detalles:
            variante_id = int(_campo(item, "variante_producto_id"))
            cantidad = int(_campo(item, "cantidad", 0) or 0)
            if variante_id in vistos:
                raise ValidationException(
                    f"La variante {variante_id} está repetida en el traspaso"
                )
            vistos.add(variante_id)
            await cls.validar_stock_origen(
                db, tenant_id, variante_id, origen_id, cantidad
            )
            lineas.append({
                "variante_id": variante_id,
                "cantidad": cantidad,
                "observaciones": _campo(item, "observaciones"),
                "variante": variantes[variante_id],
            })

        traspaso = Traspaso(
            tenant_id=tenant_id,
            numero=cls._generar_numero(),
            sucursal_origen_id=int(origen_id),
            sucursal_destino_id=int(destino_id),
            usuario_id=usuario_id,
            motivo=_campo(datos, "motivo"),
            observaciones=_campo(datos, "observaciones"),
        )
        db.add(traspaso)
        await db.flush()
        await cls.marcar_en_transito(db, traspaso)

        for linea in lineas:
            detalle = DetalleTraspaso(
                tenant_id=tenant_id,
                traspaso_id=traspaso.id,
                variante_producto_id=linea["variante_id"],
                cantidad=linea["cantidad"],
                cantidad_recibida=0,
                cantidad_merma=0,
                observaciones=linea["observaciones"],
            )
            db.add(detalle)
            await db.flush()

            await cls.descontar_stock_origen(
                db, tenant_id, linea["variante_id"], origen_id, linea["cantidad"]
            )
            await cls.registrar_movimiento_salida(
                db, tenant_id, traspaso, detalle, usuario_id
            )

        await cls.notificar_sucursal_destino(db, traspaso)
        return await cls.obtener_traspaso(db, traspaso.id, tenant_id)

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    @classmethod
    async def _cargar_traspaso(
        cls, db: AsyncSession, traspaso_id: int, tenant_id: int
    ) -> Traspaso:
        traspaso = (await db.execute(
            select(Traspaso)
            .options(selectinload(Traspaso.detalles))
            .where(
                Traspaso.id == int(traspaso_id),
                Traspaso.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if traspaso is None:
            raise NotFoundException("Traspaso no encontrado para el tenant")
        return traspaso

    @classmethod
    async def obtener_traspaso(
        cls, db: AsyncSession, traspaso_id: int, tenant_id: int
    ) -> dict:
        traspaso = await cls._cargar_traspaso(db, traspaso_id, tenant_id)
        return await cls.serializar(db, traspaso)

    @classmethod
    async def listar_traspasos(
        cls,
        db: AsyncSession,
        tenant_id: int,
        estado: str | None = None,
        sucursal_id: int | None = None,
        producto_id: int | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        consulta = (
            select(Traspaso)
            .options(selectinload(Traspaso.detalles))
            .where(Traspaso.tenant_id == tenant_id)
        )
        if estado:
            try:
                consulta = consulta.where(
                    Traspaso.estado == EstadoTraspaso(str(estado).upper())
                )
            except ValueError:
                raise ValidationException(
                    "Estado inválido; use EN_TRANSITO, RECIBIDO, RECHAZADO_PARCIAL o CANCELADO"
                )
        if sucursal_id is not None:
            consulta = consulta.where(
                (Traspaso.sucursal_origen_id == int(sucursal_id))
                | (Traspaso.sucursal_destino_id == int(sucursal_id))
            )
        if producto_id is not None:
            consulta = consulta.where(
                Traspaso.id.in_(
                    select(DetalleTraspaso.traspaso_id)
                    .join(
                        VarianteProducto,
                        VarianteProducto.id == DetalleTraspaso.variante_producto_id,
                    )
                    .where(
                        DetalleTraspaso.tenant_id == tenant_id,
                        VarianteProducto.producto_id == int(producto_id),
                    )
                )
            )
        if desde is not None:
            consulta = consulta.where(Traspaso.fecha_envio >= desde)
        if hasta is not None:
            consulta = consulta.where(Traspaso.fecha_envio <= hasta)

        resultado = await db.execute(
            consulta.order_by(Traspaso.fecha_envio.desc())
            .offset(max(0, int(skip)))
            .limit(min(max(1, int(limit)), 100))
        )
        return [await cls.serializar(db, traspaso) for traspaso in resultado.scalars().all()]

    # ------------------------------------------------------------------
    # Recepción
    # ------------------------------------------------------------------

    @classmethod
    def validar_cantidades_recibidas(
        cls, cantidad_enviada: int, cantidad_recibida: int, cantidad_merma: int
    ) -> None:
        if cantidad_recibida < 0 or cantidad_merma < 0:
            raise ValidationException(
                "La cantidad recibida y la merma no pueden ser negativas"
            )
        if cantidad_recibida + cantidad_merma != int(cantidad_enviada):
            raise ValidationException(
                "RN-013: cantidad_recibida + cantidad_merma debe ser igual a la "
                f"cantidad enviada ({cantidad_enviada}); recibido {cantidad_recibida} "
                f"+ merma {cantidad_merma}"
            )

    @classmethod
    async def recibir_traspaso(
        cls,
        db: AsyncSession,
        traspaso_id: int,
        tenant_id: int,
        usuario_id: int | None,
        cantidades_recibidas: Iterable | None = None,
        observaciones: str | None = None,
    ) -> dict:
        traspaso = await cls._cargar_traspaso(db, traspaso_id, tenant_id)
        if traspaso.estado != EstadoTraspaso.EN_TRANSITO:
            raise ConflictException(
                f"No se puede recibir un traspaso en estado {traspaso.estado.value}"
            )

        detalles = {detalle.id: detalle for detalle in traspaso.detalles}
        if not detalles:
            raise ValidationException("El traspaso no tiene líneas")

        entradas: dict[int, dict] = {}
        for item in (cantidades_recibidas or []):
            detalle_id = int(_campo(item, "detalle_id") or _campo(item, "id") or 0)
            if detalle_id not in detalles:
                raise ValidationException(
                    f"El detalle {detalle_id} no pertenece al traspaso {traspaso.numero}"
                )
            if detalle_id in entradas:
                raise ValidationException(f"El detalle {detalle_id} está repetido")
            recibida = int(_campo(item, "cantidad_recibida", 0) or 0)
            merma = int(_campo(item, "cantidad_merma", 0) or 0)
            entradas[detalle_id] = {
                "recibida": recibida,
                "merma": merma,
                "observaciones": _campo(item, "observaciones"),
            }

        # Sin lista explícita se recibe todo completo.
        for detalle_id, detalle in detalles.items():
            entradas.setdefault(detalle_id, {
                "recibida": int(detalle.cantidad),
                "merma": 0,
                "observaciones": None,
            })

        for detalle_id, entrada in entradas.items():
            detalle = detalles[detalle_id]
            cls.validar_cantidades_recibidas(
                detalle.cantidad, entrada["recibida"], entrada["merma"]
            )

        for detalle_id, entrada in entradas.items():
            detalle = detalles[detalle_id]
            detalle.cantidad_recibida = entrada["recibida"]
            detalle.cantidad_merma = entrada["merma"]
            if entrada["observaciones"]:
                detalle.observaciones = entrada["observaciones"]

            if entrada["recibida"] > 0:
                await cls.sumar_stock_destino(
                    db,
                    tenant_id,
                    detalle.variante_producto_id,
                    traspaso.sucursal_destino_id,
                    entrada["recibida"],
                )
                await cls.registrar_movimiento_entrada(
                    db, tenant_id, traspaso, detalle, entrada["recibida"], usuario_id
                )
            if entrada["merma"] > 0:
                await cls.registrar_merma(
                    db,
                    tenant_id,
                    traspaso,
                    detalle,
                    entrada["merma"],
                    entrada["observaciones"] or "Diferencia en recepción",
                    usuario_id,
                )

        traspaso.fecha_recepcion = _ahora()
        traspaso.usuario_recibe_id = usuario_id
        if observaciones:
            traspaso.observaciones = (
                f"{traspaso.observaciones or ''} {observaciones}".strip()
            )
        await cls.marcar_recibido(db, traspaso)
        await db.flush()
        return await cls.serializar(db, traspaso)

    @classmethod
    def calcular_cantidad_pendiente(cls, traspaso: Traspaso) -> int:
        return sum(
            max(0, int(detalle.cantidad) - int(detalle.cantidad_recibida or 0) - int(detalle.cantidad_merma or 0))
            for detalle in (traspaso.detalles or [])
        )

    @classmethod
    async def marcar_recibido(cls, db: AsyncSession, traspaso: Traspaso) -> EstadoTraspaso:
        completo = all(
            int(detalle.cantidad_recibida or 0) == int(detalle.cantidad)
            and int(detalle.cantidad_merma or 0) == 0
            for detalle in (traspaso.detalles or [])
        )
        traspaso.estado = (
            EstadoTraspaso.RECIBIDO if completo else EstadoTraspaso.RECHAZADO_PARCIAL
        )
        await db.flush()
        return traspaso.estado

    # ------------------------------------------------------------------
    # Rechazo y cancelación
    # ------------------------------------------------------------------

    @classmethod
    async def rechazar_traspaso(
        cls,
        db: AsyncSession,
        traspaso_id: int,
        tenant_id: int,
        usuario_id: int | None,
        motivo: str,
        devolver_origen: bool = True,
        detalles: Iterable | None = None,
    ) -> dict:
        traspaso = await cls._cargar_traspaso(db, traspaso_id, tenant_id)
        if traspaso.estado != EstadoTraspaso.EN_TRANSITO:
            raise ConflictException(
                f"No se puede rechazar un traspaso en estado {traspaso.estado.value}"
            )
        if not (motivo or "").strip():
            raise ValidationException("Debe indicar el motivo del rechazo")

        lineas = {detalle.id: detalle for detalle in traspaso.detalles}
        if detalles:
            ids = {int(_campo(item, "detalle_id") or _campo(item, "id") or 0) for item in detalles}
            desconocidos = ids - set(lineas)
            if desconocidos:
                raise ValidationException(
                    "Detalles ajenos al traspaso: "
                    + ", ".join(str(item) for item in sorted(desconocidos))
                )
            objetivo = {detalle_id: lineas[detalle_id] for detalle_id in ids}
        else:
            objetivo = dict(lineas)

        for detalle in objetivo.values():
            if int(detalle.cantidad_recibida or 0) > 0:
                raise ConflictException(
                    "No se puede rechazar una línea que ya tiene recepción registrada"
                )
            detalle.cantidad_recibida = 0
            detalle.cantidad_merma = int(detalle.cantidad)
            detalle.observaciones = (
                f"{detalle.observaciones or ''} Rechazo: {motivo}".strip()
            )
            if devolver_origen:
                await cls.devolver_stock_origen(
                    db,
                    tenant_id,
                    detalle.variante_producto_id,
                    traspaso.sucursal_origen_id,
                    int(detalle.cantidad),
                )
                inventario_origen = (await db.execute(
                    select(Inventario).where(
                        Inventario.tenant_id == tenant_id,
                        Inventario.variante_producto_id == detalle.variante_producto_id,
                        Inventario.sucursal_id == traspaso.sucursal_origen_id,
                    )
                )).scalar_one()
                await cls._registrar_movimiento(
                    db,
                    tenant_id,
                    inventario_origen.id,
                    TipoMovimiento.TRASPASO_ENTRADA,
                    int(detalle.cantidad),
                    f"Devolución al origen por rechazo del traspaso {traspaso.numero}: {motivo}",
                    usuario_id,
                )
            else:
                await cls.registrar_merma(
                    db,
                    tenant_id,
                    traspaso,
                    detalle,
                    int(detalle.cantidad),
                    f"Rechazo sin devolución: {motivo}",
                    usuario_id,
                )

        traspaso.fecha_recepcion = _ahora()
        traspaso.usuario_recibe_id = usuario_id
        traspaso.observaciones = (
            f"{traspaso.observaciones or ''} Rechazo: {motivo}".strip()
        )
        if len(objetivo) == len(lineas) and devolver_origen:
            traspaso.estado = EstadoTraspaso.CANCELADO
        else:
            traspaso.estado = EstadoTraspaso.RECHAZADO_PARCIAL
        await db.flush()
        return await cls.serializar(db, traspaso)

    @classmethod
    def validar_cancelacion(cls, traspaso: Traspaso) -> None:
        if traspaso.estado != EstadoTraspaso.EN_TRANSITO:
            raise ConflictException(
                "Solo se puede cancelar un traspaso en estado EN_TRANSITO"
            )
        if traspaso.fecha_recepcion is not None:
            raise ConflictException(
                "No se puede cancelar un traspaso con recepción registrada"
            )
        if any(int(detalle.cantidad_recibida or 0) > 0 for detalle in (traspaso.detalles or [])):
            raise ConflictException(
                "No se puede cancelar un traspaso con recepción parcial registrada"
            )

    @classmethod
    async def cancelar_traspaso(
        cls, db: AsyncSession, traspaso_id: int, tenant_id: int, usuario_id: int | None
    ) -> dict:
        traspaso = await cls._cargar_traspaso(db, traspaso_id, tenant_id)
        cls.validar_cancelacion(traspaso)

        for detalle in traspaso.detalles:
            await cls.devolver_stock_origen(
                db,
                tenant_id,
                detalle.variante_producto_id,
                traspaso.sucursal_origen_id,
                int(detalle.cantidad),
            )
            inventario_origen = (await db.execute(
                select(Inventario).where(
                    Inventario.tenant_id == tenant_id,
                    Inventario.variante_producto_id == detalle.variante_producto_id,
                    Inventario.sucursal_id == traspaso.sucursal_origen_id,
                )
            )).scalar_one()
            await cls._registrar_movimiento(
                db,
                tenant_id,
                inventario_origen.id,
                TipoMovimiento.TRASPASO_ENTRADA,
                int(detalle.cantidad),
                f"Cancelación del traspaso {traspaso.numero}",
                usuario_id,
            )
            detalle.cantidad_recibida = 0
            detalle.cantidad_merma = 0

        traspaso.estado = EstadoTraspaso.CANCELADO
        traspaso.fecha_recepcion = _ahora()
        traspaso.usuario_recibe_id = usuario_id
        await db.flush()
        return await cls.serializar(db, traspaso)

    # ------------------------------------------------------------------
    # Historial y sugerencias
    # ------------------------------------------------------------------

    @classmethod
    async def historial_producto(
        cls, db: AsyncSession, producto_id: int, tenant_id: int, limit: int = 50
    ) -> dict:
        producto = (await db.execute(
            select(Producto).where(
                Producto.id == int(producto_id), Producto.tenant_id == tenant_id
            )
        )).scalar_one_or_none()
        if producto is None:
            raise NotFoundException("Producto no encontrado para el tenant")

        resultado = await db.execute(
            select(DetalleTraspaso, Traspaso)
            .join(Traspaso, Traspaso.id == DetalleTraspaso.traspaso_id)
            .join(
                VarianteProducto,
                VarianteProducto.id == DetalleTraspaso.variante_producto_id,
            )
            .where(
                DetalleTraspaso.tenant_id == tenant_id,
                VarianteProducto.producto_id == int(producto_id),
            )
            .order_by(Traspaso.fecha_envio.desc())
            .limit(min(max(1, int(limit)), 200))
        )
        movimientos = []
        for detalle, traspaso in resultado.all():
            movimientos.append({
                "traspaso_id": traspaso.id,
                "numero": traspaso.numero,
                "estado": traspaso.estado.value,
                "fecha_envio": traspaso.fecha_envio.isoformat() if traspaso.fecha_envio else None,
                "sucursal_origen_id": traspaso.sucursal_origen_id,
                "sucursal_destino_id": traspaso.sucursal_destino_id,
                "variante_producto_id": detalle.variante_producto_id,
                "cantidad": detalle.cantidad,
                "cantidad_recibida": detalle.cantidad_recibida,
                "cantidad_merma": detalle.cantidad_merma,
            })
        return {
            "producto_id": int(producto_id),
            "producto": producto.nombre,
            "total_movimientos": len(movimientos),
            "movimientos": movimientos,
        }

    @classmethod
    async def sugerir_traspaso(
        cls, db: AsyncSession, tenant_id: int, producto_id: int | None = None
    ) -> dict:
        """Propone mover stock desde la sucursal con excedente a la de mayor demanda."""
        consulta = (
            select(
                Inventario.sucursal_id,
                Inventario.variante_producto_id,
                Inventario.cantidad,
                Inventario.cantidad_reservada,
                Inventario.stock_minimo,
            )
            .where(Inventario.tenant_id == tenant_id)
        )
        if producto_id is not None:
            consulta = consulta.join(
                VarianteProducto,
                VarianteProducto.id == Inventario.variante_producto_id,
            ).where(VarianteProducto.producto_id == int(producto_id))
        filas = (await db.execute(consulta)).all()
        if len(filas) < 2:
            return {
                "producto_id": producto_id,
                "sugerencias": [],
                "mensaje": "No hay suficiente inventario distribuido para sugerir traspasos.",
            }

        ventas = (await db.execute(
            select(
                Orden.sucursal_id,
                DetalleOrden.variante_producto_id,
                func.coalesce(func.sum(DetalleOrden.cantidad), 0).label("vendidas"),
            )
            .join(DetalleOrden, DetalleOrden.orden_id == Orden.id)
            .where(
                Orden.tenant_id == tenant_id,
                Orden.estado.in_([
                    EstadoOrden.PAGADO,
                    EstadoOrden.EN_PROCESO,
                    EstadoOrden.ENVIADO,
                    EstadoOrden.ENTREGADO,
                ]),
            )
            .group_by(Orden.sucursal_id, DetalleOrden.variante_producto_id)
        )).all()
        demanda = {
            (fila.sucursal_id, fila.variante_producto_id): int(fila.vendidas or 0)
            for fila in ventas
        }

        por_variante: dict[int, list] = {}
        for fila in filas:
            por_variante.setdefault(fila.variante_producto_id, []).append(fila)

        sugerencias = []
        for variante_id, registros in por_variante.items():
            if len(registros) < 2:
                continue
            origen = max(
                registros,
                key=lambda item: (
                    int(item.cantidad or 0) - int(item.cantidad_reservada or 0)
                ) - int(item.stock_minimo or 0),
            )
            destino = max(
                registros,
                key=lambda item: demanda.get((item.sucursal_id, variante_id), 0),
            )
            if origen.sucursal_id == destino.sucursal_id:
                continue
            excedente = (
                int(origen.cantidad or 0)
                - int(origen.cantidad_reservada or 0)
                - int(origen.stock_minimo or 0)
            )
            faltante = max(
                0,
                int(destino.stock_minimo or 0)
                - (int(destino.cantidad or 0) - int(destino.cantidad_reservada or 0)),
            )
            cantidad = min(excedente, max(faltante, 1))
            if cantidad <= 0 or faltante <= 0:
                continue
            sugerencias.append({
                "variante_producto_id": variante_id,
                "sucursal_origen_id": origen.sucursal_id,
                "sucursal_destino_id": destino.sucursal_id,
                "cantidad_sugerida": cantidad,
                "disponible_origen": int(origen.cantidad or 0) - int(origen.cantidad_reservada or 0),
                "demanda_destino": demanda.get((destino.sucursal_id, variante_id), 0),
            })
        sugerencias.sort(key=lambda item: item["demanda_destino"], reverse=True)
        return {
            "producto_id": producto_id,
            "sugerencias": sugerencias[:50],
            "mensaje": (
                "Sugerencias generadas por excedente y demanda."
                if sugerencias
                else "No se encontraron desbalances que justifiquen un traspaso."
            ),
        }

    # ------------------------------------------------------------------
    # Notificación y PDF
    # ------------------------------------------------------------------

    @classmethod
    async def notificar_sucursal_destino(
        cls, db: AsyncSession, traspaso: Traspaso
    ) -> dict:
        logger.info(
            "traspaso_evento tenant_id=%s numero=%s destino=%s estado=%s",
            traspaso.tenant_id,
            traspaso.numero,
            traspaso.sucursal_destino_id,
            traspaso.estado.value,
        )
        try:
            from app.services.firebase_service import FirebaseService

            await FirebaseService.enviar_notificacion_sucursal(
                db=db,
                sucursal_id=traspaso.sucursal_destino_id,
                titulo="Mercadería en camino",
                cuerpo=f"Traspaso {traspaso.numero} con destino a su sucursal",
                data={"traspaso_id": str(traspaso.id), "numero": traspaso.numero},
            )
        except Exception as exc:  # pragma: no cover - notificación opcional
            logger.warning("No se pudo notificar la sucursal destino: %s", exc)
        return {"notificado": True, "sucursal_destino_id": traspaso.sucursal_destino_id}

    @classmethod
    async def generar_pdf_traspaso(
        cls, db: AsyncSession, traspaso_id: int, tenant_id: int
    ) -> tuple[bytes, str]:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        traspaso = await cls._cargar_traspaso(db, traspaso_id, tenant_id)
        tenant = None
        try:
            from app.apps.gestion_saas.models import Tenant

            tenant = await db.get(Tenant, tenant_id)
        except Exception:  # pragma: no cover - defensivo
            tenant = None

        origen = await db.get(Sucursal, traspaso.sucursal_origen_id)
        destino = await db.get(Sucursal, traspaso.sucursal_destino_id)

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
            Paragraph(f"Comprobante de traspaso {traspaso.numero}", estilos["Heading2"]),
            Paragraph(
                f"Origen: {getattr(origen, 'nombre', traspaso.sucursal_origen_id)} · "
                f"Destino: {getattr(destino, 'nombre', traspaso.sucursal_destino_id)}",
                estilos["Normal"],
            ),
            Paragraph(
                f"Estado: {traspaso.estado.value} · "
                f"Envío: {traspaso.fecha_envio.strftime('%d/%m/%Y %H:%M') if traspaso.fecha_envio else '-'} · "
                f"Recepción: {traspaso.fecha_recepcion.strftime('%d/%m/%Y %H:%M') if traspaso.fecha_recepcion else '-'}",
                estilos["Normal"],
            ),
            Spacer(1, 12),
        ]

        filas = [["Variante", "Enviado", "Recibido", "Merma", "Observaciones"]]
        for detalle in traspaso.detalles or []:
            filas.append([
                str(detalle.variante_producto_id),
                str(detalle.cantidad),
                str(detalle.cantidad_recibida),
                str(detalle.cantidad_merma),
                detalle.observaciones or "",
            ])
        tabla = Table(filas, repeatRows=1)
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E11D48")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        elementos.append(tabla)
        documento.build(elementos)
        return buffer.getvalue(), f"traspaso_{traspaso.numero}.pdf"

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    @classmethod
    async def serializar(cls, db: AsyncSession, traspaso: Traspaso) -> dict:
        detalles = []
        for detalle in traspaso.detalles or []:
            variante = await db.get(VarianteProducto, detalle.variante_producto_id)
            producto_id = variante.producto_id if variante else None
            producto = await db.get(Producto, producto_id) if producto_id else None
            detalles.append({
                "id": detalle.id,
                "variante_producto_id": detalle.variante_producto_id,
                "producto_id": producto_id,
                "producto": getattr(producto, "nombre", None),
                "cantidad": int(detalle.cantidad),
                "cantidad_recibida": int(detalle.cantidad_recibida or 0),
                "cantidad_merma": int(detalle.cantidad_merma or 0),
                "cantidad_pendiente": max(
                    0,
                    int(detalle.cantidad)
                    - int(detalle.cantidad_recibida or 0)
                    - int(detalle.cantidad_merma or 0),
                ),
                "observaciones": detalle.observaciones,
            })
        return {
            "id": traspaso.id,
            "numero": traspaso.numero,
            "sucursal_origen_id": traspaso.sucursal_origen_id,
            "sucursal_destino_id": traspaso.sucursal_destino_id,
            "estado": traspaso.estado.value if hasattr(traspaso.estado, "value") else traspaso.estado,
            "fecha_envio": traspaso.fecha_envio.isoformat() if traspaso.fecha_envio else None,
            "fecha_recepcion": (
                traspaso.fecha_recepcion.isoformat() if traspaso.fecha_recepcion else None
            ),
            "usuario_id": traspaso.usuario_id,
            "usuario_recibe_id": traspaso.usuario_recibe_id,
            "motivo": traspaso.motivo,
            "observaciones": traspaso.observaciones,
            "cantidad_pendiente": cls.calcular_cantidad_pendiente(traspaso),
            "detalles": detalles,
        }
