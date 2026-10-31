"""[EXAMEN-2] CU12 — Dashboard analítico y machine learning (plan, 9.3.6).

Cubre KPIs por rol y sucursal, caché de 5 minutos, datos insuficientes,
entrenamiento supervisado con métricas, predicción, segmentación, anomalías,
bloqueo del plan Básico y aislamiento de modelos por tenant.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.apps.analitica_avanzada.dashboard_service import DashboardService, limpiar_cache
from tests._fase3_helpers import crear_sucursal, crear_venta, sembrar_sinteticos


@pytest.fixture(autouse=True)
def cache_limpia():
    limpiar_cache()
    yield
    limpiar_cache()


@pytest.mark.asyncio
async def test_dashboard_admin_con_cache(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional,
    catalogo_tenant, cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="75.00", stock=10)
    cliente = await cliente_tenant(tenant)
    await crear_venta(db_session, tenant, catalogo, cliente["id"], cantidad=2)

    primera = await client.get("/api/v1/analitica/dashboard", headers=tenant["headers"])
    assert primera.status_code == 200, primera.text
    cuerpo = primera.json()
    assert cuerpo["rol"] == "Administrador"
    assert cuerpo["cache"] is False
    assert cuerpo["periodo"] == "MES"
    assert set(cuerpo["widgets"]) == {
        "ventas_dia", "ventas_mes", "stock_critico", "reservas_pendientes",
        "tasa_devoluciones", "top_cambiadas", "ticket_promedio",
    }
    assert cuerpo["widgets"]["ventas_dia"]["ordenes"] >= 1
    assert cuerpo["widgets"]["ventas_mes"]["actual"]["total"] >= 150.0

    segunda = await client.get("/api/v1/analitica/dashboard", headers=tenant["headers"])
    assert segunda.status_code == 200
    assert segunda.json()["cache"] is True

    DashboardService.invalidar_cache(int(tenant["id"]))
    tercera = await client.get("/api/v1/analitica/dashboard", headers=tenant["headers"])
    assert tercera.json()["cache"] is False


def test_widgets_por_rol_y_variacion():
    admin = DashboardService.obtener_widgets_por_rol("Administrador")
    encargado = DashboardService.obtener_widgets_por_rol("Encargado")
    cajero = DashboardService.obtener_widgets_por_rol("Cajero")

    assert "tasa_devoluciones" in admin
    assert "tasa_devoluciones" not in encargado
    assert "ventas_mes" in encargado
    assert "ventas_mes" not in cajero
    assert set(cajero) <= set(encargado) <= set(admin)
    assert DashboardService.calcular_variacion(120, 100) == 20.0
    assert DashboardService.calcular_variacion(100, 0) == 100.0


@pytest.mark.asyncio
async def test_kpis_por_sucursal_y_datos_insuficientes(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional,
    catalogo_tenant, cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="50.00", stock=10)
    cliente = await cliente_tenant(tenant)
    await crear_venta(db_session, tenant, catalogo, cliente["id"])

    otra_sucursal = await crear_sucursal(db_session, tenant, "Sucursal Secundaria Fase 3")
    sucursal_id = otra_sucursal["id"]

    con_ventas = await client.get(
        f"/api/v1/analitica/kpis?sucursal_id={catalogo['sucursal_id']}",
        headers=tenant["headers"],
    )
    assert con_ventas.status_code == 200, con_ventas.text
    assert con_ventas.json()["kpis"]["ventas_dia"]["ordenes"] >= 1

    sin_ventas = await client.get(
        f"/api/v1/analitica/kpis?sucursal_id={sucursal_id}",
        headers=tenant["headers"],
    )
    assert sin_ventas.status_code == 200, sin_ventas.text
    assert sin_ventas.json()["kpis"]["ventas_dia"]["ordenes"] == 0
    assert sin_ventas.json()["datos_suficientes"]["suficientes"] is False

    drilldown = await client.get(
        "/api/v1/analitica/kpis/ventas_dia/detalle", headers=tenant["headers"]
    )
    assert drilldown.status_code == 200, drilldown.text
    assert drilldown.json()["kpi"] == "ventas_dia"


@pytest.mark.asyncio
async def test_ml_bloqueado_para_plan_basico(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    entrenar = await client.post(
        "/api/v1/analitica/ml/entrenar",
        json={"objetivo": "VENTAS"},
        headers=tenant["headers"],
    )
    assert entrenar.status_code == 403, entrenar.text
    assert entrenar.json()["codigo"] == "FUNCIONALIDAD_NO_DISPONIBLE"

    modelos = await client.get(
        "/api/v1/analitica/ml/modelos", headers=tenant["headers"]
    )
    assert modelos.status_code == 403


@pytest.mark.asyncio
async def test_ml_sin_datos_suficientes(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    respuesta = await client.post(
        "/api/v1/analitica/ml/entrenar",
        json={"objetivo": "VENTAS"},
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 400, respuesta.text
    assert "insuficientes" in respuesta.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ml_entrenar_predecir_segmentar_y_anomalias(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional, catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    await catalogo_tenant(tenant, precio="100.00", stock=1000)
    await sembrar_sinteticos(
        db_session, tenant, cantidad_clientes=15, cantidad_ventas=180,
        dias=180, semilla=77,
    )

    entrenamiento = await client.post(
        "/api/v1/analitica/ml/entrenar",
        json={"objetivo": "VENTAS", "hiperparametros": {"n_estimators": 30}},
        headers=tenant["headers"],
    )
    assert entrenamiento.status_code == 200, entrenamiento.text
    modelo = entrenamiento.json()
    assert modelo["modelo_id"]
    assert modelo["algoritmo"] in ("RandomForestRegressor", "LinearRegression")
    for metrica in ("rmse", "mae", "r2"):
        assert metrica in modelo["metricas"]
    assert "LinearRegression" in modelo["metricas"]["comparativa"]
    assert modelo["mensaje"]

    prediccion = await client.post(
        "/api/v1/analitica/ml/predecir",
        json={"modelo_id": modelo["modelo_id"], "horizonte": 5},
        headers=tenant["headers"],
    )
    assert prediccion.status_code == 200, prediccion.text
    proyeccion = prediccion.json()
    assert proyeccion["horizonte"] == 5
    assert len(proyeccion["predicciones"]) == 5
    for fila in proyeccion["predicciones"]:
        assert fila["limite_inferior"] <= fila["prediccion"] <= fila["limite_superior"]
        assert fila["prediccion"] >= 0

    segmentacion = await client.post(
        "/api/v1/analitica/ml/segmentar",
        json={"n_clusters": 3, "persistir": True},
        headers=tenant["headers"],
    )
    assert segmentacion.status_code == 200, segmentacion.text
    segmentos = segmentacion.json()
    assert segmentos["algoritmo"] == "KMeans"
    assert "silhouette" in segmentos
    assert segmentos["segmentos"]
    assert segmentos["modelo_id"]

    anomalias = await client.post(
        "/api/v1/analitica/ml/anomalias",
        json={"periodo_dias": 180, "persistir": True},
        headers=tenant["headers"],
    )
    assert anomalias.status_code == 200, anomalias.text
    hallazgos = anomalias.json()
    assert hallazgos["algoritmo"] == "IsolationForest"
    assert hallazgos["total_ventas"] >= 5
    assert hallazgos["anomalias_detectadas"] >= 0
    assert hallazgos["modelo_id"]

    listado = await client.get(
        "/api/v1/analitica/ml/modelos", headers=tenant["headers"]
    )
    assert listado.status_code == 200, listado.text
    objetivos = {item["objetivo"] for item in listado.json()["modelos"]}
    assert {"VENTAS", "SEGMENTACION_CLIENTES", "ANOMALIAS_VENTAS"} <= objetivos

    detalle = await client.get(
        f"/api/v1/analitica/ml/modelos/{modelo['modelo_id']}", headers=tenant["headers"]
    )
    assert detalle.status_code == 200, detalle.text
    assert detalle.json()["id"] == modelo["modelo_id"]


@pytest.mark.asyncio
async def test_modelos_aislados_por_tenant(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional, catalogo_tenant,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="ML A")
    await catalogo_tenant(tenant_a, stock=1000)
    await sembrar_sinteticos(
        db_session, tenant_a, cantidad_clientes=12, cantidad_ventas=160,
        dias=160, semilla=93,
    )
    entrenado = await client.post(
        "/api/v1/analitica/ml/entrenar",
        json={"objetivo": "VENTAS", "hiperparametros": {"n_estimators": 20}},
        headers=tenant_a["headers"],
    )
    assert entrenado.status_code == 200, entrenado.text
    modelo_id = entrenado.json()["modelo_id"]

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="ML B")
    ajeno = await client.get(
        f"/api/v1/analitica/ml/modelos/{modelo_id}", headers=tenant_b["headers"]
    )
    assert ajeno.status_code == 404, ajeno.text

    listado_b = await client.get(
        "/api/v1/analitica/ml/modelos", headers=tenant_b["headers"]
    )
    assert listado_b.status_code == 200
    assert listado_b.json()["modelos"] == []
