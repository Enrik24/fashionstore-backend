"""
Seed de Ventas Presenciales Históricas (dataset para dashboard y ML).

Genera ~6 meses de ventas presenciales (POS) realistas para poblar el
dashboard analítico (CU12) y entrenar los modelos de machine learning.

Qué crea:
- Órdenes (tipo PRESENCIAL, estado PAGADO) con numero_orden con prefijo
  `ORD-SEED…` para poder identificarlas y borrarlas con --force.
- Detalles de orden (items con precio, costo y subtotal), replicando lo que
  escribe VentaPresencialService.crear_venta.
- VentasPresenciales vinculadas a la orden, la sucursal, el cajero y el
  cliente (opcional: ~35% de ventas "de mostrador" sin cliente).
- Descuento de inventario (cantidad / cantidad_vendida) + movimientos de
  tipo VENTA con la fecha retroactiva de cada orden (trazabilidad por sucursal).
- Reposición previa de inventario (movimiento RECEPCION) para que 6 meses de
  ventas no agoten el stock: cada variante×sucursal se eleva a STOCK_OBJETIVO.
- Fase 2 — Solicitudes de devolución / cambio / reposición (CU28) sobre las
  órdenes del dataset (prefijo `DEV-SEED…`): tipos DEVOLUCION/CAMBIO/
  REPOSICION, estados completos (COMPLETADA, RECHAZADA, PENDIENTE,
  EN_REVISION, APROBADA), diferencia de precio CU13 y reintegros de
  inventario para las completadas (movimientos DEVOLUCION/VENTA).

Patrones de negocio simulados (deterministas, con semilla fija):
- 5 sucursales con pesos distintos (Central es la más concurrida).
- Fines de semana con más tráfico que los días hábiles.
- Tendencia de crecimiento suave mes a mes (útil para los KPIs de tendencia
  y para que el modelo ML encuentre señal, no ruido plano).
- Horarios dentro del horario de atención de la sucursal.

REQUISITO: haber corrido antes `python seed.py` (sucursales, usuarios,
cajeros, clientes y catálogo con inventario). Las ventas usan únicamente
los clientes con rol Cliente y los cajeros ya registrados.

IDEMPOTENTE: si el tenant ya tiene órdenes OR-SEED, el script se omite.
Con --force borra ese dataset previo (órdenes + detalles + ventas) y
regenera. El inventario se re-infla de nuevo antes de crear ventas.

Uso:
    python seed_ventas.py            # crea el dataset si no existe
    python seed_ventas.py --force    # borra y regenera
    python seed_ventas.py --dias 91  # ventana distinta (por defecto 183 días)
"""
import argparse
import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import engine, Base, AsyncSessionLocal
import app.apps.gestion_ventas.models
import app.apps.servicios_inteligentes.models
import app.apps.operaciones_offline.models
import app.apps.pagos_diferidos.models
from app.apps.gestion_usuarios.models import Cliente, Cajero, Usuario
from app.apps.gestion_catalogo.models import (
    Sucursal, VarianteProducto, Inventario, MovimientoInventario,
    EstadoStock, TipoMovimiento, EstadoProducto,
)
from app.apps.gestion_ventas.models import (
    Orden, DetalleOrden, VentaPresencial,
    TipoOrden, EstadoOrden, MetodoPagoPresencial,
    SolicitudDevolucion, DetalleSolicitudDevolucion,
    TipoSolicitudDevolucion, MotivoDevolucion, EstadoSolicitudDevolucion,
    TipoDiferencia, MetodoPagoDigital, TransaccionPago, EstadoTransaccion,
)
from app.apps.operaciones_offline.models import OrigenOperacion
from app.tenant import clear_tenant_context, get_tenant_demo, set_tenant_context


# =====================================================================
# CONFIGURACIÓN DEL DATASET
# =====================================================================

SEMILLA = 90210
random.seed(SEMILLA)

DIAS_HISTORICO = 183          # ~6 meses hacia atrás desde hoy
STOCK_OBJETIVO = 150          # stock al que se infla cada variante×sucursal
MAX_ITEMS_POR_VENTA = 4       # topes de carrito presencial
MAX_UNIDADES_POR_ITEM = 3

# Motivos identificables en movimientos_inventario (idempotencia/auditoría)
MOTIVO_VENTA = "Seed ventas históricas - POS"
MOTIVO_REPO = "Reposición dataset histórico - seed_ventas"
PREFIJO_SEED = "ORD-SEED"

# ---- Fase 2: devoluciones / cambios / reposiciones (CU28) ----
PREFIJO_SEED_DEV = "DEV-SEED"           # identificación/idempotencia por numero_solicitud
PROBABILIDAD_DEVOLUCION = 0.07          # ~7% de las órdenes con cliente generan solicitud

# ---- Fase 3: ventas online (DIGITAL, carrito -> pasarela) ----
PREFIJO_SEED_ON = "ORD-ON-SEED"
PROBABILIDAD_VENTA_ONLINE_DIA = (1.0, 3.0)   # base de órdenes digitales por día
ESTADOS_ONLINE_PESOS = [
    (EstadoOrden.ENTREGADO, 60),        # cerrado y entregado (mayoría histórica)
    (EstadoOrden.ENVIADO, 15),
    (EstadoOrden.PAGADO, 10),
    (EstadoOrden.EN_PROCESO, 8),
    (EstadoOrden.PENDIENTE_PAGO, 4),    # carrito abandonado a medio pagar
    (EstadoOrden.CANCELADO, 3),
]
METODOS_DIGITALES_PESOS = [
    (MetodoPagoDigital.STRIPE, 40),
    (MetodoPagoDigital.TARJETA_CREDITO, 20),
    (MetodoPagoDigital.TARJETA_DEBITO, 15),
    (MetodoPagoDigital.QR, 12),
    (MetodoPagoDigital.PAYPAL, 8),
    (MetodoPagoDigital.TRANSFERENCIA, 5),
]
# Estados que ya cobraron: con transacción CONFIRMADO + stock descontado.
ESTADOS_ONLINE_PAGADOS = [
    EstadoOrden.PAGADO, EstadoOrden.EN_PROCESO,
    EstadoOrden.ENVIADO, EstadoOrden.ENTREGADO,
]
TIPOS_DEV_SOLICITUD = [
    (TipoSolicitudDevolucion.DEVOLUCION, 50),
    (TipoSolicitudDevolucion.CAMBIO, 35),
    (TipoSolicitudDevolucion.REPOSICION, 15),
]
MOTIVOS_DEV_SOLICITUD = [
    (MotivoDevolucion.TALLA_INCORRECTA, 55),
    (MotivoDevolucion.DEFECTO_FABRICA, 20),
    (MotivoDevolucion.COLOR_INCORRECTO, 15),
    (MotivoDevolucion.OTRO, 10),
]
MOTIVOS_RECHAZO = [
    "Fuera del plazo de devolución establecido",
    "Prenda usada y sin etiqueta original",
    "No corresponde a un defecto de fábrica",
]

# Peso comercial de cada sucursal (Central > resto)
PESO_SUCURSAL = {
    'Sucursal Central': 30,
    'Sucursal Equipetrol': 22,
    'Sucursal Ventura Mall': 18,
    'Sucursal Calacoto': 15,
    'Sucursal Cochabamba Plaza': 15,
}

# Mezcla de métodos de pago del POS (valores de MetodoPagoPresencial)
METODOS_PAGO = [
    (MetodoPagoPresencial.EFECTIVO, 38),
    (MetodoPagoPresencial.QR, 30),
    (MetodoPagoPresencial.TARJETA_DEBITO, 16),
    (MetodoPagoPresencial.TARJETA_CREDITO, 16),
]


def roster_metodos() -> List[MetodoPagoPresencial]:
    """Expande las ponderaciones a una lista para random.choice."""
    roster: List[MetodoPagoPresencial] = []
    for metodo, peso in METODOS_PAGO:
        roster.extend([metodo] * peso)
    return roster


def elegir_sucursales_ponderado(sucursales: List[Sucursal]) -> List[Sucursal]:
    """Repite cada sucursal según su peso comercial para random.choice."""
    roster: List[Sucursal] = []
    for s in sucursales:
        peso = PESO_SUCURSAL.get(s.nombre, 15)
        roster.extend([s] * peso)
    return roster


def generar_numero_orden_seed(fecha: datetime) -> str:
    """ numero_orden identificable como periodo del seed (prefijo OR-SEED)."""
    sufijo = uuid.uuid4().hex[:6].upper()
    return f"{PREFIJO_SEED}-{fecha.strftime('%Y%m%d')}-{sufijo}"


def generar_numero_solicitud_dev(fecha: datetime) -> str:
    """numero_solicitud marcado como seed (prefijo DEV-SEED), como DEV-... real."""
    sufijo = uuid.uuid4().hex[:6].upper()
    return f"{PREFIJO_SEED_DEV}-{fecha.strftime('%Y%m%d')}-{sufijo}"


# =====================================================================
# TRANSFORMADORES AUXILIARES
# =====================================================================

async def inflar_stock(db: AsyncSession, tenant_id: int) -> Tuple[int, int]:
    """Sube el stock de cada variante×sucursal del tenant a STOCK_OBJETIVO.

    Los 6 meses de ventas pueden consumir cientos de unidades por variante:
    sin esta reposición los items se agotarían a mitad del dataset.
    Devuelve (inventarios tocados, unidades agregadas).
    """
    inventarios = (await db.execute(
        select(Inventario).where(Inventario.tenant_id == tenant_id)
    )).scalars().all()

    tocados = 0
    unidades = 0
    for inv in inventarios:
        falta = STOCK_OBJETIVO - inv.cantidad
        if falta <= 0:
            continue
        inv.cantidad += falta
        if inv.estado == EstadoStock.AGOTADO and inv.cantidad > 0:
            inv.estado = EstadoStock.DISPONIBLE
        db.add(MovimientoInventario(
            inventario_id=inv.id,
            tipo=TipoMovimiento.RECEPCION,
            cantidad=falta,
            motivo=MOTIVO_REPO,
            tenant_id=inv.tenant_id,
            es_sintetico=True,
        ))
        tocados += 1
        unidades += falta
    await db.flush()
    return tocados, unidades


async def construir_mapas(
    db: AsyncSession, tenant_id: int
) -> Tuple[List[Sucursal], Dict[int, int], List[int], Dict[int, Dict[int, Dict]]]:
    """Carga sucursales, cajeros, clientes e inventario vendible del tenant.

    Return: (sucursales, cajero_por_sucursal, clientes_id, oferta_por_sucursal)
    donde oferta_por_sucursal[sucursal_id][variante_id] = {qty, precio, costo}.
    """
    sucursales = (await db.execute(
        select(Sucursal).order_by(Sucursal.id)
    )).scalars().all()
    if not sucursales:
        raise RuntimeError("No hay sucursales: corre primero python seed.py")

    cajeros = (await db.execute(
        select(Cajero).where(Cajero.tenant_id == tenant_id)
    )).scalars().all()
    cajero_por_sucursal: Dict[int, int] = {}
    for caj in cajeros:
        cajero_por_sucursal.setdefault(caj.sucursal_id, caj.id)

    clientes = (await db.execute(
        select(Cliente).where(Cliente.tenant_id == tenant_id)
    )).scalars().all()
    clientes_ids = [c.id for c in clientes]

    inventarios = (await db.execute(
        select(Inventario)
        .options(
            joinedload(Inventario.variante_producto)
            .joinedload(VarianteProducto.producto)
        )
        .where(
            Inventario.tenant_id == tenant_id,
            Inventario.cantidad >= 1,  # nada vendido a stock cero
        )
    )).scalars().all()

    oferta_por_sucursal: Dict[int, Dict[int, Dict]] = {}
    for inv in inventarios:
        variante = inv.variante_producto
        if variante is None or variante.producto is None:
            continue
        precio = variante.precio_variante or variante.producto.precio
        costo = Decimal("0.00")
        if variante.costo_variante:
            costo = variante.costo_variante
        elif variante.producto.costo_compra:
            costo = variante.producto.costo_compra
        if not precio:
            continue
        oferta_por_sucursal.setdefault(inv.sucursal_id, {})[variante.id] = {
            'inv_id': inv.id,                  # para actualizar inventario + movimientos
            'cantidad': inv.cantidad,          # se descuenta en memoria y se persiste al final
            'precio': Decimal(precio),
            'costo': Decimal(costo),
        }
    return list(sucursales), cajero_por_sucursal, clientes_ids, oferta_por_sucursal


async def borrar_dataset_previo(db: AsyncSession) -> int:
    """Elimina órdenes/detalles/ventas/devoluciones con prefijo seed (para --force)."""
    borradas = 0
    ordenes_ids = (await db.execute(
        select(Orden.id).where(Orden.numero_orden.like(f"{PREFIJO_SEED}%"))
    )).scalars().all()
    if ordenes_ids:
        borradas += len(ordenes_ids)
        # Devoluciones: las propias del seed (por numero) y las que apuntan a
        # órdenes ORD-SEED (borran detalles primero por el FK).
        solicitudes_ids = (await db.execute(
            select(SolicitudDevolucion.id).where(
                SolicitudDevolucion.numero_solicitud.like(f"{PREFIJO_SEED_DEV}%")
                | SolicitudDevolucion.orden_id.in_(ordenes_ids)
            )
        )).scalars().all()
        if solicitudes_ids:
            await db.execute(delete(DetalleSolicitudDevolucion).where(
                DetalleSolicitudDevolucion.solicitud_id.in_(solicitudes_ids)))
            await db.execute(delete(SolicitudDevolucion).where(
                SolicitudDevolucion.id.in_(solicitudes_ids)))
        await db.execute(
            delete(VentaPresencial).where(VentaPresencial.orden_id.in_(ordenes_ids)))
        await db.execute(
            delete(DetalleOrden).where(DetalleOrden.orden_id.in_(ordenes_ids)))
        await db.execute(
            delete(Orden).where(Orden.id.in_(ordenes_ids)))
    # Fase 3: órdenes digitales marcadas como seed (transacciones van CASCADE).
    online_ids = (await db.execute(
        select(Orden.id).where(Orden.numero_orden.like(f"{PREFIJO_SEED_ON}%"))
    )).scalars().all()
    if online_ids:
        borradas += len(online_ids)
        await db.execute(
            delete(DetalleOrden).where(DetalleOrden.orden_id.in_(online_ids)))
        await db.execute(
            delete(TransaccionPago).where(TransaccionPago.orden_id.in_(online_ids)))
        await db.execute(
            delete(Orden).where(Orden.id.in_(online_ids)))
    await db.flush()
    return borradas


def generar_venta_del_dia(
    dia,
    sucursal: Sucursal,
    oferta: Dict[int, Dict],
) -> Tuple[List[Dict], Decimal, datetime]:
    """Arma los ítems, el total y la fecha-hora de una venta simulada.

    Respeta el stock en memoria: los artículos elegibles son los que aún
    tienen unidades y la cantidad pedida no puede dejar el balance negativo.
    """
    fecha_orden = datetime(
        dia.year, dia.month, dia.day,
        random.randint(9, 20), random.randint(0, 59), random.randint(0, 59),
    )
    items: List[Dict] = []
    total = Decimal("0.00")

    productos_posibles = list(oferta.items())
    random.shuffle(productos_posibles)
    max_items = min(MAX_ITEMS_POR_VENTA, len(productos_posibles))
    if max_items <= 0:
        return items, total

    for _ in range(random.randint(1, max_items)):
        elegido = None
        for variante_id, data in productos_posibles:
            if data['cantidad'] >= 1:
                elegido = (variante_id, data)
                break
        if elegido is None:
            break  # sin stock en esta sucursal
        variante_id, data = elegido
        cantidad = min(
            random.randint(1, MAX_UNIDADES_POR_ITEM),
            data['cantidad'],
        )
        if cantidad <= 0:
            break
        subtotal = (data['precio'] * cantidad).quantize(Decimal("0.01"))
        items.append({
            'variante_id': variante_id,
            'inv_id': data['inv_id'],
            'cantidad': cantidad,
            'precio': data['precio'],
            'costo': data['costo'],
            'subtotal': subtotal,
        })
        data['cantidad'] -= cantidad   # descuento en memoria
        total += subtotal
    return items, total, fecha_orden


# =====================================================================
# FASE 2: DEVOLUCIONES / CAMBIOS / REPOSICIONES (CU28)
# =====================================================================

def _elegir_ponderado(opciones, pesos):
    """random.choice ponderado: expande pesos a un roster y elige."""
    roster = []
    for opcion, peso in zip(opciones, pesos):
        roster.extend([opcion] * peso)
    return random.choice(roster)


async def borrar_devoluciones_seed(db: AsyncSession) -> int:
    """Elimina solo las solicitudes marcadas como seed (DEV-SEED*)."""
    solicitudes_ids = (await db.execute(
        select(SolicitudDevolucion.id).where(
            SolicitudDevolucion.numero_solicitud.like(f"{PREFIJO_SEED_DEV}%")
        )
    )).scalars().all()
    if not solicitudes_ids:
        return 0
    await db.execute(delete(DetalleSolicitudDevolucion).where(
        DetalleSolicitudDevolucion.solicitud_id.in_(solicitudes_ids)))
    await db.execute(delete(SolicitudDevolucion).where(
        SolicitudDevolucion.id.in_(solicitudes_ids)))
    await db.flush()
    return len(solicitudes_ids)


async def seed_devoluciones(db: AsyncSession, tenant_id: int, force: bool) -> None:
    """Fase 2 del dataset: solicitudes de devolución/cambio/reposición (CU28).

    Replica el flujo de DevolucionService.crear_solicitud / resolver:
    - Solo órdenes CON cliente y PAGADO (el CU28 exige titularidad).
    - fecha_solicitud dentro del plazo RN-012 (orden + 30 días) y <= hoy.
    - Distribución de tipos: DEVOLUCION 50% / CAMBIO 35% / REPOSICION 15%.
      Para cambio y reposición la nueva variante pertenece al mismo producto
      (si el producto no tiene otras variantes, se convierte a DEVOLUCION).
    - Estados realistas: la mayoría COMPLETADA/RECHAZADA, las recientes
      quedan PENDIENTE o EN_REVISION (así el staff del tenant tiene trabajo).
    - Las COMPLETADAS reproducen el efecto de inventario del servicio real:
      reintegro de la prenda original (movimiento DEVOLUCION) y, en cambios,
      salida de la prenda nueva (movimiento VENTA).

    Idempotente: se respeta el prefijo DEV-SEED; con --force se borra y regenera.
    """
    previas = 0
    if force:
        previas = await borrar_devoluciones_seed(db)
    elif (await db.execute(
        select(SolicitudDevolucion.id).where(
            SolicitudDevolucion.numero_solicitud.like(f"{PREFIJO_SEED_DEV}%")
        ).limit(1)
    )).scalar_one_or_none():
        print("  [=] Devoluciones seed (DEV-SEED*) ya presentes: se omiten.")
        return
    if force and previas:
        print(f"  [-] Devoluciones seed previas eliminadas: {previas}")

    print("  [+] Fase 2: generando devoluciones / cambios / reposiciones ...")

    # Staff que "resuelve" las solicitudes (el Administrador del tenant).
    admin_id = (await db.execute(
        select(Usuario.id).where(Usuario.correo == "admin@fashionstore.com")
    )).scalar_one_or_none()

    # Órdenes del dataset con cliente, únicas solicitables (estado PAGADO).
    ordenes = (await db.execute(
        select(Orden)
        .options(
            joinedload(Orden.detalles)
            .joinedload(DetalleOrden.variante_producto)
        )
        .where(
            Orden.numero_orden.like(f"{PREFIJO_SEED}%"),
            Orden.cliente_id.isnot(None),
            Orden.estado == EstadoOrden.PAGADO,
        )
    )).unique().scalars().all()
    if not ordenes:
        print("  [!] Sin órdenes con cliente: no hay devoluciones que generar.")
        return
    random.shuffle(ordenes)

    # UTC: la BD guarda timestamptz (+00:00); naive provocaría TypeError.
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    hoy = ahora.date()
    por_estado = {estado: 0 for estado in EstadoSolicitudDevolucion}
    n_solicitudes = 0
    n_detalles = 0
    # (variante_id, sucursal_id) -> unidades a reintegro / entrega
    deltas_reintegro: Dict[Tuple[int, int], int] = {}
    deltas_entrega: Dict[Tuple[int, int], int] = {}
    fechas_movimiento: Dict[Tuple[int, int], datetime] = {}

    for orden in ordenes:
        if random.random() >= PROBABILIDAD_DEVOLUCION:
            continue

        detalles = list(orden.detalles)
        if not detalles:
            continue
        det = random.choice(detalles)
        cantidad = random.randint(1, det.cantidad) or 1

        tipo = _elegir_ponderado(
            [t for t, _ in TIPOS_DEV_SOLICITUD], [p for _, p in TIPOS_DEV_SOLICITUD])

        # Cambio/reposición: la nueva variante debe ser del MISMO producto
        # (la etapa "elegir otra talla/color") y el stock se valida en
        # densidad: si no existe variante alternativa, queda como DEVOLUCION.
        variante_cambio_id = None
        precio_variante_nueva = None
        if tipo != TipoSolicitudDevolucion.DEVOLUCION:
            producto_id = det.variante_producto.producto_id
            alternativas = (await db.execute(
                select(VarianteProducto)
                .options(joinedload(VarianteProducto.producto))
                .where(
                    VarianteProducto.producto_id == producto_id,
                    VarianteProducto.id != det.variante_producto_id,
                    VarianteProducto.tenant_id == tenant_id,
                )
            )).unique().scalars().all()
            if alternativas:
                nueva = random.choice(alternativas)
                variante_cambio_id = nueva.id
                precio_variante_nueva = Decimal(
                    nueva.precio_variante or nueva.producto.precio)
            else:
                tipo = TipoSolicitudDevolucion.DEVOLUCION

        # Fecha de solicitud: 1-3 días tras la compra y siempre <= ahora.
        fecha_solicitud = orden.fecha + timedelta(days=random.randint(1, 3))
        if fecha_solicitud > ahora:
            fecha_solicitud = ahora - timedelta(seconds=random.randint(0, 43200))
        dias_desde = (hoy - fecha_solicitud.date()).days

        # Estados: las muy recientes quedan vivas (PENDIENTE/EN_REVISION) para
        # que la pantalla de operación tenga trabajo; el resto se cierra.
        if dias_desde <= 2:
            estado = random.choice([
                EstadoSolicitudDevolucion.PENDIENTE,
                EstadoSolicitudDevolucion.EN_REVISION,
            ])
        else:
            estado = _elegir_ponderado(
                [
                    EstadoSolicitudDevolucion.COMPLETADA,
                    EstadoSolicitudDevolucion.RECHAZADA,
                    EstadoSolicitudDevolucion.EN_REVISION,
                    EstadoSolicitudDevolucion.APROBADA,
                    EstadoSolicitudDevolucion.PENDIENTE,
                ],
                [62, 12, 10, 8, 8],
            )

        motivo = _elegir_ponderado(
            [m for m, _ in MOTIVOS_DEV_SOLICITUD], [p for _, p in MOTIVOS_DEV_SOLICITUD])

        precio_unit = Decimal(det.precio_unitario)
        monto_reembolso = (precio_unit * cantidad).quantize(Decimal("0.01"))

        # Diferencia de precio del cambio/reposición (CU13).
        diferencia_precio = None
        tipo_diferencia = None
        monto_pagado_diferencia = None
        saldo_favor = None
        if variante_cambio_id and precio_variante_nueva:
            subtotal_dif = ((precio_variante_nueva - precio_unit) * cantidad
                            ).quantize(Decimal("0.01"))
            diferencia_precio = abs(subtotal_dif)
            if subtotal_dif == 0:
                tipo_diferencia = TipoDiferencia.SIN_DIFERENCIA
            elif subtotal_dif > 0:
                tipo_diferencia = TipoDiferencia.COBRO
            else:
                tipo_diferencia = TipoDiferencia.SALDO_FAVOR
            if estado == EstadoSolicitudDevolucion.COMPLETADA:
                if subtotal_dif > 0:
                    monto_pagado_diferencia = diferencia_precio
                elif subtotal_dif < 0:
                    saldo_favor = diferencia_precio
        else:
            subtotal_dif = None

        fecha_resolucion = None
        motivo_rechazo = None
        if estado == EstadoSolicitudDevolucion.COMPLETADA:
            fecha_resolucion = min(
                fecha_solicitud + timedelta(days=random.randint(1, 3)),
                ahora,
            )
        elif estado == EstadoSolicitudDevolucion.RECHAZADA:
            fecha_resolucion = fecha_solicitud + timedelta(hours=random.randint(4, 48))
            motivo_rechazo = random.choice(MOTIVOS_RECHAZO)

        if variante_cambio_id:
            detalle_estado = (
                "RECHAZADO" if estado == EstadoSolicitudDevolucion.RECHAZADA
                else ("ENTREGADO" if estado == EstadoSolicitudDevolucion.COMPLETADA
                      else "PENDIENTE")
            )
        else:
            detalle_estado = (
                "RECHAZADO" if estado == EstadoSolicitudDevolucion.RECHAZADA
                else ("PROCESADO" if estado == EstadoSolicitudDevolucion.COMPLETADA
                      else "PENDIENTE")
            )

        solicitud = SolicitudDevolucion(
            numero_solicitud=generar_numero_solicitud_dev(fecha_resolucion or fecha_solicitud),
            cliente_id=orden.cliente_id,
            orden_id=orden.id,
            sucursal_id=orden.sucursal_id,
            tipo=tipo,
            motivo=motivo,
            motivo_detalle=(
                "Solicitud registrada por el dataset histórico (seed fase 2)."
            ),
            estado=estado,
            monto_reembolso=monto_reembolso,
            revisado_por_id=admin_id if estado not in (
                EstadoSolicitudDevolucion.PENDIENTE,) else None,
            fecha_solicitud=fecha_solicitud,
            fecha_resolucion=fecha_resolucion,
            tenant_id=tenant_id,
            diferencia_precio=diferencia_precio,
            tipo_diferencia=tipo_diferencia,
            monto_pagado_diferencia=monto_pagado_diferencia,
            saldo_favor=saldo_favor,
            motivo_rechazo=motivo_rechazo,
            detalles=[DetalleSolicitudDevolucion(
                detalle_orden_id=det.id,
                variante_producto_id=det.variante_producto_id,
                cantidad=cantidad,
                variante_cambio_id=variante_cambio_id,
                precio_unitario=precio_unit,
                precio_variante_nueva=precio_variante_nueva,
                subtotal_diferencia=subtotal_dif,
                estado_item=detalle_estado,
                tenant_id=tenant_id,
            )],
        )
        db.add(solicitud)
        await db.flush()

        n_solicitudes += 1
        n_detalles += 1
        por_estado[estado] += 1

        # Efecto de inventario solo de las COMPLETADAS (como el servicio real).
        if estado == EstadoSolicitudDevolucion.COMPLETADA:
            clave_orig = (det.variante_producto_id, orden.sucursal_id)
            deltas_reintegro[clave_orig] = deltas_reintegro.get(clave_orig, 0) + cantidad
            fechas_movimiento[clave_orig] = fecha_resolucion or fecha_solicitud
            if variante_cambio_id:
                clave_nueva = (variante_cambio_id, orden.sucursal_id)
                deltas_entrega[clave_nueva] = deltas_entrega.get(clave_nueva, 0) + cantidad
                fechas_movimiento.setdefault(clave_nueva, fecha_resolucion or fecha_solicitud)

        if n_solicitudes % 200 == 0:
            await db.commit()

    # Persistir el efecto de inventario de devoluciones completadas.
    reintegradas = 0
    for clave, cant in deltas_reintegro.items():
        variante_id, sucursal_id = clave
        inv = (await db.execute(
            select(Inventario).where(
                Inventario.variante_producto_id == variante_id,
                Inventario.sucursal_id == sucursal_id,
                Inventario.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if inv is None:
            continue
        inv.cantidad += cant
        inv.cantidad_vendida = max(0, (inv.cantidad_vendida or 0) - cant)
        if inv.cantidad > 0 and inv.estado == EstadoStock.AGOTADO:
            inv.estado = EstadoStock.DISPONIBLE
        db.add(MovimientoInventario(
            inventario_id=inv.id,
            tipo=TipoMovimiento.DEVOLUCION,
            cantidad=cant,
            motivo=f"Reintegro por devolución ({MOTIVO_VENTA} - seed fase 2)",
            fecha_hora=fechas_movimiento.get(clave),
            tenant_id=tenant_id,
            es_sintetico=True,
        ))
        reintegradas += 1

    entregadas = 0
    for clave, cant in deltas_entrega.items():
        if clave in deltas_reintegro:
            continue  # la misma prenda en ambos extremos: ya quedó compensada
        variante_id, sucursal_id = clave
        inv = (await db.execute(
            select(Inventario).where(
                Inventario.variante_producto_id == variante_id,
                Inventario.sucursal_id == sucursal_id,
                Inventario.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if inv is None:
            continue
        inv.cantidad = max(0, inv.cantidad - cant)
        inv.cantidad_vendida = (inv.cantidad_vendida or 0) + cant
        if inv.cantidad == 0:
            inv.estado = EstadoStock.AGOTADO
        db.add(MovimientoInventario(
            inventario_id=inv.id,
            tipo=TipoMovimiento.VENTA,
            cantidad=cant,
            motivo="Entrega por cambio (seed fase 2)",
            fecha_hora=fechas_movimiento.get(clave),
            tenant_id=tenant_id,
            es_sintetico=True,
        ))
        entregadas += 1
    await db.flush()

    print(f"\n  [OK] Devoluciones seed generadas:")
    print(f"      Solicitudes        : {n_solicitudes} (detalles: {n_detalles})")
    print(f"      Completada/Rechaz. : {por_estado[EstadoSolicitudDevolucion.COMPLETADA]}"
          f"/{por_estado[EstadoSolicitudDevolucion.RECHAZADA]}"
          f"  | Vivas: {por_estado[EstadoSolicitudDevolucion.PENDIENTE]}"
          f"+{por_estado[EstadoSolicitudDevolucion.EN_REVISION]}"
          f"+{por_estado[EstadoSolicitudDevolucion.APROBADA]}")
    print(f"      Inventario          : +reintegros {reintegradas} / -entregas {entregadas}")


# =====================================================================
# FASE 3: VENTAS ONLINE (DIGITAL, carrito -> pasarela)
# =====================================================================

async def borrar_ventas_online_seed(db: AsyncSession) -> int:
    """Elimina solo las órdenes digitales marcadas como seed."""
    online_ids = (await db.execute(
        select(Orden.id).where(Orden.numero_orden.like(f"{PREFIJO_SEED_ON}%"))
    )).scalars().all()
    if not online_ids:
        return 0
    await db.execute(
        delete(DetalleOrden).where(DetalleOrden.orden_id.in_(online_ids)))
    await db.execute(
        delete(TransaccionPago).where(TransaccionPago.orden_id.in_(online_ids)))
    await db.execute(
        delete(Orden).where(Orden.id.in_(online_ids)))
    await db.flush()
    return len(online_ids)


async def seed_ventas_online(db: AsyncSession, tenant_id: int, dias: int, force: bool) -> None:
    """Fase 3 del dataset: órdenes DIGITALES (compra web) de ~medio año.

    Reproduce el flujo real de TraducciónService/checkout:
    - `Orden` tipo DIGITAL SIEMPRE con cliente (la web exige cuenta) y una
      sucursal que despacha (para los filtros y la logística).
    - `TransaccionPago` CONFIRMADO por pasarela (STRIPE/PAYPAL/tarjeta/QR)
      para las órdenes que ya pagaron; PENDIENTE en PENDIENTE_PAGO y
      CANCELADO en las canceladas.
    - Estados realistas: mayoría ENTREGADO, más ENVIADO/PAGADO/EN_PROCESO,
      y una fracción mínima PENDIENTE_PAGO / CANCELADO.
    - Descuento de inventario + movimiento VENTA solo para órdenes pagadas
      (igual que el carrito/pago real); PENDIENTE_PAGO y CANCELADO no tocan
      stock.

    Idempotente por prefijo ORD-ON-SEED (con --force se borra y regenera).
    """
    previas = 0
    if force:
        previas = await borrar_ventas_online_seed(db)
    elif (await db.execute(
        select(Orden.id).where(Orden.numero_orden.like(f"{PREFIJO_SEED_ON}%")).limit(1)
    )).scalar_one_or_none():
        print("  [=] Ventas online seed (ORD-ON-SEED*) ya presentes: se omiten.")
        return
    if force and previas:
        print(f"  [-] Ventas online previas eliminadas: {previas}")

    print(f"  [+] Fase 3: verificando stock y generando ventas ONLINE ...")
    # Stock suficiente: re-subida a STOCK_OBJETIVO antes de vender online
    # (las fases 1-2 ya consumieron unidades).
    tocados, unidades = await inflar_stock(db, tenant_id)
    print(f"      Reposición previa: {tocados} inventarios (+{unidades} uds)")

    sucursales, _cajeros, clientes_ids, oferta = await construir_mapas(db, tenant_id)
    if not clientes_ids:
        print("  [!] Sin clientes: no se pueden generar ventas online.")
        return
    if not any(oferta.values()):
        print("  [!] Sin inventario con stock: no se pueden generar ventas online.")
        return

    roster_suc = elegir_sucursales_ponderado(sucursales)
    estados_online = [e for e, _ in ESTADOS_ONLINE_PESOS]
    pesos_estados = [p for _, p in ESTADOS_ONLINE_PESOS]
    metodos_digital = [m for m, _ in METODOS_DIGITALES_PESOS]
    pesos_metodo = [p for _, p in METODOS_DIGITALES_PESOS]

    hoy = datetime.now().date()
    fecha_inicio = hoy - timedelta(days=dias - 1)

    n_ordenes = 0
    n_confirmadas = 0
    n_detalles = 0
    n_unidades = 0
    total_monto = Decimal("0.00")
    deltas_inv: Dict[int, int] = {}   # inv_id -> unidades vendidas online

    for offset in range(dias):
        dia = fecha_inicio + timedelta(days=offset)
        fin_de_semana = dia.weekday() >= 5
        progreso = offset / max(1, dias - 1)

        factor = random.uniform(*PROBABILIDAD_VENTA_ONLINE_DIA)
        if fin_de_semana:
            factor *= 1.3
        factor *= (0.85 + 0.45 * progreso)   # el canal online crece más fuerte
        n_ordenar_hoy = max(0, int(round(factor)))

        for _ in range(n_ordenar_hoy):
            sucursal = random.choice(roster_suc)
            oferta_suc = oferta.get(sucursal.id, {})
            if not oferta_suc:
                continue

            fecha_orden = datetime(
                dia.year, dia.month, dia.day,
                random.randint(8, 23), random.randint(0, 59), random.randint(0, 59),
                tzinfo=timezone.utc,
            )

            # Ítems del carrito (1-3), respetando stock en memoria.
            items: List[Dict] = []
            total = Decimal("0.00")
            objetivo_items = random.randint(1, 3)
            candidatos = list(oferta_suc.items())
            random.shuffle(candidatos)
            for variante_id, data in candidatos:
                if len(items) >= objetivo_items:
                    break
                if data['cantidad'] < 1:
                    continue
                cantidad = min(random.randint(1, MAX_UNIDADES_POR_ITEM), data['cantidad'])
                if cantidad <= 0:
                    continue
                subtotal = (data['precio'] * cantidad).quantize(Decimal("0.01"))
                items.append({
                    'variante_id': variante_id,
                    'inv_id': data['inv_id'],
                    'cantidad': cantidad,
                    'precio': data['precio'],
                    'costo': data['costo'],
                    'subtotal': subtotal,
                })
                data['cantidad'] -= cantidad
                total += subtotal
            if not items:
                continue

            estado = _elegir_ponderado(estados_online, pesos_estados)
            if estado in ESTADOS_ONLINE_PAGADOS:
                delta_items = deltas_inv  # con stock descontado
            else:
                delta_items = None

            orden = Orden(
                numero_orden=f"{PREFIJO_SEED_ON}-{fecha_orden.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}",
                cliente_id=random.choice(clientes_ids),
                sucursal_id=sucursal.id,
                fecha=fecha_orden,
                tipo=TipoOrden.DIGITAL,
                estado=estado,
                total=total.quantize(Decimal("0.01")),
                impuestos=Decimal("0.00"),
                descuentos=Decimal("0.00"),
                tenant_id=tenant_id,
            )
            db.add(orden)
            await db.flush()

            for item in items:
                db.add(DetalleOrden(
                    orden_id=orden.id,
                    variante_producto_id=item['variante_id'],
                    cantidad=item['cantidad'],
                    precio_unitario=item['precio'].quantize(Decimal("0.01")),
                    costo_unitario=item['costo'].quantize(Decimal("0.01")) if item['costo'] else None,
                    subtotal=item['subtotal'],
                    tenant_id=tenant_id,
                ))
                if delta_items is not None:
                    delta_items[item['inv_id']] = delta_items.get(item['inv_id'], 0) + item['cantidad']

            # Transacción de pago (registro contable del checkout).
            metodo_digital = _elegir_ponderado(metodos_digital, pesos_metodo)
            if estado in ESTADOS_ONLINE_PAGADOS:
                estado_tx = EstadoTransaccion.CONFIRMADO
                n_confirmadas += 1
            elif estado == EstadoOrden.PENDIENTE_PAGO:
                estado_tx = EstadoTransaccion.PENDIENTE
            else:  # CANCELADO
                estado_tx = EstadoTransaccion.CANCELADO
            db.add(TransaccionPago(
                orden_id=orden.id,
                monto=orden.total,
                moneda="BOB",
                metodo_pago=metodo_digital,
                estado=estado_tx,
                referencia_externa=f"seed_{uuid.uuid4().hex[:16]}" if estado_tx == EstadoTransaccion.CONFIRMADO else None,
                fecha=fecha_orden,
                tenant_id=tenant_id,
                origen=OrigenOperacion.ONLINE,
                intentos=1,
            ))

            n_ordenes += 1
            n_detalles += len(items)
            n_unidades += sum(i['cantidad'] for i in items)
            total_monto += orden.total

            if n_ordenes % 200 == 0:
                await db.commit()
                print(f"     ... {n_ordenes} órdenes online (Bs. {total_monto:,.2f})")

    # Persistir decrements de stock de las órdenes pagadas (fase 1 pattern).
    actualizados = 0
    for inv_id, vendidas in deltas_inv.items():
        inv = await db.get(Inventario, inv_id)
        if inv is None:
            continue
        inv.cantidad = max(0, inv.cantidad - vendidas)
        inv.cantidad_vendida = (inv.cantidad_vendida or 0) + vendidas
        if inv.cantidad == 0:
            inv.estado = EstadoStock.AGOTADO
        actualizados += 1
    await db.flush()

    print(f"\n  [OK] Ventas online generadas:")
    print(f"      Ventana              : {fecha_inicio.isoformat()} -> {hoy.isoformat()} ({dias} días)")
    print(f"      Órdenes DIGITALES    : {n_ordenes} (con pago confirmado: {n_confirmadas})")
    print(f"      Detalles (líneas)    : {n_detalles} | Unidades: {n_unidades}")
    print(f"      Monto total          : Bs. {total_monto:,.2f}")
    print(f"      Inventarios tocados  : {actualizados}")


# =====================================================================
# SEED PRINCIPAL
# =====================================================================

async def seed_ventas(db: AsyncSession, dias: int, force: bool) -> None:
    """Punto de entrada del dataset: borra (si force), infla stock y genera."""
    tenant_id = int((await get_tenant_demo(db)).id)

    # Idempotencia / --force
    previas = await borrar_dataset_previo(db) if force else 0
    hay_previas = (await db.execute(
        select(Orden.id).where(Orden.numero_orden.like(f"{PREFIJO_SEED}%")).limit(1)
    )).scalar_one_or_none()
    if hay_previas and not force:
        print("  [=] Ya existe el dataset de ventas históricas (ORD-SEED*).")
        print("      Se omite su regeneración; se verifican fases 2 y 3.")
        await seed_devoluciones(db, tenant_id, force=force)
        await seed_ventas_online(db, tenant_id, dias=dias, force=force)
        return
    if force and previas:
        print(f"  [-] Dataset previo eliminado: {previas} órdenes ORD-SEED*")

    print(f"  [+] Yendo de vuelta: inflando stock a {STOCK_OBJETIVO} uds ...")
    tocados, unidades = await inflar_stock(db, tenant_id)
    print(f"      Inventarios ajustados: {tocados} (+{unidades} uds disponibles)")

    sucursales, cajeros_de_sucursal, clientes_ids, oferta = await construir_mapas(db, tenant_id)
    if not any(oferta.values()):
        raise RuntimeError("No hay inventario con stock: corre python seed.py antes.")
    sin_cajero = [s.nombre for s in sucursales if s.id not in cajeros_de_sucursal]
    if sin_cajero:
        print(f"  [!] Sucursales sin cajero (ventas quedarán sin cajero): {sin_cajero}")

    roster_suc = elegir_sucursales_ponderado(sucursales)
    roster_pago = roster_metodos()

    hoy = datetime.now().date()
    fecha_inicio = hoy - timedelta(days=dias - 1)

    total_ordenes = 0
    total_detalles = 0
    total_items = 0
    total_monto = Decimal("0.00")
    total_ventas_sin_cliente = 0
    deltas_inventario: Dict[int, int] = {}   # inv_id -> unidades vendidas en el dataset

    for offset in range(dias):
        dia = fecha_inicio + timedelta(days=offset)
        fin_de_semana = dia.weekday() >= 5  # sábado o domingo
        progreso = offset / max(1, dias - 1)

        base_diaria = (2.2, 5.2)
        factor = random.uniform(*base_diaria)
        if fin_de_semana:
            factor *= 1.55
        factor *= (0.88 + 0.30 * progreso)  # crecimiento suave del negocio
        n_ventas_hoy = max(1, int(round(factor)))

        for _ in range(n_ventas_hoy):
            sucursal = random.choice(roster_suc)
            oferta_suc = oferta.get(sucursal.id, {})
            if not oferta_suc:
                continue
            cajero_id = cajeros_de_sucursal.get(sucursal.id)

            items, total_venta, fecha_orden = generar_venta_del_dia(
                dia, sucursal, oferta_suc,
            )
            if not items:
                continue  # día agotado (stock cero)

            cliente_id = random.choice(clientes_ids) \
                if clientes_ids and random.random() < 0.65 else None

            orden = Orden(
                numero_orden=generar_numero_orden_seed(fecha_orden),
                cliente_id=cliente_id,
                sucursal_id=sucursal.id,
                fecha=fecha_orden,
                tipo=TipoOrden.PRESENCIAL,
                estado=EstadoOrden.PAGADO,
                total=total_venta.quantize(Decimal("0.01")),
                impuestos=Decimal("0.00"),
                descuentos=Decimal("0.00"),
                tenant_id=tenant_id,
            )
            db.add(orden)
            await db.flush()

            for item in items:
                db.add(DetalleOrden(
                    orden_id=orden.id,
                    variante_producto_id=item['variante_id'],
                    cantidad=item['cantidad'],
                    precio_unitario=item['precio'].quantize(Decimal("0.01")),
                    costo_unitario=item['costo'].quantize(Decimal("0.01")) if item['costo'] else None,
                    subtotal=item['subtotal'],
                    tenant_id=tenant_id,
                ))
                # [EXAMEN-2] Trazabilidad de inventario: movimiento VENTA con la
                # fecha retroactiva de la orden ( igual que VentaPresencialService).
                db.add(MovimientoInventario(
                    inventario_id=item['inv_id'],
                    tipo=TipoMovimiento.VENTA,
                    cantidad=item['cantidad'],
                    motivo=MOTIVO_VENTA,
                    fecha_hora=fecha_orden,
                    tenant_id=tenant_id,
                    es_sintetico=True,
                ))
                deltas_inventario[item['inv_id']] = \
                    deltas_inventario.get(item['inv_id'], 0) + item['cantidad']

            db.add(VentaPresencial(
                orden_id=orden.id,
                cajero_id=cajero_id,
                sucursal_id=sucursal.id,
                cliente_id=cliente_id,
                fecha=fecha_orden,
                metodo_pago=random.choice(roster_pago),
                tenant_id=tenant_id,
                es_offline=False,
            ))

            total_ordenes += 1
            total_detalles += len(items)
            total_items += sum(i['cantidad'] for i in items)
            total_monto += orden.total
            if cliente_id is None:
                total_ventas_sin_cliente += 1

            periodicidad = 250
            if total_ordenes % periodicidad == 0:
                await db.commit()
                print(f"     ... {total_ordenes} órdenes generadas (monto Bs. {total_monto:,.2f})")

    # [EXAMEN-2] Persistir el stock real: cada variante vendida baja su
    # cantidad y sube cantidad_vendida (UPDATE masivo por inventario tocado).
    if deltas_inventario:
        acumulado = 0
        for inv_id, vendidas in deltas_inventario.items():
            inv = await db.get(Inventario, inv_id)
            if inv is None:
                continue
            inv.cantidad = max(0, inv.cantidad - vendidas)
            inv.cantidad_vendida = (inv.cantidad_vendida or 0) + vendidas
            if inv.cantidad == 0:
                inv.estado = EstadoStock.AGOTADO
            acumulado += 1
        await db.flush()
        print(f"      Inventarios actualizados: {acumulado} (cantidad/cantidad_vendida)")

    print(f"\n  [OK] Dataset de ventas generado:")
    print(f"      Ventana             : {fecha_inicio.isoformat()} -> {hoy.isoformat()} ({dias} días)")
    print(f"      Órdenes/ventas      : {total_ordenes}")
    print(f"      Detalles (líneas)   : {total_detalles}")
    print(f"      Unidades vendidas   : {total_items}")
    print(f"      Monto total         : Bs. {total_monto:,.2f}")
    print(f"      Ventas sin cliente  : {total_ventas_sin_cliente} (de mostrador)")

    # [EXAMEN-2] Fase 2: devoluciones/cambios/reposiciones sobre las ventas.
    await seed_devoluciones(db, tenant_id, force)
    # [EXAMEN-2] Fase 3: ventas online (DIGITAL) con pasarela y estados.
    await seed_ventas_online(db, tenant_id, dias=dias, force=force)


# =====================================================================
# EJECUTOR PRINCIPAL
# =====================================================================

async def run_seed_ventas(force: bool = False, dias: int = DIAS_HISTORICO) -> None:
    print("=" * 65)
    print(" FashionStore - Seed de Ventas Presenciales Históricas")
    print("=" * 65)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        try:
            from app.apps.gestion_usuarios.services import DatosInicialesService
            from app.tenant import get_tenant_demo, set_tenant_context

            print("\n0. Verificando núcleo SaaS (tenant demo)...")
            await DatosInicialesService.crear_datos_iniciales(session)
            tenant_demo = await get_tenant_demo(session)
            if tenant_demo is None:
                raise RuntimeError("No se pudo crear el tenant demo")
            set_tenant_context(tenant_demo.id)

            print("\n1. Generando dataset de ventas presenciales...")
            await seed_ventas(session, dias, force)

            await session.commit()
            print("\n" + "=" * 65)
            print(" [OK] Seed de ventas completado sin duplicados.")
            print("=" * 65)
        except Exception as e:
            await session.rollback()
            print(f"\n[ERROR] Ocurrió un error durante el seed de ventas: {e}")
            raise e
        finally:
            clear_tenant_context()
            await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed de ventas presenciales históricas")
    parser.add_argument("--force", action="store_true",
                        help="Borra el dataset ORD-SEED previo y regenera")
    parser.add_argument("--dias", type=int, default=DIAS_HISTORICO,
                        help=f"Ventana en días (por defecto {DIAS_HISTORICO})")
    args = parser.parse_args()
    asyncio.run(run_seed_ventas(force=args.force, dias=args.dias))
