"""
Esquemas Pydantic para Servicios Inteligentes y Reportes.
Maneja validación y serialización de:
- Reportes dinámicos (Ventas, Inventario, Reservas, Clientes, Financiero)
- Indicadores KPI
- Recomendaciones de moda con IA
- Asistente virtual
- Vestidor virtual
- Procesamiento de comandos de voz
- Análisis de tendencias
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal
from app.apps.servicios_inteligentes.models import TipoReporte, FormatoReporte


# ==========================================
# REPORTES Y KPIS
# ==========================================

class ReporteBase(BaseModel):
    tipo: TipoReporte
    titulo: str = Field(..., max_length=200)
    parametros: Optional[Dict[str, Any]] = None
    formato: FormatoReporte = FormatoReporte.JSON


class ReporteCreate(ReporteBase):
    pass


class ReporteResponse(ReporteBase):
    id: int
    administrador_id: Optional[int] = None
    fecha_generacion: Optional[datetime] = None
    contenido: Optional[str] = None
    datos_resumen: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class IndicadorKPIBase(BaseModel):
    nombre: str = Field(..., max_length=100)
    descripcion: Optional[str] = None
    valor_actual: float = 0.0
    valor_objetivo: float = 0.0
    unidad_medida: Optional[str] = None
    tendencia: Optional[str] = None
    periodo: Optional[str] = None


class IndicadorKPICreate(IndicadorKPIBase):
    pass


class IndicadorKPIResponse(IndicadorKPIBase):
    id: int
    fecha_actualizacion: Optional[datetime] = None

    class Config:
        from_attributes = True


class DashboardKPISummary(BaseModel):
    total_ventas_mes: float
    total_pedidos_mes: int
    tasa_conversion_reservas: float
    productos_bajo_stock: int
    clientes_activos: int
    ticket_promedio: float
    kpis_detallados: List[IndicadorKPIResponse] = []


# ==========================================
# RECOMENDACIONES IA (CU13)
# ==========================================

class RecomendacionItem(BaseModel):
    producto_id: int
    nombre: str
    razon: str
    imagen_url: Optional[str] = None
    precio: Optional[float] = None
    categoria: Optional[str] = None


class RecomendacionRequest(BaseModel):
    cliente_id: Optional[int] = None
    preferencias: Optional[str] = None
    limite: int = Field(default=5, ge=1, le=20)


class RecomendacionResponse(BaseModel):
    cliente_id: Optional[int] = None
    estilo_detectado: Optional[str] = None
    mensaje_personalizado: str
    recomendaciones: List[RecomendacionItem]


# ==========================================
# ASISTENTE VIRTUAL IA (CU23)
# ==========================================

class MensajeChat(BaseModel):
    role: str = Field(..., description="'user', 'assistant' o 'system'")
    content: str


class AsistenteChatRequest(BaseModel):
    mensaje: str = Field(..., min_length=1)
    historial: List[MensajeChat] = []
    categoria_interes: Optional[str] = None


class AsistenteChatResponse(BaseModel):
    respuesta: str
    sugerencias: List[str] = []
    productos_mencionados: List[Dict[str, Any]] = []
    tipo_respuesta: str = Field(
        default="texto",
        description="Cómo presentar la respuesta en el frontend: 'texto', 'catalogo', 'producto' u 'outfit'"
    )


# ==========================================
# VESTIDOR VIRTUAL (CU21)
# ==========================================

class VestidorVirtualRequest(BaseModel):
    producto_id: int
    variante_id: Optional[int] = None
    imagen_usuario_base64: Optional[str] = None
    imagen_usuario_url: Optional[str] = None


class VestidorVirtualResponse(BaseModel):
    resultado_url: str
    producto_id: int
    producto_nombre: str
    mensaje: str
    detalles_ajuste: Optional[Dict[str, Any]] = None


# ==========================================
# REPORTE POR VOZ (CU22)
# ==========================================

class ReporteVozRequest(BaseModel):
    transcripcion: str = Field(..., min_length=2)
    formato: FormatoReporte = FormatoReporte.JSON


class ReporteVozResponse(BaseModel):
    comando_original: str
    tipo_reporte: TipoReporte
    interpretacion: str
    datos: Dict[str, Any]
    reporte_guardado_id: Optional[int] = None


# ==========================================
# TENDENCIAS DE MODA (CU20)
# ==========================================

class TendenciaItem(BaseModel):
    tendencia: str
    impacto: str
    recomendacion: str


class TendenciasResponse(BaseModel):
    fecha_analisis: datetime
    tendencias_destacadas: List[TendenciaItem]
    categorias_en_alza: List[str]
    prediccion_demanda: str
    datos_respaldo: Optional[Dict[str, Any]] = None
