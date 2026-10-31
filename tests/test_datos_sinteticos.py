"""[EXAMEN-2] Datos sintéticos RF-023 (plan, 9.3.7 y 9.3.11).

Cubre la generación coherente, la marca `es_sintetico`, la no contaminación de
reportes/dashboard, el uso exclusivo en ML, el borrado seguro y el aislamiento
entre tenants.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.apps.analitica_avanzada.dashboard_service import limpiar_cache
from app.apps.analitica_avanzada.datos_sinteticos_service import (
    DatosSinteticosService,
    limpiar_datasets,
)
from app.apps.analitica_avanzada.ml_service import MLService
from app.apps.gestion_catalogo.models import MovimientoInventario
from app.apps.gestion_usuarios.models import Cliente
from app.apps.gestion_ventas.models import Orden
from tests._fase3_helpers import crear_venta, sembrar_sinteticos


@pytest.fixture(autouse=True)
def estado_limpio():
    limpiar_datasets()
    limpiar_cache()
    yield
    limpiar_datasets()
    limpiar_cache()


@pytest.mark.asyncio
async def test_generacion_coherente_y_marcada(
    db_session, tenant_nuevo, plan_profesional, catalogo_tenant
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    await catalogo_tenant(tenant, precio="100.00", stock=1000)

    resumen = await DatosSinteticosService.generar_dataset(
        db_session,
        int(tenant["id"]),
        {
            "cantidad_clientes": 10,
            "cantidad_ventas": 60,
            "dias": 90,
            "semilla": 11,
        },
    )
    assert resumen["validacion"]["ok"] is True, resumen["validacion"]["errores"]
    assert resumen["clientes"] == 10
    assert resumen["ventas"] >= 10
    assert resumen["sembrado"] is False

    dataset = DatosSinteticosService._obtener_dataset(
        resumen["dataset_id"], int(tenant["id"])
    )
    assert dataset["es_sintetico"] is True
    assert all(cliente["es_sintetico"] for cliente in dataset["clientes"])
    assert all(venta["es_sintetico"] for venta in dataset["ventas"])
    assert all(
        movimiento["es_sintetico"] for movimiento in dataset["movimientos"]
    )

    limite = datetime.utcnow() + timedelta(days=1)
    for venta in dataset["ventas"]:
        subtotal = round(
            sum(float(linea["subtotal"]) for linea in venta["detalles"]), 2
        )
        assert subtotal == pytest.approx(float(venta["total"]))
        assert datetime.fromisoformat(venta["fecha"]) <= limite
        assert all(linea["cantidad"] > 0 for linea in venta["detalles"])

    lineas = sum(len(venta["detalles"]) for venta in dataset["ventas"])
    assert len(dataset["movimientos"]) == lineas
    assert all(movimiento["cantidad"] < 0 for movimiento in dataset["movimientos"])


@pytest.mark.asyncio
async def test_siembra_marca_sinteticos_y_no_contamina_reportes(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    tenant_id = int(tenant["id"])
    await catalogo_tenant(tenant, precio="100.00", stock=1000)

    sembrado = await sembrar_sinteticos(
        db_session,
        tenant,
        cantidad_clientes=12,
        cantidad_ventas=90,
        dias=120,
        semilla=2026,
    )
    siembra = sembrado["siembra"]
    assert siembra["sembrado"] is True
    assert siembra["ordenes"] >= 30
    assert siembra["clientes"] == 12

    ordenes = (await db_session.execute(
        select(Orden).where(
            Orden.tenant_id == tenant_id, Orden.es_sintetico.is_(True)
        )
    )).scalars().all()
    assert len(ordenes) == siembra["ordenes"]
    assert all(orden.es_sintetico for orden in ordenes)

    clientes = (await db_session.execute(
        select(Cliente).where(
            Cliente.tenant_id == tenant_id, Cliente.es_sintetico.is_(True)
        )
    )).scalars().all()
    assert len(clientes) == siembra["clientes"]

    movimientos = (await db_session.execute(
        select(MovimientoInventario).where(
            MovimientoInventario.tenant_id == tenant_id,
            MovimientoInventario.es_sintetico.is_(True),
        )
    )).scalars().all()
    assert len(movimientos) == siembra["movimientos"]

    # El dashboard y los reportes solo consideran datos reales.
    dashboard = await client.get(
        "/api/v1/analitica/dashboard", headers=tenant["headers"]
    )
    assert dashboard.status_code == 200, dashboard.text
    widgets = dashboard.json()["widgets"]
    assert widgets["ventas_dia"]["ordenes"] == 0
    assert widgets["ventas_mes"]["actual"]["ordenes"] == 0

    reporte = await client.post(
        "/api/v1/reportes-ia/solicitar",
        json={"texto": "reporte de ventas"},
        headers=tenant["headers"],
    )
    assert reporte.status_code == 200, reporte.text
    cuerpo = reporte.json()
    assert cuerpo["estado"] == "EJECUTADA"
    assert cuerpo["resultado"] == []
    assert "es_sintetico = false" in cuerpo["sql"]


@pytest.mark.asyncio
async def test_ml_usa_sinteticos_solo_si_no_hay_reales(
    db_session, tenant_nuevo, plan_profesional, catalogo_tenant, cliente_tenant
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    tenant_id = int(tenant["id"])
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=1000)

    await sembrar_sinteticos(
        db_session,
        tenant,
        cantidad_clientes=15,
        cantidad_ventas=180,
        dias=180,
        semilla=77,
    )

    assert await MLService._origen_datos(db_session, tenant_id) is True
    dataset_sintetico = await MLService.preparar_dataset_ventas(
        db_session, tenant_id
    )
    assert len(dataset_sintetico) > 0

    cliente = await cliente_tenant(tenant)
    await crear_venta(
        db_session, tenant, catalogo, cliente["id"], cantidad=1, precio="100.00"
    )

    # Con una venta real, el entrenamiento deja de mirar los sintéticos: nunca
    # se mezclan ambos orígenes.
    assert await MLService._origen_datos(db_session, tenant_id) is False
    dataset_real = await MLService.preparar_dataset_ventas(db_session, tenant_id)
    assert len(dataset_real) == 1
    assert int(dataset_real.iloc[0]["ordenes"]) == 1
    assert float(dataset_real.iloc[0]["total"]) == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_borrado_seguro_solo_elimina_sinteticos(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    tenant_id = int(tenant["id"])
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=1000)
    cliente = await cliente_tenant(tenant)
    venta_real = await crear_venta(
        db_session, tenant, catalogo, cliente["id"], cantidad=1, precio="100.00"
    )

    sembrado = await sembrar_sinteticos(
        db_session,
        tenant,
        cantidad_clientes=10,
        cantidad_ventas=60,
        dias=90,
        semilla=5,
    )
    dataset_id = sembrado["dataset"]["dataset_id"]

    eliminado = await client.delete(
        f"/api/v1/analitica/datos-sinteticos/{dataset_id}",
        headers=tenant["headers"],
    )
    assert eliminado.status_code == 200, eliminado.text
    cuerpo = eliminado.json()
    assert cuerpo["eliminado"] is True
    assert cuerpo["ordenes"] == sembrado["siembra"]["ordenes"]
    assert cuerpo["clientes"] == sembrado["siembra"]["clientes"]
    assert cuerpo["movimientos"] == sembrado["siembra"]["movimientos"]

    # El dato real sigue intacto y vuelve a aparecer en el dashboard.
    real = (await db_session.execute(
        select(Orden).where(Orden.id == venta_real["orden_id"])
    )).scalar_one_or_none()
    assert real is not None
    assert real.es_sintetico is False
    assert (await db_session.get(Cliente, cliente["id"])) is not None

    for modelo, condicion in (
        (Orden, Orden.es_sintetico.is_(True)),
        (Cliente, Cliente.es_sintetico.is_(True)),
        (MovimientoInventario, MovimientoInventario.es_sintetico.is_(True)),
    ):
        restantes = (await db_session.execute(
            select(func.count(modelo.id)).where(
                modelo.tenant_id == tenant_id, condicion
            )
        )).scalar_one()
        assert restantes == 0

    dashboard = await client.get(
        "/api/v1/analitica/dashboard", headers=tenant["headers"]
    )
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["widgets"]["ventas_dia"]["ordenes"] == 1


@pytest.mark.asyncio
async def test_datasets_aislados_por_tenant(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Synth A")
    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Synth B")
    await catalogo_tenant(tenant_a, stock=1000)
    await catalogo_tenant(tenant_b, stock=1000)

    sembrado_a = await sembrar_sinteticos(
        db_session, tenant_a, cantidad_clientes=10, cantidad_ventas=60,
        dias=90, semilla=101,
    )
    sembrado_b = await sembrar_sinteticos(
        db_session, tenant_b, cantidad_clientes=10, cantidad_ventas=60,
        dias=90, semilla=202,
    )

    listado_a = await client.get(
        "/api/v1/analitica/datos-sinteticos", headers=tenant_a["headers"]
    )
    assert listado_a.status_code == 200, listado_a.text
    ids_a = {item["dataset_id"] for item in listado_a.json()["datasets"]}
    assert ids_a == {sembrado_a["dataset"]["dataset_id"]}

    ajeno = await client.get(
        f"/api/v1/analitica/datos-sinteticos/"
        f"{sembrado_b['dataset']['dataset_id']}/exportar",
        headers=tenant_a["headers"],
    )
    assert ajeno.status_code == 404, ajeno.text

    eliminado = await client.delete(
        f"/api/v1/analitica/datos-sinteticos/"
        f"{sembrado_a['dataset']['dataset_id']}",
        headers=tenant_a["headers"],
    )
    assert eliminado.status_code == 200, eliminado.text

    restantes_b = (await db_session.execute(
        select(func.count(Orden.id)).where(
            Orden.tenant_id == int(tenant_b["id"]),
            Orden.es_sintetico.is_(True),
        )
    )).scalar_one()
    assert restantes_b == sembrado_b["siembra"]["ordenes"]
    assert restantes_b > 0
