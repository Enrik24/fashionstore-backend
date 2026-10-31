"""[EXAMEN-2] Aislamiento multitenant de la operación offline (plan 9.2.10).

Verifica que ningún recurso de la Fase 2 cruce el límite del tenant: lotes con
sucursal ajena, paquetes offline, carritos, pagos diferidos, conflictos,
operaciones y dispositivos.
"""
import json
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.apps.operaciones_offline.models import LogConflicto, TipoConflicto
from app.apps.pagos_diferidos.models import PagoDiferido
from tests.conftest import operacion_payload


def _reserva_payload(catalogo: dict, cliente: dict, **extra) -> dict:
    cuerpo = {
        "cliente_id": cliente["id"],
        "sucursal_id": catalogo["sucursal_id"],
        "detalles": [
            {"variante_producto_id": catalogo["variante_id"], "cantidad": 1}
        ],
    }
    cuerpo.update(extra)
    return cuerpo


async def _crear_carrito(client: AsyncClient, dispositivo: dict, catalogo: dict) -> dict:
    respuesta = await client.post(
        "/api/v1/carrito/offline",
        json={
            "uuid_carrito": str(uuid4()),
            "sucursal_id": catalogo["sucursal_id"],
            "items": [
                {"variante_producto_id": catalogo["variante_id"], "cantidad": 1}
            ],
        },
        headers=dispositivo["headers"],
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


# ============================================================
# Descarga offline
# ============================================================

async def test_el_paquete_offline_no_incluye_datos_de_otro_tenant(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Paquete A")
    catalogo_a = await catalogo_tenant(tenant_a, nombre="Prenda exclusiva A")

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Paquete B")
    catalogo_b = await catalogo_tenant(tenant_b, nombre="Prenda exclusiva B")

    paquete_a = await client.get(
        "/api/v1/offline/paquete", headers=tenant_a["headers"]
    )
    assert paquete_a.status_code == 200, paquete_a.text
    texto_a = json.dumps(paquete_a.json())
    assert catalogo_a["sku"] in texto_a
    assert catalogo_b["sku"] not in texto_a

    paquete_b = await client.get(
        "/api/v1/offline/paquete", headers=tenant_b["headers"]
    )
    assert paquete_b.status_code == 200, paquete_b.text
    texto_b = json.dumps(paquete_b.json())
    assert catalogo_b["sku"] in texto_b
    assert catalogo_a["sku"] not in texto_b

    # Pedir una sucursal ajena es 404, no una descarga silenciosa.
    ajena = await client.get(
        f"/api/v1/offline/paquete?sucursal_id={catalogo_a['sucursal_id']}",
        headers=tenant_b["headers"],
    )
    assert ajena.status_code == 404, ajena.text


# ============================================================
# Sincronización y dispositivos
# ============================================================

async def test_lote_con_sucursal_de_otro_tenant_es_rechazado(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Sucursal A")
    catalogo_a = await catalogo_tenant(tenant_a)
    cliente_a = await cliente_tenant(tenant_a)
    dispositivo_a = await dispositivo_offline(tenant_a)

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Sucursal B")
    catalogo_b = await catalogo_tenant(tenant_b)

    uuid_operacion = str(uuid4())
    respuesta = await client.post(
        "/api/v1/offline/sync",
        json={
            "operaciones": [
                operacion_payload(
                    uuid_operacion=uuid_operacion,
                    tipo_operacion="RESERVA",
                    # La sucursal y la variante pertenecen al tenant B.
                    payload=_reserva_payload(catalogo_b, cliente_a),
                    sucursal_id=catalogo_b["sucursal_id"],
                )
            ]
        },
        headers=dispositivo_a["headers"],
    )
    # La sucursal se valida antes de registrar la operación.
    assert respuesta.status_code == 400, respuesta.text
    assert "sucursal ajena" in respuesta.json()["detail"].lower()

    # No se creó la reserva en el tenant A.
    from app.apps.gestion_ventas.models import Reserva
    from app.apps.operaciones_offline.models import OperacionOffline

    reservas = (await db_session.execute(
        select(Reserva).where(Reserva.tenant_id == tenant_a["id"])
    )).scalars().all()
    assert reservas == []

    operaciones = (await db_session.execute(
        select(OperacionOffline).where(
            OperacionOffline.uuid_operacion == uuid_operacion
        )
    )).scalars().all()
    assert operaciones == []


async def test_no_se_puede_sincronizar_con_un_dispositivo_ajeno(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Dispositivo A")
    catalogo_a = await catalogo_tenant(tenant_a)
    cliente_a = await cliente_tenant(tenant_a)
    dispositivo_a = await dispositivo_offline(tenant_a)

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Dispositivo B")
    catalogo_b = await catalogo_tenant(tenant_b)
    cliente_b = await cliente_tenant(tenant_b)

    # El administrador de B conoce el id del dispositivo de A: el motor lo
    # busca dentro del tenant B y no lo encuentra.
    respuesta = await client.post(
        "/api/v1/offline/sync",
        json={
            "dispositivo_id": dispositivo_a["id"],
            "operaciones": [
                operacion_payload(
                    uuid_operacion=str(uuid4()),
                    tipo_operacion="RESERVA",
                    payload=_reserva_payload(catalogo_b, cliente_b),
                )
            ],
        },
        headers=tenant_b["headers"],
    )
    assert respuesta.status_code == 403, respuesta.text
    assert respuesta.json()["codigo"] == "DISPOSITIVO_REVOCADO"


# ============================================================
# Carrito offline
# ============================================================

async def test_el_carrito_no_se_lee_ni_modifica_desde_otro_tenant(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Carrito A")
    catalogo_a = await catalogo_tenant(tenant_a)
    dispositivo_a = await dispositivo_offline(tenant_a)
    carrito_a = await _crear_carrito(client, dispositivo_a, catalogo_a)
    uuid_carrito = carrito_a["uuid_carrito"]

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Carrito B")
    await catalogo_tenant(tenant_b)

    lectura = await client.get(
        f"/api/v1/carrito/offline/{uuid_carrito}",
        headers=tenant_b["headers"],
    )
    assert lectura.status_code == 404, lectura.text

    items = await client.put(
        f"/api/v1/carrito/offline/{uuid_carrito}/items",
        json={
            "items": [
                {
                    "variante_producto_id": catalogo_a["variante_id"],
                    "cantidad": 9,
                }
            ]
        },
        headers=tenant_b["headers"],
    )
    assert items.status_code == 404, items.text

    borrado = await client.delete(
        f"/api/v1/carrito/offline/{uuid_carrito}/items/{carrito_a['items'][0]['id']}",
        headers=tenant_b["headers"],
    )
    assert borrado.status_code == 404, borrado.text

    pendientes = await client.get(
        "/api/v1/carrito/offline/pendientes",
        headers=tenant_b["headers"],
    )
    assert pendientes.status_code == 200, pendientes.text
    assert uuid_carrito not in {c["uuid_carrito"] for c in pendientes.json()}

    # El carrito del tenant A sigue intacto.
    original = await client.get(
        f"/api/v1/carrito/offline/{uuid_carrito}",
        headers=dispositivo_a["headers"],
    )
    assert original.status_code == 200, original.text
    assert original.json()["items"][0]["cantidad"] == 1


# ============================================================
# Pago diferido
# ============================================================

async def test_el_pago_diferido_no_se_consulta_ni_reutiliza_desde_otro_tenant(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Pago A")
    catalogo_a = await catalogo_tenant(tenant_a)
    dispositivo_a = await dispositivo_offline(tenant_a)
    carrito_a = await _crear_carrito(client, dispositivo_a, catalogo_a)
    conversion = await client.post(
        f"/api/v1/carrito/offline/{carrito_a['uuid_carrito']}/convertir",
        json={},
        headers=dispositivo_a["headers"],
    )
    assert conversion.status_code == 201, conversion.text
    uuid_orden_a = conversion.json()["uuid_orden"]

    uuid_pago = str(uuid4())
    creado = await client.post(
        "/api/v1/pagos/diferidos",
        json={"uuid_pago": uuid_pago, "uuid_orden": uuid_orden_a},
        headers=dispositivo_a["headers"],
    )
    assert creado.status_code == 201, creado.text
    pago_id = creado.json()["id"]

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Pago B")
    await catalogo_tenant(tenant_b)

    consulta = await client.get(
        f"/api/v1/pagos/diferidos/{uuid_pago}",
        headers=tenant_b["headers"],
    )
    assert consulta.status_code == 404, consulta.text

    listado = await client.get(
        "/api/v1/pagos/diferidos", headers=tenant_b["headers"]
    )
    assert listado.status_code == 200, listado.text
    assert uuid_pago not in {p["uuid_pago"] for p in listado.json()}

    # Reutilizar el uuid_pago ajeno tampoco revela su existencia.
    reutilizado = await client.post(
        "/api/v1/pagos/diferidos",
        json={"uuid_pago": uuid_pago, "uuid_orden": uuid_orden_a},
        headers=tenant_b["headers"],
    )
    assert reutilizado.status_code == 404, reutilizado.text

    reembolso = await client.post(
        f"/api/v1/pagos/{pago_id}/reembolsar",
        json={"motivo": "Intento ajeno"},
        headers=tenant_b["headers"],
    )
    assert reembolso.status_code == 404, reembolso.text

    eventos = await client.get(
        f"/api/v1/pagos/{pago_id}/eventos", headers=tenant_b["headers"]
    )
    assert eventos.status_code == 404, eventos.text

    pendientes = await client.get(
        "/api/v1/pagos/pendientes/count", headers=tenant_b["headers"]
    )
    assert pendientes.status_code == 200, pendientes.text
    assert pendientes.json()["pendientes"] == 0

    db_session.expire_all()
    pagos = (await db_session.execute(
        select(PagoDiferido).where(PagoDiferido.tenant_id == tenant_a["id"])
    )).scalars().all()
    assert len(pagos) == 1
    assert pagos[0].reembolso_id is None


# ============================================================
# Conflictos y operaciones
# ============================================================

async def test_los_conflictos_no_se_consultan_ni_resuelven_desde_otro_tenant(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Conflicto A")
    catalogo_a = await catalogo_tenant(tenant_a, stock=0)
    cliente_a = await cliente_tenant(tenant_a)
    dispositivo_a = await dispositivo_offline(tenant_a)

    respuesta = await client.post(
        "/api/v1/offline/sync",
        json={
            "operaciones": [
                operacion_payload(
                    uuid_operacion=str(uuid4()),
                    tipo_operacion="RESERVA",
                    payload=_reserva_payload(catalogo_a, cliente_a),
                )
            ]
        },
        headers=dispositivo_a["headers"],
    )
    assert respuesta.json()["resultados"][0]["estado"] == "RECHAZADA"

    conflicto = (await db_session.execute(
        select(LogConflicto).where(LogConflicto.tenant_id == tenant_a["id"])
    )).scalar_one()
    assert conflicto.tipo_conflicto == TipoConflicto.STOCK_INSUFICIENTE.value

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Conflicto B")

    detalle = await client.get(
        f"/api/v1/offline/conflictos/{conflicto.id}",
        headers=tenant_b["headers"],
    )
    assert detalle.status_code == 404, detalle.text

    listado = await client.get(
        "/api/v1/offline/conflictos", headers=tenant_b["headers"]
    )
    assert listado.status_code == 200, listado.text
    assert conflicto.id not in {c["id"] for c in listado.json()}

    resolver = await client.post(
        f"/api/v1/offline/conflictos/{conflicto.id}/resolver",
        json={"decision": "CONFIRMAR"},
        headers=tenant_b["headers"],
    )
    assert resolver.status_code == 404, resolver.text

    conteo = await client.get(
        "/api/v1/offline/conflictos/pendientes/count",
        headers=tenant_b["headers"],
    )
    assert conteo.status_code == 200, conteo.text
    assert conteo.json()["pendientes"] == 0
