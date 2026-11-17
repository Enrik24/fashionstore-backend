from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import stripe

import main
from app.apps.gestion_ventas.models import DetalleOrden, EstadoOrden, EstadoTransaccion, MetodoPagoDigital, Orden
from app.apps.gestion_ventas.services import CarritoService, CuponService, OrdenService, PagoService
from app.exceptions import BadRequestException, NotFoundException
from app.services.firebase_service import FirebaseService
from app.services.stripe_service import StripeService


@pytest.fixture
def crear_session(monkeypatch):
    crear = Mock(return_value=SimpleNamespace(id="cs_unit", url="https://checkout.stripe.com/c/pay/cs_unit"))
    monkeypatch.setattr(stripe.checkout.Session, "create", crear)
    return crear


@pytest.mark.parametrize("total,centavos", [
    ("350.00", 35000),
    ("297.50", 29750),
    ("280.00", 28000),
    ("647.49", 64749),
    ("0.25", 25),
])
def test_checkout_cobra_total_final_sin_aumentar_importe(crear_session, total, centavos):
    resultado = StripeService.create_checkout_session(
        orden_id=15, numero_orden="ORD-UNIT", total=Decimal(total), moneda="BOB",
        email_usuario="cliente@example.com",
    )
    datos = crear_session.call_args.kwargs
    assert datos["line_items"] == [{
        "price_data": {
            "currency": "bob", "unit_amount": centavos,
            "product_data": {"name": "Orden ORD-UNIT"},
        },
        "quantity": 1,
    }]
    assert datos["metadata"]["orden_id"] == "15"
    assert datos["customer_email"] == "cliente@example.com"
    assert resultado["session_id"] == "cs_unit"


@pytest.mark.parametrize("moneda", ["BOB", "bob", "USD", "EUR"])
def test_checkout_respeta_moneda_de_orden(crear_session, moneda):
    StripeService.create_checkout_session(
        orden_id=15, numero_orden="ORD-UNIT", total=Decimal("297.50"), moneda=moneda,
    )
    assert crear_session.call_args.kwargs["line_items"][0]["price_data"]["currency"] == moneda.lower()


@pytest.mark.parametrize("total", ["0.00", "-1.00"])
def test_total_no_positivo_no_crea_checkout(crear_session, total):
    with pytest.raises(BadRequestException):
        StripeService.create_checkout_session(
            orden_id=15, numero_orden="ORD-UNIT", total=Decimal(total), moneda="BOB",
        )
    crear_session.assert_not_called()


def test_stripe_rechaza_importe_pequeno_sin_cambiarlo(crear_session):
    crear_session.side_effect = stripe.error.InvalidRequestError("Amount too small", "line_items")
    with pytest.raises(BadRequestException, match="Stripe rechazó"):
        StripeService.create_checkout_session(
            orden_id=15, numero_orden="ORD-UNIT", total=Decimal("0.25"), moneda="BOB",
        )
    assert crear_session.call_args.kwargs["line_items"][0]["price_data"]["unit_amount"] == 25


@pytest.mark.asyncio
@pytest.mark.parametrize("items,descuento_promo,descuento_cupon,total", [
    ([("350.00", 1)], "52.50", "0.00", "297.50"),
    ([("350.00", 1)], "0.00", "0.00", "350.00"),
    ([("350.00", 1)], "0.00", "70.00", "280.00"),
    ([("350.00", 2), ("50.00", 1)], "105.00", "0.00", "645.00"),
    ([("350.00", 1)], "52.50", "70.00", "297.50"),
])
async def test_carrito_orden_y_checkout_conservan_total(
    monkeypatch, crear_session, items, descuento_promo, descuento_cupon, total,
):
    registros = []

    def guardar(registro):
        if isinstance(registro, Orden):
            registro.id = 15
            registro.tenant_id = 10
            registro.moneda = "BOB"
        elif isinstance(registro, DetalleOrden):
            registros[0].detalles.append(registro)
        registros.append(registro)

    db = SimpleNamespace(
        add=Mock(side_effect=guardar), flush=AsyncMock(), commit=AsyncMock(),
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))),
    )
    carrito = SimpleNamespace(
        items=[SimpleNamespace(
            variante_producto_id=i + 1, precio_unitario=Decimal(precio), cantidad=cantidad,
            subtotal=Decimal(precio) * cantidad,
        ) for i, (precio, cantidad) in enumerate(items)],
        cupon=SimpleNamespace(codigo="CUPON-UNIT") if Decimal(descuento_cupon) else None,
        cupon_id=7,
    )
    monkeypatch.setattr(CarritoService, "obtener_o_crear_carrito", AsyncMock(return_value=carrito))
    monkeypatch.setattr(CarritoService, "calcular_descuento_promocion", AsyncMock(return_value={
        "descuento": Decimal(descuento_promo),
    }))
    monkeypatch.setattr(CarritoService, "vaciar_carrito", AsyncMock())
    monkeypatch.setattr(CuponService, "validar_cupon", AsyncMock(return_value=(
        True, "Cupón válido", SimpleNamespace(id=7), Decimal(descuento_cupon),
    )))
    monkeypatch.setattr(CuponService, "incrementar_uso", AsyncMock())
    monkeypatch.setattr(OrdenService, "obtener_orden", AsyncMock(side_effect=lambda db, id: registros[0]))
    orden_creada = await OrdenService.crear_orden_desde_carrito(db, cliente_id=1)
    assert orden_creada.total == Decimal(total)
    assert orden_creada.detalles[0].precio_unitario == Decimal(items[0][0])
    await PagoService.crear_sesion_stripe(db, orden_creada.id, email_usuario="cliente@example.com")
    assert registros[-1].monto == Decimal(total)
    assert registros[-1].moneda == "BOB"
    assert crear_session.call_args.kwargs["line_items"][0]["price_data"]["unit_amount"] == int(Decimal(total) * 100)
    if Decimal(descuento_promo):
        CuponService.incrementar_uso.assert_not_awaited()


@pytest.fixture
def orden():
    return SimpleNamespace(
        id=15, numero_orden="ORD-UNIT", tenant_id=10, total=Decimal("297.50"), moneda="BOB",
        descuentos=Decimal("52.50"), estado=EstadoOrden.PENDIENTE_PAGO, cliente=None,
        detalles=[SimpleNamespace(precio_unitario=Decimal("350.00"), cantidad=1)],
    )


@pytest.fixture
def tx(orden):
    return SimpleNamespace(
        orden_id=orden.id, tenant_id=orden.tenant_id, monto=orden.total, moneda=orden.moneda,
        estado=EstadoTransaccion.PENDIENTE, referencia_externa="cs_unit", datos_respuesta=None,
        stripe_payment_intent_id=None,
    )


@pytest.fixture
def session_stripe():
    return SimpleNamespace(
        id="cs_unit", payment_status="paid", payment_intent="pi_unit", amount_total=29750,
        currency="bob", metadata={"orden_id": "15"},
    )


def db_falso(*valores):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=[SimpleNamespace(scalar_one_or_none=lambda v=v: v) for v in valores]),
        add=Mock(), commit=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_transaccion_guarda_monto_moneda_y_tenant_de_orden(monkeypatch, crear_session, orden):
    monkeypatch.setattr(OrdenService, "obtener_orden", AsyncMock(return_value=orden))
    db = db_falso()
    await PagoService.crear_sesion_stripe(db, orden.id, email_usuario="cliente@example.com")
    registro = db.add.call_args.args[0]
    assert registro.monto == Decimal("297.50")
    assert registro.moneda == "BOB"
    assert registro.tenant_id == 10
    assert registro.metodo_pago == MetodoPagoDigital.STRIPE
    assert crear_session.call_args.kwargs["line_items"][0]["price_data"]["unit_amount"] == 29750
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", [EstadoOrden.PAGADO, EstadoOrden.CANCELADO])
async def test_no_crea_checkout_para_orden_no_pendiente(monkeypatch, crear_session, orden, estado):
    orden.estado = estado
    monkeypatch.setattr(OrdenService, "obtener_orden", AsyncMock(return_value=orden))
    db = db_falso()
    with pytest.raises(BadRequestException):
        await PagoService.crear_sesion_stripe(db, orden.id)
    crear_session.assert_not_called()
    db.add.assert_not_called()


@pytest.fixture
def confirmacion(monkeypatch, orden, tx, session_stripe):
    monkeypatch.setattr(StripeService, "retrieve_session", Mock(return_value=session_stripe))
    monkeypatch.setattr(OrdenService, "obtener_orden", AsyncMock(return_value=orden))
    monkeypatch.setattr(OrdenService, "actualizar_estado", AsyncMock())
    monkeypatch.setattr(FirebaseService, "enviar_notificacion_usuario", AsyncMock())
    return db_falso(orden, tx)


@pytest.mark.asyncio
async def test_pago_correcto_confirma_y_conserva_datos_stripe(confirmacion, orden, tx):
    status = await PagoService.procesar_pago_stripe_completado(confirmacion, "cs_unit", orden.id)
    assert status == "paid"
    assert tx.estado == EstadoTransaccion.CONFIRMADO
    assert tx.stripe_payment_intent_id == "pi_unit"
    assert tx.datos_respuesta["amount_total"] == 29750
    assert tx.datos_respuesta["currency"] == "bob"
    OrdenService.actualizar_estado.assert_awaited_once_with(confirmacion, orden.id, EstadoOrden.PAGADO)
    assert all(c.args[0]._for_update_arg is not None for c in confirmacion.execute.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("datos", [
    {"amount_total": 35000}, {"currency": "usd"}, {"amount_total": 29749},
])
async def test_discrepancia_registra_evidencia_sin_confirmar(confirmacion, orden, tx, session_stripe, datos):
    for campo, valor in datos.items():
        setattr(session_stripe, campo, valor)
    with pytest.raises(BadRequestException, match="no coincide"):
        await PagoService.procesar_pago_stripe_completado(confirmacion, "cs_unit", orden.id)
    assert tx.estado == EstadoTransaccion.PENDIENTE
    assert orden.estado == EstadoOrden.PENDIENTE_PAGO
    assert tx.datos_respuesta["error"]
    assert tx.datos_respuesta["amount_total"] == session_stripe.amount_total
    confirmacion.commit.assert_awaited_once()
    OrdenService.actualizar_estado.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("campo,valor", [("monto", Decimal("350.00")), ("moneda", "USD")])
async def test_transaccion_inconsistente_no_confirma(confirmacion, orden, tx, campo, valor):
    setattr(tx, campo, valor)
    with pytest.raises(BadRequestException, match="no coincide"):
        await PagoService.procesar_pago_stripe_completado(confirmacion, "cs_unit", orden.id)
    OrdenService.actualizar_estado.assert_not_awaited()


@pytest.mark.asyncio
async def test_sesion_de_otra_orden_no_confirma(confirmacion, orden, session_stripe, tx):
    session_stripe.metadata = {"orden_id": "99"}
    with pytest.raises(BadRequestException, match="no corresponde"):
        await PagoService.procesar_pago_stripe_completado(confirmacion, "cs_unit", orden.id)
    assert tx.estado == EstadoTransaccion.PENDIENTE
    OrdenService.actualizar_estado.assert_not_awaited()
    confirmacion.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sesion_sin_transaccion_no_confirma(monkeypatch, orden, session_stripe):
    monkeypatch.setattr(StripeService, "retrieve_session", Mock(return_value=session_stripe))
    actualizar = AsyncMock()
    monkeypatch.setattr(OrdenService, "actualizar_estado", actualizar)
    db = db_falso(orden, None)
    with pytest.raises(BadRequestException, match="no corresponde"):
        await PagoService.procesar_pago_stripe_completado(db, "cs_unit", orden.id)
    actualizar.assert_not_awaited()


@pytest.mark.asyncio
async def test_orden_inexistente_no_confirma(monkeypatch, session_stripe):
    monkeypatch.setattr(StripeService, "retrieve_session", Mock(return_value=session_stripe))
    with pytest.raises(NotFoundException):
        await PagoService.procesar_pago_stripe_completado(db_falso(None), "cs_unit", 99)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unpaid", "no_payment_required"])
async def test_sesion_no_pagada_no_altera_bd(confirmacion, orden, tx, session_stripe, status):
    session_stripe.payment_status = status
    assert await PagoService.procesar_pago_stripe_completado(confirmacion, "cs_unit", orden.id) == status
    assert tx.estado == EstadoTransaccion.PENDIENTE
    confirmacion.execute.assert_not_awaited()
    confirmacion.commit.assert_not_awaited()
    OrdenService.actualizar_estado.assert_not_awaited()


@pytest.mark.asyncio
async def test_retorno_y_webhook_no_duplican_efectos(confirmacion, orden, tx):
    await PagoService.procesar_pago_stripe_completado(confirmacion, "cs_unit", orden.id)
    confirmacion.execute.side_effect = db_falso(orden, tx).execute.side_effect
    assert await PagoService.procesar_pago_stripe_completado(confirmacion, "cs_unit", orden.id) == "paid"
    OrdenService.actualizar_estado.assert_awaited_once()


@pytest.mark.asyncio
async def test_pago_de_orden_cancelada_requiere_revision(confirmacion, orden, tx):
    orden.estado = EstadoOrden.CANCELADO
    with pytest.raises(BadRequestException, match="ya no está pendiente"):
        await PagoService.procesar_pago_stripe_completado(confirmacion, "cs_unit", orden.id)
    assert tx.estado == EstadoTransaccion.PENDIENTE
    assert tx.datos_respuesta["error"]
    OrdenService.actualizar_estado.assert_not_awaited()


@pytest.mark.asyncio
async def test_retorno_http_rechaza_importe_incorrecto(confirmacion, orden, session_stripe):
    from starlette.requests import Request
    from app.apps.gestion_ventas.router import confirmar_retorno_stripe

    session_stripe.amount_total = 35000
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)})
    with pytest.raises(BadRequestException, match="no coincide"):
        await confirmar_retorno_stripe(
            request, "cs_unit", orden.id, current_user=SimpleNamespace(id=1), db=confirmacion,
        )
    OrdenService.actualizar_estado.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_rechaza_moneda_incorrecta(monkeypatch, confirmacion, orden, session_stripe):
    from starlette.requests import Request
    from app.apps.gestion_ventas.router import stripe_webhook

    session_stripe.currency = "usd"
    evento = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_unit", "metadata": {"orden_id": str(orden.id)},
    }}}
    monkeypatch.setattr(StripeService, "construct_webhook_event", Mock(return_value=evento))

    async def recibir():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)}, recibir)
    with pytest.raises(BadRequestException, match="no coincide"):
        await stripe_webhook(request, stripe_signature="firma_unit", db=confirmacion)
    OrdenService.actualizar_estado.assert_not_awaited()
