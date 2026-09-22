"""
Router FastAPI para Servicios Inteligentes, Reportes y KPIs (Iteración 3).
Incluye:
- CU13: Recomendaciones de moda personalizadas con IA
- CU20: Tendencias y estilos globales de moda
- CU21: Vestidor virtual con simulación de prendas
- CU22: Generación ágil de reportes mediante comandos de voz
- CU23: Asistente virtual interactivo para clientes
- CU15: Reportes analíticos (Ventas, Inventario, Reservas, Clientes, Financiero)
- CU07: Indicadores de rendimiento y KPIs gerenciales
"""
import io
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException, status, Response, Request, UploadFile, File
from fastapi.responses import PlainTextResponse, Response as FastAPIResponse
from app.apps.servicios_inteligentes.export_service import responder_reporte
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.security import get_current_user, get_optional_current_user, require_role, get_client_ip
from app.exceptions import NotFoundException, BadRequestException
from app.apps.gestion_usuarios.models import Usuario, Cliente, Administrador
from app.apps.gestion_usuarios.services import BitacoraService

from app.apps.servicios_inteligentes.models import (
    Reporte, IndicadorKPI, TipoReporte, FormatoReporte
)
from app.apps.servicios_inteligentes.schemas import (
    ReporteCreate, ReporteResponse,
    IndicadorKPIBase, IndicadorKPICreate, IndicadorKPIResponse, DashboardKPISummary,
    RecomendacionRequest, RecomendacionResponse,
    AsistenteChatRequest, AsistenteChatResponse,
    VestidorVirtualRequest, VestidorVirtualResponse,
    ReporteVozRequest, ReporteVozResponse,
    AudioTranscripcionResponse,
    TendenciasResponse
)
from app.apps.servicios_inteligentes.services import (
    ReporteService, KPIService, RecomendacionService,
    AsistenteService, VestidorVirtualService, ReporteVozService, TendenciasService,
    ReporteClienteService
)

router = APIRouter(prefix="/api/v1", tags=["Servicios Inteligentes y Reportes"])


# ==============================================================================
# REPORTES (CU15)
# ==============================================================================

@router.get("/reportes/ventas", name="reporte_ventas")
async def get_reporte_ventas(
    fecha_inicio: Optional[datetime] = Query(None),
    fecha_fin: Optional[datetime] = Query(None),
    sucursal_id: Optional[int] = Query(None),
    formato: FormatoReporte = Query(FormatoReporte.JSON),
    incluir_serie: bool = Query(False),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera reporte analítico de ventas y recaudación por canal."""
    datos = await ReporteService.generar_reporte_ventas(db, fecha_inicio, fecha_fin, sucursal_id, incluir_serie)
    if formato == FormatoReporte.CSV:
        csv_data = await ReporteService.exportar_csv(datos)
        return PlainTextResponse(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_ventas.csv"})
    if formato in (FormatoReporte.EXCEL, FormatoReporte.HTML, FormatoReporte.PDF):
        content, media, fname = responder_reporte("VENTAS", datos, formato.value)
        return FastAPIResponse(content=content, media_type=media, headers={"Content-Disposition": f"attachment; filename={fname}"})
    return datos


@router.get("/reportes/inventario", name="reporte_inventario")
async def get_reporte_inventario(
    categoria_id: Optional[int] = Query(None),
    bajo_stock: bool = Query(False),
    sucursal_id: Optional[int] = Query(None),
    limite: int = Query(500, ge=1, le=2000),
    formato: FormatoReporte = Query(FormatoReporte.JSON),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera reporte de inventario, stock por sucursal y alertas."""
    datos = await ReporteService.generar_reporte_inventario(db, categoria_id, bajo_stock, sucursal_id, limite)
    if formato == FormatoReporte.CSV:
        csv_data = await ReporteService.exportar_csv(datos)
        return PlainTextResponse(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_inventario.csv"})
    if formato in (FormatoReporte.EXCEL, FormatoReporte.HTML, FormatoReporte.PDF):
        content, media, fname = responder_reporte("INVENTARIO", datos, formato.value)
        return FastAPIResponse(content=content, media_type=media, headers={"Content-Disposition": f"attachment; filename={fname}"})
    return datos


@router.get("/reportes/reservas", name="reporte_reservas")
async def get_reporte_reservas(
    fecha_inicio: Optional[datetime] = Query(None),
    fecha_fin: Optional[datetime] = Query(None),
    sucursal_id: Optional[int] = Query(None),
    formato: FormatoReporte = Query(FormatoReporte.JSON),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera reporte de efectividad y tasa de conversión de reservas."""
    datos = await ReporteService.generar_reporte_reservas(db, fecha_inicio, fecha_fin, sucursal_id)
    if formato == FormatoReporte.CSV:
        csv_data = await ReporteService.exportar_csv(datos)
        return PlainTextResponse(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_reservas.csv"})
    if formato in (FormatoReporte.EXCEL, FormatoReporte.HTML, FormatoReporte.PDF):
        content, media, fname = responder_reporte("RESERVAS", datos, formato.value)
        return FastAPIResponse(content=content, media_type=media, headers={"Content-Disposition": f"attachment; filename={fname}"})
    return datos


@router.get("/reportes/clientes", name="reporte_clientes")
async def get_reporte_clientes(
    formato: FormatoReporte = Query(FormatoReporte.JSON),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera reporte de fidelidad y top clientes con mayor gasto."""
    datos = await ReporteService.generar_reporte_clientes(db)
    if formato == FormatoReporte.CSV:
        csv_data = await ReporteService.exportar_csv(datos)
        return PlainTextResponse(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_clientes.csv"})
    if formato in (FormatoReporte.EXCEL, FormatoReporte.HTML, FormatoReporte.PDF):
        content, media, fname = responder_reporte("CLIENTES", datos, formato.value)
        return FastAPIResponse(content=content, media_type=media, headers={"Content-Disposition": f"attachment; filename={fname}"})
    return datos


@router.get("/reportes/financiero", name="reporte_financiero")
async def get_reporte_financiero(
    fecha_inicio: Optional[datetime] = Query(None),
    fecha_fin: Optional[datetime] = Query(None),
    sucursal_id: Optional[int] = Query(None),
    formato: FormatoReporte = Query(FormatoReporte.JSON),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera reporte financiero con ingresos por pasarela y beneficios estimados."""
    datos = await ReporteService.generar_reporte_financiero(db, fecha_inicio, fecha_fin, sucursal_id)
    if formato == FormatoReporte.CSV:
        csv_data = await ReporteService.exportar_csv(datos)
        return PlainTextResponse(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_financiero.csv"})
    if formato in (FormatoReporte.EXCEL, FormatoReporte.HTML, FormatoReporte.PDF):
        content, media, fname = responder_reporte("FINANCIERO", datos, formato.value)
        return FastAPIResponse(content=content, media_type=media, headers={"Content-Disposition": f"attachment; filename={fname}"})
    return datos


@router.get("/reportes/guardados", response_model=List[ReporteResponse], name="listar_reportes_guardados")
async def listar_reportes_guardados(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista el histórico de reportes generados y almacenados."""
    res = await db.execute(
        select(Reporte).order_by(Reporte.id.desc()).offset(skip).limit(limit)
    )
    return res.scalars().all()


@router.get("/reportes/guardados/{reporte_id}", response_model=ReporteResponse, name="obtener_reporte_guardado")
async def obtener_reporte_guardado(
    reporte_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene un reporte guardado por su ID."""
    res = await db.execute(select(Reporte).where(Reporte.id == reporte_id))
    rep = res.scalar_one_or_none()
    if not rep:
        raise NotFoundException("Reporte no encontrado")
    return rep


@router.post("/reportes", response_model=ReporteResponse, status_code=status.HTTP_201_CREATED, name="guardar_reporte_manual")
async def crear_reporte(
    reporte_in: ReporteCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera y guarda un reporte específico en la base de datos."""
    admin_res = await db.execute(select(Administrador).where(Administrador.usuario_id == current_user.id))
    admin = admin_res.scalar_one_or_none()
    admin_id = admin.id if admin else None

    # Obtener los datos según el tipo (respeta filtros de parametros si vienen)
    _p = reporte_in.parametros or {}
    _fi = _p.get("fecha_inicio")
    _ff = _p.get("fecha_fin")
    _sid = _p.get("sucursal_id")
    try:
        _fi = datetime.fromisoformat(_fi) if isinstance(_fi, str) and _fi else None
        _ff = datetime.fromisoformat(_ff) if isinstance(_ff, str) and _ff else None
    except Exception:
        _fi, _ff = None, None
    if reporte_in.tipo == TipoReporte.VENTAS:
        datos = await ReporteService.generar_reporte_ventas(db, _fi, _ff, _sid)
    elif reporte_in.tipo == TipoReporte.INVENTARIO:
        datos = await ReporteService.generar_reporte_inventario(db)
    elif reporte_in.tipo == TipoReporte.RESERVAS:
        datos = await ReporteService.generar_reporte_reservas(db, _fi, _ff)
    elif reporte_in.tipo == TipoReporte.CLIENTES:
        datos = await ReporteService.generar_reporte_clientes(db)
    else:
        datos = await ReporteService.generar_reporte_financiero(db, _fi, _ff)

    rep = await ReporteService.guardar_reporte(
        db=db,
        admin_id=admin_id,
        tipo=reporte_in.tipo,
        titulo=reporte_in.titulo,
        parametros=reporte_in.parametros or {},
        formato=reporte_in.formato,
        datos=datos
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="GENERAR_REPORTE",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Reportes",
        detalles=f"Reporte generado: {rep.titulo} ({rep.tipo}) - Formato: {rep.formato}"
    )
    return rep


# ==============================================================================
# INDICADORES KPI (CU07)
# ==============================================================================

@router.get("/kpis/dashboard", response_model=DashboardKPISummary, name="kpi_dashboard")
async def get_dashboard_kpis(
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Retorna el resumen de KPIs clave para el panel de control administrativo."""
    return await KPIService.obtener_dashboard_resumen(db)


@router.get("/kpis/export", name="exportar_kpis")
async def exportar_kpis(
    formato: FormatoReporte = Query(FormatoReporte.CSV),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Exporta el resumen de KPIs + detalle en CSV/Excel/HTML/PDF."""
    resumen = await KPIService.obtener_dashboard_resumen(db)
    datos = resumen.model_dump() if hasattr(resumen, "model_dump") else dict(resumen)
    if formato == FormatoReporte.CSV:
        csv_data = await ReporteService.exportar_csv(datos)
        return PlainTextResponse(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_kpis.csv"})
    if formato in (FormatoReporte.EXCEL, FormatoReporte.HTML, FormatoReporte.PDF):
        content, media, fname = responder_reporte("KPIS", datos, formato.value)
        return FastAPIResponse(content=content, media_type=media, headers={"Content-Disposition": f"attachment; filename={fname}"})
    return datos


@router.get("/kpis", response_model=List[IndicadorKPIResponse], name="listar_kpis")
async def listar_kpis(
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los indicadores KPI configurados."""
    return await KPIService.listar_kpis(db)


@router.post("/kpis", response_model=IndicadorKPIResponse, status_code=status.HTTP_201_CREATED, name="crear_kpi")
async def crear_kpi(
    kpi_in: IndicadorKPICreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva métrica o indicador KPI."""
    kpi = await KPIService.crear_kpi(db, kpi_in)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_KPI",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="KPIs",
        detalles=f"KPI creado: {kpi.nombre} (ID: {kpi.id})"
    )
    return kpi


@router.put("/kpis/{kpi_id}", response_model=IndicadorKPIResponse, name="actualizar_kpi")
async def actualizar_kpi(
    kpi_id: int,
    kpi_in: IndicadorKPIBase,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza valores u objetivos de un indicador KPI."""
    kpi = await KPIService.actualizar_kpi(db, kpi_id, kpi_in)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_KPI",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="KPIs",
        detalles=f"KPI actualizado: ID {kpi_id} ({kpi.nombre})"
    )
    return kpi


# ==============================================================================
# RECOMENDACIONES DE MODA CON IA (CU13)
# ==============================================================================

@router.post("/inteligencia/recomendaciones", response_model=RecomendacionResponse, name="obtener_recomendaciones_ia")
async def obtener_recomendaciones(
    req: RecomendacionRequest,
    current_user: Optional[Usuario] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Genera recomendaciones de productos personalizadas usando IA.
    Si el usuario está autenticado como cliente, aprovecha su historial de compras.
    """
    cliente_id = req.cliente_id
    if not cliente_id and current_user:
        res_c = await db.execute(select(Cliente).where(Cliente.usuario_id == current_user.id))
        c = res_c.scalar_one_or_none()
        if c:
            cliente_id = c.id

    return await RecomendacionService.obtener_recomendaciones(
        db=db,
        cliente_id=cliente_id,
        preferencias=req.preferencias,
        limite=req.limite
    )


@router.get("/inteligencia/recomendaciones/{cliente_id}", response_model=RecomendacionResponse, name="obtener_recomendaciones_por_cliente_id")
async def obtener_recomendaciones_cliente_id(
    cliente_id: int,
    preferencias: Optional[str] = Query(None),
    limite: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene recomendaciones de IA para un ID de cliente específico."""
    return await RecomendacionService.obtener_recomendaciones(
        db=db,
        cliente_id=cliente_id,
        preferencias=preferencias,
        limite=limite
    )


# ==============================================================================
# ASISTENTE VIRTUAL DE MODA (CU23)
# ==============================================================================

@router.post("/inteligencia/asistente-chat", response_model=AsistenteChatResponse, name="chat_asistente_virtual")
async def chat_asistente(
    req: AsistenteChatRequest,
    current_user: Optional[Usuario] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Chat interactivo con el asesor de moda inteligente.
    Responde consultas de estilo, tallas, combinaciones y catálogo de FashionStore.
    """
    cliente_id = None
    if current_user:
        res_c = await db.execute(select(Cliente).where(Cliente.usuario_id == current_user.id))
        c = res_c.scalar_one_or_none()
        if c:
            cliente_id = c.id

    historial_dict = [m.model_dump() for m in req.historial]
    return await AsistenteService.responder_chat(
        db=db,
        mensaje=req.mensaje,
        historial=historial_dict,
        cliente_id=cliente_id
    )


# ==============================================================================
# REPORTES PARA CLIENTES (CU23 / CU14 / CU15)
# ==============================================================================

@router.get("/cliente/reportes/compras/export", name="exportar_reporte_compras_cliente")
async def exportar_reporte_compras_cliente(
    formato: FormatoReporte = Query(FormatoReporte.PDF),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Genera y exporta el reporte detallado de compras del cliente autenticado (PDF, Excel, HTML, CSV)."""
    res_c = await db.execute(select(Cliente).where(Cliente.usuario_id == current_user.id))
    cliente = res_c.scalar_one_or_none()
    if not cliente:
        raise NotFoundException("Perfil de cliente no encontrado")

    datos = await ReporteClienteService.obtener_datos_compras(db, cliente.id)
    content, media, fname = responder_reporte("Compras_Cliente", datos, formato.value)
    if formato == FormatoReporte.JSON:
        return datos
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    return FastAPIResponse(content=content, media_type=media, headers=headers)


@router.get("/cliente/reportes/reservas/export", name="exportar_reporte_reservas_cliente")
async def exportar_reporte_reservas_cliente(
    formato: FormatoReporte = Query(FormatoReporte.PDF),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Genera y exporta el reporte detallado de reservas del cliente autenticado (PDF, Excel, HTML, CSV)."""
    res_c = await db.execute(select(Cliente).where(Cliente.usuario_id == current_user.id))
    cliente = res_c.scalar_one_or_none()
    if not cliente:
        raise NotFoundException("Perfil de cliente no encontrado")

    datos = await ReporteClienteService.obtener_datos_reservas(db, cliente.id)
    content, media, fname = responder_reporte("Reservas_Cliente", datos, formato.value)
    if formato == FormatoReporte.JSON:
        return datos
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    return FastAPIResponse(content=content, media_type=media, headers=headers)


@router.get("/cliente/reportes/resumen", name="resumen_reportes_cliente")
async def obtener_resumen_reportes_cliente(
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el resumen consolidado de compras y reservas del cliente autenticado."""
    res_c = await db.execute(select(Cliente).where(Cliente.usuario_id == current_user.id))
    cliente = res_c.scalar_one_or_none()
    if not cliente:
        raise NotFoundException("Perfil de cliente no encontrado")

    return await ReporteClienteService.obtener_resumen_completo(db, cliente.id)


# ==============================================================================
# VESTIDOR VIRTUAL (CU21)
# ==============================================================================

@router.post("/inteligencia/vestidor-virtual", response_model=VestidorVirtualResponse, name="probar_prenda_vestidor")
async def vestidor_virtual(
    req: VestidorVirtualRequest,
    current_user: Optional[Usuario] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Simulación de atuendo en probador virtual.
    Acepta foto del usuario (base64 o URL) y producto a probar.
    """
    return await VestidorVirtualService.probar_prenda(
        db=db,
        producto_id=req.producto_id,
        variante_id=req.variante_id,
        imagen_usuario_base64=req.imagen_usuario_base64,
        imagen_usuario_url=req.imagen_usuario_url
    )


# ==============================================================================
# REPORTE POR COMANDO DE VOZ (CU22)
# ==============================================================================

@router.post("/inteligencia/reporte-voz", response_model=ReporteVozResponse, name="generar_reporte_por_voz")
async def generar_reporte_voz(
    req: ReporteVozRequest,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """
    Interpreta un comando de voz / texto en lenguaje natural y genera el reporte solicitado.
    """
    admin_res = await db.execute(select(Administrador).where(Administrador.usuario_id == current_user.id))
    admin = admin_res.scalar_one_or_none()
    admin_id = admin.id if admin else None

    return await ReporteVozService.procesar_comando(
        db=db,
        transcripcion=req.transcripcion,
        admin_id=admin_id,
        formato=req.formato
    )


@router.post("/inteligencia/transcribir-audio", response_model=AudioTranscripcionResponse, name="transcribir_audio")
async def transcribir_audio(
    file: UploadFile = File(...),
    current_user: Usuario = Depends(require_role("Administrador"))
):
    """
    Transcribe un archivo de audio a texto usando Whisper de Groq.
    Reemplazo del webkitSpeechRecognition del navegador: el frontend captura
    audio con getUserMedia/MediaRecorder y lo envía aquí para transcripción.
    """
    audio_bytes = await file.read()
    texto = await ReporteVozService.transcribir_audio(
        audio_bytes, file.filename or "audio.webm"
    )
    return AudioTranscripcionResponse(transcripcion=texto)


# ==============================================================================
# TENDENCIAS DE MODA (CU20)
# ==============================================================================

@router.get("/inteligencia/tendencias", response_model=TendenciasResponse, name="analisis_tendencias_moda")
async def get_tendencias_moda(
    db: AsyncSession = Depends(get_db)
):
    """
    Genera un análisis predictivo de tendencias de moda globales y locales
    basado en el comportamiento de ventas y colecciones vigentes.
    """
    return await TendenciasService.analizar_tendencias(db)
