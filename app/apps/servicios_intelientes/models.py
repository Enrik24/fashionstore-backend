"""
Modelos SQLAlchemy para Servicios Inteligentes y Reportes.
Contiene: Reporte, IndicadorKPI.
(Implementación completa en Iteración 3)
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Text, Float, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base


# Enumeraciones
class TipoReporte(str, enum.Enum):
    VENTAS = "VENTAS"
    INVENTARIO = "INVENTARIO"
    RESERVAS = "RESERVAS"
    CLIENTES = "CLIENTES"
    FINANCIERO = "FINANCIERO"


class FormatoReporte(str, enum.Enum):
    PDF = "PDF"
    EXCEL = "EXCEL"
    CSV = "CSV"
    JSON = "JSON"


# Modelo: Reporte
class Reporte(Base):
    """Tabla de reportes generados."""
    __tablename__ = "reportes"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    administrador_id = Column(Integer, ForeignKey("administradores.id", ondelete="SET NULL"), nullable=True)
    tipo = Column(Enum(TipoReporte), nullable=False)
    titulo = Column(String(200), nullable=False)
    fecha_generacion = Column(DateTime(timezone=True), server_default=func.now())
    parametros = Column(JSON, nullable=True)
    formato = Column(Enum(FormatoReporte), default=FormatoReporte.JSON)
    contenido = Column(Text, nullable=True)  # Para reportes pequeños en JSON/CSV
    
    # Relaciones
    administrador = relationship("Administrador", back_populates="reportes")
    
    def __repr__(self):
        return f"<Reporte {self.tipo} - {self.fecha_generacion}>"


# Modelo: IndicadorKPI
class IndicadorKPI(Base):
    """Tabla de indicadores KPI."""
    __tablename__ = "indicadores_kpi"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    descripcion = Column(Text, nullable=True)
    valor_actual = Column(Float, default=0)
    valor_objetivo = Column(Float, default=0)
    tendencia = Column(String(20), nullable=True)  # subir, bajar, estable
    periodo = Column(String(50), nullable=True)  # mensual, semanal, anual
    ultimo_actualizado = Column(DateTime(timezone=True), server_default=func.now())
    
    def __repr__(self):
        return f"<IndicadorKPI {self.nombre}>"


# Importar modelos que faltan
from app.apps.gestion_usuarios.models import Administrador