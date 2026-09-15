"""
Tests completos para los endpoints de la Iteración 3:
- Reportes Analíticos Gerenciales (CU15)
- Indicadores KPI y Dashboard Ejecutivo (CU07)
- Recomendaciones de Moda con Inteligencia Artificial (CU13)
- Asistente Virtual Interactivo (CU23)
- Vestidor Virtual (CU21)
- Reportes por Comando de Voz con IA (CU22)
- Análisis de Tendencias de Moda (CU20)
- Endpoints de Catálogo por Categoría, Temporada y Colección
- Historial de Compras y Reservas del Cliente
"""
import pytest
from httpx import AsyncClient
from datetime import datetime


@pytest.mark.asyncio
async def test_01_reportes_gerenciales(client: AsyncClient, admin_auth_tokens: dict):
    """Prueba la generación de reportes analíticos (CU15)."""
    headers = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}

    # 1. Reporte de Ventas (JSON)
    res_v = await client.get("/api/v1/reportes/ventas", headers=headers)
    assert res_v.status_code == 200, f"Error: {res_v.text}"
    data_v = res_v.json()
    assert "resumen" in data_v
    assert "total_recaudado" in data_v["resumen"]

    # 2. Reporte de Ventas (CSV)
    res_csv = await client.get("/api/v1/reportes/ventas?formato=CSV", headers=headers)
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers.get("content-type", "")

    # 3. Reporte de Inventario
    res_inv = await client.get("/api/v1/reportes/inventario", headers=headers)
    assert res_inv.status_code == 200
    assert "total_items_registrados" in res_inv.json()

    # 4. Reporte de Reservas
    res_res = await client.get("/api/v1/reportes/reservas", headers=headers)
    assert res_res.status_code == 200
    assert "tasa_conversion_recogida_pct" in res_res.json()

    # 5. Reporte de Clientes
    res_cli = await client.get("/api/v1/reportes/clientes", headers=headers)
    assert res_cli.status_code == 200
    assert "total_clientes_registrados" in res_cli.json()

    # 6. Reporte Financiero
    res_fin = await client.get("/api/v1/reportes/financiero", headers=headers)
    assert res_fin.status_code == 200
    assert "ingresos" in res_fin.json()


@pytest.mark.asyncio
async def test_02_kpis_y_dashboard(client: AsyncClient, admin_auth_tokens: dict):
    """Prueba los endpoints de KPIs y Dashboard (CU07)."""
    headers = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}

    # 1. Dashboard de KPIs
    res_dash = await client.get("/api/v1/kpis/dashboard", headers=headers)
    assert res_dash.status_code == 200, f"Error: {res_dash.text}"
    data_dash = res_dash.json()
    assert "total_ventas_mes" in data_dash
    assert "tasa_conversion_reservas" in data_dash

    # 2. Crear un nuevo KPI
    res_kpi = await client.post(
        "/api/v1/kpis",
        json={
            "nombre": "Rotación de Stock Trimestral",
            "descripcion": "Índice de velocidad de rotación de prendas",
            "valor_actual": 4.5,
            "valor_objetivo": 6.0,
            "unidad_medida": "veces/año",
            "tendencia": "Positiva",
            "periodo": "Q3 2026"
        },
        headers=headers
    )
    assert res_kpi.status_code == 201
    kpi_id = res_kpi.json()["id"]

    # 3. Listar KPIs
    res_list = await client.get("/api/v1/kpis", headers=headers)
    assert res_list.status_code == 200
    assert len(res_list.json()) >= 1

    # 4. Actualizar KPI
    res_up = await client.put(
        f"/api/v1/kpis/{kpi_id}",
        json={
            "nombre": "Rotación de Stock Trimestral Actualizado",
            "valor_actual": 5.2,
            "valor_objetivo": 6.0
        },
        headers=headers
    )
    assert res_up.status_code == 200
    assert res_up.json()["valor_actual"] == 5.2


@pytest.mark.asyncio
async def test_03_servicios_inteligentes_ia(client: AsyncClient, admin_auth_tokens: dict):
    """Prueba los servicios de IA: Recomendaciones, Asistente, Vestidor, Voz y Tendencias."""
    headers = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}

    # 1. Recomendaciones IA (CU13)
    res_rec = await client.post(
        "/api/v1/inteligencia/recomendaciones",
        json={"preferencias": "Prendas elegantes para clima cálido", "limite": 4}
    )
    assert res_rec.status_code == 200, f"Error: {res_rec.text}"
    data_rec = res_rec.json()
    assert "recomendaciones" in data_rec
    assert "mensaje_personalizado" in data_rec

    # 2. Asistente Virtual de Moda (CU23)
    res_chat = await client.post(
        "/api/v1/inteligencia/asistente-chat",
        json={
            "mensaje": "¿Qué vestido me recomiendas para una fiesta de noche?",
            "historial": []
        }
    )
    assert res_chat.status_code == 200
    data_chat = res_chat.json()
    assert "respuesta" in data_chat
    assert "sugerencias" in data_chat

    # 3. Tendencias de Moda (CU20)
    res_tend = await client.get("/api/v1/inteligencia/tendencias")
    assert res_tend.status_code == 200
    data_tend = res_tend.json()
    assert "tendencias_destacadas" in data_tend
    assert "prediccion_demanda" in data_tend

    # 4. Reporte por Comando de Voz (CU22)
    res_voz = await client.post(
        "/api/v1/inteligencia/reporte-voz",
        json={"transcripcion": "Muéstrame el resumen de ventas de esta semana"},
        headers=headers
    )
    assert res_voz.status_code == 200
    data_voz = res_voz.json()
    assert data_voz["tipo_reporte"] == "VENTAS"
    assert "datos" in data_voz


@pytest.mark.asyncio
async def test_04_catalogo_filtros_productos(client: AsyncClient):
    """Prueba endpoints de productos por categoría, temporada y colección."""
    # Listar categorías existentes
    res_cat = await client.get("/api/v1/categorias/")
    if res_cat.status_code == 200 and len(res_cat.json()) > 0:
        cat_id = res_cat.json()[0]["id"]
        res_p = await client.get(f"/api/v1/categorias/{cat_id}/productos")
        assert res_p.status_code == 200
        assert isinstance(res_p.json(), list)

    # Listar temporadas existentes
    res_temp = await client.get("/api/v1/temporadas/")
    if res_temp.status_code == 200 and len(res_temp.json()) > 0:
        temp_id = res_temp.json()[0]["id"]
        res_tp = await client.get(f"/api/v1/temporadas/{temp_id}/productos")
        assert res_tp.status_code == 200
        assert isinstance(res_tp.json(), list)
