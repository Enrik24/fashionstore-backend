from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import hashlib
import hmac
import json
import time

import pytest
import stripe
from starlette.requests import Request

import main
from app.apps.gestion_saas.models import EstadoSuscripcion, EstadoTenant
from app.apps.gestion_saas.router import checkout_suscripcion, confirmar_retorno_suscripcion, webhook_stripe
from app.apps.gestion_saas.schemas import SuscripcionStripeCheckoutRequest
from app.apps.gestion_saas.services import PagoSuscripcionService, SuscripcionService, TenantService
from app.apps.gestion_ventas.models import EstadoTransaccion
from app.config import settings
from app.exceptions import BadRequestException, NotFoundException, ValidationException
from app.services.stripe_service import StripeService


@pytest.fixture
def crear_session(monkeypatch):
    crear = Mock(return_value=SimpleNamespace(id="cs_plan_unit", url="https://checkout.stripe.com/c/pay/cs_plan_unit"))
    monkeypatch.setattr(stripe.checkout.Session, "create", crear)
    return crear


@pytest.mark.parametrize("precio,centavos", [("199.00", 19900), ("499.00", 49900), ("99.99", 9999), ("0.25", 25)])
def test_checkout_plan_respeta_importe_sin_aumentarlo(crear_session, precio, centavos):
    StripeService.create_checkout_session_suscripcion(
        suscripcion_id=7, tenant_id=10, plan_id=1, plan_nombre="Básico",
        precio_mensual=Decimal(precio), moneda="BOB",
    )
    datos = crear_session.call_args.kwargs
    assert datos["line_items"] == [{
        "price_data": {
            "currency": "bob", "unit_amount": centavos, "product_data": {"name": "Plan Básico"},
        },
        "quantity": 1,
    }]
    assert datos["metadata"] == {
        "tipo": "suscripcion", "suscripcion_id": "7", "tenant_id": "10", "plan_id": "1",
    }
    assert "tenant_id=10" in datos["success_url"]


@pytest.mark.parametrize("precio", ["0.00", "-1.00"])
def test_plan_no_positivo_no_crea_checkout(crear_session, precio):
    with pytest.raises(BadRequestException):
        StripeService.create_checkout_session_suscripcion(
            suscripcion_id=7, tenant_id=10, plan_id=1, plan_nombre="Básico",
            precio_mensual=Decimal(precio), moneda="BOB",
        )
    crear_session.assert_not_called()


def test_stripe_rechaza_importe_pequeno_sin_alterarlo(crear_session):
    crear_session.side_effect = stripe.error.InvalidRequestError("Amount too small", "line_items")
    with pytest.raises(BadRequestException, match="Stripe rechazó"):
        StripeService.create_checkout_session_suscripcion(
            suscripcion_id=7, tenant_id=10, plan_id=1, plan_nombre="Básico",
            precio_mensual=Decimal("0.25"), moneda="BOB",
        )
    assert crear_session.call_args.kwargs["line_items"][0]["price_data"]["unit_amount"] == 25


@pytest.fixture
def suscripcion():
    return SimpleNamespace(
        id=7, tenant_id=10, plan_id=1, estado=EstadoSuscripcion.ACTIVA,
        estado_pago="PENDIENTE", plan=SimpleNamespace(id=1, nombre="Básico", precio_mensual=Decimal("199.00")),
    )


@pytest.mark.asyncio
async def test_pago_plan_guarda_bob_no_moneda_del_tenant(monkeypatch, crear_session, suscripcion):
    monkeypatch.setattr(SuscripcionService, "obtener", AsyncMock(return_value=suscripcion))
    monkeypatch.setattr(TenantService, "obtener_tenant", AsyncMock(return_value=SimpleNamespace(
        correo_contacto="contacto@example.com", moneda="USD",
    )))
    db = SimpleNamespace(add=Mock(), commit=AsyncMock())
    await PagoSuscripcionService.crear_sesion_stripe(db, suscripcion.id, tenant_id=10)
    pago = db.add.call_args.args[0]
    assert pago.monto == Decimal("199.00")
    assert pago.moneda == "BOB"
    assert pago.tenant_id == 10
    assert pago.datos_respuesta["plan_id"] == 1
    assert crear_session.call_args.kwargs["line_items"][0]["price_data"]["currency"] == "bob"


@pytest.mark.asyncio
async def test_checkout_no_acepta_importe_o_moneda_del_cliente(monkeypatch, crear_session, suscripcion):
    monkeypatch.setattr(SuscripcionService, "obtener", AsyncMock(return_value=suscripcion))
    db = SimpleNamespace(add=Mock(), commit=AsyncMock())
    datos = SuscripcionStripeCheckoutRequest.model_validate({
        "suscripcion_id": 7, "monto": 1, "moneda": "USD",
    })
    await checkout_suscripcion(
        datos, tenant=SimpleNamespace(id=10), db=db, current_user=SimpleNamespace(correo="superadmin@example.com"),
    )
    precio = crear_session.call_args.kwargs["line_items"][0]["price_data"]
    assert precio["unit_amount"] == 19900
    assert precio["currency"] == "bob"
    assert db.add.call_args.args[0].moneda == "BOB"


@pytest.mark.asyncio
async def test_no_crea_pago_para_otro_tenant(monkeypatch, crear_session, suscripcion):
    monkeypatch.setattr(SuscripcionService, "obtener", AsyncMock(return_value=suscripcion))
    with pytest.raises(NotFoundException):
        await PagoSuscripcionService.crear_sesion_stripe(SimpleNamespace(), 7, tenant_id=99)
    crear_session.assert_not_called()


@pytest.fixture
def pago():
    return SimpleNamespace(
        id=50, tenant_id=10, suscripcion_id=7, monto=Decimal("199.00"), moneda="BOB",
        estado=EstadoTransaccion.PENDIENTE, stripe_checkout_session_id="cs_plan_unit",
        stripe_payment_intent_id=None, datos_respuesta={"session_id": "cs_plan_unit", "plan_id": 1},
    )


@pytest.fixture
def session_stripe():
    return SimpleNamespace(
        id="cs_plan_unit", payment_status="paid", payment_intent="pi_plan_unit",
        amount_total=19900, currency="bob", metadata={
            "tipo": "suscripcion", "suscripcion_id": "7", "tenant_id": "10", "plan_id": "1",
        },
    )


def db_falso(*valores):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=[SimpleNamespace(scalar_one_or_none=lambda v=v: v) for v in valores]),
        flush=AsyncMock(), commit=AsyncMock(),
    )


@pytest.fixture
def confirmacion(monkeypatch, pago, suscripcion, session_stripe):
    monkeypatch.setattr(StripeService, "retrieve_session", Mock(return_value=session_stripe))

    async def confirmar(db, id):
        pago.estado = EstadoTransaccion.CONFIRMADO
        suscripcion.estado_pago = "CONFIRMADO"

    monkeypatch.setattr(PagoSuscripcionService, "confirmar", AsyncMock(side_effect=confirmar))
    return db_falso(pago, suscripcion)


@pytest.mark.asyncio
async def test_pago_correcto_confirma_suscripcion(confirmacion, pago, suscripcion):
    status = await PagoSuscripcionService.procesar_pago_stripe_completado(
        confirmacion, "cs_plan_unit", 7, tenant_id=10,
    )
    assert status == "paid"
    assert pago.estado == EstadoTransaccion.CONFIRMADO
    assert pago.stripe_payment_intent_id == "pi_plan_unit"
    assert pago.datos_respuesta["amount_total"] == 19900
    assert pago.datos_respuesta["currency"] == "bob"
    assert suscripcion.estado_pago == "CONFIRMADO"
    PagoSuscripcionService.confirmar.assert_awaited_once_with(confirmacion, 50)
    assert all(c.args[0]._for_update_arg is not None for c in confirmacion.execute.call_args_list)
    assert confirmacion.execute.call_args_list[0].args[0].compile().params["tenant_id_1"] == 10


@pytest.mark.asyncio
async def test_pago_validado_activa_suscripcion_y_tenant(monkeypatch, pago, suscripcion, session_stripe):
    suscripcion.estado = EstadoSuscripcion.SUSPENDIDA
    tenant = SimpleNamespace(estado=EstadoTenant.SUSPENDIDO)
    monkeypatch.setattr(StripeService, "retrieve_session", Mock(return_value=session_stripe))
    monkeypatch.setattr(SuscripcionService, "obtener", AsyncMock(return_value=suscripcion))
    monkeypatch.setattr(TenantService, "obtener_tenant", AsyncMock(return_value=tenant))
    db = db_falso(pago, suscripcion, pago)
    db.refresh = AsyncMock()
    assert await PagoSuscripcionService.procesar_pago_stripe_completado(db, "cs_plan_unit", 7, tenant_id=10) == "paid"
    assert pago.estado == EstadoTransaccion.CONFIRMADO
    assert suscripcion.estado == EstadoSuscripcion.ACTIVA
    assert suscripcion.estado_pago == "CONFIRMADO"
    assert tenant.estado == EstadoTenant.OPERATIVO
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(pago)


@pytest.mark.asyncio
@pytest.mark.parametrize("datos", [{"amount_total": 1}, {"amount_total": 49900}, {"currency": "usd"}])
async def test_importe_o_moneda_incorrectos_no_activan(confirmacion, pago, suscripcion, session_stripe, datos):
    for campo, valor in datos.items():
        setattr(session_stripe, campo, valor)
    with pytest.raises(ValidationException, match="no coincide"):
        await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 7)
    assert pago.estado == EstadoTransaccion.PENDIENTE
    assert suscripcion.estado_pago == "PENDIENTE"
    assert pago.datos_respuesta["error"]
    assert pago.stripe_payment_intent_id is None
    PagoSuscripcionService.confirmar.assert_not_awaited()
    confirmacion.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sesion_antigua_usd_requiere_revision(confirmacion, pago, session_stripe):
    pago.moneda = "USD"
    session_stripe.currency = "usd"
    with pytest.raises(ValidationException, match="no coincide"):
        await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 7)
    assert pago.moneda == "USD"
    assert pago.monto == Decimal("199.00")
    PagoSuscripcionService.confirmar.assert_not_awaited()


@pytest.mark.asyncio
async def test_pago_historico_confirmado_usd_no_se_reescribe(confirmacion, pago, session_stripe):
    pago.estado = EstadoTransaccion.CONFIRMADO
    pago.moneda = "USD"
    session_stripe.currency = "usd"
    respuesta_original = dict(pago.datos_respuesta)
    with pytest.raises(ValidationException, match="no coincide"):
        await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 7)
    assert pago.estado == EstadoTransaccion.CONFIRMADO
    assert pago.moneda == "USD"
    assert pago.datos_respuesta == respuesta_original
    PagoSuscripcionService.confirmar.assert_not_awaited()
    confirmacion.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("campo,valor", [
    ("tipo", "orden"), ("suscripcion_id", "99"), ("tenant_id", "99"), ("plan_id", "99"),
])
async def test_metadata_de_otro_pago_no_confirma(confirmacion, session_stripe, campo, valor):
    session_stripe.metadata[campo] = valor
    with pytest.raises(ValidationException, match="no corresponde"):
        await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 7)
    PagoSuscripcionService.confirmar.assert_not_awaited()
    confirmacion.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_id_sesion_incorrecto_no_confirma(confirmacion, session_stripe):
    session_stripe.id = "cs_otro"
    with pytest.raises(ValidationException, match="no corresponde"):
        await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 7)
    PagoSuscripcionService.confirmar.assert_not_awaited()


@pytest.mark.asyncio
async def test_suscripcion_solicitada_distinta_no_confirma(confirmacion):
    with pytest.raises(ValidationException, match="no corresponde"):
        await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 99)
    PagoSuscripcionService.confirmar.assert_not_awaited()


@pytest.mark.asyncio
async def test_sesion_sin_pago_no_usa_otro_pago_pendiente(monkeypatch, session_stripe):
    monkeypatch.setattr(StripeService, "retrieve_session", Mock(return_value=session_stripe))
    db = db_falso(None)
    with pytest.raises(NotFoundException):
        await PagoSuscripcionService.procesar_pago_stripe_completado(db, "cs_plan_unit", 7, tenant_id=10)
    assert db.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unpaid", "no_payment_required"])
async def test_pago_no_pagado_no_altera_bd(confirmacion, session_stripe, status):
    session_stripe.payment_status = status
    assert await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 7) == status
    confirmacion.execute.assert_not_awaited()
    PagoSuscripcionService.confirmar.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmacion_repetida_no_reactiva_dos_veces(confirmacion, pago):
    await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 7)
    confirmacion.execute.side_effect = db_falso(pago).execute.side_effect
    assert await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 7) == "paid"
    PagoSuscripcionService.confirmar.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cambio", ["plan", "cancelacion", "reembolso"])
async def test_pago_de_plan_cambiado_o_cancelado_requiere_revision(confirmacion, pago, suscripcion, cambio):
    if cambio == "plan":
        suscripcion.plan_id = 2
    elif cambio == "cancelacion":
        suscripcion.estado = EstadoSuscripcion.CANCELADA
    else:
        pago.estado = EstadoTransaccion.REEMBOLSADO
    with pytest.raises(ValidationException, match="cambió"):
        await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 7)
    assert pago.datos_respuesta["error"]
    PagoSuscripcionService.confirmar.assert_not_awaited()


@pytest.mark.asyncio
async def test_edicion_del_precio_no_altera_importe_congelado(confirmacion, suscripcion):
    suscripcion.plan.precio_mensual = Decimal("299.00")
    assert await PagoSuscripcionService.procesar_pago_stripe_completado(confirmacion, "cs_plan_unit", 7) == "paid"
    PagoSuscripcionService.confirmar.assert_awaited_once()


@pytest.mark.asyncio
async def test_retorno_del_superadmin_utiliza_tenant(confirmacion):
    resultado = await confirmar_retorno_suscripcion(
        "cs_plan_unit", 7, tenant=SimpleNamespace(id=10), db=confirmacion, _=SimpleNamespace(id=1),
    )
    assert resultado["payment_status"] == "paid"
    assert confirmacion.execute.call_args_list[0].args[0].compile().params["tenant_id_1"] == 10


def request_evento(evento):
    payload = json.dumps(evento).encode()

    async def recibir():
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)}, recibir), payload


@pytest.fixture
def evento(session_stripe):
    return {
        "id": "evt_plan_unit", "object": "event", "type": "checkout.session.completed",
        "data": {"object": {"id": session_stripe.id, "metadata": session_stripe.metadata}},
    }


@pytest.fixture
def firmar(monkeypatch):
    secreto = "whsec_plan_unit_only"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secreto)

    def firma(payload):
        timestamp = int(time.time())
        firmado = str(timestamp).encode() + b"." + payload
        digest = hmac.new(secreto.encode(), firmado, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={digest}"

    return firma


@pytest.mark.asyncio
async def test_webhook_sin_firma_no_activa(confirmacion, evento):
    request, _ = request_evento(evento)
    with pytest.raises(BadRequestException, match="stripe-signature"):
        await webhook_stripe(request, stripe_signature=None, db=confirmacion)
    PagoSuscripcionService.confirmar.assert_not_awaited()
    confirmacion.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_firma_invalida_no_activa(confirmacion, evento, firmar):
    request, _ = request_evento(evento)
    with pytest.raises(BadRequestException, match="validando webhook"):
        await webhook_stripe(request, stripe_signature="t=1,v1=firma_invalida", db=confirmacion)
    PagoSuscripcionService.confirmar.assert_not_awaited()
    confirmacion.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_alterado_despues_de_firmar_no_activa(confirmacion, evento, firmar):
    _, payload = request_evento(evento)
    firma = firmar(payload)
    evento["data"]["object"]["id"] = "cs_otro_pago"
    request, _ = request_evento(evento)
    with pytest.raises(BadRequestException, match="validando webhook"):
        await webhook_stripe(request, stripe_signature=firma, db=confirmacion)
    PagoSuscripcionService.confirmar.assert_not_awaited()
    confirmacion.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_firmado_confirma_una_sola_vez(confirmacion, pago, evento, firmar):
    request, payload = request_evento(evento)
    resultado = await webhook_stripe(request, stripe_signature=firmar(payload), db=confirmacion)
    assert resultado["procesado"] is True
    confirmacion.execute.side_effect = db_falso(pago).execute.side_effect
    request, payload = request_evento(evento)
    await webhook_stripe(request, stripe_signature=firmar(payload), db=confirmacion)
    PagoSuscripcionService.confirmar.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_firmado_no_acepta_importe_incorrecto(confirmacion, evento, firmar, session_stripe):
    session_stripe.amount_total = 1
    request, payload = request_evento(evento)
    with pytest.raises(ValidationException, match="no coincide"):
        await webhook_stripe(request, stripe_signature=firmar(payload), db=confirmacion)
    PagoSuscripcionService.confirmar.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", ["compra", "otro_evento"])
async def test_webhook_no_procesa_eventos_ajenos(confirmacion, evento, firmar, tipo):
    if tipo == "compra":
        evento["data"]["object"]["metadata"]["tipo"] = "orden"
    else:
        evento["type"] = "payment_intent.succeeded"
    request, payload = request_evento(evento)
    resultado = await webhook_stripe(request, stripe_signature=firmar(payload), db=confirmacion)
    assert resultado["procesado"] is False
    confirmacion.execute.assert_not_awaited()
    PagoSuscripcionService.confirmar.assert_not_awaited()
