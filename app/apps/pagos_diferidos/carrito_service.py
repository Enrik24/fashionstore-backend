"""
[EXAMEN-2] CarritoOfflineService — carrito que sobrevive al cierre de la app.

El carrito se identifica por `uuid_carrito` generado en el dispositivo y se
persiste en la tabla `carritos` (no se crea una tabla espejo). Al validar o
convertir, el servidor recalcula precios, impuestos y total con los valores
centrales (RN-014) y exige aceptación si cambian (RN-007).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.exceptions import (
    AceptacionRequerida,
    CarritoNoHabilitado,
    NotFoundException,
    OperacionRechazada,
    ValidationException,
)
from app.apps.gestion_catalogo.models import (
    EstadoSucursal,
    Inventario,
    Sucursal,
    VarianteProducto,
)
from app.apps.gestion_ventas.models import (
    Carrito,
    EstadoCarrito,
    ItemCarrito,
)
from app.apps.operaciones_offline.conflicto_service import ConflictoService
from app.apps.operaciones_offline.models import (
    EstadoSincronizacion,
    TipoConflicto,
)
from app.apps.pagos_diferidos.orden_diferida_service import OrdenDiferidaService
from app.apps.pagos_diferidos.schemas import (
    CarritoOfflineRequest,
    CarritoValidacionResponse,
)


def _d(valor) -> Decimal:
    return Decimal(str(valor if valor is not None else 0)).quantize(Decimal("0.01"))


def _uuid(valor, campo: str = "uuid_carrito") -> UUID:
    try:
        return UUID(str(valor))
    except (TypeError, ValueError):
        raise ValidationException(f"{campo} inválido")


class CarritoOfflineService:

    # ------------------------------------------------------------------
    # Carga y serialización
    # ------------------------------------------------------------------
    @staticmethod
    def _opciones():
        return (
            selectinload(Carrito.items)
            .selectinload(ItemCarrito.variante_producto)
            .selectinload(VarianteProducto.producto),
            selectinload(Carrito.items)
            .selectinload(ItemCarrito.variante_producto)
            .selectinload(VarianteProducto.talla),
            selectinload(Carrito.items)
            .selectinload(ItemCarrito.variante_producto)
            .selectinload(VarianteProducto.color),
        )

    @classmethod
    async def obtener_carrito(
        cls, db: AsyncSession, uuid_carrito, tenant_id: Optional[int] = None
    ) -> Carrito:
        uuid_valor = _uuid(uuid_carrito)
        result = await db.execute(
            select(Carrito)
            .options(*cls._opciones())
            .where(Carrito.uuid_carrito == uuid_valor)
            .execution_options(populate_existing=True)
        )
        carrito = result.scalar_one_or_none()
        if carrito is None or (tenant_id is not None and carrito.tenant_id != tenant_id):
            raise NotFoundException("Carrito offline no encontrado")
        return carrito

    @staticmethod
    async def resolver_sucursal(
        db: AsyncSession, tenant_id: int, sucursal_id: Optional[int] = None
    ) -> int:
        if sucursal_id is not None:
            result = await db.execute(
                select(Sucursal.id).where(
                    Sucursal.id == int(sucursal_id),
                    Sucursal.tenant_id == tenant_id,
                )
            )
            if result.scalar_one_or_none() is None:
                raise ValidationException("La sucursal no pertenece al tenant")
            return int(sucursal_id)
        result = await db.execute(
            select(Sucursal.id)
            .where(Sucursal.tenant_id == tenant_id, Sucursal.deleted_at.is_(None))
            .order_by(Sucursal.id.asc())
            .limit(1)
        )
        valor = result.scalar_one_or_none()
        if valor is None:
            raise NotFoundException("El tenant no tiene sucursales configuradas")
        return int(valor)

    @staticmethod
    async def _stock_central(
        db: AsyncSession, tenant_id: int, sucursal_id: int, variante_id: int
    ) -> int:
        result = await db.execute(
            select(Inventario.cantidad, Inventario.cantidad_reservada).where(
                Inventario.tenant_id == tenant_id,
                Inventario.sucursal_id == sucursal_id,
                Inventario.variante_producto_id == variante_id,
            )
        )
        fila = result.first()
        if fila is None:
            return 0
        return max(0, int(fila[0] or 0) - int(fila[1] or 0))

    @classmethod
    async def serializar(
        cls,
        db: AsyncSession,
        carrito: Carrito,
        tenant_id: int,
        sucursal_id: Optional[int] = None,
    ) -> dict:
        sucursal = sucursal_id or await cls.resolver_sucursal(db, tenant_id)
        items = []
        subtotal = Decimal("0.00")
        requiere_aceptacion = False
        for item in carrito.items:
            variante = item.variante_producto
            precio_central = _d(
                variante.precio_variante
                if variante is not None and variante.precio_variante is not None
                else (variante.producto.precio if variante is not None and variante.producto else item.precio_unitario)
            )
            precio_local = (
                _d(item.precio_unitario_local)
                if item.precio_unitario_local is not None else None
            )
            if precio_local is not None and precio_local != precio_central:
                requiere_aceptacion = True
            subtotal += precio_central * item.cantidad
            items.append({
                "id": item.id,
                "variante_producto_id": item.variante_producto_id,
                "sku_variante": getattr(variante, "sku_variante", None),
                "producto": getattr(getattr(variante, "producto", None), "nombre", None),
                "cantidad": item.cantidad,
                "precio_unitario": precio_central,
                "precio_unitario_local": precio_local,
                "stock_local_conocido": item.stock_local_conocido,
                "stock_central": await cls._stock_central(
                    db, tenant_id, sucursal, item.variante_producto_id
                ),
                "subtotal": _d(precio_central * item.cantidad),
            })
        return {
            "carrito_id": carrito.id,
            "uuid_carrito": str(carrito.uuid_carrito),
            "estado": carrito.estado.value if hasattr(carrito.estado, "value") else str(carrito.estado),
            "cliente_id": carrito.cliente_id,
            "sucursal_id": sucursal,
            "dispositivo_id": str(carrito.dispositivo_id) if carrito.dispositivo_id else None,
            "items": items,
            "subtotal": _d(subtotal),
            "descuento": Decimal("0.00"),
            "total": _d(subtotal),
            "moneda": settings.STRIPE_MONEDA.upper(),
            "requiere_aceptacion": requiere_aceptacion,
        }

    # ------------------------------------------------------------------
    # Alta / actualización
    # ------------------------------------------------------------------
    @classmethod
    async def _variantes_y_precios(
        cls, db: AsyncSession, tenant_id: int, items
    ) -> list[dict]:
        procesados = []
        for item in items:
            if isinstance(item, dict):
                variante_id = int(item.get("variante_producto_id"))
                cantidad = int(item.get("cantidad", 1))
                precio_local = item.get("precio_unitario_local")
                stock_local = item.get("stock_local_conocido")
            else:
                variante_id = int(item.variante_producto_id)
                cantidad = int(item.cantidad)
                precio_local = item.precio_unitario_local
                stock_local = item.stock_local_conocido
            if cantidad <= 0:
                raise ValidationException("La cantidad debe ser positiva")
            result = await db.execute(
                select(VarianteProducto)
                .options(selectinload(VarianteProducto.producto))
                .where(
                    VarianteProducto.id == variante_id,
                    VarianteProducto.tenant_id == tenant_id,
                    VarianteProducto.deleted_at.is_(None),
                )
            )
            variante = result.scalar_one_or_none()
            if variante is None:
                raise NotFoundException(f"Variante #{variante_id} no encontrada")
            if variante.producto is None or getattr(variante.producto.estado, "value", variante.producto.estado) != "ACTIVO":
                raise OperacionRechazada(
                    f"El producto de la variante #{variante_id} no está activo",
                    tipo_conflicto=TipoConflicto.PRODUCTO_INACTIVO.value,
                    estrategia="RECHAZO",
                )
            precio = _d(
                variante.precio_variante
                if variante.precio_variante is not None else variante.producto.precio
            )
            procesados.append({
                "variante_producto_id": variante_id,
                "cantidad": cantidad,
                "precio_unitario": precio,
                "precio_unitario_local": _d(precio_local) if precio_local is not None else None,
                "stock_local_conocido": stock_local,
                "subtotal": _d(precio * cantidad),
            })
        return procesados

    @classmethod
    async def _resolver_carrito(
        cls,
        db: AsyncSession,
        tenant_id: int,
        uuid_valor: UUID,
        cliente_id: Optional[int],
        dispositivo_id,
    ) -> Carrito:
        carrito = (await db.execute(
            select(Carrito)
            .options(*cls._opciones())
            .where(Carrito.uuid_carrito == uuid_valor)
        )).scalar_one_or_none()
        if carrito is not None:
            if carrito.estado != EstadoCarrito.ACTIVO:
                raise OperacionRechazada(
                    "El carrito ya fue convertido o abandonado",
                    datos={"uuid_carrito": str(uuid_valor), "estado": carrito.estado.value},
                )
            return carrito

        # Reutiliza un carrito activo del mismo dispositivo para no chocar con
        # el índice parcial (tenant_id, dispositivo_id, cliente_id).
        query = (
            select(Carrito)
            .options(*cls._opciones())
            .where(
                Carrito.estado == EstadoCarrito.ACTIVO,
                Carrito.es_offline.is_(True),
            )
        )
        if dispositivo_id is not None:
            query = query.where(Carrito.dispositivo_id == dispositivo_id)
        if cliente_id is not None:
            query = query.where(Carrito.cliente_id == cliente_id)
        carrito = (await db.execute(query.order_by(Carrito.id.asc()))).scalars().first()
        if carrito is None:
            carrito = Carrito(
                cliente_id=cliente_id,
                estado=EstadoCarrito.ACTIVO,
                tenant_id=tenant_id,
                dispositivo_id=dispositivo_id,
                es_offline=True,
                estado_sincronizacion=EstadoSincronizacion.CONFIRMADA,
            )
            db.add(carrito)
        carrito.uuid_carrito = uuid_valor
        if cliente_id is not None:
            carrito.cliente_id = cliente_id
        return carrito

    @classmethod
    async def registrar_carrito(
        cls,
        db: AsyncSession,
        tenant_id: int,
        datos: CarritoOfflineRequest,
        dispositivo_id=None,
    ) -> dict:
        uuid_valor = _uuid(datos.uuid_carrito)
        if datos.cupon_codigo:
            # RN-014: el cupón solo se valida con conexión.
            raise ValidationException(
                "El cupón solo puede validarse con conexión; reintente en línea"
            )
        sucursal_id = await cls.resolver_sucursal(db, tenant_id, datos.sucursal_id)
        carrito = await cls._resolver_carrito(
            db, tenant_id, uuid_valor, datos.cliente_id, dispositivo_id
        )
        carrito_nuevo = carrito.id is None
        carrito.es_offline = True
        carrito.estado_sincronizacion = EstadoSincronizacion.CONFIRMADA
        if dispositivo_id is not None:
            carrito.dispositivo_id = dispositivo_id
        await db.flush()

        items = await cls._variantes_y_precios(db, tenant_id, datos.items)
        # El carrito refleja el estado del dispositivo: se reemplazan los ítems.
        if not carrito_nuevo:
            for item in list(carrito.items or []):
                await db.delete(item)
            await db.flush()
        for item in items:
            db.add(ItemCarrito(
                carrito_id=carrito.id,
                variante_producto_id=item["variante_producto_id"],
                cantidad=item["cantidad"],
                precio_unitario=item["precio_unitario"],
                subtotal=item["subtotal"],
                tenant_id=tenant_id,
                precio_unitario_local=item["precio_unitario_local"],
                stock_local_conocido=item["stock_local_conocido"],
            ))
        carrito.descuento_aplicado = Decimal("0.00")
        await db.flush()
        await db.commit()
        await db.refresh(carrito)
        carrito = await cls.obtener_carrito(db, uuid_valor, tenant_id)
        return await cls.serializar(db, carrito, tenant_id, sucursal_id)

    @classmethod
    async def actualizar_items(
        cls,
        db: AsyncSession,
        uuid_carrito,
        tenant_id: int,
        items,
        dispositivo_id=None,
    ) -> dict:
        carrito = await cls.obtener_carrito(db, uuid_carrito, tenant_id)
        if carrito.estado != EstadoCarrito.ACTIVO:
            raise OperacionRechazada("El carrito no está activo")
        nuevos = await cls._variantes_y_precios(db, tenant_id, items)
        existentes = {i.variante_producto_id: i for i in carrito.items}
        for item in nuevos:
            actual = existentes.get(item["variante_producto_id"])
            if actual is None:
                db.add(ItemCarrito(
                    carrito_id=carrito.id,
                    variante_producto_id=item["variante_producto_id"],
                    cantidad=item["cantidad"],
                    precio_unitario=item["precio_unitario"],
                    subtotal=item["subtotal"],
                    tenant_id=tenant_id,
                    precio_unitario_local=item["precio_unitario_local"],
                    stock_local_conocido=item["stock_local_conocido"],
                ))
            else:
                actual.cantidad = item["cantidad"]
                actual.precio_unitario = item["precio_unitario"]
                actual.subtotal = item["subtotal"]
                actual.precio_unitario_local = item["precio_unitario_local"]
                actual.stock_local_conocido = item["stock_local_conocido"]
        carrito.es_offline = True
        carrito.estado_sincronizacion = EstadoSincronizacion.CONFIRMADA
        if dispositivo_id is not None:
            carrito.dispositivo_id = dispositivo_id
        await db.flush()
        await db.commit()
        carrito = await cls.obtener_carrito(db, uuid_carrito, tenant_id)
        return await cls.serializar(db, carrito, tenant_id)

    @classmethod
    async def quitar_item(
        cls, db: AsyncSession, uuid_carrito, tenant_id: int, item_id: int
    ) -> dict:
        carrito = await cls.obtener_carrito(db, uuid_carrito, tenant_id)
        item = next((i for i in carrito.items if i.id == item_id), None)
        if item is None:
            raise NotFoundException("Ítem del carrito no encontrado")
        await db.delete(item)
        await db.flush()
        await db.commit()
        carrito = await cls.obtener_carrito(db, uuid_carrito, tenant_id)
        return await cls.serializar(db, carrito, tenant_id)

    # ------------------------------------------------------------------
    # Validación y conversión
    # ------------------------------------------------------------------
    @classmethod
    async def validar_carrito(
        cls,
        db: AsyncSession,
        uuid_carrito,
        tenant_id: int,
        sucursal_id: Optional[int] = None,
        cupon_codigo: Optional[str] = None,
    ) -> dict:
        carrito = await cls.obtener_carrito(db, uuid_carrito, tenant_id)
        sucursal = await cls.resolver_sucursal(db, tenant_id, sucursal_id)
        totales = await OrdenDiferidaService.calcular_totales(carrito)
        conflictos = await ConflictoService.detectar_stock(
            db,
            tenant_id,
            sucursal,
            [{"variante_producto_id": i["variante_producto_id"], "cantidad": i["cantidad"]}
             for i in totales["items"]],
        )
        cupon = {"aplicable": False, "motivo": "El cupón solo se valida con conexión"}
        if cupon_codigo:
            cupon = {
                "codigo": cupon_codigo,
                "aplicable": False,
                "motivo": "RN-014: el cupón no se puede validar sin conexión",
            }
        return CarritoValidacionResponse(
            carrito_id=carrito.id,
            uuid_carrito=str(carrito.uuid_carrito),
            subtotal_local=totales["subtotal_local"],
            total_local=totales["subtotal_local"],
            subtotal_servidor=totales["subtotal_servidor"],
            total_servidor=totales["total_servidor"],
            diferencia=_d(totales["subtotal_local"] - totales["subtotal_servidor"]),
            requiere_aceptacion=bool(totales["diferencias"]),
            diferencias=totales["diferencias"],
            stock_suficiente=not conflictos,
            faltantes=conflictos,
            cupon=cupon,
        ).model_dump()

    @classmethod
    async def convertir_carrito(
        cls,
        db: AsyncSession,
        uuid_carrito,
        tenant_id: int,
        acepta_nuevo_total: bool = False,
        direccion_envio: Optional[str] = None,
        cliente_id: Optional[int] = None,
        sucursal_id: Optional[int] = None,
    ) -> dict:
        carrito = await cls.obtener_carrito(db, uuid_carrito, tenant_id)
        if carrito.estado != EstadoCarrito.ACTIVO:
            raise OperacionRechazada("El carrito ya fue convertido")
        if cliente_id is not None:
            carrito.cliente_id = cliente_id
        sucursal = await cls.resolver_sucursal(db, tenant_id, sucursal_id)
        resultado = await OrdenDiferidaService.crear_orden_desde_carrito(
            db,
            carrito,
            tenant_id,
            acepta_nuevo_total=acepta_nuevo_total,
            direccion_envio=direccion_envio,
            sucursal_id=sucursal,
        )
        await db.commit()
        return resultado

    @classmethod
    async def listar_pendientes(
        cls, db: AsyncSession, tenant_id: int, dispositivo_id=None
    ) -> list[dict]:
        query = (
            select(Carrito)
            .options(*cls._opciones())
            .where(
                Carrito.estado == EstadoCarrito.ACTIVO,
                Carrito.es_offline.is_(True),
            )
        )
        if dispositivo_id is not None:
            query = query.where(Carrito.dispositivo_id == dispositivo_id)
        query = query.order_by(Carrito.updated_at.desc()).limit(50)
        carritos = list((await db.execute(query)).scalars().unique().all())
        return [await cls.serializar(db, c, tenant_id) for c in carritos]
