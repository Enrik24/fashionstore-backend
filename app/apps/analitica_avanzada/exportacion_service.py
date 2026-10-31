"""[EXAMEN-2] Exportación de reportes con branding (CU11).

Genera PDF y Excel a partir de una `SolicitudReporteIA` ejecutada. Mantiene un
registro en memoria de los archivos generados para descargarlos por id y audita
cada exportación en la bitácora.
"""
from __future__ import annotations

import gzip
import io
import math
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import NotFoundException, ValidationException
from app.apps.analitica_avanzada.models import EstadoSolicitudReporteIA
from app.apps.analitica_avanzada.reporte_service import ReporteGenerativoService

# Registro en proceso: archivo_id -> metadatos + contenido
_ARCHIVOS: dict[str, dict[str, Any]] = {}

FORMATOS = {"PDF", "EXCEL"}


def limpiar_archivos() -> None:
    """Limpia el registro en memoria (pruebas e inicio de proceso)."""
    _ARCHIVOS.clear()


def _fecha_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


class ExportacionService:
    """Orquesta PDF/Excel, branding, metadatos y control de tamaño."""

    @classmethod
    def generar_nombre_archivo(cls, tipo: str, tenant_nombre: str, fecha: datetime | None = None) -> str:
        base = "".join(ch if ch.isalnum() else "_" for ch in (tenant_nombre or "tenant"))
        base = "_".join(filter(None, base.split("_")))[:40] or "tenant"
        return f"{tipo.lower()}_{base}_{(fecha or datetime.now()).strftime('%Y%m%d_%H%M')}.{ 'xlsx' if tipo.upper() == 'EXCEL' else 'pdf'}"

    @classmethod
    def aplicar_branding(cls, documento: dict, tenant) -> dict:
        documento["branding"] = {
            "logo_url": getattr(tenant, "logo_url", None),
            "razon_social": getattr(tenant, "razon_social", None),
            "nit": getattr(tenant, "nit", None),
            "moneda": getattr(tenant, "moneda", "BOB"),
        }
        return documento

    @classmethod
    def incluir_metadatos(
        cls, documento: dict, tenant, usuario, filtros: dict | None
    ) -> dict:
        documento["metadatos"] = {
            "tenant": getattr(tenant, "razon_social", None),
            "nit": getattr(tenant, "nit", None),
            "usuario": getattr(usuario, "correo", None) or getattr(usuario, "nombre", None),
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "filtros": filtros or {},
        }
        return documento

    @classmethod
    def _datos_para_exportar(cls, solicitud) -> dict:
        resultado = solicitud.resultado_json or []
        columnas = list(resultado[0].keys()) if resultado else []
        resumen = solicitud.resultado_resumen_json or {}
        documento = {
            "titulo": solicitud.plantilla_usada or "Reporte",
            "prompt": solicitud.prompt_original,
            "columnas": columnas,
            "filas": resultado,
            "resumen": resumen,
        }
        return cls.agregar_graficos(documento, [resumen.get("visualizacion")])

    @classmethod
    def agregar_graficos(
        cls, documento: dict, visualizaciones: list[str] | None
    ) -> dict:
        """Incorpora los gráficos elegidos por el reporte (plan, CU11).

        No dibuja imágenes: describe cada gráfico (tipo, eje y serie) para que
        `generar_pdf`/`generar_excel` lo representen con las librerías del
        formato. La primera columna se usa como categoría y la segunda como
        serie de valores.
        """
        columnas = documento.get("columnas") or []
        filas = documento.get("filas") or []
        tipos = [str(tipo).upper() for tipo in (visualizaciones or []) if tipo]
        if not tipos:
            tipos = ["LINEAS" if len(filas) > 8 else "BARRAS"]
        if not filas or len(columnas) < 2:
            documento["graficos"] = []
            return documento

        etiqueta, valores = columnas[0], columnas[1]
        graficos: list[dict] = []
        for tipo in tipos:
            if tipo in ("PIE", "TARTA", "TARTAS"):
                normalizado = "TARTAS"
            elif tipo.startswith("LINEA"):
                normalizado = "LINEAS"
            else:
                normalizado = "BARRAS"
            graficos.append({
                "tipo": normalizado,
                "titulo": documento.get("titulo") or "Reporte",
                "eje_x": etiqueta,
                "series": [{
                    "nombre": valores,
                    "datos": [fila.get(valores) for fila in filas],
                }],
                "categorias": [fila.get(etiqueta) for fila in filas],
            })
        documento["graficos"] = graficos
        return documento

    @classmethod
    def generar_pdf(cls, datos: dict, opciones: dict | None = None) -> bytes:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        opciones = opciones or {}
        branding = opciones.get("branding") or {}
        metadatos = opciones.get("metadatos") or {}

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=36, bottomMargin=36)
        estilos = getSampleStyleSheet()
        elementos = [
            Paragraph(branding.get("razon_social") or "FashionStore", estilos["Title"]),
            Paragraph(f"NIT: {branding.get('nit') or '-'}", estilos["Normal"]),
            Paragraph(f"Reporte: {datos['titulo']}", estilos["Heading2"]),
            Paragraph(
                f"Usuario: {metadatos.get('usuario') or '-'} · Fecha: {metadatos.get('fecha') or '-'}",
                estilos["Normal"],
            ),
            Paragraph(f"Filtros: {metadatos.get('filtros') or {}}", estilos["Normal"]),
            Spacer(1, 12),
        ]
        if datos.get("filas"):
            cabecera = [str(c) for c in datos["columnas"]]
            filas = [[str(f.get(c, "")) for c in datos["columnas"]] for f in datos["filas"][:200]]
            tabla = Table([cabecera] + filas, repeatRows=1)
            tabla.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E11D48")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            elementos.append(tabla)
        else:
            elementos.append(Paragraph("Sin datos para los filtros seleccionados.", estilos["Normal"]))
        doc.build(elementos)
        return buf.getvalue()

    @classmethod
    def generar_excel(cls, datos: dict, opciones: dict | None = None) -> bytes:
        from openpyxl import Workbook
        from openpyxl.chart import BarChart, LineChart, PieChart, Reference
        from openpyxl.styles import Font, PatternFill

        opciones = opciones or {}
        branding = opciones.get("branding") or {}
        metadatos = opciones.get("metadatos") or {}

        wb = Workbook()
        hoja = wb.active
        hoja.title = "Datos"
        hoja.append([branding.get("razon_social") or "FashionStore", f"NIT: {branding.get('nit') or '-'}"])
        hoja.append([f"Reporte: {datos['titulo']}", f"Generado: {metadatos.get('fecha')}"])
        hoja.append([])
        columnas = datos["columnas"]
        hoja.append(columnas)
        for celda in hoja[hoja.max_row]:
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = PatternFill("solid", fgColor="E11D48")
        for fila in datos["filas"]:
            hoja.append([fila.get(c) for c in columnas])

        resumen = wb.create_sheet("Resumen")
        resumen.append(["Campo", "Valor"])
        for clave, valor in (datos.get("resumen") or {}).items():
            resumen.append([str(clave), str(valor)])
        for clave, valor in metadatos.items():
            resumen.append([f"meta.{clave}", str(valor)])

        graficos = datos.get("graficos") or []
        if graficos:
            filas_grafico = min(len(datos["filas"]), 20)
            for indice, grafico in enumerate(graficos):
                if grafico["tipo"] == "LINEAS":
                    chart = LineChart()
                elif grafico["tipo"] == "TARTAS":
                    chart = PieChart()
                else:
                    chart = BarChart()
                chart.title = grafico["titulo"]
                chart.x_axis.title = grafico["eje_x"]
                if grafico["series"]:
                    chart.y_axis.title = grafico["series"][0]["nombre"]
                datos_ref = Reference(hoja, min_col=2, min_row=4, max_row=4 + filas_grafico)
                cats = Reference(hoja, min_col=1, min_row=5, max_row=4 + filas_grafico)
                chart.add_data(datos_ref, titles_from_data=True)
                chart.set_categories(cats)
                hoja.add_chart(chart, f"H{4 + indice * 16}")
        elif datos["filas"] and len(columnas) >= 2:
            chart = BarChart()
            chart.title = datos["titulo"]
            chart.y_axis.title = "Valor"
            chart.x_axis.title = columnas[0]
            datos_ref = Reference(hoja, min_col=2, min_row=4, max_row=4 + min(len(datos["filas"]), 20))
            cats = Reference(hoja, min_col=1, min_row=5, max_row=4 + min(len(datos["filas"]), 20))
            chart.add_data(datos_ref, titles_from_data=True)
            chart.set_categories(cats)
            hoja.add_chart(chart, "H4")

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    @classmethod
    def validar_tamano(cls, archivo: bytes) -> bool:
        return len(archivo) <= settings.EXPORT_MAX_MB * 1024 * 1024

    @classmethod
    def comprimir_archivo(cls, archivo: bytes) -> bytes:
        return gzip.compress(archivo)

    @classmethod
    def dividir_archivo(cls, archivo: bytes, max_mb: int | None = None) -> list[bytes]:
        limite = (max_mb or settings.EXPORT_MAX_MB) * 1024 * 1024
        partes = max(1, math.ceil(len(archivo) / limite))
        tamano = math.ceil(len(archivo) / partes)
        return [archivo[i:i + tamano] for i in range(0, len(archivo), tamano)]

    @classmethod
    async def registrar_exportacion(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int | None,
        solicitud_id: int,
        formato: str,
        tamano: int,
    ) -> None:
        from app.apps.gestion_usuarios.services import BitacoraService

        await BitacoraService.registrar_evento(
            db,
            accion="EXPORTAR_REPORTE_IA",
            usuario_id=usuario_id,
            modulo="Reportes IA",
            detalles=f"Solicitud {solicitud_id} exportada en {formato} ({tamano} bytes)",
            registro_id=solicitud_id,
            tenant_id=tenant_id,
        )

    @classmethod
    async def registrar_fallo(
        cls,
        db: AsyncSession,
        tenant_id: int,
        solicitud_id: int,
        formato: str,
        detalle: str,
        usuario_id: int | None = None,
    ) -> None:
        """Deja rastro del intento fallido (plan, 9.3.5, flujo alterno 1).

        Se confirma en el acto: la petición va a terminar en error y, sin el
        `commit`, la bitácora se perdería con el rollback.
        """
        from app.apps.gestion_usuarios.services import BitacoraService

        await BitacoraService.registrar_evento(
            db,
            accion="EXPORTACION_FALLIDA",
            usuario_id=usuario_id,
            modulo="Reportes IA",
            detalles=f"Solicitud {solicitud_id} ({formato}) falló: {detalle[:500]}",
            registro_id=solicitud_id,
            tenant_id=tenant_id,
        )
        await db.commit()

    @classmethod
    async def exportar_reporte(
        cls,
        db: AsyncSession,
        solicitud_id: int,
        formato: str,
        tenant_id: int,
        usuario=None,
        tenant=None,
        opciones: dict | None = None,
    ) -> dict:
        formato = (formato or "PDF").upper()
        if formato not in FORMATOS:
            raise ValidationException("Formato no soportado; use PDF o EXCEL")

        solicitud = await ReporteGenerativoService.obtener_solicitud(db, solicitud_id, tenant_id)
        if solicitud.estado != EstadoSolicitudReporteIA.EJECUTADA:
            raise ValidationException("La solicitud debe estar ejecutada antes de exportar")

        datos = cls._datos_para_exportar(solicitud)
        opciones = dict(opciones or {})
        if tenant is not None:
            cls.aplicar_branding(opciones, tenant)
            cls.incluir_metadatos(opciones, tenant, usuario, solicitud.parametros_json)
        datos.update({"branding": opciones.get("branding", {}), "metadatos": opciones.get("metadatos", {})})

        try:
            if formato == "PDF":
                contenido = cls.generar_pdf(datos, opciones)
                media_type = "application/pdf"
            else:
                contenido = cls.generar_excel(datos, opciones)
                media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        except Exception as exc:
            await cls.registrar_fallo(
                db, tenant_id, solicitud_id, formato, str(exc),
                getattr(usuario, "id", None),
            )
            raise ValidationException(f"La generación del archivo falló: {exc}")

        partes: list[bytes] | None = None
        comprimido = False
        if not cls.validar_tamano(contenido):
            contenido = cls.comprimir_archivo(contenido)
            comprimido = True
            if not cls.validar_tamano(contenido):
                partes = cls.dividir_archivo(contenido)

        archivo_id = uuid.uuid4().hex
        nombre = cls.generar_nombre_archivo(
            formato, getattr(tenant, "razon_social", "tenant") or "tenant"
        )
        _ARCHIVOS[archivo_id] = {
            "contenido": contenido,
            "partes": partes,
            "media_type": media_type,
            "nombre": nombre,
            "tenant_id": tenant_id,
            "solicitud_id": solicitud_id,
            "formato": formato,
        }
        await cls.registrar_exportacion(
            db, tenant_id, getattr(usuario, "id", None), solicitud_id, formato, len(contenido)
        )
        return {
            "archivo_id": archivo_id,
            "nombre": nombre,
            "formato": formato,
            "media_type": media_type,
            "tamano": len(contenido),
            "comprimido": comprimido,
            "partes": len(partes) if partes else 1,
            "url_descarga": cls.obtener_url_descarga(archivo_id),
        }

    @classmethod
    async def reintentar_generacion(
        cls,
        db: AsyncSession,
        solicitud_id: int,
        formato: str,
        tenant_id: int,
        usuario=None,
        tenant=None,
    ) -> dict:
        """Reintenta una exportación fallida reutilizando la solicitud (CU11).

        Devuelve el mismo contrato que `exportar_reporte` (con `archivo_id` y
        `url_descarga` descargables) más la marca `reintentado`.
        """
        resultado = await cls.exportar_reporte(
            db,
            solicitud_id,
            formato,
            tenant_id,
            usuario,
            tenant,
            opciones={"reintento": True},
        )
        resultado["reintentado"] = True
        return resultado

    @classmethod
    def obtener_url_descarga(cls, archivo_id: str) -> str:
        return f"/api/v1/exportaciones/{archivo_id}"

    @classmethod
    def obtener_archivo(cls, archivo_id: str, tenant_id: int) -> dict:
        archivo = _ARCHIVOS.get(archivo_id)
        if archivo is None or archivo["tenant_id"] != tenant_id:
            raise NotFoundException("Archivo de exportación no encontrado")
        return archivo
