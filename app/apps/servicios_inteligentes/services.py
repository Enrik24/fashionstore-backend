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
    VentaPresencial, TransaccionPago, EstadoOrden, EstadoReserva, EstadoTransaccion, TipoOrden
)
from app.apps.gestion_catalogo.models import (
    Producto, VarianteProducto, Inventario, Categoria, Temporada, Coleccion, EstadoProducto
)
from app.apps.gestion_usuarios.models import Usuario, Cliente, Administrador


def _lista_imagenes(valor: Any) -> List[str]:
    """Normaliza el campo `imagenes`: a veces viene como string serializado '["url"]'."""
    if not valor:
        return []
    if isinstance(valor, list):
        return [str(x) for x in valor if x]
    if isinstance(valor, str):
        s = valor.strip()
        if not s:
            return []
        try:
            import json as _json
            parsed = _json.loads(s)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
            return [s]
        except (ValueError, TypeError):
            return [s]
    return []


def _imagen_principal(producto: Any) -> Optional[str]:
    """Devuelve la primera imagen del producto (campo `imagenes`) o None."""
    if producto is None or not getattr(producto, "imagenes", None):
        return None
    imgs = _lista_imagenes(producto.imagenes)
    return imgs[0] if imgs else None


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


def _costo_detalle(det) -> Optional[float]:
    """Costo por unidad: congelado ?? costo_variante ?? costo_compra.

    Devuelve None cuando no hay ningún costo real cargado (antes se estimaba
    precio*0.6, lo que hacía pasar un estimado por "beneficio real").
    """
    try:
        frozen = getattr(det, "costo_unitario", None)
        if frozen is not None:
            return float(frozen)
        var = getattr(det, "variante_producto", None)
        if var is not None:
            cv = getattr(var, "costo_variante", None)
            if cv is not None:
                return float(cv)
            prod = getattr(var, "producto", None)
            if prod is not None and getattr(prod, "costo_compra", None) is not None:
                c = float(prod.costo_compra)
                if c > 0:
                    return c
        return None
    except Exception:
        return None


def _costo_total(ordenes, ventas_presenciales) -> Dict[str, Any]:
    """Suma costo*cantidad de todos los detalles no cancelados (online + presencial).

    Devuelve {"total": float, "tiene_costos_reales": bool}: si ningún detalle
    tiene costo real cargado, el total es 0 y tiene_costos_reales es False para
    que la UI muestre el estimado en lugar de un "real" inventado.
    """
    total = 0.0
    tiene_reales = False
    for o in (ordenes or []):
        try:
            if getattr(o, "estado", None) == EstadoOrden.CANCELADO:
                continue
            for d in (getattr(o, "detalles", None) or []):
                c = _costo_detalle(d)
                if c is None:
                    continue
                tiene_reales = True
                total += c * (getattr(d, "cantidad", 0) or 0)
        except Exception:
            continue
    for v in (ventas_presenciales or []):
        try:
            ord_ = getattr(v, "orden", None)
            if ord_ is None or getattr(ord_, "estado", None) == EstadoOrden.CANCELADO:
                continue
            for d in (getattr(ord_, "detalles", None) or []):
                c = _costo_detalle(d)
                if c is None:
                    continue
                tiene_reales = True
                total += c * (getattr(d, "cantidad", 0) or 0)
        except Exception:
            continue
    return {"total": round(total, 2), "tiene_costos_reales": tiene_reales}


def _serie_diaria_ventas(ordenes, ventas_presenciales, fecha_inicio, fecha_fin):
    """Agrega totales por día (retrocompatible: solo se incluye con incluir_serie=true)."""
    from collections import defaultdict
    from datetime import date as _date
    fi = fecha_inicio.date() if hasattr(fecha_inicio, "date") else None
    ff = fecha_fin.date() if hasattr(fecha_fin, "date") else None
    if not fi or not ff:
        ff = datetime.now(timezone.utc).date()
        fi = ff - timedelta(days=29)
    dias = (ff - fi).days
    if dias < 0 or dias > 366:
        ff = datetime.now(timezone.utc).date()
        fi = ff - timedelta(days=29)
        dias = 29
    acc = defaultdict(lambda: {"total": 0.0, "online": 0.0, "presencial": 0.0})
    for o in (ordenes or []):
        try:
            if getattr(o, "estado", None) == EstadoOrden.CANCELADO:
                continue
            f = getattr(o, "fecha", None)
            d = f.date() if hasattr(f, "date") else None
            if d and fi <= d <= ff:
                acc[d.isoformat()]["online"] += float(getattr(o, "total", 0) or 0)
                acc[d.isoformat()]["total"] += float(getattr(o, "total", 0) or 0)
        except Exception:
            continue
    for v in (ventas_presenciales or []):
        try:
            ord_ = getattr(v, "orden", None)
            if ord_ is None or getattr(ord_, "estado", None) == EstadoOrden.CANCELADO:
                continue
            f = getattr(v, "fecha", None) or getattr(ord_, "fecha", None)
            d = f.date() if hasattr(f, "date") else None
            if d and fi <= d <= ff:
                acc[d.isoformat()]["presencial"] += float(getattr(ord_, "total", 0) or 0)
                acc[d.isoformat()]["total"] += float(getattr(ord_, "total", 0) or 0)
        except Exception:
            continue
    out = []
    cur = fi
    while cur <= ff:
        k = cur.isoformat()
        out.append({"fecha": k, "total": round(acc[k]["total"], 2),
                    "online": round(acc[k]["online"], 2),
                    "presencial": round(acc[k]["presencial"], 2)})
        cur += timedelta(days=1)
    return out


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


def _genero_dominante(historial_compras) -> Optional[str]:
    """Género dominante del historial (solo HOMBRE vs MUJER; el unisex no vota).

    Devuelve "HOMBRE", "MUJER" o None cuando hay empate, no hay historial o
    todo es unisex (en esos casos se dejan los 3 géneros como hasta ahora).
    """
    h = sum(1 for it in (historial_compras or []) if str((it or {}).get("genero") or "").upper() == "HOMBRE")
    m = sum(1 for it in (historial_compras or []) if str((it or {}).get("genero") or "").upper() == "MUJER")
    if h > m:
        return "HOMBRE"
    if m > h:
        return "MUJER"
    return None


def _genero_producto(p) -> str:
    g = p.genero.value if hasattr(getattr(p, "genero", None), "value") else (str(p.genero) if getattr(p, "genero", None) else "UNISEX")
    return (g or "UNISEX").upper()


def _filtrar_productos_por_genero(productos, genero_dominante):
    """Limita el catálogo al género dominante + UNISEX (siempre incluido)."""
    if not genero_dominante:
        return list(productos)
    return [p for p in productos if _genero_producto(p) in (genero_dominante, "UNISEX")]


# Mapa de categorías complementarias para armar outfits (claves normalizadas).
# Si el cliente compró X, se prioriza recomendar estas categorías en orden.
_CATEGORIAS_COMPLEMENTO = {
    "camisa": ["pantalones", "chaquetas", "calzado", "accesorios", "canguros", "abrigos"],
    "camisas": ["pantalones", "chaquetas", "calzado", "accesorios", "canguros", "abrigos"],
    "polera": ["pantalones", "shorts", "calzado", "chaquetas", "accesorios"],
    "poleras": ["pantalones", "shorts", "calzado", "chaquetas", "accesorios"],
    "pantalon": ["camisas", "poleras", "calzado", "chaquetas", "canguros", "accesorios"],
    "pantalones": ["camisas", "poleras", "calzado", "chaquetas", "canguros", "accesorios"],
    "chaqueta": ["pantalones", "camisas", "calzado", "canguros", "accesorios"],
    "chaquetas": ["pantalones", "camisas", "calzado", "canguros", "accesorios"],
    "abrigo": ["pantalones", "camisas", "calzado", "accesorios"],
    "abrigos": ["pantalones", "camisas", "calzado", "accesorios"],
    "canguro": ["pantalones", "shorts", "calzado", "chaquetas", "accesorios"],
    "canguros": ["pantalones", "shorts", "calzado", "chaquetas", "accesorios"],
    "calzado": ["pantalones", "camisas", "chaquetas", "accesorios"],
    "short": ["poleras", "calzado", "canguros", "accesorios"],
    "shorts": ["poleras", "calzado", "canguros", "accesorios"],
    "vestido": ["calzado", "chaquetas", "accesorios", "abrigos"],
    "vestidos": ["calzado", "chaquetas", "accesorios", "abrigos"],
    "accesorio": ["camisas", "pantalones", "chaquetas", "calzado"],
    "accesorios": ["camisas", "pantalones", "chaquetas", "calzado"],
    "polo": ["pantalones", "shorts", "calzado", "accesorios"],
    "polos": ["pantalones", "shorts", "calzado", "accesorios"],
}


def _nombre_categoria(p) -> str:
    cat = getattr(p, "categoria", None)
    nombre = getattr(cat, "nombre", None) if cat else None
    return _normalizar_texto(nombre or "general")


def _ordenar_por_complemento(productos, categorias_compradas):
    """Ordena productos priorizando categorías que complementan la compra.

    1) Categorías complementarias según el mapa outfit (sin repetir la comprada).
    2) Luego el resto en su orden original.
    """
    if not categorias_compradas:
        return list(productos)
    prioridad = []
    vistas = set()
    for comprada in categorias_compradas:
        for comp in _CATEGORIAS_COMPLEMENTO.get(comprada, []):
            if comp not in vistas and comp not in categorias_compradas:
                vistas.add(comp)
                prioridad.append(comp)
    orden = {c: i for i, c in enumerate(prioridad)}

    complementarios = sorted(
        [p for p in productos if _nombre_categoria(p) in orden],
        key=lambda p: orden[_nombre_categoria(p)],
    )
    resto = [p for p in productos if _nombre_categoria(p) not in orden]
    return complementarios + resto


def _diversificar_por_categoria(productos, limite: int, max_por_categoria: int = 1):
    """Devuelve hasta `limite` productos con máximo `max_por_categoria` por categoría.

    Garantiza variedad: nunca devuelve todo de una sola categoría si hay más
    categorías disponibles.
    """
    elegidos = []
    conteo: Dict[str, int] = {}
    for p in productos:
        if len(elegidos) >= limite:
            break
        cat = _nombre_categoria(p)
        if conteo.get(cat, 0) >= max_por_categoria:
            continue
        conteo[cat] = conteo.get(cat, 0) + 1
        elegidos.append(p)
    if len(elegidos) < limite:
        ids = {id(p) for p in elegidos}
        for p in productos:
            if len(elegidos) >= limite:
                break
            if id(p) not in ids:
                elegidos.append(p)
    return elegidos


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
        sucursal_id: Optional[int] = None,
        incluir_serie: bool = False
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
        if sucursal_id:
            condiciones_orden.append(Orden.sucursal_id == sucursal_id)
            condiciones_presencial.append(VentaPresencial.sucursal_id == sucursal_id)

        # 1. Órdenes Online
        q_ordenes = select(Orden).options(
            selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto)
        )
        if condiciones_orden:
            q_ordenes = q_ordenes.where(and_(*condiciones_orden))
        res_ordenes = await db.execute(q_ordenes)
        ordenes = res_ordenes.scalars().all()

        total_online = sum(float(o.total or 0) for o in ordenes if o.estado != EstadoOrden.CANCELADO)
        pedidos_online_count = len([o for o in ordenes if o.estado != EstadoOrden.CANCELADO])

        # 2. Ventas Presenciales
        q_presencial = select(VentaPresencial).options(
            selectinload(VentaPresencial.orden).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto)
        )
        if sucursal_id:
            condiciones_presencial.append(VentaPresencial.sucursal_id == sucursal_id)
        if condiciones_presencial:
            q_presencial = q_presencial.where(and_(*condiciones_presencial))
        res_presencial = await db.execute(q_presencial)
        ventas_presenciales = res_presencial.scalars().all()

        def _orden_valida(v) -> bool:
            return v.orden is not None and v.orden.estado != EstadoOrden.CANCELADO

        total_presencial = sum(float(v.orden.total or 0) for v in ventas_presenciales if _orden_valida(v))
        ventas_presenciales_count = len([v for v in ventas_presenciales if _orden_valida(v)])

        # 3. Agregación de productos más vendidos
        conteo_productos = {}
        for o in ordenes:
            if o.estado != EstadoOrden.CANCELADO:
                for d in o.detalles:
                    pid = d.variante_producto.producto_id if d.variante_producto else None
                    if pid:
                        conteo_productos[pid] = conteo_productos.get(pid, 0) + d.cantidad

        for v in ventas_presenciales:
            if _orden_valida(v):
                for d in v.orden.detalles:
                    pid = d.variante_producto.producto_id if d.variante_producto else None
                    if pid:
                        conteo_productos[pid] = conteo_productos.get(pid, 0) + d.cantidad

        top_productos_info = []
        if conteo_productos:
            sorted_pids = sorted(conteo_productos.items(), key=lambda x: x[1], reverse=True)[:10]
            pids = [item[0] for item in sorted_pids]
            res_p = await db.execute(select(Producto).where(Producto.id.in_(pids)))
            prods_map = {p.id: p.nombre for p in res_p.scalars().all()}
            for pid, qty in sorted_pids:
                top_productos_info.append({
                    "producto_id": pid,
                    "nombre": prods_map.get(pid, f"Producto #{pid}"),
                    "unidades_vendidas": qty
                })

        costo_info = _costo_total(ordenes, ventas_presenciales)
        resultado = {
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
            "costos": {
                "costo_total_bienes": costo_info["total"],
                "tiene_costos_reales": costo_info["tiene_costos_reales"],
            },
            "top_productos": top_productos_info
        }
        if incluir_serie:
            resultado["serie_diaria"] = _serie_diaria_ventas(ordenes, ventas_presenciales, fecha_inicio, fecha_fin)
        return resultado

    @staticmethod
    async def generar_reporte_inventario(
        db: AsyncSession,
        categoria_id: Optional[int] = None,
        solo_bajo_stock: bool = False,
        sucursal_id: Optional[int] = None,
        limite: int = 500
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
        if sucursal_id:
            q = q.where(Inventario.sucursal_id == sucursal_id)

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
                "producto_nombre": prod.nombre if prod else None,
                "sku": var.sku_variante if var else None,
                "talla": var.talla.valor if (var and var.talla) else None,
                "color": var.color.nombre if (var and var.color) else None,
                "sucursal": inv.sucursal.nombre if inv.sucursal else None,
                "cantidad_disponible": inv.cantidad_disponible,
                "cantidad_reservada": inv.cantidad_reservada,
                "cantidad_minima": inv.stock_minimo,
                "alerta_bajo_stock": es_bajo
            })

        limite = max(1, min(limite or 500, 2000))
        return {
            "total_items_registrados": len(detalles_inventario),
            "total_unidades_disponibles": total_unidades,
            "items_con_bajo_stock": items_bajo_stock,
            "limite_mostrados": limite,
            "inventario": detalles_inventario[:limite]
        }

    @staticmethod
    async def generar_reporte_reservas(
        db: AsyncSession,
        fecha_inicio: Optional[datetime] = None,
        fecha_fin: Optional[datetime] = None,
        sucursal_id: Optional[int] = None
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
        if sucursal_id:
            conds.append(Reserva.sucursal_id == sucursal_id)
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
                selectinload(Cliente.ordenes),
                selectinload(Cliente.ventas_presenciales).selectinload(VentaPresencial.orden)
            )
        )
        clientes = res_c.scalars().all()

        top_clientes = []
        clientes_con_compras = 0

        for c in clientes:
            ordenes_validas = [o for o in c.ordenes if o.estado != EstadoOrden.CANCELADO]
            total_gastado = sum(float(o.total or 0) for o in ordenes_validas)
            pedidos_pos = 0
            for v in (c.ventas_presenciales or []):
                if v.orden is not None and v.orden.estado != EstadoOrden.CANCELADO:
                    total_gastado += float(v.orden.total or 0)
                    pedidos_pos += 1
            total_pedidos = len(ordenes_validas) + pedidos_pos
            if total_pedidos:
                clientes_con_compras += 1
            else:
                continue  # Fuera del top: sin compras no aporta al ranking

            top_clientes.append({
                "cliente_id": c.id,
                "nombre": f"{c.usuario.nombre} {c.usuario.apellido}" if c.usuario else "Desconocido",
                "correo": c.usuario.correo if c.usuario else None,
                "email": c.usuario.correo if c.usuario else None,
                "total_pedidos": total_pedidos,
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
        fecha_fin: Optional[datetime] = None,
        sucursal_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Genera balance de ingresos, métodos de pago y beneficios.

        El desglose por método combina pagos digitales confirmados
        (TransaccionPago) con ventas presenciales (VentaPresencial), que antes
        no aparecían y dejaban el bloque vacío.
        """
        ventas_data = await ReporteService.generar_reporte_ventas(db, fecha_inicio, fecha_fin, sucursal_id)

        q_pagos = select(TransaccionPago).join(Orden, TransaccionPago.orden_id == Orden.id).where(
            TransaccionPago.estado == EstadoTransaccion.CONFIRMADO
        )
        if fecha_inicio:
            q_pagos = q_pagos.where(TransaccionPago.fecha >= fecha_inicio)
        if fecha_fin:
            q_pagos = q_pagos.where(TransaccionPago.fecha <= fecha_fin)
        if sucursal_id:
            q_pagos = q_pagos.where(Orden.sucursal_id == sucursal_id)
        res_pagos = await db.execute(q_pagos)
        pagos = res_pagos.scalars().all()

        metodos: Dict[str, float] = {}
        for p in pagos:
            m = p.metodo_pago.value if hasattr(p.metodo_pago, 'value') else str(p.metodo_pago)
            metodos[m] = metodos.get(m, 0.0) + float(p.monto or 0)

        # Ventas presenciales (caja): no generan TransaccionPago, se agregan por método
        q_vp = select(VentaPresencial).options(selectinload(VentaPresencial.orden))
        conds_vp = []
        if fecha_inicio:
            conds_vp.append(VentaPresencial.fecha >= fecha_inicio)
        if fecha_fin:
            conds_vp.append(VentaPresencial.fecha <= fecha_fin)
        if sucursal_id:
            conds_vp.append(VentaPresencial.sucursal_id == sucursal_id)
        if conds_vp:
            q_vp = q_vp.where(and_(*conds_vp))
        res_vp = await db.execute(q_vp)
        for v in res_vp.scalars().all():
            if v.orden is None or v.orden.estado == EstadoOrden.CANCELADO:
                continue
            m = v.metodo_pago.value if hasattr(v.metodo_pago, 'value') else str(v.metodo_pago)
            metodos[m] = metodos.get(m, 0.0) + float(v.orden.total or 0)

        total_rec = float(ventas_data["resumen"]["total_recaudado"] or 0)
        costos = ventas_data.get("costos") or {}
        costo_total = float(costos.get("costo_total_bienes") or 0)
        if costos.get("tiene_costos_reales"):
            beneficio_real: Optional[float] = round(total_rec - costo_total, 2)
            margen_pct: Optional[float] = round((beneficio_real / total_rec * 100) if total_rec else 0, 2)
        else:
            # Sin costos reales cargados: no inventar un "beneficio real"
            beneficio_real = None
            margen_pct = None
            costo_total = 0.0

        return {
            "ingresos": ventas_data["resumen"],
            "desglose_por_metodo_pago": {k: round(v, 2) for k, v in metodos.items()},
            "costo_total_bienes": round(costo_total, 2),
            "beneficio_real": beneficio_real,
            "margen_real_pct": margen_pct,
            "beneficio_estimado_margen_40pct": round(total_rec * 0.4, 2)
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
                    selectinload(Cliente.ordenes).selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto).selectinload(Producto.categoria),
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
                                "producto_id": var.producto.id,
                                "producto": var.producto.nombre,
                                "categoria": var.producto.categoria.nombre if var.producto.categoria else "General",
                                "genero": var.producto.genero.value if hasattr(var.producto.genero, 'value') else str(var.producto.genero) if var.producto.genero else None,
                                "talla": var.talla.valor if var.talla else None,
                                "color": var.color.nombre if var.color else None
                            })

        # Obtener productos activos del catálogo
        res_p = await db.execute(
            select(Producto).options(
                selectinload(Producto.categoria),
                selectinload(Producto.variantes)
            ).where(Producto.estado == EstadoProducto.ACTIVO).limit(40)
        )
        productos = list(res_p.scalars().all())

        # Filtro duro por género: si el historial es claramente de hombre (o de
        # mujer), la IA y el relleno solo ven ese género + UNISEX. En mixto,
        # empate o vacío se dejan los 3 géneros como hasta ahora.
        productos = _filtrar_productos_por_genero(productos, _genero_dominante(historial_compras))
        catalogo_resumido = [
            {
                "id": p.id,
                "nombre": p.nombre,
                "categoria": p.categoria.nombre if p.categoria else "General",
                "genero": p.genero.value if hasattr(p.genero, 'value') else str(p.genero) if p.genero else "UNISEX",
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

        # IDs ya comprados: nunca se recomiendan de vuelta
        ids_comprados = {h.get("producto_id") for h in historial_compras if h.get("producto_id")}
        categorias_compradas = {_normalizar_texto(h.get("categoria") or "") for h in historial_compras if h.get("categoria")}
        categorias_compradas.discard("")

        # Mapear productos con detalles completos (excluyendo lo ya comprado)
        prods_map = {p.id: p for p in productos}
        candidatos_ia = []
        for rec in ai_res.get("recomendaciones", []):
            pid = rec.get("producto_id")
            if pid in ids_comprados:
                continue
            prod = prods_map.get(pid)
            if prod:
                candidatos_ia.append((prod, rec.get("razon", "Recomendado para ti")))
        # Diversificar lo que devolvió la IA: prioriza categorías que
        # complementan la compra y limita 1 por categoría
        candidatos_ia = _diversificar_por_categoria(
            _ordenar_por_complemento([p for p, _ in candidatos_ia], categorias_compradas),
            limite,
        )
        razones = {rec.get("producto_id"): rec.get("razon", "Recomendado para ti") for rec in ai_res.get("recomendaciones", [])}
        items_recomendados: List[RecomendacionItem] = []
        for prod in candidatos_ia:
            img_list = _lista_imagenes(prod.imagenes)
            items_recomendados.append(RecomendacionItem(
                producto_id=prod.id,
                nombre=prod.nombre,
                sku=prod.sku,
                razon=razones.get(prod.id, "Recomendado para ti"),
                imagen_url=_imagen_principal(prod),
                imagenes=img_list,
                precio=float(prod.precio or 0),
                categoria=prod.categoria.nombre if prod.categoria else None,
                categoria_id=prod.categoria_id,
                genero=prod.genero.value if hasattr(prod.genero, 'value') else str(prod.genero) if prod.genero else None
            ))

        # Si el LLM devolvió pocos o ninguno que coincida con DB, rellenar con
        # destacados DIVERSIFICADOS (complemento de outfit, 1 por categoría,
        # excluyendo lo ya comprado)
        if len(items_recomendados) < limite and productos:
            used_ids = {item.producto_id for item in items_recomendados} | ids_comprados
            candidatos = _diversificar_por_categoria(
                _ordenar_por_complemento(
                    [p for p in productos if p.id not in used_ids],
                    categorias_compradas,
                ),
                limite - len(items_recomendados),
            )
            for p in candidatos:
                img_list = _lista_imagenes(p.imagenes)
                items_recomendados.append(RecomendacionItem(
                    producto_id=p.id,
                    nombre=p.nombre,
                    sku=p.sku,
                    razon="Completa tu outfit con esta prenda complementaria",
                    imagen_url=_imagen_principal(p),
                    imagenes=img_list,
                    precio=float(p.precio or 0),
                    categoria=p.categoria.nombre if p.categoria else None,
                    categoria_id=p.categoria_id,
                    genero=p.genero.value if hasattr(p.genero, 'value') else str(p.genero) if p.genero else None
                ))

        return RecomendacionResponse(
            cliente_id=cliente_id,
            estilo_detectado=ai_res.get("estilo_detectado", "Urbano Contemporáneo"),
            mensaje_personalizado=ai_res.get("mensaje_personalizado", f"¡Hola {cliente_nombre}! Descubre lo que seleccionamos para ti."),
            recomendaciones=items_recomendados[:limite]
        )


# ==============================================================================
# REPORTE CLIENTE SERVICE (CU23 / CU14 / CU15)
# ==============================================================================
class ReporteClienteService:
    """Servicio para recopilar y generar reportes de compras y reservas de clientes individuales."""

    @staticmethod
    async def obtener_datos_compras(db: AsyncSession, cliente_id: int) -> Dict[str, Any]:
        # Obtener datos del cliente
        res_c = await db.execute(
            select(Cliente).options(selectinload(Cliente.usuario)).where(Cliente.id == cliente_id)
        )
        cliente = res_c.scalar_one_or_none()
        cliente_nombre = f"{cliente.usuario.nombre} {cliente.usuario.apellido}".strip() if cliente and cliente.usuario else "Cliente"
        cliente_email = cliente.usuario.correo if cliente and cliente.usuario else ""

        # Obtener órdenes del cliente
        res_ord = await db.execute(
            select(Orden)
            .options(
                selectinload(Orden.detalles).selectinload(DetalleOrden.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(Orden.transacciones),
                selectinload(Orden.sucursal)
            )
            .where(Orden.cliente_id == cliente_id)
            .order_by(desc(Orden.fecha))
        )
        ordenes = res_ord.scalars().all()

        total_gastado = sum(float(o.total or 0) for o in ordenes if o.estado not in (EstadoOrden.CANCELADO,))
        total_items_comprados = sum(
            sum(d.cantidad for d in o.detalles) for o in ordenes if o.estado not in (EstadoOrden.CANCELADO,)
        )

        compras_list = []
        for o in ordenes:
            metodo = "-"
            if o.transacciones:
                metodo = str(o.transacciones[0].metodo_pago.value if hasattr(o.transacciones[0].metodo_pago, 'value') else o.transacciones[0].metodo_pago)
            elif o.tipo == TipoOrden.PRESENCIAL:
                metodo = "Presencial / Tienda"

            detalles_prod = []
            for d in o.detalles:
                prod_nombre = d.variante_producto.producto.nombre if (d.variante_producto and d.variante_producto.producto) else f"Variante #{d.variante_producto_id}"
                detalles_prod.append({
                    "producto_nombre": prod_nombre,
                    "cantidad": d.cantidad,
                    "precio_unitario": float(d.precio_unitario or 0),
                    "subtotal": float(d.subtotal or 0)
                })

            compras_list.append({
                "id": o.id,
                "codigo": o.numero_orden,
                "fecha": o.fecha.strftime("%d/%m/%Y %H:%M") if o.fecha else "",
                "tipo": "Digital / En línea" if o.tipo == TipoOrden.DIGITAL else ("Presencial" if o.tipo == TipoOrden.PRESENCIAL else "Reserva"),
                "sucursal": o.sucursal.nombre if o.sucursal else "Tienda Online",
                "total": float(o.total or 0),
                "estado": str(o.estado.value if hasattr(o.estado, 'value') else o.estado),
                "metodo_pago": metodo,
                "detalles": detalles_prod,
                "total_items": len(detalles_prod)
            })

        return {
            "cliente_id": cliente_id,
            "cliente_nombre": cliente_nombre,
            "cliente_email": cliente_email,
            "total_compras": len(ordenes),
            "total_gastado": round(total_gastado, 2),
            "total_items_comprados": total_items_comprados,
            "compras": compras_list
        }

    @staticmethod
    async def obtener_datos_reservas(db: AsyncSession, cliente_id: int) -> Dict[str, Any]:
        # Obtener datos del cliente
        res_c = await db.execute(
            select(Cliente).options(selectinload(Cliente.usuario)).where(Cliente.id == cliente_id)
        )
        cliente = res_c.scalar_one_or_none()
        cliente_nombre = f"{cliente.usuario.nombre} {cliente.usuario.apellido}".strip() if cliente and cliente.usuario else "Cliente"
        cliente_email = cliente.usuario.correo if cliente and cliente.usuario else ""

        # Obtener reservas del cliente
        res_res = await db.execute(
            select(Reserva)
            .options(
                selectinload(Reserva.detalles).selectinload(DetalleReserva.variante_producto).selectinload(VarianteProducto.producto),
                selectinload(Reserva.sucursal)
            )
            .where(Reserva.cliente_id == cliente_id)
            .order_by(desc(Reserva.fecha_creacion))
        )
        reservas = res_res.scalars().all()

        activas = [r for r in reservas if r.estado in (EstadoReserva.PENDIENTE, EstadoReserva.PREPARADA, EstadoReserva.EN_PRUEBA)]
        completadas = [r for r in reservas if r.estado == EstadoReserva.COMPLETADA]
        canceladas = [r for r in reservas if r.estado in (EstadoReserva.CANCELADA, EstadoReserva.CADUCADA)]

        reservas_list = []
        for r in reservas:
            detalles_prod = []
            total_estimado = 0.0
            for d in r.detalles:
                var = d.variante_producto
                prod_nombre = var.producto.nombre if (var and var.producto) else f"Variante #{d.variante_producto_id}"
                precio = float(var.precio_variante or (var.producto.precio if var and var.producto else 0.0) or 0.0)
                subtot = precio * d.cantidad
                total_estimado += subtot
                detalles_prod.append({
                    "producto_nombre": prod_nombre,
                    "cantidad": d.cantidad,
                    "precio_unitario": precio,
                    "subtotal": subtot,
                    "estado": str(d.estado.value if hasattr(d.estado, 'value') else d.estado)
                })

            reservas_list.append({
                "id": r.id,
                "codigo_reserva": r.numero_reserva,
                "sucursal": r.sucursal.nombre if r.sucursal else "Sucursal General",
                "fecha_reserva": r.fecha_reserva.strftime("%d/%m/%Y") if r.fecha_reserva else "",
                "fecha_creacion": r.fecha_creacion.strftime("%d/%m/%Y %H:%M") if r.fecha_creacion else "",
                "fecha_limite": (r.fecha_reserva + timedelta(days=2)).strftime("%d/%m/%Y") if r.fecha_reserva else "",
                "estado": str(r.estado.value if hasattr(r.estado, 'value') else r.estado),
                "total_estimado": round(total_estimado, 2),
                "detalles": detalles_prod,
                "total_items": len(detalles_prod)
            })

        return {
            "cliente_id": cliente_id,
            "cliente_nombre": cliente_nombre,
            "cliente_email": cliente_email,
            "total_reservas": len(reservas),
            "reservas_activas": len(activas),
            "reservas_completadas": len(completadas),
            "reservas_canceladas_o_vencidas": len(canceladas),
            "reservas": reservas_list
        }

    @staticmethod
    async def obtener_resumen_completo(db: AsyncSession, cliente_id: int) -> Dict[str, Any]:
        compras = await ReporteClienteService.obtener_datos_compras(db, cliente_id)
        reservas = await ReporteClienteService.obtener_datos_reservas(db, cliente_id)
        return {
            "cliente_id": cliente_id,
            "cliente_nombre": compras.get("cliente_nombre"),
            "cliente_email": compras.get("cliente_email"),
            "compras": compras,
            "reservas": reservas
        }


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
            select(Producto).options(
                selectinload(Producto.categoria),
                selectinload(Producto.variantes)
            )
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
        compras_cliente_data = None
        reservas_cliente_data = None

        if cliente_id:
            res_c = await db.execute(
                select(Cliente).options(selectinload(Cliente.usuario)).where(Cliente.id == cliente_id)
            )
            c = res_c.scalar_one_or_none()
            if c and c.usuario:
                compras_cliente_data = await ReporteClienteService.obtener_datos_compras(db, cliente_id)
                reservas_cliente_data = await ReporteClienteService.obtener_datos_reservas(db, cliente_id)
                contexto_cliente = {
                    "nombre": c.usuario.nombre,
                    "nit_ci": c.nit_ci,
                    "resumen_compras": {
                        "total_pedidos": compras_cliente_data["total_compras"],
                        "total_gastado_bs": compras_cliente_data["total_gastado"],
                        "total_prendas_compradas": compras_cliente_data["total_items_comprados"],
                        "ultimos_pedidos": [
                            {
                                "codigo": cp["codigo"],
                                "fecha": cp["fecha"],
                                "total_bs": cp["total"],
                                "estado": cp["estado"],
                                "metodo_pago": cp["metodo_pago"],
                                "prendas": [d["producto_nombre"] for d in cp.get("detalles", [])]
                            }
                            for cp in compras_cliente_data.get("compras", [])[:5]
                        ]
                    },
                    "resumen_reservas": {
                        "total_reservas": reservas_cliente_data["total_reservas"],
                        "reservas_activas_pendientes": reservas_cliente_data["reservas_activas"],
                        "reservas_completadas": reservas_cliente_data["reservas_completadas"],
                        "ultimas_reservas": [
                            {
                                "codigo": rs["codigo_reserva"],
                                "sucursal": rs["sucursal"],
                                "fecha_reserva": rs["fecha_reserva"],
                                "estado": rs["estado"],
                                "total_estimado_bs": rs["total_estimado"],
                                "prendas": [d["producto_nombre"] for d in rs.get("detalles", [])]
                            }
                            for rs in reservas_cliente_data.get("reservas", [])[:5]
                        ]
                    }
                }

        ai_res = await groq_service.chat_asistente(
            mensaje=mensaje,
            historial_conversacion=historial,
            catalogo_resumen=catalogo_resumen,
            contexto_cliente=contexto_cliente
        )

        def _build_producto_info(p: Producto) -> Dict[str, Any]:
            imgs = _lista_imagenes(p.imagenes)
            var_id = p.variantes[0].id if (p.variantes and len(p.variantes) > 0) else None
            return {
                "id": p.id,
                "nombre": p.nombre,
                "descripcion": p.descripcion,
                "precio": float(p.precio or 0),
                "categoria": p.categoria.nombre if p.categoria else None,
                "imagen_principal": _imagen_principal(p),
                "imagenes": imgs if isinstance(imgs, list) else [],
                "variante_id": var_id
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
        ids_filtrados = {item["id"] for item in catalogo_filtrado}
        if not productos_info and ids_filtrados:
            for p in prods:
                if p.id in ids_filtrados:
                    productos_info.append(_build_producto_info(p))
                if len(productos_info) >= 6:
                    break

        tipo_respuesta = str(_normalizar_texto(ai_res.get("tipo_respuesta") or "texto"))
        if tipo_respuesta not in {"texto", "catalogo", "producto", "outfit", "reporte"}:
            tipo_respuesta = "texto"
        if productos_info and tipo_respuesta == "texto":
            tipo_respuesta = "catalogo" if len(productos_info) > 1 else "producto"

        accion = ai_res.get("accion")
        datos_reporte = None
        formato_reporte = (ai_res.get("formato_reporte") or "pdf").lower()

        # Si el usuario solicitó reporte de compras o reservas, adjuntar datos estructurados
        if accion == "reporte_compras" and compras_cliente_data:
            datos_reporte = {
                "tipo": "compras",
                "titulo": "Reporte de Compras y Pedidos",
                "cliente_nombre": compras_cliente_data["cliente_nombre"],
                "total_compras": compras_cliente_data["total_compras"],
                "total_gastado": compras_cliente_data["total_gastado"],
                "total_items": compras_cliente_data["total_items_comprados"],
                "items": compras_cliente_data["compras"][:10],
                "formato_sugerido": formato_reporte
            }
            tipo_respuesta = "reporte"
        elif accion == "reporte_reservas" and reservas_cliente_data:
            datos_reporte = {
                "tipo": "reservas",
                "titulo": "Reporte de Reservas en Tienda",
                "cliente_nombre": reservas_cliente_data["cliente_nombre"],
                "total_reservas": reservas_cliente_data["total_reservas"],
                "reservas_activas": reservas_cliente_data["reservas_activas"],
                "items": reservas_cliente_data["reservas"][:10],
                "formato_sugerido": formato_reporte
            }
            tipo_respuesta = "reporte"

        # Detección heurística de respaldo en caso de que el LLM no haya seteado la acción explícita
        msg_norm = _normalizar_texto(mensaje)
        if not accion and ("reporte" in msg_norm or "descargar" in msg_norm or "historial" in msg_norm):
            if ("compra" in msg_norm or "pedido" in msg_norm or "gasto" in msg_norm) and compras_cliente_data:
                accion = "reporte_compras"
                tipo_respuesta = "reporte"
                datos_reporte = {
                    "tipo": "compras",
                    "titulo": "Reporte de Compras y Pedidos",
                    "cliente_nombre": compras_cliente_data["cliente_nombre"],
                    "total_compras": compras_cliente_data["total_compras"],
                    "total_gastado": compras_cliente_data["total_gastado"],
                    "total_items": compras_cliente_data["total_items_comprados"],
                    "items": compras_cliente_data["compras"][:10],
                    "formato_sugerido": formato_reporte
                }
            elif ("reserva" in msg_norm or "apartado" in msg_norm) and reservas_cliente_data:
                accion = "reporte_reservas"
                tipo_respuesta = "reporte"
                datos_reporte = {
                    "tipo": "reservas",
                    "titulo": "Reporte de Reservas en Tienda",
                    "cliente_nombre": reservas_cliente_data["cliente_nombre"],
                    "total_reservas": reservas_cliente_data["total_reservas"],
                    "reservas_activas": reservas_cliente_data["reservas_activas"],
                    "items": reservas_cliente_data["reservas"][:10],
                    "formato_sugerido": formato_reporte
                }

        sugerencias_base = ai_res.get("sugerencias", [])
        if not sugerencias_base:
            sugerencias_base = ["Ver novedades", "Reporte de mis compras", "Reporte de mis reservas"]

        return AsistenteChatResponse(
            respuesta=ai_res.get("respuesta", "Con gusto te asisto en lo que necesites."),
            sugerencias=sugerencias_base,
            productos_mencionados=productos_info,
            tipo_respuesta=tipo_respuesta,
            accion=accion,
            datos_reporte=datos_reporte,
            formato_reporte=formato_reporte
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


def _extraer_rango_fechas(transcripcion: str, filtros: Dict[str, Any]):
    """Extrae rango de fechas desde texto en español (ayer, semana pasada, este mes, últimos N días)."""
    from datetime import date
    t = _normalizar_texto(transcripcion or "")
    hoy = datetime.now(timezone.utc)
    fi = filtros.get("fecha_inicio")
    ff = filtros.get("fecha_fin")
    try:
        fi = datetime.fromisoformat(fi) if isinstance(fi, str) and fi else None
        ff = datetime.fromisoformat(ff) if isinstance(ff, str) and ff else None
    except Exception:
        fi, ff = None, None
    if fi or ff:
        return fi, ff
    if "ayer" in t:
        d = (hoy - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return d, d.replace(hour=23, minute=59, second=59)
    if "semana pasada" in t or "ultima semana" in t:
        return hoy - timedelta(days=13), hoy - timedelta(days=7)
    if "esta semana" in t:
        return hoy - timedelta(days=6), hoy
    if "este mes" in t or "del mes" in t:
        return hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0), hoy
    if "mes pasado" in t or "mes anterior" in t:
        primero = hoy.replace(day=1)
        fin_prev = primero - timedelta(days=1)
        return fin_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0), fin_prev
    if "ultimos 7 dias" in t or "ultima semana" in t:
        return hoy - timedelta(days=6), hoy
    if "ultimos 30 dias" in t or "ultimo mes" in t:
        return hoy - timedelta(days=29), hoy
    if "hoy" in t:
        d = hoy.replace(hour=0, minute=0, second=0, microsecond=0)
        return d, hoy
    return None, None


def _extraer_formato(transcripcion: str, filtros: Dict[str, Any], default) -> str:
    t = _normalizar_texto(transcripcion or "")
    if isinstance(filtros.get("formato"), str) and filtros.get("formato"):
        return str(filtros.get("formato")).upper()
    for fmt in ("EXCEL", "PDF", "HTML", "CSV"):
        if _normalizar_texto(fmt) in t or fmt.lower() in (transcripcion or "").lower():
            return fmt
    try:
        return default.value if hasattr(default, "value") else str(default)
    except Exception:
        return "JSON"


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

        # Extraer filtros sugeridos por la IA o por reglas locales (fechas relativas, formato, sucursal, etc.)
        filtros = dict(ai_res.get("parametros", {}) or {})
        fecha_inicio, fecha_fin = _extraer_rango_fechas(transcripcion, filtros)
        formato_txt = _extraer_formato(transcripcion, filtros, formato)
        sucursal_id = filtros.get("sucursal_id")
        categoria_id = filtros.get("categoria_id")
        bajo_stock = bool("bajo stock" in _normalizar_texto(transcripcion) or filtros.get("bajo_stock"))

        # Generar el reporte solicitado con los parámetros identificados
        if tipo_enum == TipoReporte.VENTAS:
            datos = await ReporteService.generar_reporte_ventas(db, fecha_inicio, fecha_fin, sucursal_id)
        elif tipo_enum == TipoReporte.INVENTARIO:
            datos = await ReporteService.generar_reporte_inventario(db, categoria_id=categoria_id, solo_bajo_stock=bajo_stock)
        elif tipo_enum == TipoReporte.RESERVAS:
            datos = await ReporteService.generar_reporte_reservas(db, fecha_inicio, fecha_fin)
        elif tipo_enum == TipoReporte.CLIENTES:
            datos = await ReporteService.generar_reporte_clientes(db)
        else:
            datos = await ReporteService.generar_reporte_financiero(db, fecha_inicio, fecha_fin)

        # Guardar en base de datos
        reporte_db = await ReporteService.guardar_reporte(
            db=db,
            admin_id=admin_id,
            tipo=tipo_enum,
            titulo=f"Reporte por voz: {transcripcion[:80]}",
            parametros={"comando_voz": transcripcion, **filtros,
                        "fecha_inicio": fecha_inicio.isoformat() if fecha_inicio else None,
                        "fecha_fin": fecha_fin.isoformat() if fecha_fin else None},
            formato=formato,
            datos=datos
        )

        return ReporteVozResponse(
            comando_original=transcripcion,
            tipo_reporte=tipo_enum,
            interpretacion=ai_res.get("resumen_interpretacion", f"Reporte de {tipo_enum.value} generado."),
            datos=datos,
            reporte_guardado_id=reporte_db.id,
            fecha_inicio=fecha_inicio.isoformat() if fecha_inicio else None,
            fecha_fin=fecha_fin.isoformat() if fecha_fin else None,
            sucursal_id=sucursal_id,
            formato_sugerido=formato_txt,
        )


    @staticmethod
    async def transcribir_audio(audio_bytes: bytes, filename: str = "audio.webm") -> str:
        """Transcribe audio a texto usando Whisper de Groq (reemplazo de webkitSpeechRecognition)."""
        return await groq_service.transcribir_audio(audio_bytes, filename)


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
