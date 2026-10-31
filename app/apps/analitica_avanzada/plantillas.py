"""[EXAMEN-2] Catálogo cerrado de plantillas de reportes (CU10).

La IA nunca escribe SQL libre: elige una de estas plantillas aprobadas y el
servicio rellena parámetros tipados. Todas las consultas incorporan
`tenant_id` de forma obligatoria (RN-009).
"""
from __future__ import annotations

from typing import Any


def _p(nombre: str, tipo: str, requerido: bool = False, por_defecto: Any = None) -> dict:
    return {"nombre": nombre, "tipo": tipo, "requerido": requerido, "por_defecto": por_defecto}


PLANTILLAS: list[dict] = [
    {
        "clave": "ventas_periodo",
        "nombre": "Ventas por período",
        "descripcion": "Total vendido y número de órdenes agrupados por día.",
        "categoria": "VENTAS",
        "palabras_clave": ["venta", "ventas", "ingreso", "facturación", "facturacion", "vendido"],
        "parametros": [_p("fecha_inicio", "fecha"), _p("fecha_fin", "fecha")],
        "columnas": ["fecha", "ordenes", "total"],
        "visualizacion": "LINEAS",
        "sql": """
            SELECT DATE(o.fecha) AS fecha,
                   COUNT(*) AS ordenes,
                   COALESCE(SUM(o.total), 0) AS total
            FROM ordenes o
            WHERE o.tenant_id = :tenant_id
              AND o.es_sintetico = false
              AND o.fecha >= :fecha_inicio
              AND o.fecha < :fecha_fin
            GROUP BY DATE(o.fecha)
            ORDER BY fecha
        """,
    },
    {
        "clave": "ventas_sucursal",
        "nombre": "Ventas por sucursal",
        "descripcion": "Total vendido por cada sucursal del tenant.",
        "categoria": "VENTAS",
        "palabras_clave": ["sucursal", "sucursales", "tienda", "local"],
        "parametros": [_p("fecha_inicio", "fecha"), _p("fecha_fin", "fecha")],
        "columnas": ["sucursal", "ordenes", "total"],
        "visualizacion": "BARRAS",
        "sql": """
            SELECT s.nombre AS sucursal,
                   COUNT(o.id) AS ordenes,
                   COALESCE(SUM(o.total), 0) AS total
            FROM ordenes o
            JOIN sucursales s ON s.id = o.sucursal_id
            WHERE o.tenant_id = :tenant_id
              AND o.es_sintetico = false
              AND o.fecha >= :fecha_inicio
              AND o.fecha < :fecha_fin
            GROUP BY s.nombre
            ORDER BY total DESC
        """,
    },
    {
        "clave": "ventas_categoria",
        "nombre": "Ventas por categoría",
        "descripcion": "Unidades y monto vendido por categoría de producto.",
        "categoria": "VENTAS",
        "palabras_clave": ["categoría", "categoria", "categorias", "rubro"],
        "parametros": [_p("fecha_inicio", "fecha"), _p("fecha_fin", "fecha")],
        "columnas": ["categoria", "unidades", "total"],
        "visualizacion": "CIRCULAR",
        "sql": """
            SELECT c.nombre AS categoria,
                   SUM(d.cantidad) AS unidades,
                   COALESCE(SUM(d.subtotal), 0) AS total
            FROM detalles_orden d
            JOIN ordenes o ON o.id = d.orden_id
            JOIN variantes_producto v ON v.id = d.variante_producto_id
            JOIN productos p ON p.id = v.producto_id
            JOIN categorias c ON c.id = p.categoria_id
            WHERE o.tenant_id = :tenant_id
              AND o.es_sintetico = false
              AND o.fecha >= :fecha_inicio
              AND o.fecha < :fecha_fin
            GROUP BY c.nombre
            ORDER BY total DESC
        """,
    },
    {
        "clave": "top_productos",
        "nombre": "Top N productos",
        "descripcion": "Productos con más unidades vendidas en el período.",
        "categoria": "PRODUCTOS",
        "palabras_clave": ["top", "producto", "productos", "más vendido", "mas vendido", "ranking"],
        "parametros": [_p("fecha_inicio", "fecha"), _p("fecha_fin", "fecha"), _p("limite", "entero", por_defecto=10)],
        "columnas": ["producto", "unidades", "total"],
        "visualizacion": "BARRAS",
        "sql": """
            SELECT p.nombre AS producto,
                   SUM(d.cantidad) AS unidades,
                   COALESCE(SUM(d.subtotal), 0) AS total
            FROM detalles_orden d
            JOIN ordenes o ON o.id = d.orden_id
            JOIN variantes_producto v ON v.id = d.variante_producto_id
            JOIN productos p ON p.id = v.producto_id
            WHERE o.tenant_id = :tenant_id
              AND o.es_sintetico = false
              AND o.fecha >= :fecha_inicio
              AND o.fecha < :fecha_fin
            GROUP BY p.nombre
            ORDER BY unidades DESC
            LIMIT :limite
        """,
    },
    {
        "clave": "top_clientes",
        "nombre": "Top N clientes",
        "descripcion": "Clientes con mayor monto comprado en el período.",
        "categoria": "CLIENTES",
        "palabras_clave": ["cliente", "clientes", "mejores", "top"],
        "parametros": [_p("fecha_inicio", "fecha"), _p("fecha_fin", "fecha"), _p("limite", "entero", por_defecto=10)],
        "columnas": ["cliente", "ordenes", "total"],
        "visualizacion": "TABLA",
        "sql": """
            SELECT COALESCE(cl.nit_ci, CONCAT('Cliente #', cl.id)) AS cliente,
                   COUNT(o.id) AS ordenes,
                   COALESCE(SUM(o.total), 0) AS total
            FROM ordenes o
            JOIN clientes cl ON cl.id = o.cliente_id
            WHERE o.tenant_id = :tenant_id
              AND o.es_sintetico = false
              AND o.fecha >= :fecha_inicio
              AND o.fecha < :fecha_fin
            GROUP BY cl.id, cl.nit_ci
            ORDER BY total DESC
            LIMIT :limite
        """,
    },
    {
        "clave": "stock_critico",
        "nombre": "Stock crítico",
        "descripcion": "Variantes por debajo del stock mínimo en cada sucursal.",
        "categoria": "INVENTARIO",
        "palabras_clave": ["stock", "inventario", "crítico", "critico", "bajo", "mínimo", "minimo"],
        "parametros": [_p("sucursal_id", "entero")],
        "columnas": ["producto", "sucursal", "disponible", "minimo"],
        "visualizacion": "TABLA",
        "sql": """
            SELECT p.nombre AS producto,
                   s.nombre AS sucursal,
                   (i.cantidad - i.cantidad_reservada) AS disponible,
                   i.stock_minimo AS minimo
            FROM inventario i
            JOIN variantes_producto v ON v.id = i.variante_producto_id
            JOIN productos p ON p.id = v.producto_id
            JOIN sucursales s ON s.id = i.sucursal_id
            WHERE i.tenant_id = :tenant_id
              AND (i.cantidad - i.cantidad_reservada) <= i.stock_minimo
              AND (:sucursal_id IS NULL OR i.sucursal_id = :sucursal_id)
            ORDER BY disponible ASC
        """,
    },
    {
        "clave": "reservas_pendientes",
        "nombre": "Reservas pendientes",
        "descripcion": "Reservas activas que todavía no se completaron.",
        "categoria": "OPERACION",
        "palabras_clave": ["reserva", "reservas", "pendiente", "pendientes"],
        "parametros": [_p("sucursal_id", "entero")],
        "columnas": ["numero_reserva", "cliente", "sucursal", "fecha_reserva", "estado"],
        "visualizacion": "TABLA",
        "sql": """
            SELECT r.numero_reserva AS numero_reserva,
                   COALESCE(cl.nit_ci, CONCAT('Cliente #', cl.id)) AS cliente,
                   s.nombre AS sucursal,
                   r.fecha_reserva AS fecha_reserva,
                   CAST(r.estado AS TEXT) AS estado
            FROM reservas r
            JOIN sucursales s ON s.id = r.sucursal_id
            LEFT JOIN clientes cl ON cl.id = r.cliente_id
            WHERE r.tenant_id = :tenant_id
              AND r.estado IN ('PENDIENTE', 'PREPARADA', 'EN_PRUEBA')
              AND (:sucursal_id IS NULL OR r.sucursal_id = :sucursal_id)
            ORDER BY r.fecha_reserva ASC
        """,
    },
    {
        "clave": "devoluciones_periodo",
        "nombre": "Devoluciones y cambios del período",
        "descripcion": "Solicitudes de devolución/cambio registradas en el período.",
        "categoria": "DEVOLUCIONES",
        "palabras_clave": ["devolución", "devolucion", "devoluciones", "cambio", "cambios"],
        "parametros": [_p("fecha_inicio", "fecha"), _p("fecha_fin", "fecha")],
        "columnas": ["numero_solicitud", "tipo", "estado", "fecha_solicitud", "monto_reembolso"],
        "visualizacion": "TABLA",
        "sql": """
            SELECT d.numero_solicitud AS numero_solicitud,
                   CAST(d.tipo AS TEXT) AS tipo,
                   CAST(d.estado AS TEXT) AS estado,
                   d.fecha_solicitud AS fecha_solicitud,
                   d.monto_reembolso AS monto_reembolso
            FROM solicitudes_devolucion d
            WHERE d.tenant_id = :tenant_id
              AND d.fecha_solicitud >= :fecha_inicio
              AND d.fecha_solicitud < :fecha_fin
            ORDER BY d.fecha_solicitud DESC
        """,
    },
    {
        "clave": "comparativa_sucursales",
        "nombre": "Comparativa entre sucursales",
        "descripcion": "Ventas, ticket promedio y variación entre sucursales.",
        "categoria": "VENTAS",
        "palabras_clave": ["comparativa", "comparar", "sucursales", "entre"],
        "parametros": [_p("fecha_inicio", "fecha"), _p("fecha_fin", "fecha")],
        "columnas": ["sucursal", "ordenes", "total", "ticket_promedio"],
        "visualizacion": "BARRAS",
        "sql": """
            SELECT s.nombre AS sucursal,
                   COUNT(o.id) AS ordenes,
                   COALESCE(SUM(o.total), 0) AS total,
                   COALESCE(AVG(o.total), 0) AS ticket_promedio
            FROM ordenes o
            JOIN sucursales s ON s.id = o.sucursal_id
            WHERE o.tenant_id = :tenant_id
              AND o.es_sintetico = false
              AND o.fecha >= :fecha_inicio
              AND o.fecha < :fecha_fin
            GROUP BY s.nombre
            ORDER BY total DESC
        """,
    },
    {
        "clave": "prendas_mas_cambiadas",
        "nombre": "Prendas más cambiadas",
        "descripcion": "Variantes con más unidades devueltas o cambiadas.",
        "categoria": "DEVOLUCIONES",
        "palabras_clave": ["prenda", "prendas", "cambiada", "cambiadas", "más cambiada", "mas cambiada"],
        "parametros": [_p("limite", "entero", por_defecto=10)],
        "columnas": ["producto", "variante", "unidades_devueltas"],
        "visualizacion": "BARRAS",
        "sql": """
            SELECT p.nombre AS producto,
                   CONCAT(COALESCE(t.valor, ''), ' / ', COALESCE(c.nombre, '')) AS variante,
                   SUM(ds.cantidad) AS unidades_devueltas
            FROM detalles_solicitud_devolucion ds
            JOIN solicitudes_devolucion sd ON sd.id = ds.solicitud_id
            JOIN variantes_producto v ON v.id = ds.variante_producto_id
            JOIN productos p ON p.id = v.producto_id
            LEFT JOIN tallas t ON t.id = v.talla_id
            LEFT JOIN colores c ON c.id = v.color_id
            WHERE ds.tenant_id = :tenant_id
            GROUP BY p.nombre, t.valor, c.nombre
            ORDER BY unidades_devueltas DESC
            LIMIT :limite
        """,
    },
]


def obtener_plantilla(clave: str) -> dict | None:
    for plantilla in PLANTILLAS:
        if plantilla["clave"] == clave:
            return plantilla
    return None


def claves_plantillas() -> list[str]:
    return [p["clave"] for p in PLANTILLAS]
