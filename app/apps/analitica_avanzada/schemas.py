"""[EXAMEN-2] Schemas de la API de analítica avanzada (CU10–CU12)."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# CU10 / CU11 — Reportes generativos y exportación
# ----------------------------------------------------------------------

class ReporteSolicitudRequest(BaseModel):
    texto: Optional[str] = Field(None, max_length=2000)
    audio_base64: Optional[str] = None
    formato: str = Field("PDF", description="Formato sugerido para exportar (PDF o EXCEL)")


class ReporteInterpretarRequest(BaseModel):
    texto: Optional[str] = Field(None, max_length=2000)
    audio_base64: Optional[str] = None


class ReporteExportarRequest(BaseModel):
    formato: str = Field("PDF", pattern="^(PDF|EXCEL|pdf|excel)$")
    opciones: Optional[dict[str, Any]] = None


# ----------------------------------------------------------------------
# CU12 — Dashboard
# ----------------------------------------------------------------------

class EntrenarModeloRequest(BaseModel):
    objetivo: str = Field("VENTAS", max_length=40)
    hiperparametros: Optional[dict[str, Any]] = None


class PredecirVentasRequest(BaseModel):
    modelo_id: int
    horizonte: int = Field(7, ge=1, le=90)


class SegmentarClientesRequest(BaseModel):
    n_clusters: int = Field(4, ge=2, le=20)
    persistir: bool = True


class DetectarAnomaliasRequest(BaseModel):
    periodo_dias: int = Field(90, ge=7, le=730)
    persistir: bool = True


# ----------------------------------------------------------------------
# Datos sintéticos (RF-023)
# ----------------------------------------------------------------------

class DatasetSinteticoRequest(BaseModel):
    cantidad_clientes: int = Field(15, ge=1, le=500)
    cantidad_ventas: int = Field(120, ge=10, le=5000)
    dias: int = Field(120, ge=30, le=730)
    semilla: int = 2026
    sucursal_id: Optional[int] = None


class SembrarDatasetRequest(BaseModel):
    dataset_id: Optional[str] = None
