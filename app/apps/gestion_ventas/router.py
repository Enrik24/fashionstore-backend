"""
Router FastAPI para la Gestión de Ventas, Reservas y Pagos.
Contiene endpoints para:
- Cupones de descuento (CU18)
- Carrito de compras (CU10)
- Órdenes y Comprobantes (CU11, CU12)
- Ventas presenciales en caja (CU09, CU14)
- Reservas de prendas en sucursal (CU16, CU17)
- Pasarelas de Pago Stripe y PayPal (CU12)
"""
from fastapi import APIRouter, Depends, Query, Request, Header, HTTPException, status
from fastapi.responses import StreamingResponse
import io
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional, Dict, Any
from decimal import Decimal

from app.database import get_db
from app.security import get_current_user, get_current_active_user, require_role, get_client_ip
from app.exceptions import ForbiddenException, NotFoundException, BadRequestException
from app.apps.gestion_usuarios.models import Usuario, Cliente, EncargadoSucursal, Cajero
from app.apps.gestion_usuarios.services import BitacoraService
from app.apps.gestion_ventas import services as ventas_services
from app.apps.gestion_ventas.models import (
    EstadoOrden, EstadoReserva, TipoOrden, MetodoPagoPresencial, EstadoSolicitudDevolucion
)
from app.apps.gestion_ventas.schemas import (
    CuponCreate, CuponUpdate, CuponResponse, AplicarCuponRequest, ValidarCuponRequest, CuponValidacionResponse,
    CarritoResponse, ItemCarritoCreate, ItemCarritoUpdate,
    OrdenCreateFromCarrito, OrdenResponse, OrdenEstadoUpdate, ComprobanteResponse,
    VentaPresencialCreate, VentaPresencialResponse,
    ReservaCreate, ReservaResponse, CompletarReservaRequest,
    StripeCheckoutRequest, StripeCheckoutResponse,
    PayPalOrderRequest, PayPalOrderResponse, PayPalCaptureRequest,
    TransaccionPagoResponse, SolicitudDevolucionCreate, SolicitudDevolucionResponse,
    RevisionSolicitudRequest
)

router = APIRouter(prefix="/api/v1", tags=["Gestión de Ventas, Reservas y Pagos"])


# ==============================================================================
# CUPONES DE DESCUENTO
# ==============================================================================

@router.post("/cupones/", response_model=CuponResponse, name="crear_cupon", status_code=status.HTTP_201_CREATED)
async def crear_cupon(
    cupon_in: CuponCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo cupón de descuento (Solo Administradores)."""
    cupon = await ventas_services.CuponService.crear_cupon(db, cupon_in, creado_por_id=current_user.id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_CUPON",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Cupones",
        detalles=f"Cupón creado: {cupon.codigo} (Valor: {cupon.valor} {cupon.tipo})"
    )
    return cupon


@router.get("/cupones/", response_model=List[CuponResponse], name="listar_cupones")
async def listar_cupones(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los cupones de descuento (Solo Administradores)."""
    return await ventas_services.CuponService.listar_cupones(db, skip, limit)


@router.get("/cupones/disponibles", response_model=List[CuponResponse], name="listar_cupones_disponibles", tags=["Cupones (CU27)"])
async def listar_cupones_disponibles(
    current_user: Usuario = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Lista los cupones activos y vigentes disponibles para el cliente."""
    return await ventas_services.CuponService.listar_disponibles(db)


@router.post("/cupones/validar", response_model=CuponValidacionResponse, name="validar_cupon", tags=["Cupones (CU27)"])
async def validar_cupon(
    req: ValidarCuponRequest,
    subtotal: Decimal = Query(Decimal("0.00"), ge=0),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Valida si un cupón es aplicable a la compra actual considerando aplicabilidad."""
    subtotal_final = req.subtotal if req.subtotal is not None else subtotal
    valido, mensaje, cupon, descuento = await ventas_services.CuponService.validar_cupon(
        db, req.codigo, subtotal_final, items=req.items
    )
    return {
        "valido": valido,
        "mensaje": mensaje,
        "cupon": cupon,
        "descuento_calculado": descuento
    }


@router.get("/cupones/{cupon_id}", response_model=CuponResponse, name="obtener_cupon")
async def obtener_cupon(
    cupon_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el detalle de un cupón por su ID (Solo Administradores)."""
    cupon = await ventas_services.CuponService.obtener_por_id(db, cupon_id)
    if not cupon:
        raise NotFoundException(f"Cupón con ID {cupon_id} no encontrado")
    return cupon


@router.put("/cupones/{cupon_id}", response_model=CuponResponse, name="actualizar_cupon")
async def actualizar_cupon(
    cupon_id: int,
    cupon_in: CuponUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza los datos de un cupón de descuento (Solo Administradores)."""
    antes = BitacoraService.foto(await ventas_services.CuponService.obtener_por_id(db, cupon_id))
    cupon = await ventas_services.CuponService.actualizar_cupon(db, cupon_id, cupon_in)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_CUPON",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Cupones",
        detalles=f"Cupón actualizado: ID {cupon_id} ({cupon.codigo})",
        registro_id=cupon_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(cupon)
    )
    return cupon


@router.delete("/cupones/{cupon_id}", status_code=status.HTTP_200_OK, name="eliminar_cupon")
async def eliminar_cupon(
    cupon_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina o desactiva un cupón de descuento (Solo Administradores)."""
    await ventas_services.CuponService.eliminar_cupon(db, cupon_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_CUPON",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Cupones",
        detalles=f"Cupón eliminado/desactivado: ID {cupon_id}"
    )
    return {"message": f"Cupón con ID {cupon_id} procesado correctamente"}


# ==============================================================================
# CARRITO DE COMPRAS
# ==============================================================================

async def _obtener_cliente_actual(db: AsyncSession, usuario: Usuario) -> Cliente:
    q = select(Cliente).where(Cliente.usuario_id == usuario.id)
    res = await db.execute(q)
    cliente = res.scalar_one_or_none()
    if not cliente:
        raise ForbiddenException("El usuario actual no tiene perfil de cliente registrado")
    return cliente


@router.get("/carrito/", response_model=CarritoResponse, name="obtener_carrito")
async def obtener_carrito(
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el carrito activo del cliente autenticado."""
    cliente = await _obtener_cliente_actual(db, current_user)
    carrito = await ventas_services.CarritoService.obtener_o_crear_carrito(db, cliente.id)
    calc = await ventas_services.CarritoService.recalcular_carrito(db, carrito)
    return {
        "id": carrito.id,
        "cliente_id": carrito.cliente_id,
        "fecha_creacion": carrito.fecha_creacion,
        "estado": carrito.estado,
        "cupon_id": carrito.cupon_id,
        "descuento_aplicado": calc["descuento"],
        "items": carrito.items,
        "subtotal": calc["subtotal"],
        "total": calc["total"],
        "cupon": carrito.cupon
    }


@router.post("/carrito/items", response_model=CarritoResponse, name="agregar_item_carrito")
async def agregar_item_carrito(
    item_in: ItemCarritoCreate,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Agrega un producto/variante al carrito de compras."""
    cliente = await _obtener_cliente_actual(db, current_user)
    carrito = await ventas_services.CarritoService.agregar_item(db, cliente.id, item_in)
    calc = await ventas_services.CarritoService.recalcular_carrito(db, carrito)
    return {
        "id": carrito.id,
        "cliente_id": carrito.cliente_id,
        "fecha_creacion": carrito.fecha_creacion,
        "estado": carrito.estado,
        "cupon_id": carrito.cupon_id,
        "descuento_aplicado": calc["descuento"],
        "items": carrito.items,
        "subtotal": calc["subtotal"],
        "total": calc["total"],
        "cupon": carrito.cupon
    }


@router.put("/carrito/items/{item_id}", response_model=CarritoResponse, name="actualizar_item_carrito")
async def actualizar_item_carrito(
    item_id: int,
    item_in: ItemCarritoUpdate,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza la cantidad de un item en el carrito."""
    cliente = await _obtener_cliente_actual(db, current_user)
    carrito = await ventas_services.CarritoService.actualizar_item(db, cliente.id, item_id, item_in.cantidad)
    calc = await ventas_services.CarritoService.recalcular_carrito(db, carrito)
    return {
        "id": carrito.id,
        "cliente_id": carrito.cliente_id,
        "fecha_creacion": carrito.fecha_creacion,
        "estado": carrito.estado,
        "cupon_id": carrito.cupon_id,
        "descuento_aplicado": calc["descuento"],
        "items": carrito.items,
        "subtotal": calc["subtotal"],
        "total": calc["total"],
        "cupon": carrito.cupon
    }


@router.delete("/carrito/items/{item_id}", response_model=CarritoResponse, name="eliminar_item_carrito")
async def eliminar_item_carrito(
    item_id: int,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un item específico del carrito."""
    cliente = await _obtener_cliente_actual(db, current_user)
    carrito = await ventas_services.CarritoService.eliminar_item(db, cliente.id, item_id)
    calc = await ventas_services.CarritoService.recalcular_carrito(db, carrito)
    return {
        "id": carrito.id,
        "cliente_id": carrito.cliente_id,
        "fecha_creacion": carrito.fecha_creacion,
        "estado": carrito.estado,
        "cupon_id": carrito.cupon_id,
        "descuento_aplicado": calc["descuento"],
        "items": carrito.items,
        "subtotal": calc["subtotal"],
        "total": calc["total"],
        "cupon": carrito.cupon
    }


@router.delete("/carrito/", response_model=CarritoResponse, name="vaciar_carrito")
async def vaciar_carrito(
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Vacía completamente el carrito de compras del cliente."""
    cliente = await _obtener_cliente_actual(db, current_user)
    carrito = await ventas_services.CarritoService.vaciar_carrito(db, cliente.id)
    return {
        "id": carrito.id,
        "cliente_id": carrito.cliente_id,
        "fecha_creacion": carrito.fecha_creacion,
        "estado": carrito.estado,
        "cupon_id": None,
        "descuento_aplicado": Decimal("0.00"),
        "items": [],
        "subtotal": Decimal("0.00"),
        "total": Decimal("0.00"),
        "cupon": None
    }


@router.post("/carrito/aplicar-cupon", response_model=CarritoResponse, name="aplicar_cupon_carrito")
async def aplicar_cupon_carrito(
    req: AplicarCuponRequest,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Aplica un cupón de descuento al carrito de compras."""
    cliente = await _obtener_cliente_actual(db, current_user)
    carrito, _ = await ventas_services.CarritoService.aplicar_cupon(db, cliente.id, req.codigo)
    calc = await ventas_services.CarritoService.recalcular_carrito(db, carrito)
    return {
        "id": carrito.id,
        "cliente_id": carrito.cliente_id,
        "fecha_creacion": carrito.fecha_creacion,
        "estado": carrito.estado,
        "cupon_id": carrito.cupon_id,
        "descuento_aplicado": calc["descuento"],
        "items": carrito.items,
        "subtotal": calc["subtotal"],
        "total": calc["total"],
        "cupon": carrito.cupon
    }


@router.delete("/carrito/remover-cupon", response_model=CarritoResponse, name="remover_cupon_carrito")
async def remover_cupon_carrito(
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Remueve el cupón de descuento actualmente aplicado."""
    cliente = await _obtener_cliente_actual(db, current_user)
    carrito = await ventas_services.CarritoService.remover_cupon(db, cliente.id)
    calc = await ventas_services.CarritoService.recalcular_carrito(db, carrito)
    return {
        "id": carrito.id,
        "cliente_id": carrito.cliente_id,
        "fecha_creacion": carrito.fecha_creacion,
        "estado": carrito.estado,
        "cupon_id": None,
        "descuento_aplicado": Decimal("0.00"),
        "items": carrito.items,
        "subtotal": calc["subtotal"],
        "total": calc["total"],
        "cupon": None
    }


# ==============================================================================
# ÓRDENES DE VENTA
# ==============================================================================

@router.post("/ordenes/", response_model=OrdenResponse, name="crear_orden_desde_carrito", status_code=status.HTTP_201_CREATED)
async def crear_orden_desde_carrito(
    req: OrdenCreateFromCarrito,
    request: Request,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva orden a partir de los items en el carrito activo."""
    cliente = await _obtener_cliente_actual(db, current_user)
    dir_envio = req.direccion_envio or cliente.direccion_envio
    orden = await ventas_services.OrdenService.crear_orden_desde_carrito(
        db=db,
        cliente_id=cliente.id,
        sucursal_id=req.sucursal_id,
        direccion_envio=dir_envio,
        tipo=req.tipo,
        usuario_id=current_user.id
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_ORDEN",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Ventas",
        detalles=f"Orden creada ID {orden.id}, Total: {orden.total}, Tipo: {orden.tipo}"
    )
    return orden


@router.get("/ordenes/", response_model=List[OrdenResponse], name="listar_ordenes")
async def listar_ordenes(
    cliente_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Lista las órdenes (Clientes ven las suyas, Admin/Encargado/Cajero pueden listar todas)."""
    roles = [r.nombre for r in current_user.roles]
    if "Cliente" in roles and not any(r in ["Administrador", "Encargado", "Cajero"] for r in roles):
        cliente = await _obtener_cliente_actual(db, current_user)
        return await ventas_services.OrdenService.obtener_ordenes_cliente(db, cliente.id, skip, limit)
    return await ventas_services.OrdenService.listar_todas_ordenes(db, cliente_id, skip, limit)


@router.get("/ordenes/{orden_id}", response_model=OrdenResponse, name="obtener_orden")
async def obtener_orden(
    orden_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene los detalles de una orden por ID."""
    orden = await ventas_services.OrdenService.obtener_orden(db, orden_id)
    # Validar permisos si es Cliente
    roles = [r.nombre for r in current_user.roles]
    if "Cliente" in roles and not any(r in ["Administrador", "Encargado", "Cajero"] for r in roles):
        if not current_user.cliente or orden.cliente_id != current_user.cliente.id:
            raise ForbiddenException("No tienes permiso para ver esta orden")
    return orden


@router.post("/ordenes/{orden_id}/restaurar-carrito", response_model=CarritoResponse, name="restaurar_carrito_orden")
async def restaurar_carrito_orden(
    orden_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Devuelve los ítems de una orden pendiente al carrito y la cancela (pago cancelado)."""
    cliente = await _obtener_cliente_actual(db, current_user)
    carrito = await ventas_services.OrdenService.restaurar_carrito_desde_orden(db, orden_id, cliente.id)
    calc = await ventas_services.CarritoService.recalcular_carrito(db, carrito)
    await BitacoraService.registrar_evento(
        db=db,
        accion="RESTAURAR_CARRITO_ORDEN",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Ventas",
        detalles=f"Orden ID {orden_id} cancelada y sus ítems devueltos al carrito"
    )
    return {
        "id": carrito.id,
        "cliente_id": carrito.cliente_id,
        "fecha_creacion": carrito.fecha_creacion,
        "estado": carrito.estado,
        "cupon_id": carrito.cupon_id,
        "descuento_aplicado": calc["descuento"],
        "items": carrito.items,
        "subtotal": calc["subtotal"],
        "total": calc["total"],
        "cupon": carrito.cupon
    }


@router.patch("/ordenes/{orden_id}/estado", response_model=OrdenResponse, name="actualizar_estado_orden")
async def actualizar_estado_orden(
    orden_id: int,
    estado_in: OrdenEstadoUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado", "Cajero")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza el estado de una orden (Admin/Encargado/Cajero)."""
    antes = BitacoraService.foto(await ventas_services.OrdenService.obtener_orden(db, orden_id))
    orden = await ventas_services.OrdenService.actualizar_estado(
        db, orden_id, estado_in.estado, usuario_id=current_user.id
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_ESTADO_ORDEN",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Ventas",
        detalles=f"Orden ID {orden_id} cambió estado a {estado_in.estado}",
        registro_id=orden_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(orden)
    )
    return orden


@router.get("/ordenes/{orden_id}/comprobante", response_model=ComprobanteResponse, name="obtener_comprobante_orden")
async def obtener_comprobante_orden(
    orden_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene o genera el comprobante asociado a una orden."""
    orden = await ventas_services.OrdenService.obtener_orden(db, orden_id)
    roles = [r.nombre for r in current_user.roles]
    if "Cliente" in roles and not any(r in ["Administrador", "Encargado", "Cajero"] for r in roles):
        if not current_user.cliente or orden.cliente_id != current_user.cliente.id:
            raise ForbiddenException("No tienes permiso para ver este comprobante")
            
    return await ventas_services.OrdenService.generar_comprobante(db, orden_id)


@router.get("/ordenes/{orden_id}/comprobante/pdf", name="descargar_comprobante_pdf")
async def descargar_comprobante_pdf(
    orden_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Genera y descarga/visualiza el comprobante en PDF."""
    pdf_bytes, numero_comp = await ventas_services.OrdenService.generar_pdf_orden(db, orden_id)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=comprobante_{numero_comp}.pdf"
        }
    )


# ==============================================================================
# VENTAS PRESENCIALES
# ==============================================================================

@router.post("/ventas-presenciales/", response_model=VentaPresencialResponse, name="crear_venta_presencial", status_code=status.HTTP_201_CREATED)
async def crear_venta_presencial(
    venta_in: VentaPresencialCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Cajero", "Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Registra una venta física en punto de venta / caja (Cajero/Admin)."""
    venta = await ventas_services.VentaPresencialService.crear_venta(
        db=db,
        cajero_usuario_id=current_user.id,
        sucursal_id=venta_in.sucursal_id,
        metodo_pago=venta_in.metodo_pago,
        detalles=venta_in.detalles,
        cliente_id=venta_in.cliente_id,
        nit_ci_cliente=venta_in.nit_ci_cliente,
        cupon_codigo=venta_in.cupon_codigo
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="VENTA_PRESENCIAL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Ventas",
        detalles=f"Venta presencial registrada ID {venta.id}, Orden ID: {venta.orden_id}, Método: {venta.metodo_pago}"
    )
    return venta


@router.get("/ventas-presenciales/{venta_id}/comprobante/pdf", name="descargar_comprobante_venta_pdf")
async def descargar_comprobante_venta_pdf(
    venta_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Genera y descarga el PDF del comprobante de una venta presencial."""
    q_v = select(ventas_services.VentaPresencial).where(ventas_services.VentaPresencial.id == venta_id)
    res_v = await db.execute(q_v)
    venta = res_v.scalar_one_or_none()
    if not venta or not venta.orden_id:
        raise NotFoundException("Venta presencial u orden no encontrada")
    
    pdf_bytes, numero_comp = await ventas_services.OrdenService.generar_pdf_orden(db, venta.orden_id)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=comprobante_venta_{numero_comp}.pdf"
        }
    )


@router.get("/ventas-presenciales/", response_model=List[VentaPresencialResponse], name="listar_ventas_presenciales")
async def listar_ventas_presenciales(
    sucursal_id: Optional[int] = None,
    cajero_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: Usuario = Depends(require_role("Cajero", "Encargado", "Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista las ventas presenciales filtradas por sucursal o cajero."""
    return await ventas_services.VentaPresencialService.listar_ventas(
        db, sucursal_id, cajero_id, skip, limit
    )


# ==============================================================================
# RESERVAS DE PRENDAS
# ==============================================================================

@router.post("/reservas/", response_model=ReservaResponse, name="crear_reserva", status_code=status.HTTP_201_CREATED)
async def crear_reserva(
    reserva_in: ReservaCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una reserva de prendas para prueba física en sucursal."""
    cliente = await _obtener_cliente_actual(db, current_user)
    reserva = await ventas_services.ReservaService.crear_reserva(
        db=db,
        cliente_id=cliente.id,
        reserva_in=reserva_in,
        usuario_id=current_user.id
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_RESERVA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Reservas",
        detalles=f"Reserva creada ID {reserva.id} en sucursal ID {reserva.sucursal_id}, Fecha reserva: {reserva.fecha_reserva}"
    )
    return reserva


@router.get("/reservas/", response_model=List[ReservaResponse], name="listar_reservas")
async def listar_reservas(
    sucursal_id: Optional[int] = None,
    estado: Optional[EstadoReserva] = None,
    current_user: Usuario = Depends(require_role("Encargado", "Cajero", "Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista las reservas en tienda filtradas opcionalmente por sucursal o estado."""
    return await ventas_services.ReservaService.listar_reservas(db, sucursal_id, estado)


@router.get("/reservas/mis-reservas", response_model=List[ReservaResponse], name="listar_mis_reservas")
async def listar_mis_reservas(
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Lista las reservas realizadas por el cliente autenticado."""
    cliente = await _obtener_cliente_actual(db, current_user)
    return await ventas_services.ReservaService.listar_reservas_cliente(db, cliente.id)


@router.get("/reservas/sucursal/{sucursal_id}", response_model=List[ReservaResponse], name="listar_reservas_sucursal")
async def listar_reservas_sucursal(
    sucursal_id: int,
    estado: Optional[EstadoReserva] = None,
    current_user: Usuario = Depends(require_role("Encargado", "Cajero", "Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista las reservas agendadas en una sucursal específica."""
    return await ventas_services.ReservaService.listar_reservas_sucursal(db, sucursal_id, estado)



@router.get("/reservas/{reserva_id}", response_model=ReservaResponse, name="obtener_reserva")
async def obtener_reserva(
    reserva_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene los detalles de una reserva."""
    reserva = await ventas_services.ReservaService.obtener_reserva(db, reserva_id)
    roles = [r.nombre for r in current_user.roles]
    if "Cliente" in roles and not any(r in ["Administrador", "Encargado", "Cajero"] for r in roles):
        if not current_user.cliente or reserva.cliente_id != current_user.cliente.id:
            raise ForbiddenException("No tienes permiso para ver esta reserva")
    return reserva


@router.patch("/reservas/{reserva_id}/estado", response_model=ReservaResponse, name="cambiar_estado_reserva")
async def cambiar_estado_reserva(
    reserva_id: int,
    request: Request,
    estado: EstadoReserva = Query(...),
    current_user: Usuario = Depends(require_role("Encargado", "Cajero", "Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza el estado de una reserva (PREPARADA, EN_PRUEBA, etc.)."""
    antes = BitacoraService.foto(await ventas_services.ReservaService.obtener_reserva(db, reserva_id))
    reserva = await ventas_services.ReservaService.cambiar_estado(
        db, reserva_id, estado, usuario_id=current_user.id
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_ESTADO_RESERVA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Reservas",
        detalles=f"Reserva ID {reserva_id} cambió a estado {estado}",
        registro_id=reserva_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(reserva)
    )
    return reserva


@router.post("/reservas/{reserva_id}/cancelar", response_model=ReservaResponse, name="cancelar_reserva")
async def cancelar_reserva(
    reserva_id: int,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Cancela una reserva y libera el stock reservado."""
    reserva = await ventas_services.ReservaService.obtener_reserva(db, reserva_id)
    roles = [r.nombre for r in current_user.roles]
    if "Cliente" in roles and not any(r in ["Administrador", "Encargado", "Cajero"] for r in roles):
        if not current_user.cliente or reserva.cliente_id != current_user.cliente.id:
            raise ForbiddenException("No tienes permiso para cancelar esta reserva")
            
    res_cancelada = await ventas_services.ReservaService.cambiar_estado(
        db, reserva_id, EstadoReserva.CANCELADA, usuario_id=current_user.id
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="CANCELAR_RESERVA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Reservas",
        detalles=f"Reserva ID {reserva_id} cancelada"
    )
    return res_cancelada


@router.post("/reservas/{reserva_id}/completar", name="completar_reserva")
async def completar_reserva(
    reserva_id: int,
    req: CompletarReservaRequest,
    request: Request,
    current_user: Usuario = Depends(require_role("Cajero", "Encargado", "Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Finaliza la prueba de prendas, cobrando las prendas adquiridas y liberando las devueltas."""
    resultado = await ventas_services.ReservaService.completar_reserva(
        db=db,
        reserva_id=reserva_id,
        req=req,
        cajero_usuario_id=current_user.id
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="COMPLETAR_RESERVA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Reservas",
        detalles=f"Reserva ID {reserva_id} completada en tienda"
    )
    return resultado


# ==============================================================================
# PAGOS DIGITALES (STRIPE / PAYPAL)
# ==============================================================================

@router.post("/pagos/stripe/crear-checkout", response_model=StripeCheckoutResponse, name="crear_stripe_checkout")
async def crear_stripe_checkout(
    req: StripeCheckoutRequest,
    current_user: Usuario = Depends(require_role("Cliente", "Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera una sesión de pago con Stripe Checkout pre-llenada con el email del usuario."""
    return await ventas_services.PagoService.crear_sesion_stripe(
        db=db,
        orden_id=req.orden_id,
        email_usuario=current_user.correo,
        success_url=req.success_url,
        cancel_url=req.cancel_url
    )


@router.post("/pagos/stripe/confirmar-retorno", name="confirmar_retorno_stripe")
async def confirmar_retorno_stripe(
    request: Request,
    session_id: str = Query(..., description="ID de sesión de Stripe"),
    orden_id: int = Query(..., description="ID de la orden"),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Verifica y procesa el retorno exitoso del checkout de Stripe."""
    status = await ventas_services.PagoService.procesar_pago_stripe_completado(db, session_id, orden_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="PAGO_STRIPE_CONFIRMADO",
        usuario_id=current_user.id if current_user else None,
        ip_address=get_client_ip(request),
        modulo="Pagos",
        detalles=f"Pago Stripe confirmado para orden ID {orden_id} (Sesión: {session_id}, estado Stripe: {status})"
    )
    if status != "paid":
        raise BadRequestException(
            f"Stripe aún no registra el pago (estado: {status}). "
            "Si ya pagaste, espera unos segundos y reintenta; "
            "si usaste tarjeta real en modo prueba, el cobro no se completa."
        )
    return {"mensaje": "Pago de Stripe procesado exitosamente", "payment_status": status}


@router.post("/pagos/stripe/webhook", name="stripe_webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_db)
):
    """Webhook para recibir eventos asíncronos de Stripe (checkout.session.completed)."""
    if not stripe_signature:
        raise BadRequestException("Falta cabecera stripe-signature")
        
    payload = await request.body()
    try:
        event = ventas_services.StripeService.construct_webhook_event(payload, stripe_signature)
    except Exception as e:
        raise BadRequestException(f"Error validando webhook de Stripe: {str(e)}")
        
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        orden_id_str = session.get("metadata", {}).get("orden_id")
        if orden_id_str:
            orden_id = int(orden_id_str)
            await ventas_services.PagoService.procesar_pago_stripe_completado(db, session["id"], orden_id)
            await BitacoraService.registrar_evento(
                db=db,
                accion="PAGO_STRIPE_WEBHOOK",
                usuario_id=None,
                ip_address=get_client_ip(request),
                modulo="Pagos",
                detalles=f"Webhook Stripe completado para orden ID {orden_id} (Sesión: {session.get('id')})"
            )
            
    return {"status": "success"}


@router.post("/pagos/paypal/crear-orden", response_model=PayPalOrderResponse, name="crear_orden_paypal")
async def crear_orden_paypal(
    req: PayPalOrderRequest,
    current_user: Usuario = Depends(require_role("Cliente", "Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera una orden en PayPal v2 y retorna la URL de aprobación."""
    return await ventas_services.PagoService.crear_orden_paypal(
        db=db,
        orden_id=req.orden_id,
        return_url=req.return_url,
        cancel_url=req.cancel_url
    )


@router.post("/pagos/paypal/capturar", name="capturar_pago_paypal")
async def capturar_pago_paypal(
    req: PayPalCaptureRequest,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Captura el pago de una orden previamente aprobada por el comprador en PayPal."""
    resultado = await ventas_services.PagoService.capturar_pago_paypal(
        db=db,
        paypal_order_id=req.paypal_order_id,
        orden_id=req.orden_id
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="PAGO_PAYPAL_CAPTURADO",
        usuario_id=current_user.id if current_user else None,
        ip_address=get_client_ip(request),
        modulo="Pagos",
        detalles=f"Pago PayPal capturado para orden ID {req.orden_id} (PayPal Order: {req.paypal_order_id})"
    )
    return resultado


@router.get("/pagos/transacciones/orden/{orden_id}", response_model=List[TransaccionPagoResponse], name="listar_transacciones_orden")
async def listar_transacciones_orden(
    orden_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Lista el historial de transacciones de pago para una orden."""
    return await ventas_services.PagoService.listar_transacciones(db, orden_id)


@router.get("/pagos/diagnostico/paypal", name="diagnostico_paypal")
async def diagnostico_paypal(
    current_user: Usuario = Depends(require_role("Administrador"))
):
    """
    Endpoint de diagnóstico para verificar la configuración de PayPal.
    Solo para administradores.
    """
    from app.config import settings
    from app.services.paypal_service import PayPalService
    
    diagnostico = {
        "client_id_configurado": bool(settings.PAYPAL_CLIENT_ID),
        "client_secret_configurado": bool(settings.PAYPAL_CLIENT_SECRET),
        "modo": settings.PAYPAL_MODE,
        "base_url": PayPalService._get_base_url(),
        "success_url": settings.STRIPE_SUCCESS_URL,
        "cancel_url": settings.STRIPE_CANCEL_URL,
    }
    
    # Ocultar credenciales sensibles
    if settings.PAYPAL_CLIENT_ID:
        diagnostico["client_id_preview"] = settings.PAYPAL_CLIENT_ID[:20] + "..."
    
    # Intentar obtener token
    try:
        token = await PayPalService.get_access_token()
        diagnostico["autenticacion"] = "OK"
        diagnostico["token_preview"] = token[:30] + "..." if token else None
    except Exception as e:
        diagnostico["autenticacion"] = "FALLO"
        diagnostico["error_autenticacion"] = str(e)
    
    return diagnostico


# ==============================================================================
# HISTORIAL Y RESERVAS DEL CLIENTE AUTENTICADO
# ==============================================================================

@router.get("/cliente/reservas", response_model=List[ReservaResponse], name="listar_reservas_cliente_autenticado")
async def listar_mis_reservas(
    current_user: Usuario = Depends(require_role("Cliente", "Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista las reservas pertenecientes al cliente autenticado."""
    res_c = await db.execute(select(Cliente).where(Cliente.usuario_id == current_user.id))
    cliente = res_c.scalar_one_or_none()
    if not cliente:
        raise NotFoundException("Perfil de cliente no encontrado")
    return await ventas_services.ReservaService.listar_reservas_cliente(db, cliente.id)


@router.get("/cliente/historial", response_model=List[OrdenResponse], name="listar_ordenes_cliente_autenticado")
async def listar_mi_historial_compras(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Usuario = Depends(require_role("Cliente", "Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista el historial de órdenes de compra del cliente autenticado."""
    res_c = await db.execute(select(Cliente).where(Cliente.usuario_id == current_user.id))
    cliente = res_c.scalar_one_or_none()
    if not cliente:
        raise NotFoundException("Perfil de cliente no encontrado")
    return await ventas_services.OrdenService.obtener_ordenes_cliente(db, cliente.id, skip=skip, limit=limit)


# ==============================================================================
# DEVOLUCIONES Y CAMBIOS (CU28)
# ==============================================================================

async def _obtener_sucursal_staff(db: AsyncSession, usuario: Usuario) -> Optional[int]:
    q_enc = select(EncargadoSucursal.sucursal_id).where(EncargadoSucursal.usuario_id == usuario.id)
    res_enc = await db.execute(q_enc)
    suc_id = res_enc.scalar_one_or_none()
    if suc_id:
        return suc_id
    q_caj = select(Cajero.sucursal_id).where(Cajero.usuario_id == usuario.id)
    res_caj = await db.execute(q_caj)
    return res_caj.scalar_one_or_none()


@router.post("/devoluciones/", response_model=SolicitudDevolucionResponse, status_code=status.HTTP_201_CREATED, name="crear_solicitud_devolucion", tags=["Devoluciones (CU28)"])
async def crear_solicitud_devolucion(
    datos: SolicitudDevolucionCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva solicitud de devolución o cambio de prendas (CU28)."""
    cliente = await _obtener_cliente_actual(db, current_user)
    resultado = await ventas_services.DevolucionService.crear_solicitud(db, cliente.id, datos)

    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_SOLICITUD_DEVOLUCION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Devoluciones",
        detalles=f"Solicitud {resultado.numero_solicitud} de tipo {resultado.tipo} creada para la orden ID {datos.orden_id}."
    )
    return resultado


@router.get("/devoluciones/mis-solicitudes", response_model=List[SolicitudDevolucionResponse], name="listar_mis_solicitudes_devolucion", tags=["Devoluciones (CU28)"])
async def listar_mis_solicitudes_devolucion(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Lista las solicitudes de devolución/cambio realizadas por el cliente autenticado."""
    cliente = await _obtener_cliente_actual(db, current_user)
    return await ventas_services.DevolucionService.mis_solicitudes(db, cliente.id, skip=skip, limit=limit)


@router.get("/devoluciones/", response_model=List[SolicitudDevolucionResponse], name="listar_solicitudes_devolucion_staff", tags=["Devoluciones (CU28)"])
async def listar_solicitudes_devolucion_staff(
    sucursal_id: Optional[int] = Query(None, description="Filtrar por sucursal (solo Administrador)"),
    estado: Optional[EstadoSolicitudDevolucion] = Query(None, description="Filtrar por estado"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado", "Cajero")),
    db: AsyncSession = Depends(get_db)
):
    """Lista solicitudes de devolución para el personal (Encargado/Cajero filtrados a su sucursal)."""
    roles_usuario = [r.nombre for r in current_user.roles]
    es_admin = "Administrador" in roles_usuario

    sucursal_filtro = sucursal_id if es_admin else await _obtener_sucursal_staff(db, current_user)
    return await ventas_services.DevolucionService.listar_para_staff(
        db=db,
        sucursal_id=sucursal_filtro,
        estado=estado,
        skip=skip,
        limit=limit
    )


@router.get("/devoluciones/{solicitud_id}", response_model=SolicitudDevolucionResponse, name="obtener_solicitud_devolucion", tags=["Devoluciones (CU28)"])
async def obtener_solicitud_devolucion(
    solicitud_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el detalle completo de una solicitud de devolución o cambio."""
    solicitud = await ventas_services.DevolucionService.obtener_solicitud_detalle(db, solicitud_id)
    roles_usuario = [r.nombre for r in current_user.roles]

    if "Administrador" not in roles_usuario:
        if "Cliente" in roles_usuario:
            res_c = await db.execute(select(Cliente.id).where(Cliente.usuario_id == current_user.id))
            cliente_id = res_c.scalar_one_or_none()
            if solicitud.cliente_id != cliente_id:
                raise ForbiddenException("No tienes permiso para ver esta solicitud")
        elif "Encargado" in roles_usuario or "Cajero" in roles_usuario:
            suc_id = await _obtener_sucursal_staff(db, current_user)
            if solicitud.sucursal_id and solicitud.sucursal_id != suc_id:
                raise ForbiddenException("Esta solicitud pertenece a otra sucursal")
    return solicitud


@router.patch("/devoluciones/{solicitud_id}/revisar", response_model=SolicitudDevolucionResponse, name="revisar_solicitud_devolucion", tags=["Devoluciones (CU28)"])
async def revisar_solicitud_devolucion(
    solicitud_id: int,
    datos: RevisionSolicitudRequest,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado", "Cajero")),
    db: AsyncSession = Depends(get_db)
):
    """Revisa y resuelve una solicitud de devolución (APROBAR / RECHAZAR)."""
    roles_usuario = [r.nombre for r in current_user.roles]
    if "Administrador" not in roles_usuario:
        sol = await ventas_services.DevolucionService.obtener_solicitud_detalle(db, solicitud_id)
        suc_id = await _obtener_sucursal_staff(db, current_user)
        if sol.sucursal_id and sol.sucursal_id != suc_id:
            raise ForbiddenException("No puedes resolver solicitudes de otra sucursal")

    resultado = await ventas_services.DevolucionService.revisar_solicitud(db, solicitud_id, current_user, datos)

    await BitacoraService.registrar_evento(
        db=db,
        accion="REVISAR_SOLICITUD_DEVOLUCION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Devoluciones",
        detalles=f"Solicitud {resultado.numero_solicitud} resuelta con acción '{datos.accion}' -> Estado: {resultado.estado}."
    )
    return resultado


@router.post("/devoluciones/{solicitud_id}/procesar-reembolso", response_model=SolicitudDevolucionResponse, name="procesar_reembolso_pendiente", tags=["Devoluciones (CU28)"])
async def procesar_reembolso_pendiente(
    solicitud_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Reintenta o confirma manualmente el reembolso de una solicitud en PENDIENTE_REEMBOLSO."""
    resultado = await ventas_services.DevolucionService.procesar_reembolso_pendiente(db, solicitud_id, current_user)

    await BitacoraService.registrar_evento(
        db=db,
        accion="PROCESAR_REEMBOLSO_DEVOLUCION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Devoluciones",
        detalles=f"Reembolso confirmado manualmente para solicitud {resultado.numero_solicitud}."
    )
    return resultado


def include_router(app):
    """Incluye las rutas en la aplicación principal."""
    app.include_router(router)
