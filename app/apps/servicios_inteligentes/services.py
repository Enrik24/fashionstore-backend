"""
Servicios de negocio para Servicios Inteligentes, Reportes y KPIs.
Implementa:
- ReporteService: Generación y exportación de reportes (Ventas, Inventario, Reservas, Clientes, Financiero)
- KPIService: Cálculo y gestión de métricas e indicadores de rendimiento
- RecomendacionService: Recomendaciones personalizadas con Groq LLM (CU13)
- AsistenteService: Asistente virtual de moda para clientes (CU23)
- VestidorVirtualService: Probador virtual con procesamiento de imágenes (CU21)
- ReporteVozService: Generación de reportes a través de comandos por voz (CU22)
- TendenciasService: Análisis predictivo de tendencias de moda (CU20)
"""
import io
import csv
import json
import base64
import unicodedata
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundException, BadRequestException
from app.services.groq_service import groq_service
from app.services.cloudinary_service import CloudinaryService

from app.apps.servicios_inteligentes.models import (
    Reporte, IndicadorKPI, TipoReporte, FormatoReporte
)
from app.apps.servicios_inteligentes.schemas import (
    ReporteCreate, IndicadorKPICreate, IndicadorKPIBase,
    DashboardKPISummary, IndicadorKPIResponse, RecomendacionItem,
    RecomendacionResponse, AsistenteChatResponse, VestidorVirtualResponse,
    ReporteVozResponse, TendenciasResponse, TendenciaItem
)

from app.apps.gestion_ventas.models import (
    Orden, DetalleOrden, Reserva, DetalleReserva,
    VentaPresencial, TransaccionPago, EstadoOrden, EstadoReserva, TipoOrden
)
from app.apps.gestion_catalogo.models import (
    Producto, VarianteProducto, Inventario, Categoria, Temporada, Coleccion, EstadoProducto
)
from app.apps.gestion_usuarios.models import Usuario, Cliente, Administrador


def _imagen_principal(producto: Any) -> Optional[str]:
    """Devuelve la primera imagen del producto (campo `imagenes`) o None."""
    if producto is None or not producto.imagenes:
        return None
    imgs = producto.imagenes
    if isinstance(imgs, list):
        return imgs[0] if imgs else None
    if isinstance(imgs, str):
        return imgs
    return None


def _normalizar_texto(texto: Optional[str]) -> str:
    """Normaliza texto: minúsculas y sin tildes (para búsqueda de categorías)."""
    texto = (texto or "").lower()
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


_KEYWORDS_CATEGORIA = {
    "gorra": ["gorra", "sombrero", "casquete", "cap"],
    "camisa": ["camisa", "blusa", "shirt"],
    "camiseta": ["camiseta", "polera", "remera", "t-shirt", "tshirt"],
    "chaqueta": ["chaqueta", "casaca", "chamarra", "jacket"],
    "sudadera": ["sudadera", "hoodie", "buzo", "buzos"],
    "pantalon": ["pantalon", "jean", "jeans", "pants"],
    "vestido": ["vestido", "dress"],
    "falda": ["falda", "skirt"],
    "short": ["short", "bermuda", "bermudas"],
    "zapato": ["zapato", "zapatilla", "tenis", "sneaker", "calzado"],
    "abrigo": ["abrigo", "parka"],
    "polo": ["polo", "polos"],
    "bufanda": ["bufanda", "chalina"],
    "accesorio": ["accesorio", "cinturon", "cartera", "mochila", "lentes", "reloj"],
}


def _detectar_filtro_categoria(mensaje: Optional[str]) -> Optional[str]:
    """Detecta si el mensaje pide una categoría concreta (ej. 'gorras', 'camisas')."""
    if not mensaje:
        return None
    texto = _normalizar_texto(mensaje)
    for palabra_clave, sinonimos in _KEYWORDS_CATEGORIA.items():
        for sin in sinonimos:
            if _normalizar_texto(sin) in texto:
                return palabra_clave
    return None


# ==============================================================================
# REPORTE SERVICE
# ==============================================================================
class ReporteService:
    """Generación y persistencia de reportes analíticos."""

    @staticmethod
    async def generar_reporte_ventas(
        db: AsyncSession,
        fecha_inicio: Optional[datetime] = None,
        fecha_fin: Optional[datetime] = None,
        sucursal_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Genera datos agregados del reporte de ventas."""
        condiciones_orden = [Orden.tipo == TipoOrden.DIGITAL]  # Órdenes en línea (excluye POS y reservas)
        condiciones_presencial = []

        if fecha_inicio:
            condiciones_orden.append(Orden.fecha >= fecha_inicio)
            condiciones_presencial.append(VentaPresencial.fecha >= fecha_inicio)
        if fecha_fin:
            condiciones_orden.append(Orden.fecha <= fecha_fin)
            condiciones_presencial.append(VentaPresencial.fecha <= fecha_fin)

        # 1. Órdenes Online
        q_ordenes = select(Orden).options(
            selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto)
        )
        if condiciones_orden:
            q_ordenes = q_ordenes.where(and_(*condiciones_orden))
        res_ordenes = await db.execute(q_ordenes)
        ordenes = res_ordenes.scalars().all()

        total_online = sum(float(o.total or 0) for o in ordenes if o.estado != EstadoOrden.CANCELADO)
        pedidos_online_count = len([o for o in ordenes if o.estado != EstadoOrden.CANCELADO])

        # 2. Ventas Presenciales
        q_presencial = select(VentaPresencial).options(
            selectinload(VentaPresencial.orden).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto)
        )
        if sucursal_id:
            condiciones_presencial.append(VentaPresencial.sucursal_id == sucursal_id)
        if condiciones_presencial:
            q_presencial = q_presencial.where(and_(*condiciones_presencial))
        res_presencial = await db.execute(q_presencial)
        ventas_presenciales = res_presencial.scalars().all()

        total_presencial = sum(float(v.orden.total or 0) for v in ventas_presenciales if v.orden is not None)
        ventas_presenciales_count = len([v for v in ventas_presenciales if v.orden is not None])

        # 3. Agregación de productos más vendidos
        conteo_productos = {}
        for o in ordenes:
            if o.estado != EstadoOrden.CANCELADO:
                for d in o.detalles:
                    pid = d.variante_producto.producto_id if d.variante_producto else None
                    if pid:
                        conteo_productos[pid] = conteo_productos.get(pid, 0) + d.cantidad

        for v in ventas_presenciales:
            if v.orden is not None:
                for d in v.orden.detalles:
                    pid = d.variante_producto.producto_id if d.variante_producto else None
                    if pid:
                        conteo_productos[pid] = conteo_productos.get(pid, 0) + d.cantidad

        top_productos_info = []
        if conteo_productos:
            sorted_pids = sorted(conteo_productos.items(), key=lambda x: x[1], reverse=True)[:5]
            pids = [item[0] for item in sorted_pids]
            res_p = await db.execute(select(Producto).where(Producto.id.in_(pids)))
            prods_map = {p.id: p.nombre for p in res_p.scalars().all()}
            for pid, qty in sorted_pids:
                top_productos_info.append({
                    "producto_id": pid,
                    "nombre": prods_map.get(pid, f"Producto #{pid}"),
                    "unidades_vendidas": qty
                })

        return {
            "periodo": {
                "inicio": fecha_inicio.isoformat() if fecha_inicio else "Historico",
                "fin": fecha_fin.isoformat() if fecha_fin else "Actual"
            },
            "resumen": {
                "total_recaudado": round(total_online + total_presencial, 2),
                "total_online": round(total_online, 2),
                "total_presencial": round(total_presencial, 2),
                "cantidad_pedidos_online": pedidos_online_count,
                "cantidad_ventas_presenciales": ventas_presenciales_count,
                "ticket_promedio": round((total_online + total_presencial) / max(1, pedidos_online_count + ventas_presenciales_count), 2)
            },
            "top_productos": top_productos_info
        }

    @staticmethod
    async def generar_reporte_inventario(
        db: AsyncSession,
        categoria_id: Optional[int] = None,
        solo_bajo_stock: bool = False
    ) -> Dict[str, Any]:
        """Genera el reporte del estado del inventario y rotación."""
        q = select(Inventario).options(
            selectinload(Inventario.variante_producto).selectinload(VarianteProducto.producto),
            selectinload(Inventario.variante_producto).selectinload(VarianteProducto.talla),
            selectinload(Inventario.variante_producto).selectinload(VarianteProducto.color),
            selectinload(Inventario.sucursal)
        )
        if solo_bajo_stock:
            q = q.where((Inventario.cantidad - Inventario.cantidad_reservada) <= Inventario.stock_minimo)

        res = await db.execute(q)
        items = res.scalars().all()

        detalles_inventario = []
        total_unidades = 0
        items_bajo_stock = 0

        for inv in items:
            var = inv.variante_producto
            prod = var.producto if var else None
            if categoria_id and prod and prod.categoria_id != categoria_id:
                continue

            es_bajo = (inv.cantidad - inv.cantidad_reservada) <= inv.stock_minimo
            if es_bajo:
                items_bajo_stock += 1

            total_unidades += inv.cantidad_disponible
            detalles_inventario.append({
                "inventario_id": inv.id,
                "producto_id": prod.id if prod else None,
                "producto_nombre": prod.nombre if prod else "N/A",
                "sku": var.sku_variante if var else "N/A",
                "talla": var.talla.valor if (var and var.talla) else "N/A",
                "color": var.color.nombre if (var and var.color) else "N/A",
                "sucursal": inv.sucursal.nombre if inv.sucursal else "General",
                "cantidad_disponible": inv.cantidad_disponible,
                "cantidad_reservada": inv.cantidad_reservada,
                "cantidad_minima": inv.stock_minimo,
                "alerta_bajo_stock": es_bajo
            })

        return {
            "total_items_registrados": len(detalles_inventario),
            "total_unidades_disponibles": total_unidades,
            "items_con_bajo_stock": items_bajo_stock,
            "inventario": detalles_inventario[:50]
        }

    @staticmethod
    async def generar_reporte_reservas(
        db: AsyncSession,
        fecha_inicio: Optional[datetime] = None,
        fecha_fin: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Genera métricas de efectividad y conversión de reservas en tienda."""
        q = select(Reserva).options(
            selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.producto)
        )
        conds = []
        if fecha_inicio:
            conds.append(Reserva.fecha_creacion >= fecha_inicio)
        if fecha_fin:
            conds.append(Reserva.fecha_creacion <= fecha_fin)
        if conds:
            q = q.where(and_(*conds))

        res = await db.execute(q)
        reservas = res.scalars().all()

        conteo_estados = {
            "PENDIENTE": 0,
            "PREPARADA": 0,
            "EN_PRUEBA": 0,
            "COMPLETADA": 0,
            "CANCELADA": 0,
            "CADUCADA": 0
        }
        total_monto_recogido = 0.0

        for r in reservas:
            st = r.estado.value if hasattr(r.estado, 'value') else str(r.estado)
            conteo_estados[st] = conteo_estados.get(st, 0) + 1
            if st == "COMPLETADA":
                for d in r.detalles:
                    var = d.variante_producto
                    precio = Decimal("0.00")
                    if var is not None:
                        if var.precio_variante is not None:
                            precio = var.precio_variante
                        elif var.producto is not None and var.producto.precio is not None:
                            precio = var.producto.precio
                    total_monto_recogido += float(precio or 0) * (d.cantidad or 0)

        total_reservas = len(reservas)
        tasa_recogida = (conteo_estados.get("COMPLETADA", 0) / max(1, total_reservas)) * 100

        return {
            "total_reservas": total_reservas,
            "desglose_estados": conteo_estados,
            "tasa_conversion_recogida_pct": round(tasa_recogida, 2),
            "monto_total_convertido": round(total_monto_recogido, 2)
        }

    @staticmethod
    async def generar_reporte_clientes(db: AsyncSession) -> Dict[str, Any]:
        """Genera estadísticas sobre fidelidad y actividad de clientes."""
        res_c = await db.execute(
            select(Cliente).options(
                selectinload(Cliente.usuario),
                selectinload(Cliente.ordenes)
            )
        )
        clientes = res_c.scalars().all()

        top_clientes = []
        clientes_con_compras = 0

        for c in clientes:
            ordenes_validas = [o for o in c.ordenes if o.estado != EstadoOrden.CANCELADO]
            total_gastado = sum(float(o.total or 0) for o in ordenes_validas)
            if ordenes_validas:
                clientes_con_compras += 1

            top_clientes.append({
                "cliente_id": c.id,
                "nombre": f"{c.usuario.nombre} {c.usuario.apellido}" if c.usuario else "Desconocido",
                "correo": c.usuario.correo if c.usuario else "N/A",
                "total_pedidos": len(ordenes_validas),
                "total_gastado": round(total_gastado, 2)
            })

        top_clientes.sort(key=lambda x: x["total_gastado"], reverse=True)

        return {
            "total_clientes_registrados": len(clientes),
            "clientes_activos_con_compras": clientes_con_compras,
            "top_10_clientes": top_clientes[:10]
        }

    @staticmethod
    async def generar_reporte_financiero(
        db: AsyncSession,
        fecha_inicio: Optional[datetime] = None,
        fecha_fin: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Genera balance de ingresos, métodos de pago y comisiones estimadas."""
        ventas_data = await ReporteService.generar_reporte_ventas(db, fecha_inicio, fecha_fin)
        
        q_pagos = select(TransaccionPago)
        if fecha_inicio:
            q_pagos = q_pagos.where(TransaccionPago.fecha >= fecha_inicio)
        if fecha_fin:
            q_pagos = q_pagos.where(TransaccionPago.fecha <= fecha_fin)
        res_pagos = await db.execute(q_pagos)
        pagos = res_pagos.scalars().all()

        metodos = {}
        for p in pagos:
            m = p.metodo_pago.value if hasattr(p.metodo_pago, 'value') else str(p.metodo_pago)
            metodos[m] = metodos.get(m, 0.0) + float(p.monto or 0)

        return {
            "ingresos": ventas_data["resumen"],
            "desglose_por_metodo_pago": {k: round(v, 2) for k, v in metodos.items()},
            "beneficio_estimado_margen_40pct": round(ventas_data["resumen"]["total_recaudado"] * 0.4, 2)
        }

    @staticmethod
    async def guardar_reporte(
        db: AsyncSession,
        admin_id: Optional[int],
        tipo: TipoReporte,
        titulo: str,
        parametros: Dict[str, Any],
        formato: FormatoReporte,
        datos: Dict[str, Any]
    ) -> Reporte:
        """Persiste un registro de reporte en la base de datos."""
        contenido_str = json.dumps(datos, ensure_ascii=False)
        reporte = Reporte(
            administrador_id=admin_id,
            tipo=tipo,
            titulo=titulo,
            parametros=parametros,
            formato=formato,
            contenido=contenido_str
        )
        db.add(reporte)
        await db.commit()
        await db.refresh(reporte)
        return reporte

    @staticmethod
    async def exportar_csv(datos: Dict[str, Any]) -> str:
        """Convierte datos de reporte a formato CSV plano."""
        output = io.StringIO()
        writer = csv.writer(output)

        if "top_productos" in datos:
            writer.writerow(["ID Producto", "Nombre", "Unidades Vendidas"])
            for p in datos["top_productos"]:
                writer.writerow([p.get("producto_id"), p.get("nombre"), p.get("unidades_vendidas")])
        elif "inventario" in datos:
            writer.writerow(["ID", "Producto", "SKU", "Talla", "Color", "Stock Disponible", "Alerta Bajo Stock"])
            for item in datos["inventario"]:
                writer.writerow([
                    item.get("inventario_id"),
                    item.get("producto_nombre"),
                    item.get("sku"),
                    item.get("talla"),
                    item.get("color"),
                    item.get("cantidad_disponible"),
                    "SI" if item.get("alerta_bajo_stock") else "NO"
                ])
        else:
            writer.writerow(["Clave", "Valor"])
            for k, v in datos.items():
                writer.writerow([k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v])

        return output.getvalue()


# ==============================================================================
# KPI SERVICE
# ==============================================================================
class KPIService:
    """Cálculo y gestión de métricas e indicadores de rendimiento gerenciales."""

    @staticmethod
    async def obtener_dashboard_resumen(db: AsyncSession) -> DashboardKPISummary:
        """Calcula el resumen general de KPIs para el panel administrativo."""
        hace_un_mes = datetime.now(timezone.utc) - timedelta(days=30)

        # Ventas y pedidos del mes
        res_ordenes = await db.execute(
            select(
                func.sum(Orden.total),
                func.count(Orden.id)
            ).where(
                and_(
                    Orden.fecha >= hace_un_mes,
                    Orden.estado != EstadoOrden.CANCELADO
                )
            )
        )
        total_ventas, total_pedidos = res_ordenes.first()
        total_ventas = float(total_ventas or 0.0)
        total_pedidos = int(total_pedidos or 0)

        # Reservas conversion
        res_reservas = await db.execute(
            select(
                func.count(Reserva.id),
                func.count(Reserva.id).filter(Reserva.estado == EstadoReserva.COMPLETADA)
            ).where(Reserva.fecha_creacion >= hace_un_mes)
        )
        tot_res, rec_res = res_reservas.first()
        tot_res = int(tot_res or 0)
        rec_res = int(rec_res or 0)
        tasa_conversion = round((rec_res / max(1, tot_res)) * 100, 2)

        # Stock bajo
        res_stock = await db.execute(
            select(func.count(Inventario.id)).where(
                (Inventario.cantidad - Inventario.cantidad_reservada) <= Inventario.stock_minimo
            )
        )
        bajo_stock = int(res_stock.scalar() or 0)

        # Clientes
        res_clientes = await db.execute(select(func.count(Cliente.id)))
        clientes_tot = int(res_clientes.scalar() or 0)

        ticket_prom = round(total_ventas / max(1, total_pedidos), 2)

        # Cargar KPIs guardados
        res_kpis = await db.execute(select(IndicadorKPI).order_by(IndicadorKPI.id.asc()))
        kpis_db = res_kpis.scalars().all()

        return DashboardKPISummary(
            total_ventas_mes=total_ventas,
            total_pedidos_mes=total_pedidos,
            tasa_conversion_reservas=tasa_conversion,
            productos_bajo_stock=bajo_stock,
            clientes_activos=clientes_tot,
            ticket_promedio=ticket_prom,
            kpis_detallados=[IndicadorKPIResponse.model_validate(k) for k in kpis_db]
        )

    @staticmethod
    async def listar_kpis(db: AsyncSession) -> List[IndicadorKPI]:
        res = await db.execute(select(IndicadorKPI).order_by(IndicadorKPI.id.asc()))
        return res.scalars().all()

    @staticmethod
    async def crear_kpi(db: AsyncSession, data: IndicadorKPICreate) -> IndicadorKPI:
        datos_kpi = data.model_dump(exclude_unset=True)
        datos_kpi.pop("unidad_medida", None)  # Campo del schema que no existe en el modelo
        kpi = IndicadorKPI(**datos_kpi)
        db.add(kpi)
        await db.commit()
        await db.refresh(kpi)
        return kpi

    @staticmethod
    async def actualizar_kpi(db: AsyncSession, kpi_id: int, data: IndicadorKPIBase) -> IndicadorKPI:
        res = await db.execute(select(IndicadorKPI).where(IndicadorKPI.id == kpi_id))
        kpi = res.scalar_one_or_none()
        if not kpi:
            raise NotFoundException("Indicador KPI no encontrado")

        for field, val in data.model_dump(exclude_unset=True).items():
            if field == "unidad_medida":
                continue
            setattr(kpi, field, val)

        await db.commit()
        await db.refresh(kpi)
        return kpi


# ==============================================================================
# RECOMENDACION SERVICE (CU13)
# ==============================================================================
class RecomendacionService:
    """Motor de recomendaciones de moda inteligente para clientes."""

    @staticmethod
    async def obtener_recomendaciones(
        db: AsyncSession,
        cliente_id: Optional[int] = None,
        preferencias: Optional[str] = None,
        limite: int = 5
    ) -> RecomendacionResponse:
        cliente_nombre = "Estimado/a Cliente"
        historial_compras = []

        if cliente_id:
            res_c = await db.execute(
                select(Cliente).options(
                    selectinload(Cliente.usuario),
                    selectinload(Cliente.ordenes).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto),
                    selectinload(Cliente.ordenes).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.talla),
                    selectinload(Cliente.ordenes).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.color),
                ).where(Cliente.id == cliente_id)
            )
            cliente = res_c.scalar_one_or_none()
            if cliente and cliente.usuario:
                cliente_nombre = f"{cliente.usuario.nombre} {cliente.usuario.apellido}"
                for o in sorted(cliente.ordenes, key=lambda x: x.fecha or datetime.min, reverse=True)[:5]:
                    for d in o.detalles:
                        var = d.variante_producto
                        if var and var.producto:
                            historial_compras.append({
                                "producto": var.producto.nombre,
                                "talla": var.talla.valor if var.talla else None,
                                "color": var.color.nombre if var.color else None
                            })

        # Obtener productos activos del catálogo
        res_p = await db.execute(
            select(Producto).options(
                selectinload(Producto.categoria),
                selectinload(Producto.variantes)
            ).where(Producto.estado == EstadoProducto.ACTIVO).limit(30)
        )
        productos = res_p.scalars().all()
        catalogo_resumido = [
            {
                "id": p.id,
                "nombre": p.nombre,
                "categoria": p.categoria.nombre if p.categoria else "General",
                "precio": float(p.precio or 0)
            }
            for p in productos
        ]

        # Llamar a Groq
        ai_res = await groq_service.get_recomendaciones(
            cliente_nombre=cliente_nombre,
            historial_compras=historial_compras,
            productos_catalogo=catalogo_resumido,
            preferencias=preferencias
        )

        # Mapear productos con detalles completos
        prods_map = {p.id: p for p in productos}
        items_recomendados: List[RecomendacionItem] = []

        for rec in ai_res.get("recomendaciones", []):
            pid = rec.get("producto_id")
            prod = prods_map.get(pid)
            if prod:
                items_recomendados.append(RecomendacionItem(
                    producto_id=prod.id,
                    nombre=prod.nombre,
                    razon=rec.get("razon", "Recomendado para ti"),
                    imagen_url=_imagen_principal(prod),
                    precio=float(prod.precio or 0),
                    categoria=prod.categoria.nombre if prod.categoria else None
                ))

        # Si el LLM devolvió pocos o ninguno que coincida con DB, rellenar con destacados
        if not items_recomendados and productos:
            for p in productos[:limite]:
                items_recomendados.append(RecomendacionItem(
                    producto_id=p.id,
                    nombre=p.nombre,
                    razon="Selección destacada de nuestra colección de temporada",
                    imagen_url=_imagen_principal(p),
                    precio=float(p.precio or 0),
                    categoria=p.categoria.nombre if p.categoria else None
                ))

        return RecomendacionResponse(
            cliente_id=cliente_id,
            estilo_detectado=ai_res.get("estilo_detectado", "Urbano Contemporáneo"),
            mensaje_personalizado=ai_res.get("mensaje_personalizado", f"¡Hola {cliente_nombre}! Descubre lo que seleccionamos para ti."),
            recomendaciones=items_recomendados[:limite]
        )


# ==============================================================================
# ASISTENTE SERVICE (CU23)
# ==============================================================================
class AsistenteService:
    """Asesor y estilista virtual interactivo."""

    @staticmethod
    async def responder_chat(
        db: AsyncSession,
        mensaje: str,
        historial: List[Dict[str, str]],
        cliente_id: Optional[int] = None
    ) -> AsistenteChatResponse:
        # Obtener contexto del catálogo (más amplio para cubrir categorías específicas)
        res_p = await db.execute(
            select(Producto).options(selectinload(Producto.categoria))
            .where(Producto.estado == EstadoProducto.ACTIVO).limit(40)
        )
        prods = res_p.scalars().all()

        def _resumen_producto(p: Producto) -> Dict[str, Any]:
            return {
                "id": p.id,
                "nombre": p.nombre,
                "descripcion": (p.descripcion or "")[:280],
                "categoria": p.categoria.nombre if p.categoria else "General",
                "precio": float(p.precio or 0),
                "imagen_principal": _imagen_principal(p),
            }

        catalogo_resumen = [_resumen_producto(p) for p in prods]

        # Si el cliente pide una categoría concreta (ej. "muéstrame las gorras"),
        # pasar al modelo SOLO los productos de esa categoría.
        palabra_categoria = _detectar_filtro_categoria(mensaje)
        catalogo_filtrado: List[Dict[str, Any]] = []
        if palabra_categoria:
            for item in catalogo_resumen:
                nombre = _normalizar_texto(item["nombre"])
                categoria = _normalizar_texto(item["categoria"])
                if palabra_categoria in nombre or palabra_categoria in categoria:
                    catalogo_filtrado.append(item)
            if catalogo_filtrado:
                catalogo_resumen = catalogo_filtrado

        contexto_cliente = None
        if cliente_id:
            res_c = await db.execute(
                select(Cliente).options(selectinload(Cliente.usuario)).where(Cliente.id == cliente_id)
            )
            c = res_c.scalar_one_or_none()
            if c and c.usuario:
                contexto_cliente = {"nombre": c.usuario.nombre, "nit_ci": c.nit_ci}

        ai_res = await groq_service.chat_asistente(
            mensaje=mensaje,
            historial_conversacion=historial,
            catalogo_resumen=catalogo_resumen,
            contexto_cliente=contexto_cliente
        )

        def _build_producto_info(p: Producto) -> Dict[str, Any]:
            imgs = p.imagenes
            if isinstance(imgs, str):
                imgs = [imgs]
            return {
                "id": p.id,
                "nombre": p.nombre,
                "descripcion": p.descripcion,
                "precio": float(p.precio or 0),
                "categoria": p.categoria.nombre if p.categoria else None,
                "imagen_principal": _imagen_principal(p),
                "imagenes": imgs if isinstance(imgs, list) else [],
            }

        # Enriquecer productos mencionados con detalles visuales (sin SKU ni códigos internos)
        pids_mencionados = ai_res.get("productos_mencionados", [])
        productos_info: List[Dict[str, Any]] = []
        prods_map = {p.id: p for p in prods}
        for pid in pids_mencionados:
            try:
                pid_int = int(pid)
            except (TypeError, ValueError):
                continue
            p = prods_map.get(pid_int)
            if p:
                productos_info.append(_build_producto_info(p))

        # Fallback: el cliente pidió una categoría y el modelo no devolvió IDs válidos.
        # Mostramos igualmente los productos de esa categoría como tarjetas visuales.
        ids_filtrados = {item["id"] for item in catalogo_filtrado}
        if not productos_info and ids_filtrados:
            for p in prods:
                if p.id in ids_filtrados:
                    productos_info.append(_build_producto_info(p))
                if len(productos_info) >= 6:
                    break

        tipo_respuesta = str(_normalizar_texto(ai_res.get("tipo_respuesta") or "texto"))
        if tipo_respuesta not in {"texto", "catalogo", "producto", "outfit"}:
            tipo_respuesta = "texto"
        if productos_info and tipo_respuesta == "texto":
            tipo_respuesta = "catalogo" if len(productos_info) > 1 else "producto"

        return AsistenteChatResponse(
            respuesta=ai_res.get("respuesta", "Con gusto te asisto en lo que necesites de moda y estilo."),
            sugerencias=ai_res.get("sugerencias", ["Ver novedades", "¿Tienen vestidos de noche?", "Recomiéndame un outfit casual"]),
            productos_mencionados=productos_info,
            tipo_respuesta=tipo_respuesta
        )


# ==============================================================================
# VESTIDOR VIRTUAL SERVICE (CU21)
# ==============================================================================
class VestidorVirtualService:
    """Probador de prendas virtual con carga y simulación de outfit."""

    @staticmethod
    async def probar_prenda(
        db: AsyncSession,
        producto_id: int,
        variante_id: Optional[int] = None,
        imagen_usuario_base64: Optional[str] = None,
        imagen_usuario_url: Optional[str] = None
    ) -> VestidorVirtualResponse:
        res_p = await db.execute(
            select(Producto).options(selectinload(Producto.variantes)).where(Producto.id == producto_id)
        )
        producto = res_p.scalar_one_or_none()
        if not producto:
            raise NotFoundException("Producto no encontrado")

        resultado_imagen_url = _imagen_principal(producto) or "https://images.unsplash.com/photo-1490481651871-ab68de25d43d?w=800"

        # Si se subió imagen en base64, subir a Cloudinary
        if imagen_usuario_base64:
            try:
                # Si viene data:image/png;base64,...
                if "," in imagen_usuario_base64:
                    imagen_usuario_base64 = imagen_usuario_base64.split(",")[1]
                img_bytes = base64.b64decode(imagen_usuario_base64)
                cloud_res = CloudinaryService.upload_image(
                    file=img_bytes,
                    folder="vestidor_virtual",
                    transformation={"width": 800, "height": 1000, "crop": "fill"}
                )
                if cloud_res and cloud_res.get("secure_url"):
                    resultado_imagen_url = cloud_res.get("secure_url")
            except Exception as e:
                # Si falla Cloudinary, no romper la respuesta
                pass
        elif imagen_usuario_url:
            resultado_imagen_url = imagen_usuario_url

        return VestidorVirtualResponse(
            resultado_url=resultado_imagen_url,
            producto_id=producto.id,
            producto_nombre=producto.nombre,
            mensaje=f"¡El atuendo con '{producto.nombre}' ha sido simulado exitosamente! Combina a la perfección.",
            detalles_ajuste={
                "compatibilidad_corte": "Ajuste regular / Slim fit",
                "tono_recomendado": "Ideal para contrastes medios y altos",
                "ocasion_sugerida": "Uso diario, eventos casuales o semi-formales"
            }
        )


# ==============================================================================
# REPORTE VOZ SERVICE (CU22)
# ==============================================================================
class ReporteVozService:
    """Interpretación de comandos de voz para generación ágil de reportes."""

    @staticmethod
    async def procesar_comando(
        db: AsyncSession,
        transcripcion: str,
        admin_id: Optional[int] = None,
        formato: FormatoReporte = FormatoReporte.JSON
    ) -> ReporteVozResponse:
        ai_res = await groq_service.procesar_comando_voz(transcripcion)
        tipo_str = ai_res.get("tipo_reporte", "VENTAS")

        try:
            tipo_enum = TipoReporte(tipo_str)
        except ValueError:
            tipo_enum = TipoReporte.VENTAS

        # Generar el reporte solicitado
        if tipo_enum == TipoReporte.VENTAS:
            datos = await ReporteService.generar_reporte_ventas(db)
        elif tipo_enum == TipoReporte.INVENTARIO:
            datos = await ReporteService.generar_reporte_inventario(db)
        elif tipo_enum == TipoReporte.RESERVAS:
            datos = await ReporteService.generar_reporte_reservas(db)
        elif tipo_enum == TipoReporte.CLIENTES:
            datos = await ReporteService.generar_reporte_clientes(db)
        else:
            datos = await ReporteService.generar_reporte_financiero(db)

        # Guardar en base de datos
        reporte_db = await ReporteService.guardar_reporte(
            db=db,
            admin_id=admin_id,
            tipo=tipo_enum,
            titulo=f"Reporte por voz: {transcripcion[:80]}",
            parametros={"comando_voz": transcripcion, **ai_res.get("parametros", {})},
            formato=formato,
            datos=datos
        )

        return ReporteVozResponse(
            comando_original=transcripcion,
            tipo_reporte=tipo_enum,
            interpretacion=ai_res.get("resumen_interpretacion", f"Reporte de {tipo_enum.value} generado."),
            datos=datos,
            reporte_guardado_id=reporte_db.id
        )


# ==============================================================================
# TENDENCIAS SERVICE (CU20)
# ==============================================================================
class TendenciasService:
    """Análisis predictivo de tendencias de moda."""

    @staticmethod
    async def analizar_tendencias(db: AsyncSession) -> TendenciasResponse:
        # Obtener resumen de ventas
        rep_ventas = await ReporteService.generar_reporte_ventas(db)
        top_prods = rep_ventas.get("top_productos", [])

        # Obtener temporadas activas (dentro del rango de fechas vigente)
        hoy = datetime.now(timezone.utc).date()
        res_temp = await db.execute(
            select(Temporada).where(
                Temporada.fecha_inicio <= hoy,
                Temporada.fecha_fin >= hoy
            )
        )
        temporadas = [t.nombre for t in res_temp.scalars().all()]
        if not temporadas:
            temporadas = ["Verano 2026", "Colección Contemporánea"]

        ai_res = await groq_service.get_tendencias(
            ventas_resumen=top_prods,
            temporadas_activas=temporadas
        )

        tendencias_items = [
            TendenciaItem(
                tendencia=t.get("tendencia", "Moda Sostenible"),
                impacto=t.get("impacto", "Alto"),
                recomendacion=t.get("recomendacion", "Promover colecciones de fibras naturales")
            )
            for t in ai_res.get("tendencias_destacadas", [])
        ]

        return TendenciasResponse(
            fecha_analisis=datetime.now(timezone.utc),
            tendencias_destacadas=tendencias_items,
            categorias_en_alza=ai_res.get("categorias_en_alza", ["Casual Elegante", "Accesorios"]),
            prediccion_demanda=ai_res.get("prediccion_demanda", "Crecimiento proyectado continuo en líneas de temporada."),
            datos_respaldo={"top_ventas": top_prods, "temporadas": temporadas}
        )
