from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import json

import pytest
from fastapi import HTTPException

import main
from app.exceptions import FirmaWebhookInvalida, OperacionRechazada
from app.apps.gestion_saas.models import EstadoTenant
from app.apps.gestion_ventas.models import EstadoOrden
from app.apps.gestion_ventas.services import InventarioVentasService
from app.apps.operaciones_offline.models import EstadoPagoDiferido
from app.apps.pagos_diferidos.conciliacion_service import ConciliacionService
from app.apps.pagos_diferidos.orden_diferida_service import OrdenDiferidaService
from app.apps.pagos_diferidos.reembolso_service import ReembolsoService
from app.apps.pagos_diferidos.webhook_service import WebhookService
from app.services.websocket_manager import manager


@pytest.fixture
def secreto_local(monkeypatch):
    monkeypatch.setattr(WebhookService, "_secreto", staticmethod(lambda: "whsec_unit_local"))
    return "whsec_unit_local"


def resultado(valor):
    return SimpleNamespace(scalar_one_or_none=lambda: valor, scalar_one=lambda: valor)


def sesion_falsa(*valores):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=[resultado(valor) for valor in valores]),
        flush=AsyncMock(), refresh=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock(), add=Mock(),
    )


def evento_falso(procesado=False):
    return SimpleNamespace(
        stripe_event_id="evt_unit", tipo="checkout.session.completed", procesado=procesado,
        error_detalle=None, pago_diferido_id=None, tenant_id=10,
    )


def payload_falso():
    return json.dumps({
        "id": "evt_unit", "type": "checkout.session.completed", "data": {"object": {"id": "cs_unit"}},
    }).encode()


def test_firma_valida_y_cuerpo_alterado(secreto_local):
    payload = payload_falso()
    firma = WebhookService.generar_firma(payload, secreto=secreto_local)
    WebhookService.validar_firma(payload, firma)
    with pytest.raises(FirmaWebhookInvalida):
        WebhookService.validar_firma(payload + b" ", firma)


def test_firma_vencida_rechazada(secreto_local):
    payload = payload_falso()
    timestamp = int((datetime.now() - timedelta(minutes=10)).timestamp())
    firma = WebhookService.generar_firma(payload, secreto=secreto_local, timestamp=timestamp)
    with pytest.raises(FirmaWebhookInvalida):
        WebhookService.validar_firma(payload, firma)


@pytest.mark.asyncio
async def test_webhook_procesado_no_repite_efectos(monkeypatch, secreto_local):
    aplicar = AsyncMock()
    monkeypatch.setattr(ConciliacionService, "aplicar_evento", aplicar)
    db = sesion_falsa(evento_falso(procesado=True))
    payload = payload_falso()
    respuesta = await WebhookService.procesar(db, payload, WebhookService.generar_firma(payload, secreto_local))
    assert respuesta["duplicado"] is True
    aplicar.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_pendiente_puede_reintentarse(monkeypatch, secreto_local):
    evento = evento_falso()
    db = sesion_falsa(evento, evento)

    async def aplicar(db, evento):
        evento.procesado = True
        return {"procesado": True}

    aplicar_mock = AsyncMock(side_effect=aplicar)
    monkeypatch.setattr(ConciliacionService, "aplicar_evento", aplicar_mock)
    payload = payload_falso()
    respuesta = await WebhookService.procesar(db, payload, WebhookService.generar_firma(payload, secreto_local))
    assert respuesta["procesado"] is True
    aplicar_mock.assert_awaited_once()
    assert db.commit.await_count == 2
    sentencia = db.execute.call_args_list[1].args[0]
    assert sentencia._for_update_arg is not None


@pytest.mark.asyncio
async def test_webhook_fallido_responde_503_y_conserva_el_evento(monkeypatch, secreto_local):
    evento = evento_falso()
    db = sesion_falsa(evento, evento, evento)
    monkeypatch.setattr(ConciliacionService, "aplicar_evento", AsyncMock(side_effect=RuntimeError("fallo simulado")))
    payload = payload_falso()
    with pytest.raises(HTTPException) as error:
        await WebhookService.procesar(db, payload, WebhookService.generar_firma(payload, secreto_local))
    assert error.value.status_code == 503
    assert evento.procesado is False
    assert evento.error_detalle == "fallo simulado"
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_reserva_incompleta_no_consume_ningun_item(monkeypatch):
    orden = SimpleNamespace(sucursal_id=2, detalles=[
        SimpleNamespace(variante_producto_id=1, cantidad=1),
        SimpleNamespace(variante_producto_id=2, cantidad=2),
    ])
    obtener = AsyncMock(side_effect=[
        SimpleNamespace(cantidad=5, cantidad_reservada=1),
        SimpleNamespace(cantidad=5, cantidad_reservada=1),
    ])
    consumir = AsyncMock()
    monkeypatch.setattr(InventarioVentasService, "obtener_o_crear_inventario", obtener)
    monkeypatch.setattr(InventarioVentasService, "liberar_stock_reserva", consumir)
    with pytest.raises(OperacionRechazada):
        await ConciliacionService._consumir_stock_reservado(sesion_falsa(), orden)
    consumir.assert_not_awaited()
    assert all(llamada.kwargs["for_update"] for llamada in obtener.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("datos", [{"amount_total": 17500, "currency": "bob"}, {"amount_total": 14875, "currency": "usd"}])
async def test_importe_o_moneda_incorrectos_reembolsan_sin_consumir_stock(monkeypatch, datos):
    pago = SimpleNamespace(
        id=77, tenant_id=10, orden_id=15, monto=Decimal("148.75"), moneda="BOB",
        estado=EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE, reembolso_id=None,
    )
    orden = SimpleNamespace(id=15, tenant_id=10, estado=EstadoOrden.PENDIENTE_PAGO_DIFERIDO,
                            expira_en=datetime.utcnow() + timedelta(hours=1), fecha_limite_pago=None)
    db = sesion_falsa(orden, SimpleNamespace(estado=EstadoTenant.OPERATIVO))
    consumir = AsyncMock()
    reembolsar = AsyncMock()
    monkeypatch.setattr(OrdenDiferidaService, "verificar_habilitacion", AsyncMock())
    monkeypatch.setattr(ConciliacionService, "_consumir_stock_reservado", consumir)
    monkeypatch.setattr(ConciliacionService, "_liberar_stock_reservado", AsyncMock())
    monkeypatch.setattr(ConciliacionService, "registrar_conflicto_pago", AsyncMock())
    monkeypatch.setattr(ReembolsoService, "reembolsar_por_orden_rechazada", reembolsar)
    respuesta = await ConciliacionService.confirmar_pago(db, pago, datos_stripe=datos)
    assert respuesta["reembolsado"] is True
    assert orden.estado == EstadoOrden.PENDIENTE_REGULARIZACION
    consumir.assert_not_awaited()
    reembolsar.assert_awaited_once()
    assert db.execute.call_args_list[0].args[0]._for_update_arg is not None


@pytest.mark.asyncio
async def test_confirmacion_repetida_consume_stock_una_sola_vez(monkeypatch):
    pago = SimpleNamespace(
        id=77, uuid_pago="pago-unit", tenant_id=10, orden_id=15, monto=Decimal("148.75"), moneda="BOB",
        estado=EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE, reembolso_id=None,
    )
    orden = SimpleNamespace(id=15, tenant_id=10, numero_orden="ORD-UNIT", estado=EstadoOrden.PENDIENTE_PAGO_DIFERIDO,
                            expira_en=datetime.utcnow() + timedelta(hours=1), fecha_limite_pago=None)
    db = sesion_falsa(orden, SimpleNamespace(estado=EstadoTenant.OPERATIVO))
    consumir = AsyncMock()
    monkeypatch.setattr(OrdenDiferidaService, "verificar_habilitacion", AsyncMock())
    monkeypatch.setattr(ConciliacionService, "_consumir_stock_reservado", consumir)
    monkeypatch.setattr(ConciliacionService, "_transaccion_confirmada", AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(ConciliacionService, "_emitir_comprobante", AsyncMock(return_value=SimpleNamespace(id=1, numero="FAC-UNIT")))
    monkeypatch.setattr(manager, "difundir_canal", AsyncMock())
    datos = {"amount_total": 14875, "currency": "bob"}
    await ConciliacionService.confirmar_pago(db, pago, datos_stripe=datos)
    repetido = await ConciliacionService.confirmar_pago(db, pago, datos_stripe=datos, evento=evento_falso())
    assert repetido["duplicado"] is True
    assert pago.estado == EstadoPagoDiferido.CONFIRMADO
    assert orden.estado == EstadoOrden.PAGADO
    consumir.assert_awaited_once()
    assert db.execute.await_count == 2
