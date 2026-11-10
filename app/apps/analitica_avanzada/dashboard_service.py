"""[EXAMEN-2] Dashboard analítico con caché y alcance por rol (CU12).

KPIs del plan 9.3.6 sobre datos reales del tenant (excluye `es_sintetico`).
La caché vive en proceso con TTL de `DASHBOARD_CACHE_TTL_SEG` (5 minutos).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.apps.gestion_catalogo.models import Inventario, Producto, Talla, Color, VarianteProducto
from app.apps.gestion_usuarios.models import Cajero, EncargadoSucursal
from app.apps.gestion_ventas.models import (
    DetalleSolicitudDevolucion,
    EstadoReserva,
    Orden,
    Reserva,
    SolicitudDevolucion,
    TipoSolicitudDevolucion,
)
from app.apps.operaciones_offline.models import (
    Compensacion,
    EstadoCompensacion,
)

_CACHE: dict[tuple, dict[str, Any]] = {}
_WIDGETS = {
    "Administrador": [
        "ventas_dia", "ventas_mes", "stock_critico", "reservas_pendientes",
        "tasa_devoluciones", "tasa_reposiciones", "prendas_repuestas",
        "monto_compensaciones", "top_cambiadas", "ticket_promedio",
    ],
    "Encargado": [
        "ventas_dia", "ventas_mes", "stock_critico", "reservas_pendientes",
        "ticket_promedio", "top_cambiadas",
    ],
    "Cajero": ["ventas_dia", "stock_critico", "reservas_pendientes"],
}


def limpiar_cache() -> None:
    """Limpia la caché en memoria (pruebas e inicio de proceso)."""
    _CACHE.clear()


def _f(valor) -> float:
    if valor is None:
        return 0.0
    return float(valor)


class DashboardService:
    """Cálculo de KPIs, caché y widgets permitidos por rol."""

    @classmethod
    def obtener_widgets_por_rol(cls, rol: str | None, sucursal_id: int | None = None) -> list[str]:
        widgets = list(_WIDGETS.get(rol or "Cajero", _WIDGETS["Cajero"]))
        if sucursal_id is None and rol in ("Administrador", "Encargado"):
            return widgets
        return widgets

    @classmethod
    async def _sucursal_del_usuario(cls, db: AsyncSession, usuario) -> int | None:
        if usuario is None:
            return None
        for modelo in (EncargadoSucursal, Cajero):
            result = await db.execute(
                select(modelo.sucursal_id).where(modelo.usuario_id == usuario.id)
            )
            sucursal = result.scalar_one_or_none()
            if sucursal is not None:
                return sucursal
        return None

    @classmethod
    def _roles(cls, usuario) -> list[str]:
        return [getattr(r, "nombre", r) for r in (getattr(usuario, "roles", None) or [])]

    @classmethod
    def _rol_principal(cls, usuario) -> str:
        roles = cls._roles(usuario)
        for preferido in ("Administrador", "Encargado", "Cajero"):
            if preferido in roles:
                return preferido
        if getattr(usuario, "es_super_admin", False):
            return "Administrador"
        return "Cajero"

    @classmethod
    def calcular_variacion(cls, actual: float, anterior: float) -> float:
        if anterior in (None, 0):
            return 100.0 if actual else 0.0
        return round(((actual - anterior) / anterior) * 100, 2)

    @classmethod
    def inicio_periodo(cls, periodo: str | None = None) -> date:
        """Inicio del período consultado (CU12, paso 3).

        DIA = hoy, SEMANA = últimos 7 días, MES = día 1 del mes,
        ANIO = 1 de enero. Sin período o valor desconocido, MES.
        """
        hoy = date.today()
        p = (periodo or "MES").upper()
        if p == "DIA":
            return hoy
        if p == "SEMANA":
            return hoy - timedelta(days=6)
        if p == "ANIO":
            return hoy.replace(month=1, day=1)
        return hoy.replace(day=1)

    @classmethod
    async def _ventas_rango(
        cls, db: AsyncSession, tenant_id: int, desde: date, hasta: date, sucursal_id: int | None
    ) -> dict:
        condiciones = [
            Orden.tenant_id == tenant_id,
            Orden.es_sintetico.is_(False),
            func.date(Orden.fecha) >= desde,
            func.date(Orden.fecha) <= hasta,
        ]
        if sucursal_id is not None:
            condiciones.append(Orden.sucursal_id == sucursal_id)
        result = await db.execute(
            select(
                func.coalesce(func.sum(Orden.total), 0),
                func.count(Orden.id),
            ).where(*condiciones)
        )
        total, ordenes = result.one()
        return {"total": _f(total), "ordenes": int(ordenes or 0)}

    @classmethod
    async def calcular_ventas_dia(
        cls, db: AsyncSession, tenant_id: int, sucursal_id: int | None = None
    ) -> dict:
        hoy = date.today()
        return await cls._ventas_rango(db, tenant_id, hoy, hoy, sucursal_id)

    @classmethod
    async def calcular_ventas_mes(
        cls, db: AsyncSession, tenant_id: int, sucursal_id: int | None = None
    ) -> dict:
        hoy = date.today()
        inicio = hoy.replace(day=1)
        actual = await cls._ventas_rango(db, tenant_id, inicio, hoy, sucursal_id)
        fin_anterior = inicio - timedelta(days=1)
        inicio_anterior = fin_anterior.replace(day=1)
        anterior = await cls._ventas_rango(db, tenant_id, inicio_anterior, fin_anterior, sucursal_id)
        return {
            "actual": actual,
            "anterior": anterior,
            "variacion_pct": cls.calcular_variacion(actual["total"], anterior["total"]),
        }

    @classmethod
    async def calcular_stock_critico(
        cls, db: AsyncSession, tenant_id: int, sucursal_id: int | None = None
    ) -> dict:
        condiciones = [
            Inventario.tenant_id == tenant_id,
            (Inventario.cantidad - Inventario.cantidad_reservada) <= Inventario.stock_minimo,
        ]
        if sucursal_id is not None:
            condiciones.append(Inventario.sucursal_id == sucursal_id)
        result = await db.execute(select(func.count(Inventario.id)).where(*condiciones))
        return {"variantes": int(result.scalar() or 0)}

    @classmethod
    async def calcular_reservas_pendientes(
        cls, db: AsyncSession, tenant_id: int, sucursal_id: int | None = None
    ) -> dict:
        condiciones = [
            Reserva.tenant_id == tenant_id,
            Reserva.estado.in_([EstadoReserva.PENDIENTE, EstadoReserva.PREPARADA, EstadoReserva.EN_PRUEBA]),
        ]
        if sucursal_id is not None:
            condiciones.append(Reserva.sucursal_id == sucursal_id)
        result = await db.execute(select(func.count(Reserva.id)).where(*condiciones))
        return {"reservas": int(result.scalar() or 0)}

    @classmethod
    async def calcular_tasa_devoluciones(
        cls, db: AsyncSession, tenant_id: int, periodo: str | None = None, sucursal_id: int | None = None
    ) -> dict:
        hoy = date.today()
        inicio = cls.inicio_periodo(periodo)
        condiciones = [
            SolicitudDevolucion.tenant_id == tenant_id,
            func.date(SolicitudDevolucion.fecha_solicitud) >= inicio,
            SolicitudDevolucion.tipo.in_([
                TipoSolicitudDevolucion.DEVOLUCION,
                TipoSolicitudDevolucion.CAMBIO,
            ]),
        ]
        if sucursal_id is not None:
            condiciones.append(SolicitudDevolucion.sucursal_id == sucursal_id)
        devoluciones = await db.execute(
            select(func.count(SolicitudDevolucion.id)).where(*condiciones)
        )
        ventas = await cls._ventas_rango(db, tenant_id, inicio, hoy, sucursal_id)
        total_dev = int(devoluciones.scalar() or 0)
        base = ventas["ordenes"] or 0
        return {
            "devoluciones": total_dev,
            "ventas": base,
            "tasa_pct": round((total_dev / base) * 100, 2) if base else 0.0,
        }

    @classmethod
    async def calcular_tasa_reposiciones(
        cls, db: AsyncSession, tenant_id: int, periodo: str | None = None, sucursal_id: int | None = None
    ) -> dict:
        """[EXAMEN-2] Punto 4: reposiciones del período sobre las ventas."""
        hoy = date.today()
        inicio = cls.inicio_periodo(periodo)
        condiciones = [
            SolicitudDevolucion.tenant_id == tenant_id,
            SolicitudDevolucion.tipo == TipoSolicitudDevolucion.REPOSICION,
            func.date(SolicitudDevolucion.fecha_solicitud) >= inicio,
        ]
        if sucursal_id is not None:
            condiciones.append(SolicitudDevolucion.sucursal_id == sucursal_id)
        reposiciones = (await db.execute(
            select(func.count(SolicitudDevolucion.id)).where(*condiciones)
        )).scalar() or 0
        ventas = await cls._ventas_rango(db, tenant_id, inicio, hoy, sucursal_id)
        base = ventas["ordenes"] or 0
        return {
            "reposiciones": int(reposiciones),
            "ventas": base,
            "tasa_pct": round((int(reposiciones) / base) * 100, 2) if base else 0.0,
        }

    @classmethod
    async def calcular_monto_compensaciones(
        cls, db: AsyncSession, tenant_id: int, periodo: str | None = None, sucursal_id: int | None = None
    ) -> dict:
        """[EXAMEN-2] Punto 4: dinero compensado por ventas offline rechazadas."""
        hoy = date.today()
        inicio = cls.inicio_periodo(periodo)
        condiciones = [
            Compensacion.tenant_id == tenant_id,
            func.date(Compensacion.fecha_creacion) >= inicio,
        ]
        consulta = (
            select(Compensacion.estado, func.count(Compensacion.id), func.sum(Compensacion.monto))
            .where(*condiciones)
            .group_by(Compensacion.estado)
        )
        if sucursal_id is not None:
            # La compensación no guarda sucursal: se filtra por la orden asociada.
            consulta = (
                select(Compensacion.estado, func.count(Compensacion.id), func.sum(Compensacion.monto))
                .outerjoin(Orden, Orden.id == Compensacion.orden_id)
                .where(*condiciones, Orden.sucursal_id == sucursal_id)
                .group_by(Compensacion.estado)
            )
        filas = (await db.execute(consulta)).all()
        por_estado = {
            (estado.value if hasattr(estado, "value") else str(estado)): {
                "cantidad": int(total or 0),
                "monto": round(_f(monto), 2),
            }
            for estado, total, monto in filas
        }
        total = round(sum(v["monto"] for v in por_estado.values()), 2)
        pendientes = sum(
            v["monto"] for k, v in por_estado.items()
            if k in {EstadoCompensacion.PENDIENTE.value, EstadoCompensacion.FALLIDA.value}
        )
        return {
            "monto_total": total,
            "monto_pendiente": round(pendientes, 2),
            "cantidad": sum(v["cantidad"] for v in por_estado.values()),
            "por_estado": por_estado,
        }

    @classmethod
    async def calcular_prendas_repuestas(
        cls, db: AsyncSession, tenant_id: int, limite: int = 5,
        sucursal_id: int | None = None, periodo: str | None = None,
    ) -> list[dict]:
        """[EXAMEN-2] Punto 4: top de prendas repuestas (solo REPOSICION)."""
        inicio = cls.inicio_periodo(periodo) if periodo else None
        condiciones = [
            DetalleSolicitudDevolucion.tenant_id == tenant_id,
            SolicitudDevolucion.tipo == TipoSolicitudDevolucion.REPOSICION,
        ]
        if inicio is not None:
            condiciones.append(func.date(SolicitudDevolucion.fecha_solicitud) >= inicio)
        if sucursal_id is not None:
            condiciones.append(SolicitudDevolucion.sucursal_id == sucursal_id)
        result = await db.execute(
            select(
                Producto.nombre,
                Talla.valor,
                Color.nombre,
                func.sum(DetalleSolicitudDevolucion.cantidad),
            )
            .join(SolicitudDevolucion, SolicitudDevolucion.id == DetalleSolicitudDevolucion.solicitud_id)
            .join(VarianteProducto, VarianteProducto.id == DetalleSolicitudDevolucion.variante_producto_id)
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .outerjoin(Talla, Talla.id == VarianteProducto.talla_id)
            .outerjoin(Color, Color.id == VarianteProducto.color_id)
            .where(*condiciones)
            .group_by(Producto.nombre, Talla.valor, Color.nombre)
            .order_by(func.sum(DetalleSolicitudDevolucion.cantidad).desc())
            .limit(min(int(limite), 20))
        )
        return [
            {"producto": fila[0], "talla": fila[1], "color": fila[2], "unidades": int(fila[3] or 0)}
            for fila in result.all()
        ]

    @classmethod
    async def calcular_top_cambiadas(
        cls, db: AsyncSession, tenant_id: int, limite: int = 5,
        sucursal_id: int | None = None, periodo: str | None = None,
    ) -> list[dict]:
        inicio = cls.inicio_periodo(periodo) if periodo else None
        condiciones = [DetalleSolicitudDevolucion.tenant_id == tenant_id]
        if inicio is not None:
            condiciones.append(func.date(SolicitudDevolucion.fecha_solicitud) >= inicio)
        if sucursal_id is not None:
            condiciones.append(SolicitudDevolucion.sucursal_id == sucursal_id)
        result = await db.execute(
            select(
                Producto.nombre,
                Talla.valor,
                Color.nombre,
                func.sum(DetalleSolicitudDevolucion.cantidad),
            )
            .join(SolicitudDevolucion, SolicitudDevolucion.id == DetalleSolicitudDevolucion.solicitud_id)
            .join(VarianteProducto, VarianteProducto.id == DetalleSolicitudDevolucion.variante_producto_id)
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .outerjoin(Talla, Talla.id == VarianteProducto.talla_id)
            .outerjoin(Color, Color.id == VarianteProducto.color_id)
            .where(*condiciones)
            .group_by(Producto.nombre, Talla.valor, Color.nombre)
            .order_by(func.sum(DetalleSolicitudDevolucion.cantidad).desc())
            .limit(min(int(limite), 20))
        )
        return [
            {"producto": fila[0], "talla": fila[1], "color": fila[2], "unidades": int(fila[3] or 0)}
            for fila in result.all()
        ]

    @classmethod
    async def calcular_ticket_promedio(
        cls, db: AsyncSession, tenant_id: int, periodo: str | None = None, sucursal_id: int | None = None
    ) -> dict:
        hoy = date.today()
        inicio = cls.inicio_periodo(periodo)
        condiciones = [
            Orden.tenant_id == tenant_id,
            Orden.es_sintetico.is_(False),
            func.date(Orden.fecha) >= inicio,
        ]
        if sucursal_id is not None:
            condiciones.append(Orden.sucursal_id == sucursal_id)
        result = await db.execute(
            select(func.coalesce(func.avg(Orden.total), 0)).where(*condiciones)
        )
        return {"ticket_promedio": round(_f(result.scalar()), 2)}

    @classmethod
    async def calcular_kpis(
        cls,
        db: AsyncSession,
        tenant_id: int,
        sucursal_id: int | None = None,
        periodo: str | None = None,
    ) -> dict:
        return {
            "ventas_dia": await cls.calcular_ventas_dia(db, tenant_id, sucursal_id),
            "ventas_mes": await cls.calcular_ventas_mes(db, tenant_id, sucursal_id),
            "stock_critico": await cls.calcular_stock_critico(db, tenant_id, sucursal_id),
            "reservas_pendientes": await cls.calcular_reservas_pendientes(db, tenant_id, sucursal_id),
            "tasa_devoluciones": await cls.calcular_tasa_devoluciones(db, tenant_id, periodo, sucursal_id),
            "tasa_reposiciones": await cls.calcular_tasa_reposiciones(db, tenant_id, periodo, sucursal_id),
            "prendas_repuestas": await cls.calcular_prendas_repuestas(db, tenant_id, 5, sucursal_id, periodo),
            "monto_compensaciones": await cls.calcular_monto_compensaciones(db, tenant_id, periodo, sucursal_id),
            "top_cambiadas": await cls.calcular_top_cambiadas(db, tenant_id, 5, sucursal_id, periodo),
            "ticket_promedio": await cls.calcular_ticket_promedio(db, tenant_id, periodo, sucursal_id),
        }

    # ------------------------------------------------------------------
    # Caché
    # ------------------------------------------------------------------

    @classmethod
    def obtener_cache(
        cls, tenant_id: int, sucursal_id: int | None, periodo: str | None
    ) -> dict | None:
        clave = (tenant_id, sucursal_id, periodo or "MES")
        entrada = _CACHE.get(clave)
        if not entrada:
            return None
        if datetime.utcnow() - entrada["guardado_en"] > timedelta(seconds=settings.DASHBOARD_CACHE_TTL_SEG):
            return None
        return entrada["datos"]

    @classmethod
    def obtener_cache_fecha(
        cls, tenant_id: int, sucursal_id: int | None, periodo: str | None
    ) -> datetime | None:
        clave = (tenant_id, sucursal_id, periodo or "MES")
        entrada = _CACHE.get(clave)
        return entrada["guardado_en"] if entrada else None

    @classmethod
    def guardar_cache(cls, clave: tuple, datos: dict) -> None:
        _CACHE[clave] = {"datos": datos, "guardado_en": datetime.utcnow()}

    @classmethod
    def invalidar_cache(cls, tenant_id: int | None = None) -> None:
        if tenant_id is None:
            _CACHE.clear()
            return
        for clave in list(_CACHE):
            if clave[0] == tenant_id:
                _CACHE.pop(clave, None)

    @classmethod
    def obtener_ultimo_conocido(
        cls, tenant_id: int, sucursal_id: int | None = None, periodo: str | None = None
    ) -> dict | None:
        """Datos cacheados con su fecha de corte (flujo offline, CU12).

        Expira solo por TTL de memoria: re-ejecutar esta llamada tras el
        vencimiento retorna el último snapshot con ``desactualizado: True``
        para el banner «Datos actualizados al [hora]».
        """
        for clave, entrada in _CACHE.items():
            if clave[0] != tenant_id:
                continue
            if sucursal_id is not None and clave[1] != sucursal_id:
                continue
            if periodo is not None and clave[2] != (periodo or "MES").upper():
                continue
            # Sin periodo pedido, prioriza MES (contexto por defecto de la UI).
            if periodo is None and clave[2] != "MES":
                continue
            datos = dict(entrada["datos"])
            datos["fecha_corte"] = entrada["guardado_en"].isoformat()
            datos["desactualizado"] = True
            return datos
        # Sin coincidencia de periodo: fallback a cualquier snapshot del tenant.
        for clave, entrada in _CACHE.items():
            if clave[0] == tenant_id and (sucursal_id is None or clave[1] == sucursal_id):
                datos = dict(entrada["datos"])
                datos["fecha_corte"] = entrada["guardado_en"].isoformat()
                datos["desactualizado"] = True
                return datos
        return None

    @classmethod
    async def verificar_datos_suficientes(cls, db: AsyncSession, tenant_id: int) -> dict:
        result = await db.execute(
            select(func.min(Orden.fecha), func.count(Orden.id)).where(
                Orden.tenant_id == tenant_id,
                Orden.es_sintetico.is_(False),
            )
        )
        primera, total = result.one()
        if primera is None:
            return {"suficientes": False, "meses": 0, "ordenes": 0}
        if primera.tzinfo is None:
            primera = primera.replace(tzinfo=None)
        meses = max(0, int((datetime.utcnow() - primera.replace(tzinfo=None)).days / 30))
        return {
            "suficientes": meses >= settings.ML_MIN_MESES_HISTORIAL and int(total) >= 30,
            "meses": meses,
            "ordenes": int(total or 0),
            "minimo_meses": settings.ML_MIN_MESES_HISTORIAL,
        }

    @classmethod
    async def obtener_dashboard(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario=None,
        sucursal_id: int | None = None,
        periodo: str | None = None,
    ) -> dict:
        rol = cls._rol_principal(usuario)
        if rol in ("Encargado", "Cajero"):
            sucursal_id = await cls._sucursal_del_usuario(db, usuario) or sucursal_id

        clave = (tenant_id, sucursal_id, (periodo or "MES").upper())
        datos = cls.obtener_cache(*clave)
        cacheado = datos is not None
        if datos is None:
            todos = await cls.calcular_kpis(db, tenant_id, sucursal_id, periodo)
            permitidos = cls.obtener_widgets_por_rol(rol, sucursal_id)
            datos = {k: v for k, v in todos.items() if k in permitidos}
            cls.guardar_cache(clave, datos)

        fecha_cache = cls.obtener_cache_fecha(*clave)
        return {
            "rol": rol,
            "sucursal_id": sucursal_id,
            "periodo": (periodo or "MES").upper(),
            "widgets": datos,
            "fecha_corte": (fecha_cache or datetime.utcnow()).isoformat(),
            "cache": cacheado,
            "actualizacion_automatica_segundos": settings.DASHBOARD_CACHE_TTL_SEG,
            "datos_suficientes": await cls.verificar_datos_suficientes(db, tenant_id),
        }

    @classmethod
    async def obtener_drilldown(
        cls,
        db: AsyncSession,
        tenant_id: int,
        kpi: str,
        sucursal_id: int | None = None,
        periodo: str | None = None,
    ) -> dict:
        kpi = (kpi or "").lower()
        if kpi in ("ventas_dia", "ventas"):
            return {"kpi": kpi, **await cls.calcular_ventas_dia(db, tenant_id, sucursal_id)}
        if kpi in ("ventas_mes", "ventas_mensuales"):
            return {"kpi": kpi, **await cls.calcular_ventas_mes(db, tenant_id, sucursal_id)}
        if kpi in ("stock_critico", "stock"):
            return {"kpi": kpi, **await cls.calcular_stock_critico(db, tenant_id, sucursal_id)}
        if kpi in ("reservas_pendientes", "reservas"):
            return {"kpi": kpi, **await cls.calcular_reservas_pendientes(db, tenant_id, sucursal_id)}
        if kpi in ("tasa_devoluciones", "devoluciones"):
            return {"kpi": kpi, **await cls.calcular_tasa_devoluciones(db, tenant_id, periodo, sucursal_id)}
        if kpi in ("tasa_reposiciones", "reposiciones"):
            return {"kpi": kpi, **await cls.calcular_tasa_reposiciones(db, tenant_id, periodo, sucursal_id)}
        if kpi in ("prendas_repuestas", "repuestas"):
            return {"kpi": kpi, "items": await cls.calcular_prendas_repuestas(db, tenant_id, 5, sucursal_id, periodo)}
        if kpi in ("monto_compensaciones", "compensaciones"):
            return {"kpi": kpi, **await cls.calcular_monto_compensaciones(db, tenant_id, periodo, sucursal_id)}
        if kpi in ("top_cambiadas", "cambiadas"):
            return {"kpi": kpi, "items": await cls.calcular_top_cambiadas(db, tenant_id, 5, sucursal_id, periodo)}
        if kpi in ("ticket_promedio", "ticket"):
            return {"kpi": kpi, **await cls.calcular_ticket_promedio(db, tenant_id, periodo, sucursal_id)}
        return {"kpi": kpi, "detalle": [], "mensaje": "KPI no reconocido"}
