"""[EXAMEN-2] Modelos del Paquete 3 — Analítica avanzada (CU10–CU12).

Solo se crean las dos tablas nuevas del diagrama v2. Los KPIs y datasets
sintéticos se apoyan en las tablas del Examen 1 sin duplicarlas.
"""
import enum

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class TipoModeloML(str, enum.Enum):
    """Naturaleza del modelo entrenado (plan, 3.3.8)."""
    SUPERVISADO = "SUPERVISADO"
    NO_SUPERVISADO = "NO_SUPERVISADO"


class EstadoSolicitudReporteIA(str, enum.Enum):
    """Estados del ciclo de vida de una solicitud de reporte (plan, 3.3.7)."""
    INTERPRETADA = "INTERPRETADA"
    AMBIGUA = "AMBIGUA"
    NO_SOPORTADA = "NO_SOPORTADA"
    EJECUTADA = "EJECUTADA"
    ERROR = "ERROR"


class SolicitudReporteIA(Base):
    """Solicitud de reporte en lenguaje natural (CU10).

    Guarda la interpretación, la plantilla aprobada, los parámetros, el SQL
    final y el resultado, de modo que toda ejecución sea auditable y
    reproducible (plan, 3.3.7 y 9.3.4).
    """
    __tablename__ = "solicitudes_reporte_ia"
    __table_args__ = (
        Index("ix_solicitudes_reporte_ia_tenant_fecha", "tenant_id", "fecha_ejecucion"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    prompt_original = Column(Text, nullable=False)
    query_sql_generada = Column(Text, nullable=True)
    estado = Column(
        Enum(EstadoSolicitudReporteIA, name="estado_solicitud_reporte_ia"),
        nullable=False,
        default=EstadoSolicitudReporteIA.INTERPRETADA,
        server_default=EstadoSolicitudReporteIA.INTERPRETADA.value,
    )
    resultado_json = Column(JSON, nullable=True)
    tiempo_ms = Column(Integer, nullable=True)
    error_detalle = Column(Text, nullable=True)
    fecha_ejecucion = Column(DateTime(timezone=True), server_default=func.now())
    plantilla_usada = Column(String(80), nullable=True)
    parametros_json = Column(JSON, nullable=True)
    resultado_resumen_json = Column(JSON, nullable=True)

    usuario = relationship("Usuario")

    def __repr__(self):
        return f"<SolicitudReporteIA {self.id} - {self.estado}>"


class ModeloMachineLearning(Base):
    """Modelo de ML entrenado para un tenant (CU12).

    Registra algoritmo, objetivo, métricas, versión y ruta del artefacto
    `.joblib`. El tenant forma parte de la clave de búsqueda para impedir que
    un modelo se cargue desde otro tenant (plan, 3.3.8).
    """
    __tablename__ = "modelos_ml"
    __table_args__ = (
        # [EXAMEN-2] Plan 16.1: incluye `estado`
        Index("ix_modelos_ml_tenant_objetivo_estado", "tenant_id", "objetivo", "estado"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nombre = Column(String(100), nullable=False)
    tipo = Column(
        Enum(TipoModeloML, name="tipo_modelo_ml"),
        nullable=False,
    )
    version = Column(String(20), nullable=False, default="1.0", server_default="1.0")
    metricas_json = Column(JSON, nullable=False, default=dict)
    fecha_entrenamiento = Column(DateTime(timezone=True), server_default=func.now())
    estado = Column(String(20), nullable=False, default="ACTIVO", server_default="ACTIVO")
    algoritmo = Column(String(80), nullable=False)
    objetivo = Column(String(40), nullable=False)
    ruta_artefacto = Column(String(500), nullable=False)
    datos_entrenamiento_json = Column(JSON, nullable=True)

    def __repr__(self):
        return f"<ModeloMachineLearning {self.nombre} ({self.algoritmo})>"
