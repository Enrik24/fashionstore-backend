"""[EXAMEN-2] CU11 — Exportación de reportes (plan, 9.3.5 y 9.3.11).

Cubre PDF con branding y metadatos, Excel con datos, división de archivos
grandes, exportación de una solicitud no ejecutada, reintento y auditoría.
"""
from __future__ import annotations

import io

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.apps.analitica_avanzada.exportacion_service import ExportacionService
from app.apps.gestion_saas.models import Tenant
from app.apps.gestion_usuarios.models import Bitacora
from tests._fase3_helpers import crear_venta


async def _reporte_ejecutado(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional, catalogo_tenant,
    cliente_tenant, *, texto: str = "reporte de ventas",
) -> dict:
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="120.00", stock=10)
    cliente = await cliente_tenant(tenant)
    await crear_venta(db_session, tenant, catalogo, cliente["id"], cantidad=2)

    solicitud = await client.post(
        "/api/v1/reportes-ia/solicitar",
        json={"texto": texto},
        headers=tenant["headers"],
    )
    assert solicitud.status_code == 200, solicitud.text
    return {"tenant": tenant, "solicitud": solicitud.json()}


@pytest.mark.asyncio
async def test_exportar_pdf_con_branding_y_descarga(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional,
    catalogo_tenant, cliente_tenant,
):
    contexto = await _reporte_ejecutado(
        client, db_session, tenant_nuevo, plan_profesional,
        catalogo_tenant, cliente_tenant,
    )
    tenant = contexto["tenant"]
    solicitud = contexto["solicitud"]

    exportado = await client.post(
        f"/api/v1/reportes-ia/{solicitud['solicitud_id']}/exportar",
        json={"formato": "PDF"},
        headers=tenant["headers"],
    )
    assert exportado.status_code == 200, exportado.text
    cuerpo = exportado.json()
    assert cuerpo["formato"] == "PDF"
    assert cuerpo["media_type"] == "application/pdf"
    assert cuerpo["nombre"].endswith(".pdf")
    assert cuerpo["url_descarga"] == f"/api/v1/exportaciones/{cuerpo['archivo_id']}"

    descarga = await client.get(
        f"/api/v1/exportaciones/{cuerpo['archivo_id']}", headers=tenant["headers"]
    )
    assert descarga.status_code == 200
    assert descarga.content.startswith(b"%PDF")
    assert "application/pdf" in descarga.headers["content-type"]

    # Branding y metadatos del tenant disponibles para el documento.
    tenant_db = await db_session.get(Tenant, int(tenant["id"]))
    documento: dict = {}
    ExportacionService.aplicar_branding(documento, tenant_db)
    ExportacionService.incluir_metadatos(
        documento, tenant_db, None, solicitud["parametros"]
    )
    assert documento["branding"]["razon_social"] == tenant["tenant"]["razon_social"]
    assert documento["branding"]["nit"] == tenant["tenant"]["nit"]
    assert documento["metadatos"]["tenant"] == tenant["tenant"]["razon_social"]
    assert documento["metadatos"]["fecha"]

    # Auditoría de la descarga.
    eventos = (await db_session.execute(
        select(Bitacora).where(
            Bitacora.tenant_id == int(tenant["id"]),
            Bitacora.accion == "EXPORTAR_REPORTE_IA",
        )
    )).scalars().all()
    assert eventos, "La exportación debe quedar auditada"
    assert str(solicitud["solicitud_id"]) in (eventos[-1].detalles or "")


@pytest.mark.asyncio
async def test_exportar_excel_con_hoja_de_datos(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional,
    catalogo_tenant, cliente_tenant,
):
    from openpyxl import load_workbook

    contexto = await _reporte_ejecutado(
        client, db_session, tenant_nuevo, plan_profesional,
        catalogo_tenant, cliente_tenant,
    )
    tenant = contexto["tenant"]
    solicitud = contexto["solicitud"]

    exportado = await client.post(
        f"/api/v1/reportes-ia/{solicitud['solicitud_id']}/exportar",
        json={"formato": "EXCEL"},
        headers=tenant["headers"],
    )
    assert exportado.status_code == 200, exportado.text
    cuerpo = exportado.json()
    assert cuerpo["formato"] == "EXCEL"
    assert cuerpo["nombre"].endswith(".xlsx")

    descarga = await client.get(
        f"/api/v1/exportaciones/{cuerpo['archivo_id']}", headers=tenant["headers"]
    )
    assert descarga.status_code == 200
    assert descarga.content.startswith(b"PK")

    libro = load_workbook(io.BytesIO(descarga.content))
    assert "Datos" in libro.sheetnames
    assert "Resumen" in libro.sheetnames
    encabezados = [celda.value for celda in libro["Datos"][4]]
    assert "fecha" in encabezados


@pytest.mark.asyncio
async def test_exportar_solicitud_no_ejecutada_y_reintento(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    ambigua = await client.post(
        "/api/v1/reportes-ia/solicitar",
        json={"texto": "ventas de este mes"},
        headers=tenant["headers"],
    )
    assert ambigua.status_code == 200
    solicitud_id = ambigua.json()["solicitud_id"]

    fallo = await client.post(
        f"/api/v1/reportes-ia/{solicitud_id}/exportar",
        json={"formato": "PDF"},
        headers=tenant["headers"],
    )
    assert fallo.status_code == 400, fallo.text
    assert "ejecutada" in fallo.json()["detail"].lower()

    # El reintento es real: vuelve a validar la solicitud y falla igual,
    # dejando rastro del intento en la bitácora.
    reintento = await client.post(
        f"/api/v1/reportes-ia/{solicitud_id}/reintentar",
        json={"formato": "PDF"},
        headers=tenant["headers"],
    )
    assert reintento.status_code == 400, reintento.text
    assert "ejecutada" in reintento.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reintento_genera_un_archivo_descargable(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional,
    catalogo_tenant, cliente_tenant,
):
    contexto = await _reporte_ejecutado(
        client, db_session, tenant_nuevo, plan_profesional,
        catalogo_tenant, cliente_tenant,
    )
    tenant = contexto["tenant"]
    solicitud = contexto["solicitud"]

    reintento = await client.post(
        f"/api/v1/reportes-ia/{solicitud['solicitud_id']}/reintentar",
        json={"formato": "PDF"},
        headers=tenant["headers"],
    )
    assert reintento.status_code == 200, reintento.text
    cuerpo = reintento.json()
    assert cuerpo["reintentado"] is True
    assert cuerpo["archivo_id"]
    assert cuerpo["url_descarga"] == f"/api/v1/exportaciones/{cuerpo['archivo_id']}"

    descarga = await client.get(cuerpo["url_descarga"], headers=tenant["headers"])
    assert descarga.status_code == 200
    assert descarga.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_fallo_de_generacion_queda_en_bitacora(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional,
    catalogo_tenant, cliente_tenant, monkeypatch,
):
    contexto = await _reporte_ejecutado(
        client, db_session, tenant_nuevo, plan_profesional,
        catalogo_tenant, cliente_tenant,
    )
    tenant = contexto["tenant"]
    solicitud = contexto["solicitud"]

    def _falla(*args, **kwargs):
        raise RuntimeError("PDF roto")

    monkeypatch.setattr(ExportacionService, "generar_pdf", _falla)
    exportado = await client.post(
        f"/api/v1/reportes-ia/{solicitud['solicitud_id']}/exportar",
        json={"formato": "PDF"},
        headers=tenant["headers"],
    )
    assert exportado.status_code == 400, exportado.text
    assert "PDF roto" in exportado.json()["detail"]

    db_session.expire_all()
    eventos = (await db_session.execute(
        select(Bitacora).where(
            Bitacora.tenant_id == int(tenant["id"]),
            Bitacora.accion == "EXPORTACION_FALLIDA",
        )
    )).scalars().all()
    assert len(eventos) == 1
    assert "PDF roto" in eventos[0].detalles


def test_agregar_graficos_describe_visualizaciones():
    documento = {
        "titulo": "Ventas por período",
        "columnas": ["fecha", "total"],
        "filas": [
            {"fecha": "2026-10-01", "total": 120.0},
            {"fecha": "2026-10-02", "total": 90.0},
        ],
        "resumen": {},
    }

    ExportacionService.agregar_graficos(documento, ["LINEAS"])
    assert documento["graficos"][0]["tipo"] == "LINEAS"
    assert documento["graficos"][0]["eje_x"] == "fecha"
    assert documento["graficos"][0]["series"][0]["nombre"] == "total"
    assert documento["graficos"][0]["series"][0]["datos"] == [120.0, 90.0]
    assert documento["graficos"][0]["categorias"] == ["2026-10-01", "2026-10-02"]

    ExportacionService.agregar_graficos(documento, ["PIE"])
    assert documento["graficos"][0]["tipo"] == "TARTAS"

    vacio = {"columnas": ["fecha"], "filas": [], "resumen": {}}
    ExportacionService.agregar_graficos(vacio, None)
    assert vacio["graficos"] == []


def test_archivo_grande_se_comprime_y_se_divide():
    contenido = b"x" * (2 * 1024 * 1024)
    comprimido = ExportacionService.comprimir_archivo(contenido)
    assert len(comprimido) < len(contenido)

    partes = ExportacionService.dividir_archivo(contenido, max_mb=1)
    assert len(partes) == 2
    assert b"".join(partes) == contenido
    assert all(len(parte) <= 1024 * 1024 for parte in partes)


def test_nombre_de_archivo_sin_caracteres_invalidos():
    nombre = ExportacionService.generar_nombre_archivo(
        "EXCEL", "Moda & Estilo / Sucursal #1"
    )
    assert nombre.startswith("excel_")
    assert nombre.endswith(".xlsx")
    assert " " not in nombre
    assert "&" not in nombre
    assert "/" not in nombre
