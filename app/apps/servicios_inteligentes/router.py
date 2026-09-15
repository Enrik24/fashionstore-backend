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
from fastapi import APIRouter, Depends, Query, HTTPException, status, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.security import get_current_user, require_role
from app.exceptions import NotFoundException, BadRequestException
from app.apps.gestion_usuarios.models import Usuario, Cliente, Administrador

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
    TendenciasResponse
)
from app.apps.servicios_inteligentes.services import (
    ReporteService, KPIService, RecomendacionService,
    AsistenteService, VestidorVirtualService, ReporteVozService, TendenciasService
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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera reporte analítico de ventas y recaudación por canal."""
    datos = await ReporteService.generar_reporte_ventas(db, fecha_inicio, fecha_fin, sucursal_id)
    if formato == FormatoReporte.CSV:
        csv_data = await ReporteService.exportar_csv(datos)
        return PlainTextResponse(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_ventas.csv"})
    return datos


@router.get("/reportes/inventario", name="reporte_inventario")
async def get_reporte_inventario(
    categoria_id: Optional[int] = Query(None),
    bajo_stock: bool = Query(False),
    formato: FormatoReporte = Query(FormatoReporte.JSON),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera reporte de inventario, stock por sucursal y alertas."""
    datos = await ReporteService.generar_reporte_inventario(db, categoria_id, bajo_stock)
    if formato == FormatoReporte.CSV:
        csv_data = await ReporteService.exportar_csv(datos)
        return PlainTextResponse(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_inventario.csv"})
    return datos


@router.get("/reportes/reservas", name="reporte_reservas")
async def get_reporte_reservas(
    fecha_inicio: Optional[datetime] = Query(None),
    fecha_fin: Optional[datetime] = Query(None),
    formato: FormatoReporte = Query(FormatoReporte.JSON),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera reporte de efectividad y tasa de conversión de reservas."""
    datos = await ReporteService.generar_reporte_reservas(db, fecha_inicio, fecha_fin)
    if formato == FormatoReporte.CSV:
        csv_data = await ReporteService.exportar_csv(datos)
        return PlainTextResponse(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_reservas.csv"})
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
    return datos


@router.get("/reportes/financiero", name="reporte_financiero")
async def get_reporte_financiero(
    fecha_inicio: Optional[datetime] = Query(None),
    fecha_fin: Optional[datetime] = Query(None),
    formato: FormatoReporte = Query(FormatoReporte.JSON),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera reporte financiero con ingresos por pasarela y beneficios estimados."""
    datos = await ReporteService.generar_reporte_financiero(db, fecha_inicio, fecha_fin)
    if formato == FormatoReporte.CSV:
        csv_data = await ReporteService.exportar_csv(datos)
        return PlainTextResponse(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_financiero.csv"})
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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Genera y guarda un reporte específico en la base de datos."""
    admin_res = await db.execute(select(Administrador).where(Administrador.usuario_id == current_user.id))
    admin = admin_res.scalar_one_or_none()
    admin_id = admin.id if admin else None

    # Obtener los datos según el tipo
    if reporte_in.tipo == TipoReporte.VENTAS:
        datos = await ReporteService.generar_reporte_ventas(db)
    elif reporte_in.tipo == TipoReporte.INVENTARIO:
        datos = await ReporteService.generar_reporte_inventario(db)
    elif reporte_in.tipo == TipoReporte.RESERVAS:
        datos = await ReporteService.generar_reporte_reservas(db)
    elif reporte_in.tipo == TipoReporte.CLIENTES:
        datos = await ReporteService.generar_reporte_clientes(db)
    else:
        datos = await ReporteService.generar_reporte_financiero(db)

    return await ReporteService.guardar_reporte(
        db=db,
        admin_id=admin_id,
        tipo=reporte_in.tipo,
        titulo=reporte_in.titulo,
        parametros=reporte_in.parametros or {},
        formato=reporte_in.formato,
        datos=datos
    )


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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva métrica o indicador KPI."""
    return await KPIService.crear_kpi(db, kpi_in)


@router.put("/kpis/{kpi_id}", response_model=IndicadorKPIResponse, name="actualizar_kpi")
async def actualizar_kpi(
    kpi_id: int,
    kpi_in: IndicadorKPIBase,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza valores u objetivos de un indicador KPI."""
    return await KPIService.actualizar_kpi(db, kpi_id, kpi_in)


# ==============================================================================
# RECOMENDACIONES DE MODA CON IA (CU13)
# ==============================================================================

@router.post("/inteligencia/recomendaciones", response_model=RecomendacionResponse, name="obtener_recomendaciones_ia")
async def obtener_recomendaciones(
    req: RecomendacionRequest,
    current_user: Optional[Usuario] = Depends(get_current_user),
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
    current_user: Optional[Usuario] = Depends(get_current_user),
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
# VESTIDOR VIRTUAL (CU21)
# ==============================================================================

@router.post("/inteligencia/vestidor-virtual", response_model=VestidorVirtualResponse, name="probar_prenda_vestidor")
async def vestidor_virtual(
    req: VestidorVirtualRequest,
    current_user: Optional[Usuario] = Depends(get_current_user),
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
