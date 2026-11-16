"""
[EXAMEN-2] OrdenDiferidaService — recalcula y crea la orden pendiente de pago.

El total local nunca se cobra a ciegas: los precios y el stock se recalculan
contra el servidor central (RN-014) y el stock se reserva provisionalmente
hasta el pago o la expiración (RN-016).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import AceptacionRequerida, ValidationException
from app.apps.gestion_ventas.models import (
    Carrito,
    DetalleOrden,
    EstadoCarrito,
    EstadoOrden,
    Orden,
    TipoOrden,
)
from app.apps.gestion_ventas.services import (
    CarritoService,
    CuponService,
    InventarioVentasService,
    OrdenService,
)
from app.apps.operaciones_offline.conflicto_service import ConflictoService
from app.apps.operaciones_offline.models import EstadoSincronizacion, OrigenOperacion


def _d(valor) -> Decimal:
    return Decimal(str(valor if valor is not None else 0)).quantize(Decimal("0.01"))


class OrdenDiferidaService:

    @staticmethod
    async def verificar_habilitacion(db: AsyncSession, tenant_id: int) -> None:
        from sqlalchemy import select
        from app.tenant import get_suscripcion_activa
        from app.exceptions import CarritoNoHabilitado, OperacionRechazada, SuscripcionVencida
        from app.apps.gestion_saas.models import Tenant, EstadoTenant
        from app.apps.operaciones_offline.models import ConfiguracionOffline, TipoConflicto

        tenant = await db.get(Tenant, tenant_id)
        try:
            suscripcion = await get_suscripcion_activa(db, tenant_id)
            habilitado = bool(suscripcion.plan.permite_pago_diferido)
        except SuscripcionVencida:
            habilitado = False
        if tenant is None or tenant.estado != EstadoTenant.OPERATIVO or not habilitado:
            raise OperacionRechazada(
                "El pedido se conserva pendiente de regularización del tenant o su suscripción",
                tipo_conflicto=TipoConflicto.TENANT_SUSPENDIDO.value,
                estrategia="MANUAL",
                datos={"estado": "PENDIENTE_REGULARIZACION", "requiere_pago": False},
            )
        config = (await db.execute(select(ConfiguracionOffline).where(
            ConfiguracionOffline.tenant_id == tenant_id
        ))).scalar_one_or_none()
        if config is None or not config.habilitar_carrito_offline:
            raise CarritoNoHabilitado()

    @staticmethod
    async def calcular_totales(db: AsyncSession, carrito: Carrito, cupon_codigo: Optional[str] = None) -> dict:
        subtotal_local = Decimal("0.00")
        subtotal_servidor = Decimal("0.00")
        items: list[dict] = []
        diferencias: list[dict] = []

        for item in carrito.items:
            variante = item.variante_producto
            if (variante is None or variante.deleted_at is not None
                    or variante.tenant_id != carrito.tenant_id or variante.producto is None
                    or variante.producto.deleted_at is not None
                    or variante.producto.tenant_id != carrito.tenant_id
                    or variante.producto.estado != "ACTIVO" or item.cantidad <= 0):
                raise ValidationException("El carrito contiene un producto inactivo o una variante no disponible")
            precio_servidor = _d(
                variante.precio_variante
                if variante.precio_variante is not None
                else (variante.producto.precio if variante.producto else 0)
            )
            precio_local = _d(
                item.precio_unitario_local
                if item.precio_unitario_local is not None
                else item.precio_unitario
            )
            subtotal_local += precio_local * item.cantidad
            subtotal_servidor += precio_servidor * item.cantidad
            if precio_local != precio_servidor:
                diferencias.append({
                    "variante_producto_id": item.variante_producto_id,
                    "cantidad": item.cantidad,
                    "precio_local": float(precio_local),
                    "precio_central": float(precio_servidor),
                    "diferencia": float(precio_local - precio_servidor),
                })
            items.append({
                "variante_producto_id": item.variante_producto_id,
                "cantidad": item.cantidad,
                "precio_local": precio_local,
                "precio_servidor": precio_servidor,
                "subtotal": _d(precio_servidor * item.cantidad),
            })

        for item, calculado in zip(carrito.items, items):
            item.precio_unitario = calculado["precio_servidor"]
            item.subtotal = calculado["subtotal"]
        promocion = await CarritoService.calcular_descuento_promocion(db, carrito.items)
        descuento = _d(promocion["descuento"])
        cupon = {"aplicable": False, "motivo": "Sin cupón"}
        if cupon_codigo:
            valido, mensaje, registro, descuento_cupon = await CuponService.validar_cupon(
                db, cupon_codigo, subtotal_servidor, items=carrito.items
            )
            cupon = {"codigo": cupon_codigo, "aplicable": valido and descuento == 0, "motivo": mensaje}
            if valido and descuento == 0:
                carrito.cupon_id = registro.id
                descuento = _d(descuento_cupon)
            else:
                carrito.cupon_id = None
                if descuento > 0:
                    cupon["motivo"] = "No acumulable con la promoción vigente"
        total = _d(max(Decimal("0.00"), subtotal_servidor - descuento))
        if total != _d(subtotal_local) and not diferencias:
            diferencias.append({"tipo": "TOTAL_RECALCULADO", "total_local": float(subtotal_local),
                                "total_servidor": float(total), "descuento": float(descuento)})
        return {
            "items": items,
            "subtotal_local": _d(subtotal_local),
            "subtotal_servidor": _d(subtotal_servidor),
            "descuento": descuento,
            "total_servidor": total,
            "diferencias": diferencias,
            "cupon": cupon,
        }

    @classmethod
    async def crear_orden_desde_carrito(
        cls,
        db: AsyncSession,
        carrito: Carrito,
        tenant_id: int,
        acepta_nuevo_total: bool = False,
        direccion_envio: Optional[str] = None,
        sucursal_id: Optional[int] = None,
        uuid_orden=None,
        cupon_codigo: Optional[str] = None,
        total_aceptado=None,
    ) -> dict:
        if carrito.estado != EstadoCarrito.ACTIVO:
            raise ValidationException("El carrito no está activo")
        if not carrito.items:
            raise ValidationException("No se puede convertir un carrito vacío")

        await cls.verificar_habilitacion(db, tenant_id)
        totales = await cls.calcular_totales(db, carrito, cupon_codigo)
        aceptado = acepta_nuevo_total and (total_aceptado is None or _d(total_aceptado) == totales["total_servidor"])
        if totales["diferencias"] and not aceptado:
            raise AceptacionRequerida(
                totales["subtotal_local"],
                totales["total_servidor"],
                totales["diferencias"],
            )

        if sucursal_id is None:
            # El carrito no persiste sucursal: se usa la primera del tenant.
            from sqlalchemy import select

            from app.apps.gestion_catalogo.models import Sucursal

            sucursal_id = (await db.execute(
                select(Sucursal.id)
                .where(Sucursal.tenant_id == tenant_id, Sucursal.deleted_at.is_(None))
                .order_by(Sucursal.id.asc())
                .limit(1)
            )).scalar_one_or_none()
            if sucursal_id is None:
                raise ValidationException("El tenant no tiene sucursales configuradas")

        # RN-006: stock central suficiente antes de reservar.
        conflictos = await ConflictoService.detectar_stock(
            db,
            tenant_id,
            sucursal_id,
            [{"variante_producto_id": i["variante_producto_id"], "cantidad": i["cantidad"]}
             for i in totales["items"]],
        )
        if conflictos:
            from app.exceptions import OperacionRechazada
            from app.apps.operaciones_offline.models import TipoConflicto

            raise OperacionRechazada(
                "Stock insuficiente para convertir el carrito",
                tipo_conflicto=TipoConflicto.STOCK_INSUFICIENTE.value,
                estrategia="RECHAZO",
                datos={"faltantes": conflictos},
            )

        # RN-016: reserva provisional de stock.
        for item in totales["items"]:
            await InventarioVentasService.reservar_stock(
                db,
                item["variante_producto_id"],
                sucursal_id,
                item["cantidad"],
            )

        ahora = datetime.utcnow()
        expira = ahora + timedelta(hours=settings.PAGO_DIFERIDO_VIGENCIA_HORAS)
        numero_orden = OrdenService.generar_numero_orden()
        orden = Orden(
            numero_orden=numero_orden,
            cliente_id=carrito.cliente_id,
            sucursal_id=sucursal_id,
            tipo=TipoOrden.DIGITAL,
            estado=EstadoOrden.PENDIENTE_PAGO_DIFERIDO,
            total=totales["total_servidor"],
            impuestos=Decimal("0.00"),
            descuentos=totales["descuento"],
            cupon_id=carrito.cupon_id,
            direccion_envio=direccion_envio,
            tenant_id=tenant_id,
            uuid_orden=uuid_orden or uuid4(),
            origen=OrigenOperacion.OFFLINE,
            es_offline=True,
            estado_sincronizacion=EstadoSincronizacion.CONFIRMADA,
            fecha_limite_pago=expira,
            expira_en=expira,
            moneda=settings.STRIPE_MONEDA.upper(),
        )
        db.add(orden)
        await db.flush()

        for item in totales["items"]:
            db.add(DetalleOrden(
                orden_id=orden.id,
                variante_producto_id=item["variante_producto_id"],
                cantidad=item["cantidad"],
                precio_unitario=item["precio_servidor"],
                subtotal=item["subtotal"],
                tenant_id=tenant_id,
                precio_unitario_local=item["precio_local"],
                precio_unitario_servidor=item["precio_servidor"],
            ))

        carrito.estado = EstadoCarrito.CONVERTIDO
        carrito.estado_sincronizacion = EstadoSincronizacion.CONFIRMADA
        await db.flush()

        return {
            "orden_id": orden.id,
            "numero_orden": orden.numero_orden,
            "uuid_orden": str(orden.uuid_orden),
            "estado": orden.estado.value,
            "subtotal": totales["subtotal_servidor"],
            "descuento": totales["descuento"],
            "total": totales["total_servidor"],
            "moneda": orden.moneda,
            "expira_en": orden.expira_en,
            "fecha_limite_pago": orden.fecha_limite_pago,
            "items": len(totales["items"]),
            "stock_reservado": True,
            "diferencias": totales["diferencias"],
        }
