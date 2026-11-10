"""[EXAMEN-2] CU12 — Dashboard analítico y machine learning (plan, 9.3.6).

Cubre KPIs por rol y sucursal, caché de 5 minutos, datos insuficientes,
entrenamiento supervisado con métricas, predicción, segmentación, anomalías,
bloqueo del plan Básico y aislamiento de modelos por tenant.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.apps.analitica_avanzada.dashboard_service import DashboardService, limpiar_cache
from app.apps.analitica_avanzada.ml_service import MLService
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
        # [EXAMEN-2] Punto 4: KPIs de reposiciones y compensaciones.
        "tasa_reposiciones", "prendas_repuestas", "monto_compensaciones",
    }
    assert "tasa_pct" in cuerpo["widgets"]["tasa_reposiciones"]
    assert "monto_total" in cuerpo["widgets"]["monto_compensaciones"]
    assert isinstance(cuerpo["widgets"]["prendas_repuestas"], list)
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


# ----------------------------------------------------------------------
# CU12 — brechas cerradas: período, accuracy, último conocido
# ----------------------------------------------------------------------

def test_inicio_periodo_dia_semana_mes_anio():
    from datetime import date, timedelta

    hoy = date.today()
    assert DashboardService.inicio_periodo("DIA") == hoy
    assert DashboardService.inicio_periodo("SEMANA") == hoy - timedelta(days=6)
    assert DashboardService.inicio_periodo("MES") == hoy.replace(day=1)
    assert DashboardService.inicio_periodo("ANIO") == hoy.replace(month=1, day=1)
    assert DashboardService.inicio_periodo(None) == hoy.replace(day=1)
    assert DashboardService.inicio_periodo("DESCONOCIDO") == hoy.replace(day=1)


def test_metricas_supervisado_incluye_accuracy_mape():
    metricas = MLService.calcular_metricas(
        "VENTAS", [100, 200, 300], [110, 190, 310]
    )
    assert {"rmse", "mae", "r2", "accuracy", "mape", "filas_prueba"} <= set(metricas)
    assert 0.0 <= metricas["accuracy"] <= 1.0
    assert metricas["mape"] >= 0.0


@pytest.mark.asyncio
async def test_dashboard_periodos_ultimo_conocido_y_drilldown_sucursal(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional,
    catalogo_tenant, cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="75.00", stock=10)
    cliente = await cliente_tenant(tenant)
    await crear_venta(db_session, tenant, catalogo, cliente["id"], cantidad=2)

    for periodo in ("DIA", "SEMANA", "MES", "ANIO"):
        resp = await client.get(
            f"/api/v1/analitica/dashboard?periodo={periodo}",
            headers=tenant["headers"],
        )
        assert resp.status_code == 200, (periodo, resp.text)
        cuerpo = resp.json()
        assert cuerpo["periodo"] == periodo
        assert cuerpo["actualizacion_automatica_segundos"] == 300
        assert cuerpo["fecha_corte"]

    # Flujo offline: último conocido tras haber consultado el dashboard.
    ultimo = await client.get(
        "/api/v1/analitica/dashboard/ultimo-conocido?periodo=MES",
        headers=tenant["headers"],
    )
    assert ultimo.status_code == 200, ultimo.text
    assert ultimo.json()["desactualizado"] is True
    assert ultimo.json()["fecha_corte"]

    # Drill-down con sucursal respeta el contexto de análisis (CU12 paso 6).
    detalle = await client.get(
        f"/api/v1/analitica/kpis/tasa_devoluciones/detalle?sucursal_id={catalogo['sucursal_id']}&periodo=MES",
        headers=tenant["headers"],
    )
    assert detalle.status_code == 200, detalle.text
    assert detalle.json()["kpi"] == "tasa_devoluciones"

    detalle_repuestas = await client.get(
        f"/api/v1/analitica/kpis/prendas_repuestas/detalle?sucursal_id={catalogo['sucursal_id']}&periodo=MES",
        headers=tenant["headers"],
    )
    assert detalle_repuestas.status_code == 200, detalle_repuestas.text
    assert detalle_repuestas.json()["kpi"] == "prendas_repuestas"


@pytest.mark.asyncio
async def test_ml_entrenar_incluye_accuracy(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional, catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    await catalogo_tenant(tenant, precio="100.00", stock=1000)
    await sembrar_sinteticos(
        db_session, tenant, cantidad_clientes=15, cantidad_ventas=180,
        dias=180, semilla=77,
    )
    respuesta = await client.post(
        "/api/v1/analitica/ml/entrenar",
        json={"objetivo": "VENTAS", "hiperparametros": {"n_estimators": 30}},
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    metricas = respuesta.json()["metricas"]
    assert "accuracy" in metricas
    assert "mape" in metricas
    assert 0.0 <= metricas["accuracy"] <= 1.0


@pytest.mark.asyncio
async def test_ml_entrenar_con_pocas_reales_y_sinteticos_suficientes(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional,
    catalogo_tenant, cliente_tenant,
):
    """Regresión: 1 venta real + sintéticos → entrena con sintéticos (200)."""
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=1000)
    cliente = await cliente_tenant(tenant)
    await crear_venta(db_session, tenant, catalogo, cliente["id"], cantidad=1)
    await sembrar_sinteticos(
        db_session, tenant, cantidad_clientes=15, cantidad_ventas=180,
        dias=180, semilla=77,
    )
    respuesta = await client.post(
        "/api/v1/analitica/ml/entrenar",
        json={"objetivo": "VENTAS", "hiperparametros": {"n_estimators": 30}},
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    assert "accuracy" in respuesta.json()["metricas"]
    assert respuesta.json()["metricas"]["filas_totales"] >= 14
