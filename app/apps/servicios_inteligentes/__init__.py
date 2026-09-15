"""
App 4: Servicios Inteligentes, Reportes y KPIs (Iteración 3).
Incluye:
- Modelos: Reporte, IndicadorKPI
- Servicios: ReporteService, KPIService, RecomendacionService, AsistenteService, VestidorVirtualService, ReporteVozService, TendenciasService
- Router con endpoints para CU07, CU13, CU15, CU20, CU21, CU22, CU23
"""
from app.apps.servicios_inteligentes.models import Reporte, IndicadorKPI, TipoReporte, FormatoReporte
from app.apps.servicios_inteligentes.router import router

__all__ = [
    "Reporte",
    "IndicadorKPI",
    "TipoReporte",
    "FormatoReporte",
    "router"
]