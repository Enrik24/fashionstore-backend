"""[EXAMEN-2] Router del Paquete 3 — analítica avanzada (CU10–CU12).

Expone reportes generativos, exportación, dashboard, machine learning y datos
sintéticos. Todo se resuelve con el tenant del token; el plan Básico queda
bloqueado para reportes IA y ML (RN-003).
"""
from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ValidationException
from app.tenant import autenticacion_flexible
from app.apps.analitica_avanzada.dashboard_service import DashboardService
from app.apps.analitica_avanzada.datos_sinteticos_service import DatosSinteticosService
from app.apps.analitica_avanzada.exportacion_service import ExportacionService
from app.apps.analitica_avanzada.ml_service import (
    MLService,
    OBJETIVOS_NO_SUPERVISADOS,
)
from app.apps.analitica_avanzada.reporte_service import ReporteGenerativoService
from app.apps.analitica_avanzada.schemas import (
    DatasetSinteticoRequest,
    DetectarAnomaliasRequest,
    EntrenarModeloRequest,
    PredecirVentasRequest,
    ReporteExportarRequest,
    ReporteInterpretarRequest,
    ReporteSolicitudRequest,
    SegmentarClientesRequest,
    SembrarDatasetRequest,
)

router = APIRouter(prefix="/api/v1", tags=["Analítica avanzada (CU10–CU12)"])


def _tenant_id(contexto) -> int:
    return int(contexto["tenant"].id)


def _usuario(contexto):
    return contexto.get("usuario")


def _decodificar_audio(audio_base64: str | None) -> bytes | None:
    if not audio_base64:
        return None
    contenido = audio_base64.split(",", 1)[-1] if audio_base64.startswith("data:") else audio_base64
    try:
        return base64.b64decode(contenido)
    except (binascii.Error, ValueError):
        raise ValidationException("El audio enviado no es base64 válido")


def _serializar_solicitud(solicitud) -> dict:
    return {
        "solicitud_id": solicitud.id,
        "prompt": solicitud.prompt_original,
        "plantilla": solicitud.plantilla_usada,
        "parametros": solicitud.parametros_json,
        "estado": solicitud.estado.value if hasattr(solicitud.estado, "value") else solicitud.estado,
        "tiempo_ms": solicitud.tiempo_ms,
        "fecha_ejecucion": (
            solicitud.fecha_ejecucion.isoformat() if solicitud.fecha_ejecucion else None
        ),
        "resumen": solicitud.resultado_resumen_json or {},
        "filas": len(solicitud.resultado_json or []),
    }


# ======================================================================
# CU10 — Reportes generativos
# ======================================================================

@router.post("/reportes-ia/solicitar")
async def solicitar_reporte(
    datos: ReporteSolicitudRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    usuario = _usuario(contexto)
    if not datos.texto and getattr(datos, "prompt", None):
        datos.texto = datos.prompt  # compatibilidad con clientes que mandan { prompt }
    audio = _decodificar_audio(datos.audio_base64)
    return await ReporteGenerativoService.solicitar(
        db, tenant_id, getattr(usuario, "id", None), datos.texto, audio
    )


@router.post("/reportes-ia/interpretar")
async def interpretar_reporte(
    datos: ReporteInterpretarRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    usuario = _usuario(contexto)
    await ReporteGenerativoService.validar_plan_ia(db, tenant_id)
    if not datos.texto and getattr(datos, "prompt", None):
        datos.texto = datos.prompt  # compatibilidad con clientes que mandan { prompt }
    audio = _decodificar_audio(datos.audio_base64)
    return await ReporteGenerativoService.interpretar_solicitud(
        tenant_id, getattr(usuario, "id", None), datos.texto, audio
    )


@router.get("/reportes-ia/historial")
async def historial_reportes(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    solicitudes = await ReporteGenerativoService.listar_historial(
        db, tenant_id, skip=skip, limit=limit
    )
    return [_serializar_solicitud(solicitud) for solicitud in solicitudes]


@router.get("/reportes-ia/plantillas")
async def listar_plantillas(
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    return {
        "plantillas": ReporteGenerativoService.listar_plantillas(tenant_id),
        "descripcion": "Catálogo cerrado de reportes aprobados; la IA no escribe SQL libre.",
    }


@router.post("/reportes-ia/{solicitud_id}/ejecutar")
async def ejecutar_reporte(
    solicitud_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await ReporteGenerativoService.validar_plan_ia(db, tenant_id)
    return await ReporteGenerativoService.ejecutar_solicitud(db, solicitud_id, tenant_id)


@router.get("/reportes-ia/{solicitud_id}")
async def obtener_reporte(
    solicitud_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    solicitud = await ReporteGenerativoService.obtener_solicitud(db, solicitud_id, tenant_id)
    return {
        **_serializar_solicitud(solicitud),
        "sql": solicitud.query_sql_generada,
        "resultado": solicitud.resultado_json or [],
        "error_detalle": solicitud.error_detalle,
    }


# ======================================================================
# CU11 — Exportación
# ======================================================================

@router.post("/reportes-ia/{solicitud_id}/exportar")
async def exportar_reporte(
    solicitud_id: int,
    datos: ReporteExportarRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    usuario = _usuario(contexto)
    tenant = contexto["tenant"]
    resultado = await ExportacionService.exportar_reporte(
        db,
        solicitud_id=solicitud_id,
        formato=datos.formato,
        tenant_id=tenant_id,
        usuario=usuario,
        tenant=tenant,
        opciones=datos.opciones,
    )
    await db.commit()
    return resultado


@router.post("/reportes-ia/{solicitud_id}/reintentar")
async def reintentar_exportacion(
    solicitud_id: int,
    datos: ReporteExportarRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    """Reintenta la exportación de una solicitud ya ejecutada (CU11)."""
    resultado = await ExportacionService.reintentar_generacion(
        db,
        solicitud_id,
        datos.formato,
        _tenant_id(contexto),
        _usuario(contexto),
        contexto["tenant"],
    )
    await db.commit()
    return resultado


@router.get("/exportaciones/{archivo_id}")
async def descargar_exportacion(
    archivo_id: str,
    contexto=Depends(autenticacion_flexible),
):
    tenant_id = _tenant_id(contexto)
    archivo = ExportacionService.obtener_archivo(archivo_id, tenant_id)
    return Response(
        content=archivo["contenido"],
        media_type=archivo["media_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{archivo["nombre"]}"',
        },
    )


# ======================================================================
# CU12 — Dashboard y KPIs
# ======================================================================

@router.get("/analitica/dashboard")
async def obtener_dashboard(
    sucursal_id: int | None = Query(None),
    periodo: str | None = Query(None, pattern="^(DIA|SEMANA|MES|ANIO|dia|semana|mes|anio)$"),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return await DashboardService.obtener_dashboard(
        db,
        _tenant_id(contexto),
        usuario=_usuario(contexto),
        sucursal_id=sucursal_id,
        periodo=periodo,
    )


@router.get("/analitica/dashboard/ultimo-conocido")
async def ultimo_conocido_dashboard(
    sucursal_id: int | None = Query(None),
    periodo: str | None = Query(None),
    contexto=Depends(autenticacion_flexible),
):
    """Último dashboard conocido (flujo offline, CU12).

    Sin datos en caché retorna 404 para que el cliente muestre el
    mensaje de «sin datos conocidos» en vez de un contenido vacío.
    """
    from fastapi import HTTPException

    datos = DashboardService.obtener_ultimo_conocido(
        _tenant_id(contexto), sucursal_id=sucursal_id, periodo=periodo
    )
    if datos is None:
        raise HTTPException(status_code=404, detail="Sin datos conocidos para este contexto")
    return datos


@router.get("/analitica/kpis")
async def obtener_kpis(
    sucursal_id: int | None = Query(None),
    periodo: str | None = Query(None),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return {
        "kpis": await DashboardService.calcular_kpis(
            db, _tenant_id(contexto), sucursal_id=sucursal_id, periodo=periodo
        ),
        "datos_suficientes": await DashboardService.verificar_datos_suficientes(
            db, _tenant_id(contexto)
        ),
    }


@router.get("/analitica/kpis/{kpi}/detalle")
async def detalle_kpi(
    kpi: str,
    sucursal_id: int | None = Query(None),
    periodo: str | None = Query(None),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return await DashboardService.obtener_drilldown(
        db, _tenant_id(contexto), kpi, sucursal_id=sucursal_id, periodo=periodo
    )


# ======================================================================
# CU12 — Machine learning
# ======================================================================

async def _exigir_plan_ia(db: AsyncSession, tenant_id: int) -> None:
    await ReporteGenerativoService.validar_plan_ia(db, tenant_id)


@router.post("/analitica/ml/entrenar")
async def entrenar_modelo(
    datos: EntrenarModeloRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _exigir_plan_ia(db, tenant_id)
    objetivo = (datos.objetivo or "VENTAS").upper()
    if objetivo in OBJETIVOS_NO_SUPERVISADOS:
        resultado = await MLService.entrenar_modelo_no_supervisado(
            db, tenant_id, objetivo, datos.hiperparametros
        )
    else:
        resultado = await MLService.entrenar_modelo_supervisado(
            db, tenant_id, objetivo, datos.hiperparametros
        )
    await db.commit()
    return resultado


@router.post("/analitica/ml/predecir")
async def predecir_ventas(
    datos: PredecirVentasRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _exigir_plan_ia(db, tenant_id)
    return await MLService.predecir_ventas(
        db, tenant_id, datos.modelo_id, horizonte=datos.horizonte
    )


@router.post("/analitica/ml/segmentar")
async def segmentar_clientes(
    datos: SegmentarClientesRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _exigir_plan_ia(db, tenant_id)
    resultado = await MLService.segmentar_clientes(
        db, tenant_id, n_clusters=datos.n_clusters, persistir=datos.persistir
    )
    await db.commit()
    return resultado


@router.post("/analitica/ml/anomalias")
async def detectar_anomalias(
    datos: DetectarAnomaliasRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _exigir_plan_ia(db, tenant_id)
    resultado = await MLService.detectar_anomalias(
        db, tenant_id, periodo_dias=datos.periodo_dias, persistir=datos.persistir
    )
    await db.commit()
    return resultado


@router.get("/analitica/ml/modelos")
async def listar_modelos(
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _exigir_plan_ia(db, tenant_id)
    return {"modelos": await MLService.listar_modelos(db, tenant_id)}


@router.get("/analitica/ml/modelos/{modelo_id}")
async def obtener_modelo(
    modelo_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _exigir_plan_ia(db, tenant_id)
    modelo = await MLService.obtener_modelo(db, modelo_id, tenant_id)
    return MLService._serializar_modelo(modelo)


@router.post("/analitica/ml/modelos/{modelo_id}/desactivar")
async def desactivar_modelo(
    modelo_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _exigir_plan_ia(db, tenant_id)
    resultado = await MLService.desactivar_modelo(db, modelo_id, tenant_id)
    await db.commit()
    return resultado


@router.post("/analitica/ml/modelos/{modelo_id}/reentrenar")
async def reentrenar_modelo(
    modelo_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _exigir_plan_ia(db, tenant_id)
    resultado = await MLService.reentrenar_modelo(db, modelo_id, tenant_id)
    await db.commit()
    return resultado


# ======================================================================
# Datos sintéticos (RF-023)
# ======================================================================

@router.post("/analitica/datos-sinteticos/generar")
async def generar_dataset(
    datos: DatasetSinteticoRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return await DatosSinteticosService.generar_dataset(
        db, _tenant_id(contexto), datos.model_dump()
    )


@router.get("/analitica/datos-sinteticos")
async def listar_datasets(
    contexto=Depends(autenticacion_flexible),
):
    return {"datasets": DatosSinteticosService.listar_datasets(_tenant_id(contexto))}


@router.post("/analitica/datos-sinteticos/sembrar")
async def sembrar_dataset(
    datos: SembrarDatasetRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    resultado = await DatosSinteticosService.sembrar_dataset(
        db, _tenant_id(contexto), datos.dataset_id
    )
    await db.commit()
    return resultado


@router.get("/analitica/datos-sinteticos/{dataset_id}/exportar")
async def exportar_dataset(
    dataset_id: str,
    contexto=Depends(autenticacion_flexible),
):
    contenido, media_type, nombre = DatosSinteticosService.exportar_dataset(
        dataset_id, _tenant_id(contexto)
    )
    return Response(
        content=contenido,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.delete("/analitica/datos-sinteticos/{dataset_id}")
async def eliminar_dataset(
    dataset_id: str,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    resultado = await DatosSinteticosService.eliminar_dataset(
        db, _tenant_id(contexto), dataset_id
    )
    await db.commit()
    return resultado
