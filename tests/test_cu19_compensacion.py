"""[EXAMEN-2] Fase E — compensación de ventas offline rechazadas (RF-032 / RN-020)."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.apps.analitica_avanzada.dashboard_service import DashboardService
from app.apps.gestion_ventas.models import NotificacionCliente
from app.apps.operaciones_offline.models import (
    Compensacion,
    EstadoCompensacion,
    OperacionOffline,
    TipoCompensacion,
)
from tests.conftest import operacion_payload


def _venta_payload(catalogo: dict, *, total: float, **extra) -> dict:
    cuerpo = {
        "sucursal_id": catalogo["sucursal_id"],
        "metodo_pago": "EFECTIVO",
        "total": total,
        "detalles": [
            {"variante_producto_id": catalogo["variante_id"], "cantidad": 1}
        ],
    }
    cuerpo.update(extra)
    return cuerpo


async def _sync(client: AsyncClient, dispositivo: dict, operaciones: list[dict]):
    respuesta = await client.post(
        "/api/v1/offline/sync",
        json={"operaciones": operaciones},
        headers=dispositivo["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


@pytest.mark.asyncio
async def test_venta_efectivo_rechazada_crea_compensacion_y_no_duplica(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=0)  # sin stock
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)
    uuid_operacion = str(uuid.uuid4())

    lote = await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=uuid_operacion,
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, total=100.0, cliente_id=cliente["id"]),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    resultado = lote["resultados"][0]
    assert resultado["estado"] in {"RECHAZADA", "CONFLICTO"}, resultado

    compensaciones = (await db_session.execute(
        select(Compensacion).where(Compensacion.tenant_id == int(tenant["id"]))
    )).scalars().all()
    assert len(compensaciones) == 1
    compensacion = compensaciones[0]
    assert compensacion.tipo == TipoCompensacion.REEMBOLSO_EFECTIVO
    assert compensacion.estado == EstadoCompensacion.PENDIENTE
    assert float(compensacion.monto) == 100.0
    assert compensacion.cliente_id == cliente["id"]

    operacion = (await db_session.execute(
        select(OperacionOffline).where(
            OperacionOffline.uuid_operacion == uuid.UUID(uuid_operacion)
        )
    )).scalar_one()
    assert operacion.resultado_json["compensacion_id"] == compensacion.id
    assert operacion.requiere_revision is True

    notificaciones = (await db_session.execute(
        select(NotificacionCliente).where(
            NotificacionCliente.tenant_id == int(tenant["id"]),
            NotificacionCliente.tipo == "COMPENSACION",
        )
    )).scalars().all()
    assert notificaciones, "El cliente debe quedar notificado"

    # Reintento con el mismo UUID: no se duplica el dinero.
    await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=uuid_operacion,
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, total=100.0, cliente_id=cliente["id"]),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    total = (await db_session.execute(
        select(func.count(Compensacion.id)).where(
            Compensacion.operacion_offline_id == operacion.id
        )
    )).scalar_one()
    assert total == 1

    listado = await client.get(
        "/api/v1/offline/compensaciones", headers=tenant["headers"]
    )
    assert listado.status_code == 200
    assert len(listado.json()["compensaciones"]) == 1

    confirmada = await client.post(
        f"/api/v1/offline/compensaciones/{compensacion.id}/confirmar-efectivo",
        headers=tenant["headers"],
    )
    assert confirmada.status_code == 200, confirmada.text
    assert confirmada.json()["estado"] == "COMPLETADA"
    assert confirmada.json()["resuelto_por"] is not None

    resumen = await client.get(
        "/api/v1/offline/compensaciones/resumen", headers=tenant["headers"]
    )
    assert resumen.status_code == 200
    assert resumen.json()["por_tipo"]["REEMBOLSO_EFECTIVO"]["monto"] == 100.0


@pytest.mark.asyncio
async def test_venta_sin_evidencia_de_pago_no_compensa(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=0)
    dispositivo = await dispositivo_offline(tenant)

    await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid.uuid4()),
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, total=100.0, metodo_pago="", monto_recibido=0),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )

    compensaciones = (await db_session.execute(
        select(Compensacion).where(Compensacion.tenant_id == int(tenant["id"]))
    )).scalars().all()
    assert compensaciones == []


@pytest.mark.asyncio
async def test_compensaciones_aisladas_por_tenant(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    otro = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="50.00", stock=0)
    dispositivo = await dispositivo_offline(tenant)

    await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid.uuid4()),
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, total=50.0),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )

    ajeno = await client.get(
        "/api/v1/offline/compensaciones", headers=otro["headers"]
    )
    assert ajeno.status_code == 200
    assert ajeno.json()["compensaciones"] == []


@pytest.mark.asyncio
async def test_dashboard_reporta_el_monto_compensado(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="75.00", stock=0)
    dispositivo = await dispositivo_offline(tenant)

    await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid.uuid4()),
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, total=75.0),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )

    kpi = await DashboardService.calcular_monto_compensaciones(
        db_session, int(tenant["id"])
    )
    assert kpi["cantidad"] >= 1
    assert kpi["monto_total"] == 75.0
    assert kpi["monto_pendiente"] == 75.0
