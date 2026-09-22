"""
Servicios de Lógica de Negocio para la Gestión de Ventas, Reservas y Pagos.
Implementa:
- InventarioVentasService: Actualizaciones atómicas de inventario en Python
- CuponService: Gestión y validación de cupones de descuento
- CarritoService: Carrito de compras y cálculo de subtotales/descuentos
- OrdenService: Creación y gestión de órdenes y comprobantes
- VentaPresencialService: Registro de ventas presenciales por cajeros
- ReservaService: Flujo completo de reservas en sucursal con reserva/liberación de stock
- PagoService: Pasarelas Stripe y PayPal con registro de transacciones
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple
import uuid
import secrets
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, and_, or_, desc, func
from sqlalchemy.orm import selectinload, joinedload

from app.config import settings
from app.exceptions import NotFoundException, BadRequestException, ConflictException, ForbiddenException
from app.apps.gestion_ventas.models import (
    Cupon, TipoCupon, EstadoCupon,
    Carrito, ItemCarrito, EstadoCarrito,
    Orden, DetalleOrden, TipoOrden, EstadoOrden,
    VentaPresencial, MetodoPagoPresencial,
    Reserva, DetalleReserva, EstadoReserva, EstadoDetalleReserva,
    TransaccionPago, MetodoPagoDigital, EstadoTransaccion,
    Comprobante, TipoComprobante, PasarelaPago, EstadoPasarela,
    SolicitudDevolucion, DetalleSolicitudDevolucion,
    TipoSolicitudDevolucion, MotivoDevolucion, EstadoSolicitudDevolucion
)
from app.apps.gestion_marketing.models import CuponProducto, CuponCategoria
from app.apps.gestion_ventas.schemas import (
    CuponCreate, CuponUpdate, CuponResponse, ItemCarritoCreate, ItemCarritoUpdate,
    ReservaCreate, CompletarReservaRequest, DetalleVentaPresencialCreate,
    SolicitudDevolucionCreate, RevisionSolicitudRequest, SolicitudDevolucionResponse,
    DetalleSolicitudDevolucionResponse, VarianteResumenResponse
)
from app.apps.gestion_catalogo.models import (
    VarianteProducto, Producto, Inventario, MovimientoInventario, TipoMovimiento, EstadoStock, Sucursal
)
from app.apps.gestion_usuarios.models import Cliente, Cajero, Usuario, EncargadoSucursal
from app.services.stripe_service import StripeService
from app.services.paypal_service import PayPalService
from app.services.firebase_service import FirebaseService

logger = logging.getLogger(__name__)


def _resolver_costo_unitario(variante: Optional["VarianteProducto"]) -> Optional[Decimal]:
    """Costo vigente: costo_variante ?? producto.costo_compra ?? None (fallback precio*0.6 en reportes)."""
    if variante is None:
        return None
    cv = getattr(variante, "costo_variante", None)
    if cv is not None:
        try:
            return Decimal(str(cv))
        except Exception:
            pass
    prod = getattr(variante, "producto", None)
    if prod is not None and getattr(prod, "costo_compra", None) is not None:
        try:
            c = Decimal(str(prod.costo_compra))
            if c > 0:
                return c
        except Exception:
            pass
    return None


# ==============================================================================
# INVENTARIO Y STOCK PARA VENTAS / RESERVAS (Lógica en Python)
# ==============================================================================

class InventarioVentasService:
    @staticmethod
    async def obtener_o_crear_inventario(
        db: AsyncSession, variante_id: int, sucursal_id: int
    ) -> Inventario:
        query = select(Inventario).where(
            Inventario.variante_producto_id == variante_id,
            Inventario.sucursal_id == sucursal_id
        )
        res = await db.execute(query)
        inv = res.scalar_one_or_none()
        if not inv:
            inv = Inventario(
                variante_producto_id=variante_id,
                sucursal_id=sucursal_id,
                cantidad=0,
                cantidad_reservada=0,
                cantidad_vendida=0,
                estado=EstadoStock.AGOTADO
            )
            db.add(inv)
            await db.flush()
        return inv

    @classmethod
    async def validar_stock_disponible(
        cls, db: AsyncSession, variante_id: int, sucursal_id: int, cantidad: int
    ) -> bool:
        query = select(Inventario).where(
            Inventario.variante_producto_id == variante_id,
            Inventario.sucursal_id == sucursal_id
        )
        res = await db.execute(query)
        inv = res.scalar_one_or_none()
        if not inv:
            return False
        return inv.cantidad_disponible >= cantidad

    @classmethod
    async def descontar_stock_venta(
        cls, db: AsyncSession, variante_id: int, sucursal_id: int, cantidad: int, usuario_id: Optional[int] = None, motivo: str = "Venta realizada"
    ) -> Inventario:
        inv = await cls.obtener_o_crear_inventario(db, variante_id, sucursal_id)
        if inv.cantidad_disponible < cantidad:
            raise BadRequestException(f"Stock insuficiente en la sucursal para la variante ID {variante_id}. Disponible: {inv.cantidad_disponible}, Solicitado: {cantidad}")
        
        inv.cantidad -= cantidad
        inv.cantidad_vendida += cantidad
        
        if inv.cantidad == 0:
            inv.estado = EstadoStock.AGOTADO
            
        movimiento = MovimientoInventario(
            inventario_id=inv.id,
            tipo=TipoMovimiento.VENTA,
            cantidad=-cantidad,
            motivo=motivo,
            usuario_id=usuario_id
        )
        db.add(movimiento)
        await db.flush()
        return inv

    @classmethod
    async def reintegrar_stock_devolucion(
        cls, db: AsyncSession, variante_id: int, sucursal_id: int, cantidad: int, usuario_id: Optional[int] = None, motivo: str = "Devolución de prenda"
    ) -> Inventario:
        """Reintegra stock al inventario por devolución o cambio de prenda (CU19 / CU28)."""
        inv = await cls.obtener_o_crear_inventario(db, variante_id, sucursal_id)
        inv.cantidad += cantidad
        if inv.cantidad_vendida >= cantidad:
            inv.cantidad_vendida -= cantidad
        else:
            inv.cantidad_vendida = 0
            
        if inv.cantidad > 0 and inv.estado == EstadoStock.AGOTADO:
            inv.estado = EstadoStock.DISPONIBLE

        movimiento = MovimientoInventario(
            inventario_id=inv.id,
            tipo=TipoMovimiento.DEVOLUCION,
            cantidad=cantidad,
            motivo=motivo,
            usuario_id=usuario_id
        )
        db.add(movimiento)
        await db.flush()
        return inv

    @classmethod
    async def reservar_stock(
        cls, db: AsyncSession, variante_id: int, sucursal_id: int, cantidad: int, usuario_id: Optional[int] = None
    ) -> Inventario:
        inv = await cls.obtener_o_crear_inventario(db, variante_id, sucursal_id)
        if inv.cantidad_disponible < cantidad:
            raise BadRequestException(f"Stock insuficiente para reservar la variante ID {variante_id}. Disponible: {inv.cantidad_disponible}")
        
        inv.cantidad_reservada += cantidad
        movimiento = MovimientoInventario(
            inventario_id=inv.id,
            tipo=TipoMovimiento.RESERVA,
            cantidad=cantidad,
            motivo="Reserva de prenda para prueba en sucursal",
            usuario_id=usuario_id
        )
        db.add(movimiento)
        await db.flush()
        return inv

    @classmethod
    async def liberar_stock_reserva(
        cls, db: AsyncSession, variante_id: int, sucursal_id: int, cantidad: int, usuario_id: Optional[int] = None, comprar: bool = False
    ) -> Inventario:
        inv = await cls.obtener_o_crear_inventario(db, variante_id, sucursal_id)
        inv.cantidad_reservada = max(0, inv.cantidad_reservada - cantidad)
        
        if comprar:
            inv.cantidad = max(0, inv.cantidad - cantidad)
            inv.cantidad_vendida += cantidad
            if inv.cantidad == 0:
                inv.estado = EstadoStock.AGOTADO
            tipo_mov = TipoMovimiento.VENTA
            motivo = "Compra de prenda previamente reservada"
        else:
            tipo_mov = TipoMovimiento.CANCELACION_RESERVA
            motivo = "Liberación/Cancelación de reserva de prenda"
            
        movimiento = MovimientoInventario(
            inventario_id=inv.id,
            tipo=tipo_mov,
            cantidad=-cantidad if comprar else 0,
            motivo=motivo,
            usuario_id=usuario_id
        )
        db.add(movimiento)
        await db.flush()
        return inv


# ==============================================================================
# CUPONES DE DESCUENTO
# ==============================================================================

class CuponService:
    @staticmethod
    async def crear_cupon(db: AsyncSession, cupon_in: CuponCreate, creado_por_id: Optional[int] = None) -> Cupon:
        codigo_norm = cupon_in.codigo.strip().upper()
        # Verificar duplicados
        query = select(Cupon).where(Cupon.codigo == codigo_norm)
        res = await db.execute(query)
        if res.scalar_one_or_none():
            raise ConflictException(f"Ya existe un cupón con el código '{codigo_norm}'")
            
        if cupon_in.fecha_fin <= cupon_in.fecha_inicio:
            raise BadRequestException("La fecha de fin debe ser posterior a la fecha de inicio")
            
        cupon = Cupon(
            codigo=codigo_norm,
            tipo=cupon_in.tipo,
            valor=cupon_in.valor,
            descripcion=cupon_in.descripcion,
            fecha_inicio=cupon_in.fecha_inicio,
            fecha_fin=cupon_in.fecha_fin,
            usos_maximos=cupon_in.usos_maximos,
            usos_actuales=0,
            monto_minimo=cupon_in.monto_minimo,
            estado=cupon_in.estado,
            creado_por_id=creado_por_id
        )
        db.add(cupon)
        await db.flush()

        # Asociar productos y categorías
        for pid in set(cupon_in.producto_ids or []):
            db.add(CuponProducto(cupon_id=cupon.id, producto_id=pid))

        for cid in set(cupon_in.categoria_ids or []):
            db.add(CuponCategoria(cupon_id=cupon.id, categoria_id=cid))

        await db.commit()
        return await CuponService.obtener_por_id(db, cupon.id)

    @staticmethod
    async def obtener_por_id(db: AsyncSession, cupon_id: int) -> Optional[Cupon]:
        query = (
            select(Cupon)
            .options(
                selectinload(Cupon.cupon_productos),
                selectinload(Cupon.cupon_categorias)
            )
            .where(Cupon.id == cupon_id)
        )
        res = await db.execute(query)
        return res.scalar_one_or_none()

    @staticmethod
    async def obtener_por_codigo(db: AsyncSession, codigo: str) -> Optional[Cupon]:
        query = (
            select(Cupon)
            .options(
                selectinload(Cupon.cupon_productos),
                selectinload(Cupon.cupon_categorias)
            )
            .where(Cupon.codigo == codigo.strip().upper())
        )
        res = await db.execute(query)
        return res.scalar_one_or_none()

    @staticmethod
    async def listar_cupones(db: AsyncSession, skip: int = 0, limit: int = 50) -> List[Cupon]:
        query = (
            select(Cupon)
            .options(
                selectinload(Cupon.cupon_productos),
                selectinload(Cupon.cupon_categorias)
            )
            .offset(skip)
            .limit(limit)
            .order_by(desc(Cupon.fecha_creacion))
        )
        res = await db.execute(query)
        return list(res.scalars().all())

    @classmethod
    async def listar_disponibles(cls, db: AsyncSession) -> List[Cupon]:
        """Obtiene cupones activos, vigentes y con usos disponibles para clientes (CU27)."""
        ahora = datetime.now(timezone.utc)
        query = (
            select(Cupon)
            .options(
                selectinload(Cupon.cupon_productos),
                selectinload(Cupon.cupon_categorias)
            )
            .where(
                Cupon.estado == EstadoCupon.ACTIVO,
                Cupon.fecha_inicio <= ahora,
                Cupon.fecha_fin >= ahora,
                or_(
                    Cupon.usos_maximos.is_(None),
                    Cupon.usos_actuales < Cupon.usos_maximos
                )
            )
            .order_by(Cupon.fecha_fin.asc())
        )
        res = await db.execute(query)
        return list(res.scalars().all())

    @classmethod
    async def actualizar_cupon(cls, db: AsyncSession, cupon_id: int, cupon_in: CuponUpdate) -> Cupon:
        cupon = await cls.obtener_por_id(db, cupon_id)
        if not cupon:
            raise NotFoundException(f"Cupón con ID {cupon_id} no encontrado")

        update_data = cupon_in.model_dump(exclude_unset=True)
        if "codigo" in update_data and update_data["codigo"]:
            codigo_norm = update_data["codigo"].strip().upper()
            if codigo_norm != cupon.codigo:
                # Verificar duplicados
                query = select(Cupon).where(Cupon.codigo == codigo_norm, Cupon.id != cupon_id)
                res = await db.execute(query)
                if res.scalar_one_or_none():
                    raise ConflictException(f"Ya existe otro cupón con el código '{codigo_norm}'")
                update_data["codigo"] = codigo_norm

        f_inicio = update_data.get("fecha_inicio", cupon.fecha_inicio)
        f_fin = update_data.get("fecha_fin", cupon.fecha_fin)
        if f_fin <= f_inicio:
            raise BadRequestException("La fecha de fin debe ser posterior a la fecha de inicio")

        for key, val in update_data.items():
            if key not in ["producto_ids", "categoria_ids"]:
                setattr(cupon, key, val)

        if cupon_in.producto_ids is not None:
            await db.execute(delete(CuponProducto).where(CuponProducto.cupon_id == cupon.id))
            for pid in set(cupon_in.producto_ids):
                db.add(CuponProducto(cupon_id=cupon.id, producto_id=pid))

        if cupon_in.categoria_ids is not None:
            await db.execute(delete(CuponCategoria).where(CuponCategoria.cupon_id == cupon.id))
            for cid in set(cupon_in.categoria_ids):
                db.add(CuponCategoria(cupon_id=cupon.id, categoria_id=cid))

        await db.commit()
        return await cls.obtener_por_id(db, cupon.id)

    @classmethod
    async def eliminar_cupon(cls, db: AsyncSession, cupon_id: int) -> bool:
        cupon = await cls.obtener_por_id(db, cupon_id)
        if not cupon:
            raise NotFoundException(f"Cupón con ID {cupon_id} no encontrado")

        try:
            await db.delete(cupon)
            await db.commit()
        except Exception:
            await db.rollback()
            # Si tiene registros vinculados (carritos/órdenes), marcar como INACTIVO
            cupon.estado = EstadoCupon.INACTIVO
            await db.commit()
            await db.refresh(cupon)

        return True

    @classmethod
    async def validar_cupon(
        cls,
        db: AsyncSession,
        codigo: str,
        subtotal: Decimal,
        items: Optional[List[Any]] = None
    ) -> Tuple[bool, str, Optional[Cupon], Decimal]:
        cupon = await cls.obtener_por_codigo(db, codigo)
        if not cupon:
            return False, "El cupón no existe", None, Decimal("0.00")
            
        ahora = datetime.now(timezone.utc)
        if cupon.estado != EstadoCupon.ACTIVO:
            return False, f"El cupón está {cupon.estado.value.lower()}", cupon, Decimal("0.00")
            
        # Comparar fechas conscientes de zona horaria
        f_inicio = cupon.fecha_inicio if cupon.fecha_inicio.tzinfo else cupon.fecha_inicio.replace(tzinfo=timezone.utc)
        f_fin = cupon.fecha_fin if cupon.fecha_fin.tzinfo else cupon.fecha_fin.replace(tzinfo=timezone.utc)
        
        if ahora < f_inicio:
            return False, "El cupón aún no está vigente", cupon, Decimal("0.00")
        if ahora > f_fin:
            cupon.estado = EstadoCupon.EXPIRADO
            await db.commit()
            return False, "El cupón ha expirado", cupon, Decimal("0.00")
            
        if cupon.usos_maximos is not None and cupon.usos_actuales >= cupon.usos_maximos:
            cupon.estado = EstadoCupon.AGOTADO
            await db.commit()
            return False, "El cupón ha alcanzado el límite de usos", cupon, Decimal("0.00")
            
        if cupon.monto_minimo is not None and subtotal < cupon.monto_minimo:
            return False, f"El subtotal mínimo para este cupón es de ${cupon.monto_minimo:.2f}", cupon, Decimal("0.00")
            
        # Validar aplicabilidad por productos y categorías (CU27)
        prod_ids = {cp.producto_id for cp in (cupon.cupon_productos or [])}
        cat_ids = {cc.categoria_id for cc in (cupon.cupon_categorias or [])}

        subtotal_base = subtotal
        if prod_ids or cat_ids:
            if items:
                subtotal_aplicable = Decimal("0.00")
                for it in items:
                    pid = getattr(it, "producto_id", None)
                    cid = getattr(it, "categoria_id", None)
                    # Si no vienen en el item directo, intentar resolver desde variante
                    if pid is None and hasattr(it, "variante_producto") and it.variante_producto:
                        pid = getattr(it.variante_producto, "producto_id", None)
                        if hasattr(it.variante_producto, "producto") and it.variante_producto.producto:
                            cid = getattr(it.variante_producto.producto, "categoria_id", None)
                    
                    # Si falta categoria_id pero tenemos producto_id, buscar categoria del producto
                    if cid is None and pid is not None:
                        q_p = select(Producto.categoria_id).where(Producto.id == pid)
                        res_p = await db.execute(q_p)
                        cid = res_p.scalar_one_or_none()

                    it_precio = getattr(it, "precio_unitario", Decimal("0.00"))
                    it_cant = getattr(it, "cantidad", 1)
                    it_subtotal = getattr(it, "subtotal", it_precio * it_cant)

                    es_aplicable = (pid in prod_ids) or (cid is not None and cid in cat_ids)
                    if es_aplicable:
                        subtotal_aplicable += Decimal(str(it_subtotal))

                if subtotal_aplicable <= Decimal("0.00"):
                    return False, "El cupón no es aplicable a los productos del carrito", cupon, Decimal("0.00")
                
                subtotal_base = subtotal_aplicable

        # Calcular descuento
        if cupon.tipo == TipoCupon.PORCENTAJE:
            descuento = (subtotal_base * cupon.valor) / Decimal("100.00")
        else:
            descuento = min(cupon.valor, subtotal_base)
            
        return True, "Cupón válido", cupon, descuento

    @classmethod
    async def incrementar_uso(cls, db: AsyncSession, cupon_id: int):
        query = select(Cupon).where(Cupon.id == cupon_id)
        res = await db.execute(query)
        cupon = res.scalar_one_or_none()
        if cupon:
            cupon.usos_actuales += 1
            if cupon.usos_maximos is not None and cupon.usos_actuales >= cupon.usos_maximos:
                cupon.estado = EstadoCupon.AGOTADO
            await db.flush()


# ==============================================================================
# CARRITO DE COMPRAS
# ==============================================================================

class CarritoService:
    @staticmethod
    async def obtener_o_crear_carrito(db: AsyncSession, cliente_id: int) -> Carrito:
        query = (
            select(Carrito)
            .options(
                selectinload(Carrito.items).selectinload(ItemCarrito.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(Carrito.items).selectinload(ItemCarrito.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(Carrito.items).selectinload(ItemCarrito.variante_producto).selectinload(VarianteProducto.color),
                selectinload(Carrito.cupon)
            )
            .where(Carrito.cliente_id == cliente_id, Carrito.estado == EstadoCarrito.ACTIVO)
        )
        res = await db.execute(query)
        carrito = res.scalar_one_or_none()
        if not carrito:
            carrito = Carrito(cliente_id=cliente_id, estado=EstadoCarrito.ACTIVO)
            db.add(carrito)
            await db.commit()
            await db.refresh(carrito)
            # Volver a cargar con relaciones
            res = await db.execute(query)
            carrito = res.scalar_one_or_none()
        return carrito

    @classmethod
    async def recalcular_carrito(cls, db: AsyncSession, carrito: Carrito) -> Dict[str, Decimal]:
        subtotal = sum((item.subtotal for item in carrito.items), Decimal("0.00"))
        descuento = Decimal("0.00")
        
        if carrito.cupon_id:
            cupon = carrito.cupon
            if not cupon:
                q_cup = select(Cupon).where(Cupon.id == carrito.cupon_id)
                res_cup = await db.execute(q_cup)
                cupon = res_cup.scalar_one_or_none()
                
            if cupon:
                valido, _, _, desc_calc = await CuponService.validar_cupon(db, cupon.codigo, subtotal, items=carrito.items)
                if valido:
                    descuento = desc_calc
                else:
                    carrito.cupon_id = None
                    carrito.descuento_aplicado = Decimal("0.00")
                    descuento = Decimal("0.00")
                
        carrito.descuento_aplicado = descuento
        total = max(Decimal("0.00"), subtotal - descuento)
        await db.flush()
        return {"subtotal": subtotal, "descuento": descuento, "total": total}

    @classmethod
    async def agregar_item(
        cls, db: AsyncSession, cliente_id: int, item_in: ItemCarritoCreate
    ) -> Carrito:
        # Validar que exista la variante
        q_var = (
            select(VarianteProducto)
            .options(joinedload(VarianteProducto.producto))
            .where(VarianteProducto.id == item_in.variante_producto_id)
        )
        res_var = await db.execute(q_var)
        variante = res_var.scalar_one_or_none()
        if not variante:
            raise NotFoundException("Variante de producto no encontrada")
            
        precio = variante.precio_variante if variante.precio_variante is not None else (variante.producto.precio if variante.producto else Decimal("0.00"))
        
        carrito = await cls.obtener_o_crear_carrito(db, cliente_id)
        
        # Buscar si ya existe el item en el carrito
        item_existente = next((it for it in carrito.items if it.variante_producto_id == item_in.variante_producto_id), None)
        if item_existente:
            item_existente.cantidad += item_in.cantidad
            item_existente.precio_unitario = precio
            item_existente.subtotal = precio * item_existente.cantidad
        else:
            nuevo_item = ItemCarrito(
                carrito_id=carrito.id,
                variante_producto_id=item_in.variante_producto_id,
                cantidad=item_in.cantidad,
                precio_unitario=precio,
                subtotal=precio * item_in.cantidad
            )
            db.add(nuevo_item)
            carrito.items.append(nuevo_item)
            
        await db.commit()
        return await cls.obtener_o_crear_carrito(db, cliente_id)

    @classmethod
    async def actualizar_item(
        cls, db: AsyncSession, cliente_id: int, item_id: int, cantidad: int
    ) -> Carrito:
        carrito = await cls.obtener_o_crear_carrito(db, cliente_id)
        item = next((it for it in carrito.items if it.id == item_id), None)
        if not item:
            raise NotFoundException("Item no encontrado en el carrito")
            
        if cantidad <= 0:
            await db.delete(item)
            carrito.items.remove(item)
        else:
            item.cantidad = cantidad
            item.subtotal = item.precio_unitario * cantidad
            
        await db.commit()
        return await cls.obtener_o_crear_carrito(db, cliente_id)

    @classmethod
    async def eliminar_item(cls, db: AsyncSession, cliente_id: int, item_id: int) -> Carrito:
        carrito = await cls.obtener_o_crear_carrito(db, cliente_id)
        item = next((it for it in carrito.items if it.id == item_id), None)
        if not item:
            raise NotFoundException("Item no encontrado en el carrito")
            
        await db.delete(item)
        await db.commit()
        return await cls.obtener_o_crear_carrito(db, cliente_id)

    @classmethod
    async def vaciar_carrito(cls, db: AsyncSession, cliente_id: int) -> Carrito:
        carrito = await cls.obtener_o_crear_carrito(db, cliente_id)
        for it in list(carrito.items):
            await db.delete(it)
        carrito.cupon_id = None
        carrito.descuento_aplicado = Decimal("0.00")
        await db.commit()
        return await cls.obtener_o_crear_carrito(db, cliente_id)

    @classmethod
    async def aplicar_cupon(cls, db: AsyncSession, cliente_id: int, codigo: str) -> Tuple[Carrito, Dict[str, Any]]:
        carrito = await cls.obtener_o_crear_carrito(db, cliente_id)
        subtotal = sum((item.subtotal for item in carrito.items), Decimal("0.00"))
        
        if len(carrito.items) == 0:
            raise BadRequestException("El carrito está vacío, no se puede aplicar un cupón")
            
        valido, msg, cupon, descuento = await CuponService.validar_cupon(db, codigo, subtotal)
        if not valido:
            raise BadRequestException(msg)
            
        carrito.cupon_id = cupon.id
        carrito.descuento_aplicado = descuento
        await db.commit()
        
        carrito_actualizado = await cls.obtener_o_crear_carrito(db, cliente_id)
        return carrito_actualizado, {"mensaje": f"Cupón '{cupon.codigo}' aplicado con éxito", "descuento": descuento}

    @classmethod
    async def remover_cupon(cls, db: AsyncSession, cliente_id: int) -> Carrito:
        carrito = await cls.obtener_o_crear_carrito(db, cliente_id)
        carrito.cupon_id = None
        carrito.descuento_aplicado = Decimal("0.00")
        await db.commit()
        return await cls.obtener_o_crear_carrito(db, cliente_id)


# ==============================================================================
# ÓRDENES DE VENTA
# ==============================================================================

class OrdenService:
    @staticmethod
    def generar_numero_orden() -> str:
        fecha_str = datetime.now().strftime("%Y%m%d")
        random_suffix = uuid.uuid4().hex[:6].upper()
        return f"ORD-{fecha_str}-{random_suffix}"

    @classmethod
    async def crear_orden_desde_carrito(
        cls,
        db: AsyncSession,
        cliente_id: int,
        sucursal_id: Optional[int] = None,
        direccion_envio: Optional[str] = None,
        tipo: TipoOrden = TipoOrden.DIGITAL,
        usuario_id: Optional[int] = None
    ) -> Orden:
        carrito = await CarritoService.obtener_o_crear_carrito(db, cliente_id)
        if not carrito.items:
            raise BadRequestException("No se puede crear una orden con un carrito vacío")
            
        subtotal = sum((item.subtotal for item in carrito.items), Decimal("0.00"))
        descuento = Decimal("0.00")
        
        if carrito.cupon:
            valido, _, cupon, desc_calc = await CuponService.validar_cupon(db, carrito.cupon.codigo, subtotal)
            if valido:
                descuento = desc_calc
                await CuponService.incrementar_uso(db, cupon.id)
                
        # Impuestos (e.g. 12% o 0 según aplique, default 0 para claridad)
        impuestos = Decimal("0.00")
        total = max(Decimal("0.00"), subtotal - descuento + impuestos)
        
        # Si se especificó sucursal, validar stock antes de crear orden
        if sucursal_id:
            for it in carrito.items:
                disponible = await InventarioVentasService.validar_stock_disponible(db, it.variante_producto_id, sucursal_id, it.cantidad)
                if not disponible:
                    raise BadRequestException(f"Stock insuficiente en la sucursal para la variante #{it.variante_producto_id}")
                    
        # Crear la orden
        numero_orden = cls.generar_numero_orden()
        orden = Orden(
            numero_orden=numero_orden,
            cliente_id=cliente_id,
            sucursal_id=sucursal_id,
            tipo=tipo,
            estado=EstadoOrden.PENDIENTE_PAGO,
            total=total,
            impuestos=impuestos,
            descuentos=descuento,
            cupon_id=carrito.cupon_id if descuento > 0 else None,
            direccion_envio=direccion_envio
        )
        db.add(orden)
        await db.flush()
        
        # Crear los detalles de orden (congelar costo vigente)
        variante_ids = [item.variante_producto_id for item in carrito.items]
        variantes_map: Dict[int, VarianteProducto] = {}
        if variante_ids:
            r_vars = await db.execute(
                select(VarianteProducto).options(joinedload(VarianteProducto.producto))
                .where(VarianteProducto.id.in_(variante_ids))
            )
            for v in r_vars.scalars().all():
                variantes_map[v.id] = v
        for item in carrito.items:
            detalle = DetalleOrden(
                orden_id=orden.id,
                variante_producto_id=item.variante_producto_id,
                cantidad=item.cantidad,
                precio_unitario=item.precio_unitario,
                costo_unitario=_resolver_costo_unitario(variantes_map.get(item.variante_producto_id)),
                subtotal=item.subtotal
            )
            db.add(detalle)
            
        # Vaciar el carrito
        await CarritoService.vaciar_carrito(db, cliente_id)
        
        await db.commit()
        return await cls.obtener_orden(db, orden.id)

    @staticmethod
    async def obtener_orden(db: AsyncSession, orden_id: int) -> Orden:
        query = (
            select(Orden)
            .options(
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.color),
                selectinload(Orden.comprobante),
                selectinload(Orden.transacciones),
                selectinload(Orden.cliente).selectinload(Cliente.usuario)
            )
            .where(Orden.id == orden_id)
        )
        res = await db.execute(query)
        orden = res.scalar_one_or_none()
        if not orden:
            raise NotFoundException("Orden no encontrada")
        return orden

    @staticmethod
    async def obtener_ordenes_cliente(
        db: AsyncSession, cliente_id: int, skip: int = 0, limit: int = 20
    ) -> List[Orden]:
        query = (
            select(Orden)
            .options(
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.color),
                selectinload(Orden.comprobante)
            )
            .where(Orden.cliente_id == cliente_id)
            .order_by(desc(Orden.fecha))
            .offset(skip)
            .limit(limit)
        )
        res = await db.execute(query)
        return list(res.scalars().all())

    @staticmethod
    async def listar_todas_ordenes(
        db: AsyncSession, cliente_id: Optional[int] = None, skip: int = 0, limit: int = 50
    ) -> List[Orden]:
        query = (
            select(Orden)
            .options(
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.color),
                selectinload(Orden.comprobante)
            )
            .order_by(desc(Orden.fecha))
            .offset(skip)
            .limit(limit)
        )
        if cliente_id:
            query = query.where(Orden.cliente_id == cliente_id)
        res = await db.execute(query)
        return list(res.scalars().all())

    @classmethod
    async def actualizar_estado(
        cls, db: AsyncSession, orden_id: int, nuevo_estado: EstadoOrden, usuario_id: Optional[int] = None
    ) -> Orden:
        orden = await cls.obtener_orden(db, orden_id)
        
        # Si cambia a PAGADO y tiene sucursal asociada, descontar inventario si no fue descontado
        if nuevo_estado == EstadoOrden.PAGADO and orden.estado != EstadoOrden.PAGADO:
            if orden.sucursal_id:
                for det in orden.detalles:
                    if det.variante_producto_id:
                        await InventarioVentasService.descontar_stock_venta(
                            db, det.variante_producto_id, orden.sucursal_id, det.cantidad, usuario_id=usuario_id, motivo=f"Venta Orden {orden.numero_orden}"
                        )
            # Generar comprobante automático
            await cls.generar_comprobante(db, orden.id)
            
        orden.estado = nuevo_estado
        await db.commit()
        return await cls.obtener_orden(db, orden.id)

    @staticmethod
    async def generar_comprobante(
        db: AsyncSession, orden_id: int, tipo: TipoComprobante = TipoComprobante.FACTURA
    ) -> Comprobante:
        q_comp = select(Comprobante).where(Comprobante.orden_id == orden_id)
        res = await db.execute(q_comp)
        comp = res.scalar_one_or_none()
        pdf_url = f"/api/v1/ordenes/{orden_id}/comprobante/pdf"
        if comp:
            if not comp.archivo_pdf:
                comp.archivo_pdf = pdf_url
                await db.flush()
            return comp
            
        q_ord = select(Orden).where(Orden.id == orden_id)
        r_ord = await db.execute(q_ord)
        orden = r_ord.scalar_one_or_none()
        if not orden:
            raise NotFoundException("Orden no encontrada")
            
        prefix = "FAC" if tipo == TipoComprobante.FACTURA else "TCK"
        num_comp = f"{prefix}-{datetime.now().strftime('%Y%m')}-{orden.id:06d}"
        comprobante = Comprobante(
            numero=num_comp,
            tipo=tipo,
            monto_total=orden.total,
            archivo_pdf=pdf_url,
            orden_id=orden.id
        )
        db.add(comprobante)
        await db.flush()
        return comprobante

    @staticmethod
    async def generar_pdf_orden(db: AsyncSession, orden_id: int) -> Tuple[bytes, str]:
        """Obtiene toda la información de la orden y genera el PDF del comprobante."""
        query = (
            select(Orden)
            .options(
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.color),
                selectinload(Orden.comprobante),
                selectinload(Orden.sucursal),
                selectinload(Orden.cliente).selectinload(Cliente.usuario),
            )
            .where(Orden.id == orden_id)
        )
        res = await db.execute(query)
        orden = res.scalar_one_or_none()
        if not orden:
            raise NotFoundException("Orden no encontrada")

        # Asegurar comprobante
        if not orden.comprobante:
            comp = await OrdenService.generar_comprobante(db, orden.id)
            orden.comprobante = comp

        # Buscar si proviene de venta presencial para cajero y método de pago
        q_vp = select(VentaPresencial).options(selectinload(VentaPresencial.cajero).selectinload(Cajero.usuario)).where(VentaPresencial.orden_id == orden.id)
        res_vp = await db.execute(q_vp)
        vp = res_vp.scalar_one_or_none()

        cajero_nom = None
        metodo_pago = "PAGO DIGITAL (STRIPE/PAYPAL)"
        if vp:
            metodo_pago = vp.metodo_pago.value if hasattr(vp.metodo_pago, 'value') else str(vp.metodo_pago)
            if vp.cajero and vp.cajero.usuario:
                cajero_nom = f"{vp.cajero.usuario.nombre} {vp.cajero.usuario.apellido}".strip()

        cliente_nombre = "CLIENTE GENÉRICO / CONSUMIDOR FINAL"
        cliente_nit = "0"
        if orden.cliente:
            cliente_nit = orden.cliente.nit_ci or "0"
            if orden.cliente.usuario:
                cliente_nombre = f"{orden.cliente.usuario.nombre} {orden.cliente.usuario.apellido}".strip()

        suc_nom = orden.sucursal.nombre if orden.sucursal else "Sucursal Central"
        suc_dir = orden.sucursal.direccion if orden.sucursal else "Av. Monseñor Rivero #300, Santa Cruz"
        suc_tel = orden.sucursal.telefono if orden.sucursal else "33123456"

        items_pdf = []
        for det in orden.detalles:
            var = det.variante_producto
            prod = var.producto if var else None
            talla_val = (var.talla.valor or var.talla.nombre) if (var and var.talla) else "-"
            color_val = var.color.nombre if (var and var.color) else "-"
            sku_val = var.sku_variante if var else (prod.sku if prod else "")
            nombre_val = prod.nombre if prod else "Prenda FashionStore"

            items_pdf.append({
                "cantidad": det.cantidad,
                "sku": sku_val,
                "nombre": nombre_val,
                "talla": talla_val,
                "color": color_val,
                "precio_unitario": det.precio_unitario,
                "subtotal": det.subtotal
            })

        subtotal = sum((det.subtotal for det in orden.detalles), Decimal("0.00"))

        from app.services.pdf_service import ComprobantePDFService
        tipo_str = orden.comprobante.tipo.value if hasattr(orden.comprobante.tipo, 'value') else str(orden.comprobante.tipo)
        pdf_bytes = ComprobantePDFService.generar_pdf_comprobante(
            comprobante_numero=orden.comprobante.numero,
            tipo_comprobante=tipo_str,
            fecha=orden.comprobante.fecha_emision or orden.fecha or datetime.now(),
            orden_numero=orden.numero_orden,
            cliente_nombre=cliente_nombre,
            cliente_nit_ci=cliente_nit,
            sucursal_nombre=suc_nom,
            sucursal_direccion=suc_dir,
            sucursal_telefono=suc_tel,
            cajero_nombre=cajero_nom,
            metodo_pago=metodo_pago,
            items=items_pdf,
            subtotal=subtotal,
            descuento=orden.descuentos or Decimal("0.00"),
            total=orden.total
        )
        return pdf_bytes, orden.comprobante.numero


# ==============================================================================
# VENTAS PRESENCIALES
# ==============================================================================

class VentaPresencialService:
    @staticmethod
    async def crear_venta(
        db: AsyncSession,
        cajero_usuario_id: int,
        sucursal_id: int,
        metodo_pago: MetodoPagoPresencial,
        detalles: List[DetalleVentaPresencialCreate],
        cliente_id: Optional[int] = None,
        nit_ci_cliente: Optional[str] = None,
        cupon_codigo: Optional[str] = None
    ) -> VentaPresencial:
        # Buscar ID de cajero
        q_cajero = select(Cajero).where(Cajero.usuario_id == cajero_usuario_id)
        res_cajero = await db.execute(q_cajero)
        cajero = res_cajero.scalar_one_or_none()
        cajero_id = cajero.id if cajero else None
        
        # Si no se pasó cliente_id pero se pasó nit_ci_cliente, buscar o crear cliente
        if not cliente_id and nit_ci_cliente:
            q_cli = select(Cliente).where(Cliente.nit_ci == nit_ci_cliente)
            r_cli = await db.execute(q_cli)
            cli = r_cli.scalar_one_or_none()
            if cli:
                cliente_id = cli.id
                
        # Calcular subtotales
        subtotal = Decimal("0.00")
        items_procesados = []
        for det in detalles:
            q_var = (
                select(VarianteProducto)
                .options(joinedload(VarianteProducto.producto))
                .where(VarianteProducto.id == det.variante_producto_id)
            )
            r_var = await db.execute(q_var)
            variante = r_var.scalar_one_or_none()
            if not variante:
                raise NotFoundException(f"Variante #{det.variante_producto_id} no encontrada")
                
            precio = det.precio_unitario or variante.precio_variante or (variante.producto.precio if variante.producto else Decimal("0.00"))
            st = precio * det.cantidad
            subtotal += st
            items_procesados.append({
                "variante_id": det.variante_producto_id,
                "cantidad": det.cantidad,
                "precio": precio,
                "costo": _resolver_costo_unitario(variante),
                "subtotal": st
            })
            
            # Validar y descontar stock
            await InventarioVentasService.descontar_stock_venta(
                db, det.variante_producto_id, sucursal_id, det.cantidad, usuario_id=cajero_usuario_id, motivo="Venta Presencial en Sucursal"
            )
            
        # Cupon de descuento
        descuento = Decimal("0.00")
        cupon_id = None
        if cupon_codigo:
            valido, _, cupon, desc_calc = await CuponService.validar_cupon(db, cupon_codigo, subtotal)
            if valido and cupon:
                descuento = desc_calc
                cupon_id = cupon.id
                await CuponService.incrementar_uso(db, cupon.id)
                
        total = max(Decimal("0.00"), subtotal - descuento)
        
        # Crear Orden
        numero_orden = OrdenService.generar_numero_orden()
        orden = Orden(
            numero_orden=numero_orden,
            cliente_id=cliente_id,
            sucursal_id=sucursal_id,
            tipo=TipoOrden.PRESENCIAL,
            estado=EstadoOrden.PAGADO,
            total=total,
            impuestos=Decimal("0.00"),
            descuentos=descuento,
            cupon_id=cupon_id
        )
        db.add(orden)
        await db.flush()
        
        # Crear Detalles Orden
        for item in items_procesados:
            det_ord = DetalleOrden(
                orden_id=orden.id,
                variante_producto_id=item["variante_id"],
                cantidad=item["cantidad"],
                precio_unitario=item["precio"],
                costo_unitario=item.get("costo"),
                subtotal=item["subtotal"]
            )
            db.add(det_ord)
            
        # Crear Comprobante
        await OrdenService.generar_comprobante(db, orden.id, TipoComprobante.TICKET)
        
        # Crear VentaPresencial
        venta = VentaPresencial(
            orden_id=orden.id,
            cajero_id=cajero_id,
            sucursal_id=sucursal_id,
            cliente_id=cliente_id,
            metodo_pago=metodo_pago
        )
        db.add(venta)
        await db.commit()
        
        # Cargar relaciones completas para la respuesta
        q_venta = (
            select(VentaPresencial)
            .options(
                selectinload(VentaPresencial.orden).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(VentaPresencial.orden).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(VentaPresencial.orden).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.color),
                selectinload(VentaPresencial.orden).selectinload(Orden.comprobante),
                selectinload(VentaPresencial.cajero),
                selectinload(VentaPresencial.sucursal)
            )
            .where(VentaPresencial.id == venta.id)
        )
        res_v = await db.execute(q_venta)
        return res_v.scalar_one()

    @staticmethod
    async def listar_ventas(
        db: AsyncSession, sucursal_id: Optional[int] = None, cajero_id: Optional[int] = None, skip: int = 0, limit: int = 50
    ) -> List[VentaPresencial]:
        query = (
            select(VentaPresencial)
            .options(
                selectinload(VentaPresencial.orden).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(VentaPresencial.orden).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(VentaPresencial.orden).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.color),
                selectinload(VentaPresencial.orden).selectinload(Orden.comprobante),
                selectinload(VentaPresencial.cajero),
                selectinload(VentaPresencial.sucursal)
            )
            .order_by(desc(VentaPresencial.fecha))
            .offset(skip)
            .limit(limit)
        )
        if sucursal_id:
            query = query.where(VentaPresencial.sucursal_id == sucursal_id)
        if cajero_id:
            query = query.where(VentaPresencial.cajero_id == cajero_id)
            
        res = await db.execute(query)
        return list(res.scalars().all())


# ==============================================================================
# RESERVAS DE PRENDAS
# ==============================================================================

class ReservaService:
    @staticmethod
    def generar_numero_reserva() -> str:
        fecha_str = datetime.now().strftime("%Y%m%d")
        random_suffix = uuid.uuid4().hex[:6].upper()
        return f"RES-{fecha_str}-{random_suffix}"

    @classmethod
    async def crear_reserva(
        cls, db: AsyncSession, cliente_id: int, reserva_in: ReservaCreate, usuario_id: Optional[int] = None
    ) -> Reserva:
        # 1. Validar y reservar stock en la sucursal elegida
        for det in reserva_in.detalles:
            await InventarioVentasService.reservar_stock(
                db, det.variante_producto_id, reserva_in.sucursal_id, det.cantidad, usuario_id=usuario_id
            )
            
        # 2. Crear cabecera de reserva
        numero_reserva = cls.generar_numero_reserva()
        reserva = Reserva(
            numero_reserva=numero_reserva,
            cliente_id=cliente_id,
            sucursal_id=reserva_in.sucursal_id,
            fecha_reserva=reserva_in.fecha_reserva,
            horario_aproximado=reserva_in.horario_aproximado,
            estado=EstadoReserva.PENDIENTE,
            notas=reserva_in.notas
        )
        db.add(reserva)
        await db.flush()
        
        # 3. Crear detalles de reserva
        for det in reserva_in.detalles:
            det_res = DetalleReserva(
                reserva_id=reserva.id,
                variante_producto_id=det.variante_producto_id,
                cantidad=det.cantidad,
                estado=EstadoDetalleReserva.PENDIENTE
            )
            db.add(det_res)
            
        await db.commit()
        reserva_creada = await cls.obtener_reserva(db, reserva.id)

        # CU12: Notificar a la sucursal sobre la nueva reserva
        try:
            nombre_sucursal = reserva_creada.sucursal.nombre if reserva_creada.sucursal else "Sucursal"
            await FirebaseService.enviar_notificacion_sucursal(
                db=db,
                sucursal_id=reserva_in.sucursal_id,
                titulo=f"Nueva Reserva: #{reserva_creada.numero_reserva}",
                cuerpo=f"Se ha recibido una nueva reserva en {nombre_sucursal} ({len(reserva_in.detalles)} prenda(s) por apartar).",
                data={"reserva_id": str(reserva_creada.id), "tipo": "NUEVA_RESERVA", "url": "/branch/reservations"}
            )
        except Exception as e:
            logger.warning(f"Error al enviar notificación FCM por nueva reserva: {e}")

        return reserva_creada

    @staticmethod
    async def obtener_reserva(db: AsyncSession, reserva_id: int) -> Reserva:
        query = (
            select(Reserva)
            .options(
                selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.color),
                selectinload(Reserva.sucursal),
                selectinload(Reserva.cliente)
            )
            .where(Reserva.id == reserva_id)
        )
        res = await db.execute(query)
        reserva = res.scalar_one_or_none()
        if not reserva:
            raise NotFoundException("Reserva no encontrada")
        return reserva

    @staticmethod
    async def listar_reservas_cliente(db: AsyncSession, cliente_id: int) -> List[Reserva]:
        query = (
            select(Reserva)
            .options(
                selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.color),
                selectinload(Reserva.sucursal)
            )
            .where(Reserva.cliente_id == cliente_id)
            .order_by(desc(Reserva.fecha_creacion))
        )
        res = await db.execute(query)
        return list(res.scalars().all())

    @staticmethod
    async def listar_reservas_sucursal(
        db: AsyncSession, sucursal_id: Optional[int] = None, estado: Optional[EstadoReserva] = None
    ) -> List[Reserva]:
        query = (
            select(Reserva)
            .options(
                selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.color),
                selectinload(Reserva.cliente).selectinload(Cliente.usuario),
                selectinload(Reserva.sucursal)
            )
            .order_by(desc(Reserva.fecha_creacion))
        )
        if sucursal_id:
            query = query.where(Reserva.sucursal_id == sucursal_id)
        if estado:
            query = query.where(Reserva.estado == estado)
        res = await db.execute(query)
        return list(res.scalars().all())

    listar_reservas = listar_reservas_sucursal


    @classmethod
    async def cambiar_estado(
        cls, db: AsyncSession, reserva_id: int, nuevo_estado: EstadoReserva, usuario_id: Optional[int] = None
    ) -> Reserva:
        reserva = await cls.obtener_reserva(db, reserva_id)
        estado_anterior = reserva.estado
        
        # CU13 Excepción: Si la reserva ya está en "Preparada" o posterior, no se permite cancelar
        if nuevo_estado == EstadoReserva.CANCELADA:
            if estado_anterior in (EstadoReserva.PREPARADA, EstadoReserva.EN_PRUEBA, EstadoReserva.COMPLETADA):
                raise BadRequestException(
                    f"No se puede cancelar una reserva en estado '{estado_anterior.value}'. "
                    f"Solo se permiten cancelaciones si la reserva está en estado 'PENDIENTE'."
                )

        # Si se cancela o caduca, liberar stock reservado
        if nuevo_estado in (EstadoReserva.CANCELADA, EstadoReserva.CADUCADA) and estado_anterior not in (EstadoReserva.CANCELADA, EstadoReserva.CADUCADA, EstadoReserva.COMPLETADA):
            for det in reserva.detalles:
                await InventarioVentasService.liberar_stock_reserva(
                    db, det.variante_producto_id, reserva.sucursal_id, det.cantidad, usuario_id=usuario_id, comprar=False
                )
                det.estado = EstadoDetalleReserva.DEVUELTO
                
        reserva.estado = nuevo_estado
        await db.commit()
        reserva_actualizada = await cls.obtener_reserva(db, reserva_id)

        # Disparo de Notificaciones Push (FCM) según el cambio de estado
        try:
            nombre_suc = reserva_actualizada.sucursal.nombre if reserva_actualizada.sucursal else "Sucursal"
            num_res = reserva_actualizada.numero_reserva or f"#{reserva_actualizada.id}"
            
            # 1. Notificar al cliente si la prenda ya fue PREPARADA
            if nuevo_estado == EstadoReserva.PREPARADA and reserva_actualizada.cliente:
                await FirebaseService.enviar_notificacion_usuario(
                    db=db,
                    usuario_id=reserva_actualizada.cliente.usuario_id,
                    titulo="¡Tus prendas están listas para prueba!",
                    cuerpo=f"Tu reserva {num_res} ya está preparada en {nombre_suc}. Puedes pasar hoy a probártelas.",
                    data={"reserva_id": str(reserva_id), "tipo": "RESERVA_PREPARADA", "url": "/profile/reservations"}
                )

            # 2. Notificar cancelación a cliente y sucursal
            elif nuevo_estado == EstadoReserva.CANCELADA:
                # Notificar a la sucursal para devolver prendas
                await FirebaseService.enviar_notificacion_sucursal(
                    db=db,
                    sucursal_id=reserva_actualizada.sucursal_id,
                    titulo=f"Reserva Cancelada: {num_res}",
                    cuerpo=f"La reserva {num_res} ha sido cancelada. Las prendas han sido liberadas a inventario.",
                    data={"reserva_id": str(reserva_id), "tipo": "RESERVA_CANCELADA", "url": "/branch/reservations"}
                )
                # Confirmar al cliente si la cancelación la hizo la tienda o el sistema
                if reserva_actualizada.cliente:
                    await FirebaseService.enviar_notificacion_usuario(
                        db=db,
                        usuario_id=reserva_actualizada.cliente.usuario_id,
                        titulo=f"Reserva Cancelada: {num_res}",
                        cuerpo=f"Tu reserva {num_res} ha sido cancelada correctamente.",
                        data={"reserva_id": str(reserva_id), "tipo": "RESERVA_CANCELADA", "url": "/profile/reservations"}
                    )

            # 3. Notificar al cliente cuando se completa la prueba / retiro
            elif nuevo_estado == EstadoReserva.COMPLETADA and reserva_actualizada.cliente:
                await FirebaseService.enviar_notificacion_usuario(
                    db=db,
                    usuario_id=reserva_actualizada.cliente.usuario_id,
                    titulo=f"¡Gracias por tu visita! Reserva {num_res}",
                    cuerpo=f"Tu atención de reserva en {nombre_suc} ha finalizado con éxito.",
                    data={"reserva_id": str(reserva_id), "tipo": "RESERVA_COMPLETADA", "url": "/profile/reservations"}
                )
        except Exception as e:
            logger.warning(f"Error al enviar notificación FCM por cambio de estado de reserva: {e}")

        return reserva_actualizada

    @classmethod
    async def completar_reserva(
        cls,
        db: AsyncSession,
        reserva_id: int,
        req: CompletarReservaRequest,
        cajero_usuario_id: int
    ) -> Dict[str, Any]:
        """
        Finaliza la prueba presencial:
        - Para prendas compradas: se libera reserva y se descuenta del stock definitivo como venta.
        - Para prendas no compradas: se cancela la reserva y se devuelve al stock disponible.
        - Genera Orden presencial y Comprobante.
        """
        reserva = await cls.obtener_reserva(db, reserva_id)
        if reserva.estado in (EstadoReserva.CANCELADA, EstadoReserva.CADUCADA, EstadoReserva.COMPLETADA):
            raise BadRequestException(f"La reserva ya está en estado {reserva.estado.value}")
            
        items_comprados_ids = {it.detalle_reserva_id for it in req.items if it.comprado}
        detalles_comprados = []
        
        for det in reserva.detalles:
            if det.id in items_comprados_ids:
                # Comprado
                det.estado = EstadoDetalleReserva.COMPRADO
                await InventarioVentasService.liberar_stock_reserva(
                    db, det.variante_producto_id, reserva.sucursal_id, det.cantidad, usuario_id=cajero_usuario_id, comprar=True
                )
                
                # Obtener precio
                variante = det.variante_producto
                precio = variante.precio_variante if (variante and variante.precio_variante is not None) else (variante.producto.precio if (variante and variante.producto) else Decimal("0.00"))
                
                detalles_comprados.append({
                    "variante_id": det.variante_producto_id,
                    "cantidad": det.cantidad,
                    "precio": precio,
                    "costo": _resolver_costo_unitario(variante),
                    "subtotal": precio * det.cantidad
                })
            else:
                # Devuelto
                det.estado = EstadoDetalleReserva.DEVUELTO
                await InventarioVentasService.liberar_stock_reserva(
                    db, det.variante_producto_id, reserva.sucursal_id, det.cantidad, usuario_id=cajero_usuario_id, comprar=False
                )
                
        reserva.estado = EstadoReserva.COMPLETADA
        
        orden = None
        if detalles_comprados:
            total = sum((it["subtotal"] for it in detalles_comprados), Decimal("0.00"))
            orden = Orden(
                numero_orden=OrdenService.generar_numero_orden(),
                cliente_id=reserva.cliente_id,
                sucursal_id=reserva.sucursal_id,
                tipo=TipoOrden.RESERVA,
                estado=EstadoOrden.PAGADO,
                total=total,
                impuestos=Decimal("0.00"),
                descuentos=Decimal("0.00")
            )
            db.add(orden)
            await db.flush()
            
            for item in detalles_comprados:
                det_ord = DetalleOrden(
                    orden_id=orden.id,
                    variante_producto_id=item["variante_id"],
                    cantidad=item["cantidad"],
                    precio_unitario=item["precio"],
                    costo_unitario=item.get("costo"),
                    subtotal=item["subtotal"]
                )
                db.add(det_ord)
                
            await OrdenService.generar_comprobante(db, orden.id, TipoComprobante.TICKET)
            
            # Registrar venta presencial
            q_cajero = select(Cajero).where(Cajero.usuario_id == cajero_usuario_id)
            r_caj = await db.execute(q_cajero)
            cajero = r_caj.scalar_one_or_none()
            
            venta = VentaPresencial(
                orden_id=orden.id,
                cajero_id=cajero.id if cajero else None,
                sucursal_id=reserva.sucursal_id,
                cliente_id=reserva.cliente_id,
                metodo_pago=req.metodo_pago
            )
            db.add(venta)
            
        await db.commit()
        return {
            "mensaje": "Reserva completada con éxito",
            "reserva_id": reserva.id,
            "items_comprados": len(detalles_comprados),
            "orden_id": orden.id if orden else None
        }


# ==============================================================================
# PAGOS DIGITALES (STRIPE Y PAYPAL)
# ==============================================================================

class PagoService:
    @staticmethod
    async def crear_sesion_stripe(
        db: AsyncSession, orden_id: int, email_usuario: Optional[str] = None, success_url: Optional[str] = None, cancel_url: Optional[str] = None
    ) -> Dict[str, str]:
        orden = await OrdenService.obtener_orden(db, orden_id)
        if orden.estado == EstadoOrden.PAGADO:
            raise BadRequestException("La orden ya se encuentra pagada")
            
        # Si no se pasó email, obtener del cliente
        if not email_usuario and orden.cliente and orden.cliente.usuario:
            email_usuario = orden.cliente.usuario.correo
            
        session_data = StripeService.create_checkout_session(
            orden_id=orden.id,
            numero_orden=orden.numero_orden,
            total=orden.total,
            email_usuario=email_usuario,
            detalles=orden.detalles,
            success_url=success_url,
            cancel_url=cancel_url
        )
        
        # Registrar transacción en estado PENDIENTE
        transaccion = TransaccionPago(
            orden_id=orden.id,
            monto=orden.total,
            moneda="USD",
            metodo_pago=MetodoPagoDigital.STRIPE,
            estado=EstadoTransaccion.PENDIENTE,
            referencia_externa=session_data["session_id"]
        )
        db.add(transaccion)
        await db.commit()
        
        return session_data

    @staticmethod
    async def procesar_pago_stripe_completado(db: AsyncSession, session_id: str, orden_id: int):
        """Procesa la confirmación de pago de Stripe (vía Webhook o confirmación de retorno)."""
        session_stripe = StripeService.retrieve_session(session_id)
        if session_stripe.payment_status == "paid":
            # Actualizar transacción
            q_tx = select(TransaccionPago).where(TransaccionPago.referencia_externa == session_id)
            res_tx = await db.execute(q_tx)
            tx = res_tx.scalar_one_or_none()
            if tx:
                tx.estado = EstadoTransaccion.CONFIRMADO
                tx.datos_respuesta = {"payment_intent": session_stripe.payment_intent}
                
            # Actualizar orden
            await OrdenService.actualizar_estado(db, orden_id, EstadoOrden.PAGADO)
            await db.commit()

    @staticmethod
    @staticmethod
    async def crear_orden_paypal(
        db: AsyncSession, orden_id: int, return_url: Optional[str] = None, cancel_url: Optional[str] = None
    ) -> Dict[str, Any]:
        orden = await OrdenService.obtener_orden(db, orden_id)
        if orden.estado == EstadoOrden.PAGADO:
            raise BadRequestException("La orden ya se encuentra pagada")
        
        logger.info(f"Creando orden PayPal para orden #{orden_id}")
        
        try:
            paypal_data = await PayPalService.create_order(
                orden_id=orden.id,
                numero_orden=orden.numero_orden,
                total=orden.total,
                moneda="USD",
                return_url=return_url,
                cancel_url=cancel_url
            )
        except ValueError as e:
            logger.error(f"Error al crear orden PayPal: {str(e)}")
            # Registrar transacción fallida
            transaccion = TransaccionPago(
                orden_id=orden.id,
                monto=orden.total,
                moneda="USD",
                metodo_pago=MetodoPagoDigital.PAYPAL,
                estado=EstadoTransaccion.RECHAZADO,
                referencia_externa=None,
                datos_respuesta={"error": str(e)}
            )
            db.add(transaccion)
            await db.commit()
            raise BadRequestException(f"Error al crear orden en PayPal: {str(e)}")
        except Exception as e:
            logger.error(f"Error inesperado al crear orden PayPal: {str(e)}")
            raise BadRequestException(f"Error inesperado al procesar el pago con PayPal: {str(e)}")
        
        # Registrar transacción
        transaccion = TransaccionPago(
            orden_id=orden.id,
            monto=orden.total,
            moneda="USD",
            metodo_pago=MetodoPagoDigital.PAYPAL,
            estado=EstadoTransaccion.PENDIENTE,
            referencia_externa=paypal_data["order_id"],
            datos_respuesta=paypal_data.get("raw_response")
        )
        db.add(transaccion)
        await db.commit()
        
        logger.info(f"Orden PayPal creada exitosamente: {paypal_data['order_id']}")
        
        return paypal_data

    @staticmethod
    async def capturar_pago_paypal(db: AsyncSession, paypal_order_id: str, orden_id: int) -> Dict[str, Any]:
        capture_data = await PayPalService.capture_order(paypal_order_id)
        status = capture_data.get("status")
        
        # Buscar transacción
        q_tx = select(TransaccionPago).where(TransaccionPago.referencia_externa == paypal_order_id)
        res_tx = await db.execute(q_tx)
        tx = res_tx.scalar_one_or_none()
        
        if status == "COMPLETED":
            if tx:
                tx.estado = EstadoTransaccion.CONFIRMADO
                tx.datos_respuesta = capture_data
            await OrdenService.actualizar_estado(db, orden_id, EstadoOrden.PAGADO)
            await db.commit()
            return {"status": "SUCCESS", "message": "Pago con PayPal completado con éxito", "data": capture_data}
        else:
            if tx:
                tx.estado = EstadoTransaccion.RECHAZADO
                tx.datos_respuesta = capture_data
            await db.commit()
            return {"status": "FAILED", "message": f"Estado de pago no completado: {status}", "data": capture_data}

    @staticmethod
    async def listar_transacciones(db: AsyncSession, orden_id: int) -> List[TransaccionPago]:
        query = select(TransaccionPago).where(TransaccionPago.orden_id == orden_id).order_by(desc(TransaccionPago.fecha))
        res = await db.execute(query)
        return list(res.scalars().all())


# ==============================================================================
# DEVOLUCIONES Y CAMBIOS (CU28)
# ==============================================================================

class DevolucionService:
    """Servicio de lógica de negocio para devoluciones y cambios de prendas (CU28)."""

    @classmethod
    def _ahora(cls) -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _generar_numero_solicitud(cls) -> str:
        fecha_str = datetime.now().strftime("%Y%m%d")
        random_str = secrets.token_hex(2).upper()
        return f"DEV-{fecha_str}-{random_str}"

    @classmethod
    async def crear_solicitud(
        cls,
        db: AsyncSession,
        cliente_id: int,
        datos: SolicitudDevolucionCreate
    ) -> SolicitudDevolucion:
        ahora = cls._ahora()

        # 1. Obtener orden
        q_orden = (
            select(Orden)
            .options(selectinload(Orden.detalles))
            .where(Orden.id == datos.orden_id)
        )
        res_orden = await db.execute(q_orden)
        orden = res_orden.scalar_one_or_none()
        if not orden:
            raise NotFoundException(f"Orden con ID {datos.orden_id} no encontrada")

        if orden.cliente_id != cliente_id:
            raise ForbiddenException("No tienes permiso para solicitar devoluciones de órdenes ajenas")

        estados_permitidos = [EstadoOrden.PAGADO, EstadoOrden.EN_PROCESO, EstadoOrden.ENVIADO, EstadoOrden.ENTREGADO]
        if orden.estado not in estados_permitidos:
            raise BadRequestException(f"No se puede solicitar devolución para una orden en estado {orden.estado.value}")

        # 2. Validar plazo de devolución
        orden_fecha = orden.fecha if orden.fecha.tzinfo else orden.fecha.replace(tzinfo=timezone.utc)
        plazo_dias = getattr(settings, "PLAZO_DEVOLUCION_DIAS", 30)
        if orden_fecha + timedelta(days=plazo_dias) < ahora:
            raise BadRequestException(
                f"El plazo de devolución de {plazo_dias} días ha vencido para esta orden (comprada el {orden_fecha.strftime('%d/%m/%Y')})."
            )

        # 3. Validar items de la orden
        detalles_orden_map = {d.id: d for d in orden.detalles}
        monto_total_reembolso = Decimal("0.00")
        sucursal_id = datos.sucursal_id or orden.sucursal_id

        # Verificar que no existan solicitudes pendientes/en revisión/aprobadas para estos detalles
        detalle_ids_solicitados = [it.detalle_orden_id for it in datos.items]
        q_sol_exist = (
            select(DetalleSolicitudDevolucion)
            .join(SolicitudDevolucion, SolicitudDevolucion.id == DetalleSolicitudDevolucion.solicitud_id)
            .where(
                DetalleSolicitudDevolucion.detalle_orden_id.in_(detalle_ids_solicitados),
                SolicitudDevolucion.estado.in_([
                    EstadoSolicitudDevolucion.PENDIENTE,
                    EstadoSolicitudDevolucion.EN_REVISION,
                    EstadoSolicitudDevolucion.APROBADA,
                    EstadoSolicitudDevolucion.PENDIENTE_REEMBOLSO
                ])
            )
        )
        res_sol_exist = await db.execute(q_sol_exist)
        if res_sol_exist.scalars().all():
            raise ConflictException("Ya existe una solicitud activa para uno o más productos de esta orden")

        detalles_solicitud: List[DetalleSolicitudDevolucion] = []
        for it in datos.items:
            if it.detalle_orden_id not in detalles_orden_map:
                raise BadRequestException(f"El detalle de orden ID {it.detalle_orden_id} no pertenece a la orden ID {datos.orden_id}")

            det_orden = detalles_orden_map[it.detalle_orden_id]
            if it.cantidad > det_orden.cantidad:
                raise BadRequestException(
                    f"La cantidad a devolver ({it.cantidad}) supera la cantidad comprada ({det_orden.cantidad}) en el detalle ID {it.detalle_orden_id}"
                )

            # Si es cambio, validar disponibilidad de la nueva variante
            if datos.tipo == TipoSolicitudDevolucion.CAMBIO:
                if not it.variante_cambio_id:
                    raise BadRequestException("Para solicitudes de CAMBIO es obligatorio especificar la nueva variante deseada")
                
                if sucursal_id:
                    stock_disp = await InventarioVentasService.validar_stock_disponible(
                        db, it.variante_cambio_id, sucursal_id, it.cantidad
                    )
                    if not stock_disp:
                        raise BadRequestException(
                            f"No hay stock suficiente en la sucursal para la nueva variante ID {it.variante_cambio_id}. Considere solicitar DEVOLUCIÓN."
                        )

            precio_unit = det_orden.precio_unitario
            monto_total_reembolso += (precio_unit * it.cantidad)

            detalles_solicitud.append(
                DetalleSolicitudDevolucion(
                    detalle_orden_id=it.detalle_orden_id,
                    variante_producto_id=det_orden.variante_producto_id,
                    cantidad=it.cantidad,
                    variante_cambio_id=it.variante_cambio_id,
                    precio_unitario=precio_unit
                )
            )

        solicitud = SolicitudDevolucion(
            numero_solicitud=cls._generar_numero_solicitud(),
            cliente_id=cliente_id,
            orden_id=datos.orden_id,
            sucursal_id=sucursal_id,
            tipo=datos.tipo,
            motivo=datos.motivo,
            motivo_detalle=datos.motivo_detalle,
            estado=EstadoSolicitudDevolucion.PENDIENTE,
            monto_reembolso=monto_total_reembolso,
            detalles=detalles_solicitud
        )
        db.add(solicitud)
        await db.commit()
        await db.refresh(solicitud)

        try:
            if sucursal_id:
                q_enc = select(EncargadoSucursal.usuario_id).where(EncargadoSucursal.sucursal_id == sucursal_id)
                res_enc = await db.execute(q_enc)
                u_ids = res_enc.scalars().all()
                for uid in u_ids:
                    await FirebaseService.enviar_notificacion(
                        db=db,
                        usuario_id=uid,
                        titulo="Nueva solicitud de devolución/cambio",
                        mensaje=f"Se ha recibido la solicitud {solicitud.numero_solicitud} de tipo {solicitud.tipo.value}.",
                        tipo="DEVOLUCION_NUEVA",
                        datos_adicionales={"solicitud_id": str(solicitud.id)}
                    )
        except Exception as e:
            logger.warning(f"Error al enviar notificación push de devolución: {e}")

        return await cls.obtener_solicitud_detalle(db, solicitud.id)

    @classmethod
    async def obtener_solicitud_detalle(cls, db: AsyncSession, solicitud_id: int) -> SolicitudDevolucion:
        query = (
            select(SolicitudDevolucion)
            .options(
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_producto).selectinload(VarianteProducto.color),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_cambio).selectinload(VarianteProducto.producto),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_cambio).selectinload(VarianteProducto.talla),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_cambio).selectinload(VarianteProducto.color),
                selectinload(SolicitudDevolucion.cliente),
                selectinload(SolicitudDevolucion.orden),
                selectinload(SolicitudDevolucion.sucursal)
            )
            .where(SolicitudDevolucion.id == solicitud_id)
        )
        res = await db.execute(query)
        sol = res.scalar_one_or_none()
        if not sol:
            raise NotFoundException(f"Solicitud de devolución ID {solicitud_id} no encontrada")
        return sol

    @classmethod
    async def mis_solicitudes(cls, db: AsyncSession, cliente_id: int, skip: int = 0, limit: int = 50) -> List[SolicitudDevolucion]:
        query = (
            select(SolicitudDevolucion)
            .options(
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_producto).selectinload(VarianteProducto.color),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_cambio).selectinload(VarianteProducto.producto),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_cambio).selectinload(VarianteProducto.talla),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_cambio).selectinload(VarianteProducto.color)
            )
            .where(SolicitudDevolucion.cliente_id == cliente_id)
            .order_by(desc(SolicitudDevolucion.fecha_solicitud))
            .offset(skip)
            .limit(limit)
        )
        res = await db.execute(query)
        return list(res.scalars().all())

    @classmethod
    async def listar_para_staff(
        cls,
        db: AsyncSession,
        sucursal_id: Optional[int] = None,
        estado: Optional[EstadoSolicitudDevolucion] = None,
        skip: int = 0,
        limit: int = 50
    ) -> List[SolicitudDevolucion]:
        query = (
            select(SolicitudDevolucion)
            .options(
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_producto).selectinload(VarianteProducto.color),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_cambio).selectinload(VarianteProducto.producto),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_cambio).selectinload(VarianteProducto.talla),
                selectinload(SolicitudDevolucion.detalles).selectinload(DetalleSolicitudDevolucion.variante_cambio).selectinload(VarianteProducto.color)
            )
            .order_by(desc(SolicitudDevolucion.fecha_solicitud))
        )
        if sucursal_id is not None:
            query = query.where(SolicitudDevolucion.sucursal_id == sucursal_id)
        if estado is not None:
            query = query.where(SolicitudDevolucion.estado == estado)

        query = query.offset(skip).limit(limit)
        res = await db.execute(query)
        return list(res.scalars().all())

    @classmethod
    async def revisar_solicitud(
        cls,
        db: AsyncSession,
        solicitud_id: int,
        staff_user: Usuario,
        datos: RevisionSolicitudRequest
    ) -> SolicitudDevolucion:
        solicitud = await cls.obtener_solicitud_detalle(db, solicitud_id)
        ahora = cls._ahora()

        if solicitud.estado not in [EstadoSolicitudDevolucion.PENDIENTE, EstadoSolicitudDevolucion.EN_REVISION]:
            raise BadRequestException(f"La solicitud ya se encuentra en estado {solicitud.estado.value}")

        accion_norm = datos.accion.strip().upper()
        if accion_norm == "RECHAZAR":
            if not datos.observaciones:
                raise BadRequestException("Las observaciones son obligatorias al rechazar una solicitud")
            solicitud.estado = EstadoSolicitudDevolucion.RECHAZADA
            solicitud.observaciones_staff = datos.observaciones
            solicitud.revisado_por_id = staff_user.id
            solicitud.fecha_resolucion = ahora
            await db.commit()

            if solicitud.cliente_id:
                q_cli = select(Cliente.usuario_id).where(Cliente.id == solicitud.cliente_id)
                res_cli = await db.execute(q_cli)
                u_cli_id = res_cli.scalar_one_or_none()
                if u_cli_id:
                    try:
                        await FirebaseService.enviar_notificacion(
                            db=db,
                            usuario_id=u_cli_id,
                            titulo="Solicitud de devolución rechazada",
                            mensaje=f"Tu solicitud {solicitud.numero_solicitud} ha sido rechazada: {datos.observaciones}",
                            tipo="DEVOLUCION_RECHAZADA",
                            datos_adicionales={"solicitud_id": str(solicitud.id)}
                        )
                    except Exception:
                        pass
            return await cls.obtener_solicitud_detalle(db, solicitud.id)

        elif accion_norm == "APROBAR":
            solicitud.revisado_por_id = staff_user.id
            solicitud.observaciones_staff = datos.observaciones
            solicitud.estado = EstadoSolicitudDevolucion.APROBADA
            return await cls.procesar(db, solicitud, staff_user)

        else:
            raise BadRequestException("Acción no reconocida. Use 'APROBAR' o 'RECHAZAR'")

    @classmethod
    async def procesar(
        cls,
        db: AsyncSession,
        solicitud: SolicitudDevolucion,
        staff_user: Usuario
    ) -> SolicitudDevolucion:
        ahora = cls._ahora()
        sucursal_id = solicitud.sucursal_id
        if not sucursal_id:
            res_suc = await db.execute(select(Sucursal.id).limit(1))
            sucursal_id = res_suc.scalar_one_or_none()

        if sucursal_id:
            if solicitud.tipo == TipoSolicitudDevolucion.CAMBIO:
                for it in solicitud.detalles:
                    if it.variante_producto_id:
                        await InventarioVentasService.reintegrar_stock_devolucion(
                            db=db,
                            variante_id=it.variante_producto_id,
                            sucursal_id=sucursal_id,
                            cantidad=it.cantidad,
                            usuario_id=staff_user.id,
                            motivo=f"Reintegro por cambio {solicitud.numero_solicitud}"
                        )
                    if it.variante_cambio_id:
                        await InventarioVentasService.descontar_stock_venta(
                            db=db,
                            variante_id=it.variante_cambio_id,
                            sucursal_id=sucursal_id,
                            cantidad=it.cantidad,
                            usuario_id=staff_user.id,
                            motivo=f"Entrega por cambio {solicitud.numero_solicitud}"
                        )

            elif solicitud.tipo == TipoSolicitudDevolucion.DEVOLUCION:
                for it in solicitud.detalles:
                    if it.variante_producto_id:
                        await InventarioVentasService.reintegrar_stock_devolucion(
                            db=db,
                            variante_id=it.variante_producto_id,
                            sucursal_id=sucursal_id,
                            cantidad=it.cantidad,
                            usuario_id=staff_user.id,
                            motivo=f"Reintegro por devolución {solicitud.numero_solicitud}"
                        )

        if solicitud.tipo == TipoSolicitudDevolucion.CAMBIO:
            solicitud.estado = EstadoSolicitudDevolucion.COMPLETADA
            solicitud.fecha_resolucion = ahora
        elif solicitud.tipo == TipoSolicitudDevolucion.DEVOLUCION:
            q_tx = (
                select(TransaccionPago)
                .where(
                    TransaccionPago.orden_id == solicitud.orden_id,
                    TransaccionPago.estado == EstadoTransaccion.CONFIRMADO
                )
            )
            res_tx = await db.execute(q_tx)
            tx = res_tx.scalar_one_or_none()

            if tx and tx.metodo_pago in [MetodoPagoDigital.STRIPE, MetodoPagoDigital.PAYPAL]:
                try:
                    reembolso_ok = True
                    if tx.metodo_pago == MetodoPagoDigital.STRIPE and tx.referencia_externa and hasattr(StripeService, "refund"):
                        try:
                            await StripeService.refund(tx.referencia_externa, float(solicitud.monto_reembolso or 0))
                        except Exception as e_stripe:
                            logger.error(f"Error procesando refund en Stripe: {e_stripe}")
                            reembolso_ok = False
                    
                    if reembolso_ok:
                        tx.estado = EstadoTransaccion.REEMBOLSADO
                        solicitud.estado = EstadoSolicitudDevolucion.COMPLETADA
                        solicitud.fecha_resolucion = ahora
                    else:
                        solicitud.estado = EstadoSolicitudDevolucion.PENDIENTE_REEMBOLSO
                except Exception as e:
                    logger.error(f"Fallo en pasarela de reembolso: {e}")
                    solicitud.estado = EstadoSolicitudDevolucion.PENDIENTE_REEMBOLSO
            else:
                solicitud.estado = EstadoSolicitudDevolucion.COMPLETADA
                solicitud.fecha_resolucion = ahora

        await db.commit()

        if solicitud.cliente_id:
            q_cli = select(Cliente.usuario_id).where(Cliente.id == solicitud.cliente_id)
            res_cli = await db.execute(q_cli)
            u_cli_id = res_cli.scalar_one_or_none()
            if u_cli_id:
                try:
                    await FirebaseService.enviar_notificacion(
                        db=db,
                        usuario_id=u_cli_id,
                        titulo="Solicitud de devolución procesada",
                        mensaje=f"Tu solicitud {solicitud.numero_solicitud} ha sido {solicitud.estado.value.lower()}.",
                        tipo="DEVOLUCION_RESUELTA",
                        datos_adicionales={"solicitud_id": str(solicitud.id), "estado": solicitud.estado.value}
                    )
                except Exception:
                    pass

        return await cls.obtener_solicitud_detalle(db, solicitud.id)

    @classmethod
    async def procesar_reembolso_pendiente(
        cls,
        db: AsyncSession,
        solicitud_id: int,
        staff_user: Usuario
    ) -> SolicitudDevolucion:
        solicitud = await cls.obtener_solicitud_detalle(db, solicitud_id)
        if solicitud.estado != EstadoSolicitudDevolucion.PENDIENTE_REEMBOLSO:
            raise BadRequestException(f"La solicitud no se encuentra en estado PENDIENTE_REEMBOLSO (actual: {solicitud.estado.value})")

        solicitud.estado = EstadoSolicitudDevolucion.COMPLETADA
        solicitud.fecha_resolucion = cls._ahora()
        solicitud.revisado_por_id = staff_user.id
        await db.commit()
        return await cls.obtener_solicitud_detalle(db, solicitud.id)
