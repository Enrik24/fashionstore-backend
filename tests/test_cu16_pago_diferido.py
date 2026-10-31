"""[EXAMEN-2] CU16 — Pago diferido con Stripe (plan 9.2.10).

Cubre la creación del `PaymentIntent` con `Idempotency-Key`, el reenvío del
mismo `uuid_pago`, la confirmación por webhook firmado, el webhook duplicado,
la tarjeta rechazada con reintento sin doble cobro, el 3DS pendiente, la orden
expirada, el tenant suspendido, el reembolso y el error transitorio de Stripe.
"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.apps.gestion_catalogo.models import Inventario
from app.apps.gestion_saas.models import EstadoTenant, Tenant
from app.apps.gestion_ventas.models import (
    Comprobante,
    EstadoOrden,
    EstadoTransaccion,
    Orden,
    TransaccionPago,
    TipoComprobante,
    TipoOrden,
)
from app.apps.operaciones_offline.models import (
    EstadoPagoDiferido,
    EstadoSincronizacion,
    LogConflicto,
    OperacionOffline,
    TipoConflicto,
)
from app.apps.pagos_diferidos.models import EventoStripe, PagoDiferido
from app.apps.pagos_diferidos.stripe_pago_service import (
    StripeErrorTransitorio,
    StripePagoService,
)
from tests.conftest import operacion_payload


# ============================================================
# Utilidades
# ============================================================

async def _crear_orden(
    client: AsyncClient,
    dispositivo: dict,
    catalogo: dict,
    cantidad: int = 1,
) -> dict:
    """Crea un carrito offline y lo convierte en orden pendiente de pago."""
    alta = await client.post(
        "/api/v1/carrito/offline",
        json={
            "uuid_carrito": str(uuid4()),
            "sucursal_id": catalogo["sucursal_id"],
            "items": [
                {
                    "variante_producto_id": catalogo["variante_id"],
                    "cantidad": cantidad,
                }
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
    return conversion.json()


async def _registrar_pago(
    client: AsyncClient,
    dispositivo: dict,
    orden: dict,
    uuid_pago: str | None = None,
    **extra,
):
    cuerpo = {
        "uuid_pago": uuid_pago or str(uuid4()),
        "uuid_orden": orden["uuid_orden"],
    }
    cuerpo.update(extra)
    return await client.post(
        "/api/v1/pagos/diferidos",
        json=cuerpo,
        headers=dispositivo["headers"],
    )


def _evento(
    tipo: str,
    payment_intent_id: str,
    *,
    tenant_id: int,
    orden_id: int,
    uuid_pago: str,
    event_id: str | None = None,
    objeto_extra: dict | None = None,
) -> dict:
    objeto = {
        "id": payment_intent_id,
        "object": "payment_intent",
        "amount": 10000,
        "currency": "bob",
        "metadata": {
            "tenant_id": str(tenant_id),
            "orden_id": str(orden_id),
            "uuid_pago": str(uuid_pago),
        },
    }
    objeto.update(objeto_extra or {})
    return {
        "id": event_id or f"evt_{uuid4().hex[:16]}",
        "object": "event",
        "type": tipo,
        "data": {"object": objeto},
    }


async def _enviar_webhook(client: AsyncClient, firmar_webhook, evento: dict) -> dict:
    payload = json.dumps(evento).encode("utf-8")
    respuesta = await client.post(
        "/api/v1/pagos/webhooks/stripe",
        content=payload,
        headers={
            "stripe-signature": firmar_webhook(payload),
            "Content-Type": "application/json",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


async def _pago(db_session, uuid_pago) -> PagoDiferido:
    db_session.expire_all()
    return (await db_session.execute(
        select(PagoDiferido).where(PagoDiferido.uuid_pago == uuid_pago)
    )).scalar_one()


# ============================================================
# Registro e idempotencia del cobro
# ============================================================

async def test_registro_crea_payment_intent_con_idempotency_key(
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
    orden = await _crear_orden(client, dispositivo, catalogo, cantidad=1)

    uuid_pago = str(uuid4())
    respuesta = await _registrar_pago(client, dispositivo, orden, uuid_pago)
    assert respuesta.status_code == 201, respuesta.text
    datos = respuesta.json()

    assert datos["uuid_pago"] == uuid_pago
    assert datos["orden_id"] == orden["orden_id"]
    assert datos["numero_orden"] == orden["numero_orden"]
    assert Decimal(str(datos["monto"])) == Decimal("100.00")
    assert datos["moneda"] == "BOB"
    assert datos["estado"] == EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE.value
    assert datos["requiere_accion"] is True
    assert datos["simulado"] is True
    assert datos["duplicado"] is False
    # RN-015: la Idempotency-Key de Stripe es el propio uuid_pago.
    assert datos["idempotency_key"] == uuid_pago
    assert datos["stripe_payment_intent_id"].startswith("pi_sim_")
    assert datos["client_secret"].startswith(datos["stripe_payment_intent_id"])

    pago = await _pago(db_session, uuid_pago)
    assert pago.tenant_id == tenant["id"]
    assert pago.orden_id == orden["orden_id"]
    assert pago.monto == Decimal("100.00")
    assert pago.intentos == 1
    assert pago.fecha_expiracion is not None

    transaccion = (await db_session.execute(
        select(TransaccionPago).where(TransaccionPago.orden_id == orden["orden_id"])
    )).scalar_one()
    assert transaccion.estado == EstadoTransaccion.PENDIENTE
    assert transaccion.idempotency_key == uuid_pago
    assert transaccion.stripe_payment_intent_id == pago.stripe_payment_intent_id


async def test_mismo_uuid_pago_devuelve_el_mismo_cobro(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo)

    uuid_pago = str(uuid4())
    primera = (await _registrar_pago(client, dispositivo, orden, uuid_pago)).json()

    for _ in range(9):
        repetida = await _registrar_pago(client, dispositivo, orden, uuid_pago)
        assert repetida.status_code == 201, repetida.text
        cuerpo = repetida.json()
        assert cuerpo["duplicado"] is True
        assert cuerpo["id"] == primera["id"]
        assert (
            cuerpo["stripe_payment_intent_id"]
            == primera["stripe_payment_intent_id"]
        )

    total = (await db_session.execute(
        select(func.count(PagoDiferido.id)).where(PagoDiferido.tenant_id == tenant["id"])
    )).scalar_one()
    assert total == 1


async def test_orden_pago_diferido_offline_se_registra_por_sync(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo)

    uuid_pago = str(uuid4())
    uuid_operacion = str(uuid4())
    respuesta = await client.post(
        "/api/v1/offline/sync",
        json={
            "operaciones": [
                operacion_payload(
                    uuid_operacion=uuid_operacion,
                    tipo_operacion="PAGO_DIFERIDO",
                    payload={
                        "uuid_pago": uuid_pago,
                        "uuid_orden": orden["uuid_orden"],
                    },
                )
            ]
        },
        headers=dispositivo["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    resultado = respuesta.json()["resultados"][0]
    assert resultado["estado"] == "CONFIRMADA", resultado
    datos = resultado["datos_resultado"]
    assert datos["uuid_pago"] == uuid_pago
    assert datos["estado"] == EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE.value
    assert datos["requiere_accion"] is True

    pago = await _pago(db_session, uuid_pago)
    assert pago.operacion_offline_id is not None
    operacion = await db_session.get(OperacionOffline, pago.operacion_offline_id)
    assert str(operacion.uuid_operacion) == uuid_operacion
    assert operacion.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA

    # El reenvío del mismo lote no vuelve a crear el pago (RN-005/RN-015).
    repetida = await client.post(
        "/api/v1/offline/sync",
        json={
            "operaciones": [
                operacion_payload(
                    uuid_operacion=str(uuid4()),
                    tipo_operacion="PAGO_DIFERIDO",
                    payload={
                        "uuid_pago": uuid_pago,
                        "uuid_orden": orden["uuid_orden"],
                    },
                )
            ]
        },
        headers=dispositivo["headers"],
    )
    repetida_datos = repetida.json()["resultados"][0]
    assert repetida_datos["estado"] == "CONFIRMADA"
    assert repetida_datos["datos_resultado"]["duplicado"] is True

    total = (await db_session.execute(
        select(func.count(PagoDiferido.id)).where(PagoDiferido.tenant_id == tenant["id"])
    )).scalar_one()
    assert total == 1


# ============================================================
# Confirmación por webhook
# ============================================================

async def test_webhook_confirmado_marca_orden_stock_y_comprobante(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    firmar_webhook,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo, cantidad=1)

    uuid_pago = str(uuid4())
    pago_datos = (
        await _registrar_pago(client, dispositivo, orden, uuid_pago)
    ).json()

    evento = _evento(
        "payment_intent.succeeded",
        pago_datos["stripe_payment_intent_id"],
        tenant_id=tenant["id"],
        orden_id=orden["orden_id"],
        uuid_pago=uuid_pago,
    )
    resultado = await _enviar_webhook(client, firmar_webhook, evento)
    assert resultado["recibido"] is True
    assert resultado["procesado"] is True
    assert resultado["duplicado"] is False
    assert resultado["motivo"] is None

    consulta = await client.get(
        f"/api/v1/pagos/diferidos/{uuid_pago}",
        headers=dispositivo["headers"],
    )
    assert consulta.status_code == 200, consulta.text
    assert consulta.json()["estado"] == EstadoPagoDiferido.CONFIRMADO.value
    assert consulta.json()["requiere_accion"] is False

    db_session.expire_all()
    orden_db = await db_session.get(Orden, orden["orden_id"])
    assert orden_db.estado == EstadoOrden.PAGADO
    assert orden_db.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA

    inventario = (await db_session.execute(
        select(Inventario).where(
            Inventario.variante_producto_id == catalogo["variante_id"]
        )
    )).scalar_one()
    assert inventario.cantidad == 9
    assert inventario.cantidad_reservada == 0

    transaccion = (await db_session.execute(
        select(TransaccionPago).where(TransaccionPago.orden_id == orden["orden_id"])
    )).scalar_one()
    assert transaccion.estado == EstadoTransaccion.CONFIRMADO

    comprobante = (await db_session.execute(
        select(Comprobante).where(Comprobante.orden_id == orden["orden_id"])
    )).scalar_one()
    assert comprobante.tipo == TipoComprobante.FACTURA
    assert comprobante.es_temporal is False
    assert comprobante.tenant_id == tenant["id"]

    evento_db = (await db_session.execute(
        select(EventoStripe).where(EventoStripe.stripe_event_id == evento["id"])
    )).scalar_one()
    assert evento_db.procesado is True
    assert evento_db.pago_diferido_id == pago_datos["id"]


async def test_webhook_duplicado_no_repite_efectos(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    firmar_webhook,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo, cantidad=2)

    uuid_pago = str(uuid4())
    pago_datos = (
        await _registrar_pago(client, dispositivo, orden, uuid_pago)
    ).json()
    evento = _evento(
        "payment_intent.succeeded",
        pago_datos["stripe_payment_intent_id"],
        tenant_id=tenant["id"],
        orden_id=orden["orden_id"],
        uuid_pago=uuid_pago,
    )

    primera = await _enviar_webhook(client, firmar_webhook, evento)
    assert primera["duplicado"] is False
    segunda = await _enviar_webhook(client, firmar_webhook, evento)
    assert segunda["duplicado"] is True
    assert segunda["procesado"] is True

    db_session.expire_all()
    inventario = (await db_session.execute(
        select(Inventario).where(
            Inventario.variante_producto_id == catalogo["variante_id"]
        )
    )).scalar_one()
    assert inventario.cantidad == 8
    assert inventario.cantidad_reservada == 0

    comprobantes = (await db_session.execute(
        select(func.count(Comprobante.id)).where(
            Comprobante.orden_id == orden["orden_id"]
        )
    )).scalar_one()
    assert comprobantes == 1

    eventos = await client.get(
        f"/api/v1/pagos/{pago_datos['id']}/eventos",
        headers=dispositivo["headers"],
    )
    assert eventos.status_code == 200, eventos.text
    assert len(eventos.json()) == 1


async def test_webhook_fuera_de_orden_se_concilia_al_registrar_el_pago(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    firmar_webhook,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=5)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo)

    uuid_pago = str(uuid4())
    # El webhook llega antes de que exista el PagoDiferido.
    adelantado = _evento(
        "payment_intent.succeeded",
        "pi_sim_adelantado",
        tenant_id=tenant["id"],
        orden_id=orden["orden_id"],
        uuid_pago=uuid_pago,
    )
    previo = await _enviar_webhook(client, firmar_webhook, adelantado)
    assert previo["procesado"] is False
    assert previo["motivo"] == "PAGO_NO_ENCONTRADO"

    registro = await _registrar_pago(client, dispositivo, orden, uuid_pago)
    assert registro.status_code == 201, registro.text
    cuerpo = registro.json()
    # Al registrar el pago se concilia el evento que había llegado antes.
    assert cuerpo["estado"] == EstadoPagoDiferido.CONFIRMADO.value


# ============================================================
# Rechazo, 3DS y confirmación verificada
# ============================================================

async def test_tarjeta_rechazada_permite_reintento_sin_doble_cobro(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    firmar_webhook,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo)

    uuid_pago = str(uuid4())
    pago_datos = (
        await _registrar_pago(client, dispositivo, orden, uuid_pago)
    ).json()
    intent_original = pago_datos["stripe_payment_intent_id"]

    fallo = _evento(
        "payment_intent.payment_failed",
        intent_original,
        tenant_id=tenant["id"],
        orden_id=orden["orden_id"],
        uuid_pago=uuid_pago,
        objeto_extra={"last_payment_error": {"message": "Tarjeta rechazada"}},
    )
    resultado = await _enviar_webhook(client, firmar_webhook, fallo)
    assert resultado["procesado"] is True

    db_session.expire_all()
    pago = await _pago(db_session, uuid_pago)
    assert pago.estado == EstadoPagoDiferido.RECHAZADO
    assert pago.ultimo_error == "Tarjeta rechazada"
    assert pago.motivo_rechazo == "Tarjeta rechazada"
    assert pago.intentos == 2

    orden_db = await db_session.get(Orden, orden["orden_id"])
    assert orden_db.estado == EstadoOrden.PENDIENTE_PAGO_DIFERIDO

    # El reintento crea un nuevo intento con otra Idempotency-Key.
    reintento = await client.post(
        f"/api/v1/pagos/diferidos/{uuid_pago}/reintentar",
        headers=dispositivo["headers"],
    )
    assert reintento.status_code == 200, reintento.text
    datos_reintento = reintento.json()
    assert datos_reintento["estado"] == EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE.value
    assert datos_reintento["intentos"] == 3
    assert datos_reintento["stripe_payment_intent_id"] != intent_original

    nuevo_intent = datos_reintento["stripe_payment_intent_id"]
    exito = _evento(
        "payment_intent.succeeded",
        nuevo_intent,
        tenant_id=tenant["id"],
        orden_id=orden["orden_id"],
        uuid_pago=uuid_pago,
    )
    assert (await _enviar_webhook(client, firmar_webhook, exito))["procesado"] is True

    db_session.expire_all()
    pago = await _pago(db_session, uuid_pago)
    assert pago.estado == EstadoPagoDiferido.CONFIRMADO
    assert pago.stripe_payment_intent_id == nuevo_intent

    transacciones = (await db_session.execute(
        select(TransaccionPago).where(TransaccionPago.orden_id == orden["orden_id"])
    )).scalars().all()
    assert len(transacciones) == 1
    assert transacciones[0].estado == EstadoTransaccion.CONFIRMADO

    # Reintentar un pago confirmado no crea un segundo cobro.
    otra_vez = await client.post(
        f"/api/v1/pagos/diferidos/{uuid_pago}/reintentar",
        headers=dispositivo["headers"],
    )
    assert otra_vez.status_code == 200, otra_vez.text
    assert otra_vez.json()["duplicado"] is True
    assert otra_vez.json()["stripe_payment_intent_id"] == nuevo_intent


async def test_pago_requiere_accion_y_se_confirma_verificando_el_intent(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    firmar_webhook,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo)

    uuid_pago = str(uuid4())
    pago_datos = (
        await _registrar_pago(client, dispositivo, orden, uuid_pago)
    ).json()

    tres_d = _evento(
        "payment_intent.requires_action",
        pago_datos["stripe_payment_intent_id"],
        tenant_id=tenant["id"],
        orden_id=orden["orden_id"],
        uuid_pago=uuid_pago,
    )
    assert (await _enviar_webhook(client, firmar_webhook, tres_d))["procesado"] is True

    db_session.expire_all()
    assert (await _pago(db_session, uuid_pago)).estado == (
        EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE
    )

    # Sin confirmación del PaymentIntent, el endpoint no confirma el cobro.
    antes = await client.post(
        f"/api/v1/pagos/diferidos/{uuid_pago}/confirmar",
        headers=dispositivo["headers"],
    )
    assert antes.status_code == 409, antes.text
    assert antes.json()["codigo"] == "PAGO_NO_CONFIRMADO"
    assert antes.json()["reintentar"] is True

    # Stripe confirma el intent: la verificación directa cierra el pago.
    StripePagoService.marcar_confirmado_simulado(
        pago_datos["stripe_payment_intent_id"]
    )
    despues = await client.post(
        f"/api/v1/pagos/diferidos/{uuid_pago}/confirmar",
        headers=dispositivo["headers"],
    )
    assert despues.status_code == 200, despues.text
    assert despues.json()["estado"] == EstadoPagoDiferido.CONFIRMADO.value

    db_session.expire_all()
    orden_db = await db_session.get(Orden, orden["orden_id"])
    assert orden_db.estado == EstadoOrden.PAGADO

    duplicado = await client.post(
        f"/api/v1/pagos/diferidos/{uuid_pago}/confirmar",
        headers=dispositivo["headers"],
    )
    assert duplicado.status_code == 200
    assert duplicado.json()["duplicado"] is True


# ============================================================
# Reglas de negocio: orden expirada, tenant suspendido y reembolso
# ============================================================

async def test_orden_expirada_rechaza_el_pago_y_libera_stock(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo, cantidad=3)

    db_session.expire_all()
    orden_db = await db_session.get(Orden, orden["orden_id"])
    orden_db.expira_en = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.commit()

    respuesta = await _registrar_pago(client, dispositivo, orden)
    assert respuesta.status_code == 409, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["codigo"] == "CONFLICTO_MANUAL"
    assert "expira_en" in cuerpo["datos"]

    db_session.expire_all()
    orden_db = await db_session.get(Orden, orden["orden_id"])
    assert orden_db.estado == EstadoOrden.PAGO_VENCIDO

    inventario = (await db_session.execute(
        select(Inventario).where(
            Inventario.variante_producto_id == catalogo["variante_id"]
        )
    )).scalar_one()
    assert inventario.cantidad == 10
    assert inventario.cantidad_reservada == 0

    pagos = (await db_session.execute(
        select(func.count(PagoDiferido.id)).where(PagoDiferido.tenant_id == tenant["id"])
    )).scalar_one()
    assert pagos == 0


async def test_tenant_suspendido_reembolsa_y_regulariza_la_orden(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    firmar_webhook,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo)

    # El pago nace de una operación offline, para que el conflicto tenga traza.
    uuid_pago = str(uuid4())
    sync = await client.post(
        "/api/v1/offline/sync",
        json={
            "operaciones": [
                operacion_payload(
                    uuid_operacion=str(uuid4()),
                    tipo_operacion="PAGO_DIFERIDO",
                    payload={
                        "uuid_pago": uuid_pago,
                        "uuid_orden": orden["uuid_orden"],
                    },
                )
            ]
        },
        headers=dispositivo["headers"],
    )
    assert sync.status_code == 200, sync.text
    datos = sync.json()["resultados"][0]["datos_resultado"]
    assert datos["estado"] == EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE.value

    db_session.expire_all()
    tenant_db = await db_session.get(Tenant, int(tenant["id"]))
    tenant_db.estado = EstadoTenant.SUSPENDIDO
    await db_session.commit()

    evento = _evento(
        "payment_intent.succeeded",
        datos["stripe_payment_intent_id"],
        tenant_id=tenant["id"],
        orden_id=orden["orden_id"],
        uuid_pago=uuid_pago,
    )
    resultado = await _enviar_webhook(client, firmar_webhook, evento)
    assert resultado["procesado"] is True, resultado

    db_session.expire_all()
    pago = await _pago(db_session, uuid_pago)
    assert pago.estado == EstadoPagoDiferido.RECHAZADO
    assert pago.reembolso_id is not None

    orden_db = await db_session.get(Orden, orden["orden_id"])
    assert orden_db.estado == EstadoOrden.PENDIENTE_REGULARIZACION

    conflicto = (await db_session.execute(
        select(LogConflicto).where(LogConflicto.tenant_id == tenant["id"])
    )).scalar_one()
    assert conflicto.tipo_conflicto == TipoConflicto.TENANT_SUSPENDIDO.value
    assert conflicto.resuelto is False

    # La orden queda pendiente de regularización: la reserva se conserva para
    # poder completar el pago cuando el tenant vuelva a estar operativo.
    inventario = (await db_session.execute(
        select(Inventario).where(
            Inventario.variante_producto_id == catalogo["variante_id"]
        )
    )).scalar_one()
    assert inventario.cantidad == 10
    assert inventario.cantidad_reservada == 1


async def test_reembolso_total_y_no_duplicado(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    firmar_webhook,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="150.00", stock=10)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo)

    uuid_pago = str(uuid4())
    pago_datos = (
        await _registrar_pago(client, dispositivo, orden, uuid_pago)
    ).json()
    exito = _evento(
        "payment_intent.succeeded",
        pago_datos["stripe_payment_intent_id"],
        tenant_id=tenant["id"],
        orden_id=orden["orden_id"],
        uuid_pago=uuid_pago,
    )
    await _enviar_webhook(client, firmar_webhook, exito)

    reembolso = await client.post(
        f"/api/v1/pagos/{pago_datos['id']}/reembolsar",
        json={"motivo": "Devolución del cliente"},
        headers=dispositivo["headers"],
    )
    assert reembolso.status_code == 200, reembolso.text
    cuerpo = reembolso.json()
    assert cuerpo["estado"] == "REEMBOLSADO"
    assert cuerpo["reutilizado"] is False
    assert Decimal(str(cuerpo["monto"])) == Decimal("150.00")
    assert cuerpo["reembolso_id"].startswith("re_sim_")

    db_session.expire_all()
    pago = await _pago(db_session, uuid_pago)
    assert pago.reembolso_id == cuerpo["reembolso_id"]
    assert pago.estado == EstadoPagoDiferido.RECHAZADO

    transaccion = (await db_session.execute(
        select(TransaccionPago).where(TransaccionPago.orden_id == orden["orden_id"])
    )).scalar_one()
    assert transaccion.estado == EstadoTransaccion.REEMBOLSADO

    segunda = await client.post(
        f"/api/v1/pagos/{pago_datos['id']}/reembolsar",
        json={"monto": "50.00", "motivo": "Otro motivo"},
        headers=dispositivo["headers"],
    )
    assert segunda.status_code == 200, segunda.text
    assert segunda.json()["reutilizado"] is True
    assert segunda.json()["reembolso_id"] == cuerpo["reembolso_id"]
    assert Decimal(str(segunda.json()["monto"])) == Decimal("150.00")


# ============================================================
# Errores de Stripe
# ============================================================

async def test_error_transitorio_de_stripe_queda_en_cola_y_se_reintenta(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    monkeypatch,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo)

    def _falla(*args, **kwargs):
        raise StripeErrorTransitorio("Timeout de Stripe")

    uuid_pago = str(uuid4())
    with monkeypatch.context() as contexto:
        contexto.setattr(StripePagoService, "crear_payment_intent", _falla)
        respuesta = await _registrar_pago(client, dispositivo, orden, uuid_pago)
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == EstadoPagoDiferido.EN_COLA.value
    assert "Timeout" in cuerpo["ultimo_error"]
    assert cuerpo["stripe_payment_intent_id"] is None

    reintento = await client.post(
        f"/api/v1/pagos/diferidos/{uuid_pago}/reintentar",
        headers=dispositivo["headers"],
    )
    assert reintento.status_code == 200, reintento.text
    datos = reintento.json()
    assert datos["estado"] == EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE.value
    assert datos["stripe_payment_intent_id"].startswith("pi_sim_")
    assert datos["intentos"] == 2

    db_session.expire_all()
    pago = await _pago(db_session, uuid_pago)
    assert pago.estado == EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE
    assert pago.ultimo_error is None


async def test_webhook_con_evento_desconocido_queda_registrado(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    firmar_webhook,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=5)
    dispositivo = await dispositivo_offline(tenant)
    orden = await _crear_orden(client, dispositivo, catalogo)

    uuid_pago = str(uuid4())
    pago_datos = (
        await _registrar_pago(client, dispositivo, orden, uuid_pago)
    ).json()

    evento = _evento(
        "customer.created",
        pago_datos["stripe_payment_intent_id"],
        tenant_id=tenant["id"],
        orden_id=orden["orden_id"],
        uuid_pago=uuid_pago,
    )
    resultado = await _enviar_webhook(client, firmar_webhook, evento)
    assert resultado["recibido"] is True
    assert resultado["procesado"] is True

    eventos = await client.get(
        f"/api/v1/pagos/{pago_datos['id']}/eventos",
        headers=dispositivo["headers"],
    )
    assert eventos.status_code == 200, eventos.text
    assert len(eventos.json()) == 1
    assert eventos.json()[0]["tipo"] == "customer.created"


async def test_payment_intent_no_se_reutiliza_entre_pagos(db_session, demo_tenant):
    """Plan 3.6: `stripe_payment_intent_id` es único cuando no es NULL.

    El índice único parcial vive en el modelo, así que también se aplica en la
    base de pruebas creada con `create_all`.
    """
    tenant_id = int(demo_tenant.id)
    orden = Orden(
        numero_orden=f"ORD-PI-{uuid4().hex[:8]}",
        tenant_id=tenant_id,
        tipo=TipoOrden.PRESENCIAL,
        estado=EstadoOrden.PENDIENTE_PAGO,
        total=Decimal("100.00"),
    )
    db_session.add(orden)
    await db_session.commit()
    orden_id = orden.id

    def _pago(payment_intent: str | None = "pi_unico_examen2"):
        return PagoDiferido(
            tenant_id=tenant_id,
            orden_id=orden_id,
            uuid_pago=uuid4(),
            monto=Decimal("100.00"),
            idempotency_key=uuid4().hex,
            fecha_expiracion=datetime.now(timezone.utc) + timedelta(hours=24),
            stripe_payment_intent_id=payment_intent,
        )

    db_session.add(_pago())
    await db_session.commit()

    db_session.add(_pago())
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # El índice es parcial: dos pagos sin PaymentIntent siguen siendo válidos.
    db_session.add(_pago(None))
    db_session.add(_pago(None))
    await db_session.commit()
