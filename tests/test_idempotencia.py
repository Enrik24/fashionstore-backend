"""[EXAMEN-2] Idempotencia de operaciones y cobros (plan 9.2.10).

Verifica que reenviar el mismo lote 10 veces produce exactamente un registro
por UUID, que el mismo `uuid_pago` produce un solo cobro y el ciclo de vida de
`ClaveIdempotencia`, además de la validación de UUID y antigüedad.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.config import settings
from app.exceptions import ValidationException
from app.apps.gestion_catalogo.models import Inventario
from app.apps.gestion_ventas.models import Carrito, Orden, Reserva, TransaccionPago
from app.apps.operaciones_offline.idempotencia_service import IdempotenciaService
from app.apps.operaciones_offline.models import (
    ClaveIdempotencia,
    EstadoSincronizacion,
    OperacionOffline,
)
from app.apps.pagos_diferidos.models import PagoDiferido
from tests.conftest import operacion_payload


def _reserva(catalogo: dict, cliente: dict) -> dict:
    return {
        "cliente_id": cliente["id"],
        "sucursal_id": catalogo["sucursal_id"],
        "detalles": [
            {"variante_producto_id": catalogo["variante_id"], "cantidad": 1}
        ],
    }


def _venta(catalogo: dict) -> dict:
    return {
        "sucursal_id": catalogo["sucursal_id"],
        "metodo_pago": "EFECTIVO",
        "detalles": [
            {"variante_producto_id": catalogo["variante_id"], "cantidad": 1}
        ],
    }


def _carrito(catalogo: dict, uuid_carrito: str) -> dict:
    return {
        "uuid_carrito": uuid_carrito,
        "items": [
            {"variante_producto_id": catalogo["variante_id"], "cantidad": 1}
        ],
    }


async def _sync(client: AsyncClient, dispositivo: dict, operaciones: list[dict]):
    respuesta = await client.post(
        "/api/v1/offline/sync",
        json={"operaciones": operaciones},
        headers=dispositivo["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


# ============================================================
# Utilidades del servicio
# ============================================================

def test_validar_uuid_acepta_uuid_y_texto_y_rechaza_invalidos():
    valor = uuid4()
    assert IdempotenciaService.validar_uuid(valor) == valor
    assert IdempotenciaService.validar_uuid(str(valor)) == valor

    for invalido in ("no-es-un-uuid", "", None, 12345):
        with pytest.raises(ValidationException):
            IdempotenciaService.validar_uuid(invalido)


def test_validar_antiguedad_aplica_la_ventana_configurada():
    ahora = datetime.utcnow()
    IdempotenciaService.validar_antiguedad(None)
    IdempotenciaService.validar_antiguedad(ahora - timedelta(days=1))

    with pytest.raises(ValidationException):
        IdempotenciaService.validar_antiguedad(ahora + timedelta(minutes=10))

    con_tz = datetime.now().astimezone() - timedelta(days=1)
    IdempotenciaService.validar_antiguedad(con_tz)

    demasiado_vieja = ahora - timedelta(days=settings.OFFLINE_MAX_DIAS_PENDIENTE + 1)
    with pytest.raises(ValidationException):
        IdempotenciaService.validar_antiguedad(demasiado_vieja)


def test_hash_de_peticion_es_estable_e_insensible_al_orden():
    primero = {"b": 2, "a": 1, "c": [1, 2, 3]}
    segundo = {"c": [1, 2, 3], "a": 1, "b": 2}
    assert IdempotenciaService.calcular_request_hash(primero) == (
        IdempotenciaService.calcular_request_hash(segundo)
    )
    assert IdempotenciaService.calcular_request_hash(primero) != (
        IdempotenciaService.calcular_request_hash({"a": 2, "b": 2, "c": [1, 2, 3]})
    )
    assert len(IdempotenciaService.calcular_request_hash(primero)) == 64


# ============================================================
# Lote reenviado 10 veces
# ============================================================

async def test_mismo_lote_diez_veces_produce_un_registro_por_uuid(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    uuid_reserva = str(uuid4())
    uuid_venta = str(uuid4())
    uuid_carrito = str(uuid4())
    lote = [
        operacion_payload(
            uuid_operacion=uuid_reserva,
            tipo_operacion="RESERVA",
            payload=_reserva(catalogo, cliente),
            secuencia_local=1,
            sucursal_id=catalogo["sucursal_id"],
        ),
        operacion_payload(
            uuid_operacion=uuid_venta,
            tipo_operacion="VENTA_CONTADO",
            payload=_venta(catalogo),
            secuencia_local=2,
            sucursal_id=catalogo["sucursal_id"],
        ),
        operacion_payload(
            uuid_operacion=uuid_carrito,
            tipo_operacion="CARRITO",
            payload=_carrito(catalogo, str(uuid4())),
            secuencia_local=3,
            sucursal_id=catalogo["sucursal_id"],
        ),
    ]

    primera = await _sync(client, dispositivo, lote)
    assert primera["resumen"] == {"CONFIRMADA": 3}

    for intento in range(2, 11):
        repetida = await _sync(client, dispositivo, lote)
        assert repetida["resumen"] == {"DUPLICADA": 3}, f"intento {intento}"
        assert {r["uuid_operacion"] for r in repetida["resultados"]} == {
            uuid_reserva,
            uuid_venta,
            uuid_carrito,
        }
        for resultado in repetida["resultados"]:
            assert resultado["reintentar"] is False
            assert resultado["datos_resultado"] is not None

    uuids = {UUID(uuid_reserva), UUID(uuid_venta), UUID(uuid_carrito)}
    operaciones = (await db_session.execute(
        select(OperacionOffline).where(OperacionOffline.uuid_operacion.in_(uuids))
    )).scalars().all()
    assert len(operaciones) == 3
    assert {op.estado_sincronizacion for op in operaciones} == {
        EstadoSincronizacion.CONFIRMADA
    }
    assert {op.intentos for op in operaciones} == {1}

    assert len((await db_session.execute(
        select(Reserva).where(Reserva.tenant_id == tenant["id"])
    )).scalars().all()) == 1
    assert len((await db_session.execute(
        select(Orden).where(Orden.tenant_id == tenant["id"])
    )).scalars().all()) == 1
    assert len((await db_session.execute(
        select(Carrito).where(Carrito.tenant_id == tenant["id"])
    )).scalars().all()) == 1

    inventario = (await db_session.execute(
        select(Inventario).where(
            Inventario.variante_producto_id == catalogo["variante_id"]
        )
    )).scalar_one()
    # Solo el primer lote descontó (venta) y reservó (reserva).
    assert inventario.cantidad == 9
    assert inventario.cantidad_reservada == 1


async def test_uuid_de_operacion_reutilizado_por_otro_tenant_es_rechazado(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Idem A")
    catalogo_a = await catalogo_tenant(tenant_a)
    cliente_a = await cliente_tenant(tenant_a)
    dispositivo_a = await dispositivo_offline(tenant_a)

    uuid_compartido = str(uuid4())
    await _sync(
        client,
        dispositivo_a,
        [
            operacion_payload(
                uuid_operacion=uuid_compartido,
                tipo_operacion="RESERVA",
                payload=_reserva(catalogo_a, cliente_a),
            )
        ],
    )

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Idem B")
    catalogo_b = await catalogo_tenant(tenant_b)
    cliente_b = await cliente_tenant(tenant_b)
    dispositivo_b = await dispositivo_offline(tenant_b)

    respuesta = await _sync(
        client,
        dispositivo_b,
        [
            operacion_payload(
                uuid_operacion=uuid_compartido,
                tipo_operacion="RESERVA",
                payload=_reserva(catalogo_b, cliente_b),
            )
        ],
    )
    resultado = respuesta["resultados"][0]
    assert resultado["estado"] == "RECHAZADA"
    assert "otro tenant" in resultado["motivo"]
    assert resultado["codigo_http_sugerido"] == 409
    assert respuesta["resumen"] == {"RECHAZADA": 1}

    # No se creó una reserva en el tenant B.
    assert (await client.get(
        "/api/v1/offline/resumen",
        headers=dispositivo_b["headers"],
    )).json()["confirmadas"] == 0


# ============================================================
# uuid_pago repetido
# ============================================================

async def test_mismo_uuid_pago_diez_veces_produce_un_solo_cobro(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    dispositivo = await dispositivo_offline(tenant)

    alta = await client.post(
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
    assert alta.status_code == 201, alta.text
    uuid_carrito = alta.json()["uuid_carrito"]
    conversion = await client.post(
        f"/api/v1/carrito/offline/{uuid_carrito}/convertir",
        json={},
        headers=dispositivo["headers"],
    )
    assert conversion.status_code == 201, conversion.text
    orden = conversion.json()

    uuid_pago = str(uuid4())
    cuerpo = {"uuid_pago": uuid_pago, "uuid_orden": orden["uuid_orden"]}

    cobros = []
    for _ in range(10):
        respuesta = await client.post(
            "/api/v1/pagos/diferidos",
            json=cuerpo,
            headers=dispositivo["headers"],
        )
        assert respuesta.status_code == 201, respuesta.text
        cobros.append(respuesta.json())

    primero = cobros[0]
    assert primero["duplicado"] is False
    intent = primero["stripe_payment_intent_id"]
    for cobro in cobros[1:]:
        assert cobro["duplicado"] is True
        assert cobro["id"] == primero["id"]
        assert cobro["stripe_payment_intent_id"] == intent
        assert cobro["idempotency_key"] == uuid_pago

    db_session.expire_all()
    pagos = (await db_session.execute(
        select(PagoDiferido).where(PagoDiferido.tenant_id == tenant["id"])
    )).scalars().all()
    assert len(pagos) == 1
    assert pagos[0].idempotency_key == uuid_pago

    transacciones = (await db_session.execute(
        select(TransaccionPago).where(TransaccionPago.orden_id == orden["orden_id"])
    )).scalars().all()
    assert len(transacciones) == 1
    assert transacciones[0].stripe_payment_intent_id == intent


# ============================================================
# ClaveIdempotencia
# ============================================================

async def test_ciclo_de_vida_de_la_clave_idempotencia(
    db_session, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    clave = uuid4()
    endpoint = "POST /api/v1/ordenes"
    request_hash = IdempotenciaService.calcular_request_hash({"a": 1})

    assert (await IdempotenciaService.obtener_clave(
        db_session, tenant["id"], clave, endpoint
    )) is None

    registro = await IdempotenciaService.registrar_clave(
        db_session, tenant["id"], clave, endpoint, request_hash
    )
    await db_session.commit()
    assert registro.estado == "EN_PROCESO"
    assert registro.fecha_expiracion is not None

    encontrada = await IdempotenciaService.obtener_clave(
        db_session, tenant["id"], clave, endpoint
    )
    assert encontrada is not None
    assert encontrada.id == registro.id

    completada = await IdempotenciaService.completar_clave(
        db_session, encontrada, {"orden_id": 42}
    )
    await db_session.commit()
    assert completada.estado == "COMPLETADA"
    assert completada.respuesta_json == {"orden_id": 42}

    otra = await IdempotenciaService.registrar_clave(
        db_session,
        tenant["id"],
        uuid4(),
        endpoint,
        request_hash,
    )
    await db_session.commit()
    con_error = await IdempotenciaService.marcar_error_clave(
        db_session, otra, "fallo de negocio"
    )
    await db_session.commit()
    assert con_error.estado == "ERROR"
    assert con_error.respuesta_json == {"error": "fallo de negocio"}


async def test_limpiar_claves_expiradas(db_session, tenant_nuevo, plan_profesional):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    vigente = await IdempotenciaService.registrar_clave(
        db_session,
        tenant["id"],
        uuid4(),
        "POST /api/v1/pagos",
        IdempotenciaService.calcular_request_hash({"x": 1}),
    )
    vencida = await IdempotenciaService.registrar_clave(
        db_session,
        tenant["id"],
        uuid4(),
        "POST /api/v1/pagos",
        IdempotenciaService.calcular_request_hash({"x": 2}),
    )
    vencida.fecha_expiracion = datetime.utcnow() - timedelta(hours=1)
    await db_session.commit()

    eliminadas = await IdempotenciaService.limpiar_expiradas(db_session)
    assert eliminadas >= 1

    db_session.expire_all()
    claves = (await db_session.execute(
        select(ClaveIdempotencia).where(
            ClaveIdempotencia.tenant_id == tenant["id"]
        )
    )).scalars().all()
    ids = {c.id for c in claves}
    assert vigente.id in ids
    assert vencida.id not in ids


async def test_operacion_confirmada_se_encuentra_sin_contexto_de_tenant(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    uuid_operacion = str(uuid4())
    await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=uuid_operacion,
                tipo_operacion="RESERVA",
                payload=_reserva(catalogo, cliente),
            )
        ],
    )

    # El UUID es único global: se localiza aunque no haya contexto de tenant.
    operacion = await IdempotenciaService.buscar_operacion(db_session, uuid_operacion)
    assert operacion is not None
    assert operacion.tenant_id == tenant["id"]
    assert operacion.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA


async def test_reintentar_una_operacion_confirmada_no_duplica_datos(
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
    await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=uuid_operacion,
                tipo_operacion="RESERVA",
                payload=_reserva(catalogo, cliente),
            )
        ],
    )

    reintento = await client.post(
        f"/api/v1/offline/operaciones/{uuid_operacion}/reintentar",
        headers=dispositivo["headers"],
    )
    assert reintento.status_code == 409, reintento.text

    reservas = (await db_session.execute(
        select(Reserva).where(Reserva.tenant_id == tenant["id"])
    )).scalars().all()
    assert len(reservas) == 1

    inventario = (await db_session.execute(
        select(Inventario).where(
            Inventario.variante_producto_id == catalogo["variante_id"]
        )
    )).scalar_one()
    assert inventario.cantidad_reservada == 1

    operacion = (await db_session.execute(
        select(OperacionOffline).where(
            OperacionOffline.uuid_operacion == UUID(uuid_operacion)
        )
    )).scalar_one()
    assert operacion.intentos == 1
