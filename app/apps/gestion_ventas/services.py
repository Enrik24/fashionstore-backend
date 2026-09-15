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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple
import uuid
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, or_, desc, func
from sqlalchemy.orm import selectinload, joinedload

from app.exceptions import NotFoundException, BadRequestException, ConflictException, ForbiddenException
from app.apps.gestion_ventas.models import (
    Cupon, TipoCupon, EstadoCupon,
    Carrito, ItemCarrito, EstadoCarrito,
    Orden, DetalleOrden, TipoOrden, EstadoOrden,
    VentaPresencial, MetodoPagoPresencial,
    Reserva, DetalleReserva, EstadoReserva, EstadoDetalleReserva,
    TransaccionPago, MetodoPagoDigital, EstadoTransaccion,
    Comprobante, TipoComprobante, PasarelaPago, EstadoPasarela
)
from app.apps.gestion_ventas.schemas import (
    CuponCreate, CuponUpdate, ItemCarritoCreate, ItemCarritoUpdate,
    ReservaCreate, CompletarReservaRequest, DetalleVentaPresencialCreate
)
from app.apps.gestion_catalogo.models import (
    VarianteProducto, Producto, Inventario, MovimientoInventario, TipoMovimiento, EstadoStock, Sucursal
)
from app.apps.gestion_usuarios.models import Cliente, Cajero, Usuario
from app.services.stripe_service import StripeService
from app.services.paypal_service import PayPalService

logger = logging.getLogger(__name__)


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
        await db.commit()
        await db.refresh(cupon)
        return cupon

    @staticmethod
    async def obtener_por_id(db: AsyncSession, cupon_id: int) -> Optional[Cupon]:
        query = select(Cupon).where(Cupon.id == cupon_id)
        res = await db.execute(query)
        return res.scalar_one_or_none()

    @staticmethod
    async def obtener_por_codigo(db: AsyncSession, codigo: str) -> Optional[Cupon]:
        query = select(Cupon).where(Cupon.codigo == codigo.strip().upper())
        res = await db.execute(query)
        return res.scalar_one_or_none()

    @staticmethod
    async def listar_cupones(db: AsyncSession, skip: int = 0, limit: int = 50) -> List[Cupon]:
        query = select(Cupon).offset(skip).limit(limit).order_by(desc(Cupon.fecha_creacion))
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
            setattr(cupon, key, val)

        await db.commit()
        await db.refresh(cupon)
        return cupon

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
    async def validar_cupon(cls, db: AsyncSession, codigo: str, subtotal: Decimal) -> Tuple[bool, str, Optional[Cupon], Decimal]:
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
            
        # Calcular descuento
        if cupon.tipo == TipoCupon.PORCENTAJE:
            descuento = (subtotal * cupon.valor) / Decimal("100.00")
        else:
            descuento = min(cupon.valor, subtotal)
            
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
                valido, _, _, desc_calc = await CuponService.validar_cupon(db, cupon.codigo, subtotal)
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
        
        # Crear los detalles de orden
        for item in carrito.items:
            detalle = DetalleOrden(
                orden_id=orden.id,
                variante_producto_id=item.variante_producto_id,
                cantidad=item.cantidad,
                precio_unitario=item.precio_unitario,
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
        return await cls.obtener_reserva(db, reserva.id)

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
        
        # Si se cancela, liberar stock
        if nuevo_estado in (EstadoReserva.CANCELADA, EstadoReserva.CADUCADA) and reserva.estado not in (EstadoReserva.CANCELADA, EstadoReserva.CADUCADA, EstadoReserva.COMPLETADA):
            for det in reserva.detalles:
                await InventarioVentasService.liberar_stock_reserva(
                    db, det.variante_producto_id, reserva.sucursal_id, det.cantidad, usuario_id=usuario_id, comprar=False
                )
                det.estado = EstadoDetalleReserva.DEVUELTO
                
        reserva.estado = nuevo_estado
        await db.commit()
        return await cls.obtener_reserva(db, reserva_id)

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
    async def crear_orden_paypal(
        db: AsyncSession, orden_id: int, return_url: Optional[str] = None, cancel_url: Optional[str] = None
    ) -> Dict[str, Any]:
        orden = await OrdenService.obtener_orden(db, orden_id)
        if orden.estado == EstadoOrden.PAGADO:
            raise BadRequestException("La orden ya se encuentra pagada")
            
        paypal_data = await PayPalService.create_order(
            orden_id=orden.id,
            numero_orden=orden.numero_orden,
            total=orden.total,
            moneda="USD",
            return_url=return_url,
            cancel_url=cancel_url
        )
        
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
