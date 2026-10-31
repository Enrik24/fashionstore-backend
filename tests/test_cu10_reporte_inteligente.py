"""[EXAMEN-2] CU10 — Reporte inteligente (plan, 9.3.4 y 9.3.11).

Cubre texto, voz (transcripción simulada), bloqueo del plan Básico, solicitud
ambigua, reporte no soportado, aislamiento del tenant, rechazo de SQL sin
filtro de tenant, historial y límite de filas.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.apps.analitica_avanzada.plantilla_service import PlantillaReporteService
from app.apps.analitica_avanzada.plantillas import obtener_plantilla
from app.apps.analitica_avanzada.reporte_service import ReporteGenerativoService
from app.exceptions import FuncionalidadNoDisponible, ValidationException
from tests._fase3_helpers import crear_venta


def _texto(payload: dict) -> dict:
    return {"texto": payload}


@pytest.mark.asyncio
async def test_reporte_por_texto_ejecuta_plantilla_aprobada(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=20)
    cliente = await cliente_tenant(tenant)
    await crear_venta(db_session, tenant, catalogo, cliente["id"], cantidad=2)

    respuesta = await client.post(
        "/api/v1/reportes-ia/solicitar",
        json=_texto("Quiero un reporte de ventas"),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "EJECUTADA"
    assert cuerpo["plantilla"] == "ventas_periodo"
    assert cuerpo["requiere_aclaracion"] is False
    assert cuerpo["sql"] and "tenant_id = :tenant_id" in cuerpo["sql"]
    assert len(cuerpo["resultado"]) >= 1
    assert cuerpo["resumen"]["narrativa"]

    detalle = await client.get(
        f"/api/v1/reportes-ia/{cuerpo['solicitud_id']}", headers=tenant["headers"]
    )
    assert detalle.status_code == 200, detalle.text
    assert detalle.json()["sql"] == cuerpo["sql"]
    assert detalle.json()["resultado"] == cuerpo["resultado"]


@pytest.mark.asyncio
async def test_reporte_por_voz_usa_transcripcion(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    monkeypatch,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    cliente = await cliente_tenant(tenant)
    await crear_venta(db_session, tenant, catalogo, cliente["id"])

    async def transcripcion_simulada(archivo, filename="audio.webm"):
        assert archivo == b"nota-de-voz"
        return "reporte de ventas del mes"

    monkeypatch.setattr(
        ReporteGenerativoService, "transcribir_audio", transcripcion_simulada
    )
    respuesta = await client.post(
        "/api/v1/reportes-ia/solicitar",
        json={"audio_base64": base64.b64encode(b"nota-de-voz").decode("ascii")},
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "EJECUTADA"
    assert cuerpo["plantilla"] == "ventas_periodo"


@pytest.mark.asyncio
async def test_plan_basico_no_accede_a_reportes_ia(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    respuesta = await client.post(
        "/api/v1/reportes-ia/solicitar",
        json=_texto("reporte de ventas"),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 403, respuesta.text
    assert respuesta.json()["codigo"] == "FUNCIONALIDAD_NO_DISPONIBLE"

    interpretar = await client.post(
        "/api/v1/reportes-ia/interpretar",
        json=_texto("reporte de ventas"),
        headers=tenant["headers"],
    )
    assert interpretar.status_code == 403, interpretar.text


@pytest.mark.asyncio
async def test_solicitud_ambigua_pide_aclaracion(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)

    respuesta = await client.post(
        "/api/v1/reportes-ia/solicitar",
        json=_texto("ventas de este mes"),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["requiere_aclaracion"] is True
    assert cuerpo["estado"] == "AMBIGUA"
    assert "período" in cuerpo["pregunta"].lower()


@pytest.mark.asyncio
async def test_reporte_no_soportado(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)

    respuesta = await client.post(
        "/api/v1/reportes-ia/solicitar",
        json=_texto("quiero saber el clima de mañana"),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["requiere_aclaracion"] is True
    assert cuerpo["estado"] == "NO_SOPORTADA"
    assert cuerpo["plantilla"] is None


@pytest.mark.asyncio
async def test_reporte_aislado_por_tenant(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Tenant A")
    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Tenant B")
    catalogo_a = await catalogo_tenant(tenant_a, precio="100.00", stock=10)
    catalogo_b = await catalogo_tenant(tenant_b, precio="999.00", stock=10)
    cliente_a = await cliente_tenant(tenant_a)
    cliente_b = await cliente_tenant(tenant_b)
    await crear_venta(db_session, tenant_a, catalogo_a, cliente_a["id"], cantidad=1)
    await crear_venta(db_session, tenant_b, catalogo_b, cliente_b["id"], cantidad=1)

    respuesta = await client.post(
        "/api/v1/reportes-ia/solicitar",
        json=_texto("reporte de ventas"),
        headers=tenant_a["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    filas = respuesta.json()["resultado"]
    assert filas, "El reporte del tenant A debe tener datos"
    assert sum(float(fila["total"]) for fila in filas) == pytest.approx(100.0)


def test_sql_sin_tenant_es_rechazado():
    with pytest.raises(FuncionalidadNoDisponible):
        PlantillaReporteService.validar_filtro_tenant(
            "SELECT id, total FROM ordenes"
        )
    with pytest.raises(ValidationException):
        PlantillaReporteService.validar_filtro_tenant(
            "SELECT * FROM ordenes WHERE tenant_id = :tenant_id"
        )

    plantilla = obtener_plantilla("ventas_periodo")
    with pytest.raises(ValidationException):
        PlantillaReporteService.construir_consulta(
            plantilla,
            {"fecha_inicio": "2026-01-01", "fecha_fin": "2026-02-01"},
            None,
        )


@pytest.mark.asyncio
async def test_historial_y_catalogo_de_plantillas(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    await client.post(
        "/api/v1/reportes-ia/solicitar",
        json=_texto("reporte de ventas"),
        headers=tenant["headers"],
    )

    plantillas = await client.get(
        "/api/v1/reportes-ia/plantillas", headers=tenant["headers"]
    )
    assert plantillas.status_code == 200, plantillas.text
    claves = {item["clave"] for item in plantillas.json()["plantillas"]}
    assert {"ventas_periodo", "stock_critico", "devoluciones_periodo"} <= claves

    historial = await client.get(
        "/api/v1/reportes-ia/historial", headers=tenant["headers"]
    )
    assert historial.status_code == 200, historial.text
    assert len(historial.json()) >= 1
    assert historial.json()[0]["prompt"] == "reporte de ventas"


@pytest.mark.asyncio
async def test_limite_de_filas_en_ejecucion(
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    cliente = await cliente_tenant(tenant)
    ahora = datetime.now(timezone.utc)
    await crear_venta(
        db_session, tenant, catalogo, cliente["id"],
        fecha=ahora - timedelta(days=1), numero_orden="LIMITE-1",
    )
    await crear_venta(
        db_session, tenant, catalogo, cliente["id"],
        fecha=ahora - timedelta(days=2), numero_orden="LIMITE-2",
    )

    plantilla = obtener_plantilla("ventas_periodo")
    parametros = PlantillaReporteService.validar_parametros(
        plantilla,
        {
            "fecha_inicio": (ahora - timedelta(days=5)).date().isoformat(),
            "fecha_fin": (ahora + timedelta(days=1)).date().isoformat(),
        },
    )
    sql, binds = PlantillaReporteService.construir_consulta(
        plantilla,
        parametros,
        int(tenant["id"]),
    )
    datos, _ = await ReporteGenerativoService.ejecutar_consulta(
        db_session, sql, binds, limite=1
    )
    assert len(datos) == 1


def test_manejar_fallo_ia_ofrece_plantillas():
    respuesta = ReporteGenerativoService.manejar_fallo_ia("sin conexión")
    assert respuesta["error"] == "IA_NO_DISPONIBLE"
    assert respuesta["plantillas"]
