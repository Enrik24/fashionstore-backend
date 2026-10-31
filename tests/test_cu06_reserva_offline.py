"""[EXAMEN-2] CU06 — Reserva creada offline (plan 9.2.10)."""
from datetime import datetime, timedelta
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_catalogo.models import Inventario
from app.apps.gestion_ventas.models import (
    DetalleReserva,
    EstadoReserva,
    Reserva,
)
from app.apps.operaciones_offline.models import (
    EstadoSincronizacion,
    OperacionOffline,
)
from tests.conftest import operacion_payload


def _reserva_payload(catalogo: dict, cliente: dict, cantidad: int = 1, **extra) -> dict:
    cuerpo = {
        "cliente_id": cliente["id"],
        "sucursal_id": catalogo["sucursal_id"],
        "detalles": [
            {"variante_producto_id": catalogo["variante_id"], "cantidad": cantidad}
        ],
    }
    cuerpo.update(extra)
    return cuerpo


async def _sincronizar(client: AsyncClient, dispositivo: dict, operaciones: list[dict], sucursal_id=None):
    respuesta = await client.post(
        "/api/v1/offline/sync",
        json={"operaciones": operaciones, "sucursal_id": sucursal_id},
        headers=dispositivo["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


async def test_reserva_se_registra_con_uuid_y_estado_pendiente(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=5)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    uuid_operacion = str(uuid4())
    lote = await _sincronizar(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=uuid_operacion,
                tipo_operacion="RESERVA",
                payload=_reserva_payload(catalogo, cliente, cantidad=2),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
        sucursal_id=catalogo["sucursal_id"],
    )

    assert lote["recibidas"] == 1
    assert lote["resumen"] == {"CONFIRMADA": 1}
    resultado = lote["resultados"][0]
    assert resultado["estado"] == "CONFIRMADA"
    assert resultado["datos_resultado"]["reserva_id"]

    reserva = await db_session.get(Reserva, resultado["datos_resultado"]["reserva_id"])
    assert reserva is not None
    assert reserva.tenant_id == tenant["id"]
    assert reserva.cliente_id == cliente["id"]
    assert reserva.estado == EstadoReserva.PENDIENTE
    assert reserva.es_offline is True
    assert reserva.uuid_reserva is not None
    assert reserva.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA

    detalles = (await db_session.execute(
        select(DetalleReserva).where(DetalleReserva.reserva_id == reserva.id)
    )).scalars().all()
    assert len(detalles) == 1
    assert detalles[0].cantidad == 2
    assert detalles[0].tenant_id == tenant["id"]

    inventario = (await db_session.execute(
        select(Inventario).where(Inventario.variante_producto_id == catalogo["variante_id"])
    )).scalar_one()
    assert inventario.cantidad_reservada == 2
    assert inventario.cantidad_disponible == 3

    operacion = (await db_session.execute(
        select(OperacionOffline).where(OperacionOffline.uuid_operacion == uuid_operacion)
    )).scalar_one()
    assert operacion.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA
    assert operacion.resultado_json["uuid_reserva"]


async def test_reserva_duplicada_no_crea_otra_reserva(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=5)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    uuid_operacion = str(uuid4())
    operacion = operacion_payload(
        uuid_operacion=uuid_operacion,
        tipo_operacion="RESERVA",
        payload=_reserva_payload(catalogo, cliente),
        sucursal_id=catalogo["sucursal_id"],
    )

    primera = await _sincronizar(client, dispositivo, [operacion])
    segunda = await _sincronizar(client, dispositivo, [operacion])

    assert primera["resultados"][0]["estado"] == "CONFIRMADA"
    assert segunda["resultados"][0]["estado"] == "DUPLICADA"

    reservas = (await db_session.execute(
        select(Reserva).where(Reserva.tenant_id == tenant["id"])
    )).scalars().all()
    assert len(reservas) == 1

    operaciones = (await db_session.execute(
        select(OperacionOffline).where(OperacionOffline.uuid_operacion == uuid_operacion)
    )).scalars().all()
    assert len(operaciones) == 1


async def test_reserva_sin_stock_es_rechazada(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=1)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    lote = await _sincronizar(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid4()),
                tipo_operacion="RESERVA",
                payload=_reserva_payload(catalogo, cliente, cantidad=2),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )

    resultado = lote["resultados"][0]
    assert resultado["estado"] == "RECHAZADA"
    assert "Stock" in resultado["motivo"] or "stock" in resultado["motivo"]
    assert resultado["datos_resultado"]["faltantes"][0]["disponible"] == 1

    reservas = (await db_session.execute(
        select(Reserva).where(Reserva.tenant_id == tenant["id"])
    )).scalars().all()
    assert reservas == []

    inventario = (await db_session.execute(
        select(Inventario).where(Inventario.variante_producto_id == catalogo["variante_id"])
    )).scalar_one()
    assert inventario.cantidad_reservada == 0


async def test_orden_fifo_aunque_el_lote_llegue_desordenado(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=1)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    uuid_primera = str(uuid4())
    uuid_segunda = str(uuid4())
    operaciones = [
        operacion_payload(
            uuid_operacion=uuid_segunda,
            tipo_operacion="RESERVA",
            payload=_reserva_payload(catalogo, cliente),
            secuencia_local=2,
            sucursal_id=catalogo["sucursal_id"],
        ),
        operacion_payload(
            uuid_operacion=uuid_primera,
            tipo_operacion="RESERVA",
            payload=_reserva_payload(catalogo, cliente),
            secuencia_local=1,
            sucursal_id=catalogo["sucursal_id"],
        ),
    ]

    lote = await _sincronizar(client, dispositivo, operaciones)
    por_uuid = {r["uuid_operacion"]: r for r in lote["resultados"]}
    assert por_uuid[uuid_primera]["estado"] == "CONFIRMADA"
    assert por_uuid[uuid_segunda]["estado"] == "RECHAZADA"


async def test_uuid_repetido_en_el_mismo_lote(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=5)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    uuid_operacion = str(uuid4())
    operacion = operacion_payload(
        uuid_operacion=uuid_operacion,
        tipo_operacion="RESERVA",
        payload=_reserva_payload(catalogo, cliente),
        sucursal_id=catalogo["sucursal_id"],
    )

    lote = await _sincronizar(client, dispositivo, [operacion, dict(operacion)])
    estados = [r["estado"] for r in lote["resultados"]]
    assert estados == ["CONFIRMADA", "DUPLICADA"]


async def test_uuid_mal_formado_y_fechas_invalidas(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    payload = _reserva_payload(catalogo, cliente)

    mal_formado = await client.post(
        "/api/v1/offline/sync",
        json={
            "operaciones": [
                {
                    "uuid_operacion": "no-es-un-uuid",
                    "tipo_operacion": "RESERVA",
                    "payload": payload,
                }
            ]
        },
        headers=dispositivo["headers"],
    )
    assert mal_formado.status_code == 400, mal_formado.text

    futura = await client.post(
        "/api/v1/offline/sync",
        json={
            "operaciones": [
                operacion_payload(
                    uuid_operacion=str(uuid4()),
                    tipo_operacion="RESERVA",
                    payload=payload,
                    fecha_registro_local=datetime.utcnow() + timedelta(minutes=30),
                )
            ]
        },
        headers=dispositivo["headers"],
    )
    assert futura.status_code == 400, futura.text

    vieja = await client.post(
        "/api/v1/offline/sync",
        json={
            "operaciones": [
                operacion_payload(
                    uuid_operacion=str(uuid4()),
                    tipo_operacion="RESERVA",
                    payload=payload,
                    fecha_registro_local=datetime.utcnow() - timedelta(days=31),
                )
            ]
        },
        headers=dispositivo["headers"],
    )
    assert vieja.status_code == 400, vieja.text
