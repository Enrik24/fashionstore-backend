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
    InventarioVentasService,
    OrdenService,
)
from app.apps.operaciones_offline.conflicto_service import ConflictoService
from app.apps.operaciones_offline.models import EstadoSincronizacion, OrigenOperacion


def _d(valor) -> Decimal:
    return Decimal(str(valor if valor is not None else 0)).quantize(Decimal("0.01"))


class OrdenDiferidaService:

    @staticmethod
    async def calcular_totales(carrito: Carrito) -> dict:
        subtotal_local = Decimal("0.00")
        subtotal_servidor = Decimal("0.00")
        items: list[dict] = []
        diferencias: list[dict] = []

        for item in carrito.items:
            variante = item.variante_producto
            if variante is None:
                raise ValidationException("El carrito contiene una variante inexistente")
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

        return {
            "items": items,
            "subtotal_local": _d(subtotal_local),
            "subtotal_servidor": _d(subtotal_servidor),
            "descuento": Decimal("0.00"),
            "total_servidor": _d(subtotal_servidor),
            "diferencias": diferencias,
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
    ) -> dict:
        if carrito.estado != EstadoCarrito.ACTIVO:
            raise ValidationException("El carrito no está activo")
        if not carrito.items:
            raise ValidationException("No se puede convertir un carrito vacío")

        totales = await cls.calcular_totales(carrito)
        if totales["diferencias"] and not acepta_nuevo_total:
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
            descuentos=Decimal("0.00"),
            direccion_envio=direccion_envio,
            tenant_id=tenant_id,
            uuid_orden=uuid4(),
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
            "subtotal": totales["total_servidor"],
            "descuento": totales["descuento"],
            "total": totales["total_servidor"],
            "moneda": orden.moneda,
            "expira_en": orden.expira_en,
            "fecha_limite_pago": orden.fecha_limite_pago,
            "items": len(totales["items"]),
            "stock_reservado": True,
            "diferencias": totales["diferencias"],
        }
