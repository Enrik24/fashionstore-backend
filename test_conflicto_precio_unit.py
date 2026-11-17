from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import main
from app.apps.gestion_saas.models import EstadoTenant
from app.apps.operaciones_offline.conflicto_service import ConflictoService
from app.apps.operaciones_offline.models import EstadoSincronizacion, TipoConflicto, TipoOperacionOffline
from app.apps.operaciones_offline.schemas import LoteSyncRequest, OperacionLoteItem
from app.apps.operaciones_offline.sincronizacion_service import SincronizacionService
from app.exceptions import AceptacionRequerida, ConflictException, NotFoundException


def operacion_falsa(**cambios):
    return SimpleNamespace(**{
        "id": 30, "tenant_id": 10, "usuario_id": 7, "uuid_operacion": uuid4(),
        "dispositivo_id": uuid4(), "tipo_operacion": TipoOperacionOffline.ORDEN_PAGO_DIFERIDO,
        "estado_sincronizacion": EstadoSincronizacion.CONFIRMADA,
        "payload_json": {"acepta_nuevo_total": True, "total_aceptado": 148.75},
        "resultado_json": {"orden_id": 1267, "pago": {"id": 77}},
        "requiere_revision": False, "motivo_rechazo": None, "error_detalle": None,
        "intentos": 2, **cambios,
    })


def conflicto_falso(operacion, **cambios):
    return SimpleNamespace(**{
        "id": 3, "tenant_id": operacion.tenant_id, "operacion_offline_id": operacion.id,
        "operacion": operacion, "tipo_conflicto": TipoConflicto.PRECIO_DESACTUALIZADO.value,
        "resuelto": False, "resolucion": None, "resuelto_por": None,
        "fecha_resolucion": None, "observaciones": None,
        "estrategia_resolucion": "AUTOMATICA", **cambios,
    })


def sesion_falsa():
    return SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), refresh=AsyncMock(),
                           commit=AsyncMock(), rollback=AsyncMock(), get=AsyncMock())


@pytest.mark.asyncio
async def test_cierre_filtra_tenant_operacion_tipo_y_pendientes():
    operacion = operacion_falsa()
    conflicto = conflicto_falso(operacion)
    db = sesion_falsa()
    db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [conflicto]))
    await ConflictoService.cerrar_conflictos_precio_aceptado(db, operacion)
    sentencia = db.execute.call_args.args[0]
    filtros = sentencia.compile().params
    assert set(filtros.values()) == {10, 30, "PRECIO_DESACTUALIZADO"}
    assert "log_conflictos.resuelto IS false" in str(sentencia)
    assert sentencia._for_update_arg is not None
    assert conflicto.resuelto is True
    assert conflicto.resolucion == "AUTOMATICA"
    assert conflicto.fecha_resolucion is not None
    assert conflicto.observaciones == "Total central aceptado y operación confirmada"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cambios", [
    {"estado_sincronizacion": EstadoSincronizacion.RECHAZADA},
    {"estado_sincronizacion": EstadoSincronizacion.PENDIENTE},
    {"payload_json": {}},
    {"payload_json": {"acepta_nuevo_total": False}},
])
async def test_no_cierra_sin_confirmacion_y_aceptacion(cambios):
    db = sesion_falsa()
    await ConflictoService.cerrar_conflictos_precio_aceptado(db, operacion_falsa(**cambios))
    db.execute.assert_not_awaited()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["CONFIRMAR", "AJUSTAR"])
async def test_residual_confirmado_solo_cierra_log_sin_reintentar(monkeypatch, decision):
    operacion = operacion_falsa()
    conflicto = conflicto_falso(operacion)
    anterior = deepcopy(vars(operacion))
    db = sesion_falsa()
    monkeypatch.setattr(ConflictoService, "obtener_conflicto", AsyncMock(return_value=conflicto))
    reintentar = AsyncMock()
    monkeypatch.setattr(SincronizacionService, "reintentar_operacion", reintentar)
    respuesta = await ConflictoService.resolver_manualmente(db, 3, 10, decision, "Precio ya aceptado", 7)
    assert vars(operacion) == anterior
    assert respuesta.resuelto is True
    assert respuesta.resolucion == decision
    assert respuesta.resuelto_por == 7
    assert respuesta.observaciones == "Precio ya aceptado"
    reintentar.assert_not_awaited()
    db.commit.assert_awaited_once()
    assert all(llamada.kwargs.get("with_for_update") for llamada in db.refresh.call_args_list)
    await ConflictoService.resolver_manualmente(db, 3, 10, decision, "No reemplazar auditoría", 7)
    assert respuesta.observaciones == "Precio ya aceptado"
    assert db.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,cambios", [
    ("RECHAZAR", {}),
    ("CONFIRMAR", {"tipo_conflicto": TipoConflicto.STOCK_INSUFICIENTE.value}),
    ("AJUSTAR", {"payload_json": {"acepta_nuevo_total": False}}),
])
async def test_confirmada_no_se_modifica_por_decision_incompatible(monkeypatch, decision, cambios):
    operacion = operacion_falsa(payload_json=cambios.get("payload_json", {"acepta_nuevo_total": True}))
    conflicto = conflicto_falso(operacion, tipo_conflicto=cambios.get("tipo_conflicto", "PRECIO_DESACTUALIZADO"))
    anterior = deepcopy(vars(operacion))
    db = sesion_falsa()
    monkeypatch.setattr(ConflictoService, "obtener_conflicto", AsyncMock(return_value=conflicto))
    with pytest.raises(ConflictException):
        await ConflictoService.resolver_manualmente(db, 3, 10, decision, None, 7)
    assert vars(operacion) == anterior
    assert conflicto.resuelto is False
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolucion_rechaza_operacion_de_otro_tenant(monkeypatch):
    conflicto = conflicto_falso(operacion_falsa(tenant_id=20))
    db = sesion_falsa()
    monkeypatch.setattr(ConflictoService, "obtener_conflicto", AsyncMock(return_value=conflicto))
    with pytest.raises(NotFoundException):
        await ConflictoService.resolver_manualmente(db, 3, 10, "CONFIRMAR", None, 7)
    db.refresh.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reintento_manual_fallido_conserva_conflicto_original(monkeypatch):
    operacion = operacion_falsa(estado_sincronizacion=EstadoSincronizacion.RECHAZADA)
    conflicto = conflicto_falso(operacion)
    db = sesion_falsa()
    monkeypatch.setattr(ConflictoService, "obtener_conflicto", AsyncMock(return_value=conflicto))
    reintentar = AsyncMock(return_value={"estado": "RECHAZADA"})
    monkeypatch.setattr(SincronizacionService, "reintentar_operacion", reintentar)
    with pytest.raises(ConflictException):
        await ConflictoService.resolver_manualmente(db, 3, 10, "CONFIRMAR", None, 7)
    assert conflicto.resuelto is False
    assert conflicto.fecha_resolucion is None
    assert operacion.estado_sincronizacion == EstadoSincronizacion.RECHAZADA
    assert reintentar.call_args.kwargs["acepta_nuevo_total"] is True
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reintento_manual_exitoso_registra_resolucion(monkeypatch):
    operacion = operacion_falsa(estado_sincronizacion=EstadoSincronizacion.RECHAZADA)
    conflicto = conflicto_falso(operacion)
    db = sesion_falsa()
    monkeypatch.setattr(ConflictoService, "obtener_conflicto", AsyncMock(return_value=conflicto))
    monkeypatch.setattr(SincronizacionService, "reintentar_operacion", AsyncMock(return_value={"estado": "CONFIRMADA"}))
    await ConflictoService.resolver_manualmente(db, 3, 10, "AJUSTAR", "Aceptado", 7)
    assert conflicto.resuelto is True
    assert conflicto.resolucion == "AJUSTAR"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fallo", [False, True])
async def test_motor_cierra_solo_despues_del_handler_dentro_de_transaccion(monkeypatch, fallo):
    operacion = operacion_falsa(estado_sincronizacion=EstadoSincronizacion.RECHAZADA)
    db = sesion_falsa()
    dentro = False
    pasos = []

    @asynccontextmanager
    async def transaccion():
        nonlocal dentro
        dentro = True
        try:
            yield
        finally:
            dentro = False

    async def manejar(*args):
        pasos.append("handler")
        if fallo:
            raise AceptacionRequerida(175, 148.75)
        return {"orden_id": 1267}

    async def cerrar(db, actual):
        assert dentro is True
        assert actual.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA
        pasos.append("cierre")

    db.begin_nested = transaccion
    db.get.side_effect = [SimpleNamespace(estado=EstadoTenant.OPERATIVO), operacion]
    monkeypatch.setattr("app.apps.operaciones_offline.sincronizacion_service.obtener_manejador", lambda tipo: manejar)
    monkeypatch.setattr(ConflictoService, "cerrar_conflictos_precio_aceptado", AsyncMock(side_effect=cerrar))
    monkeypatch.setattr(SincronizacionService, "_registrar_conflicto_post_rollback", AsyncMock())
    item = OperacionLoteItem(uuid_operacion=str(operacion.uuid_operacion), tipo_operacion="ORDEN_PAGO_DIFERIDO",
                            fecha_registro_local=datetime.utcnow(), payload=operacion.payload_json)
    respuesta = await SincronizacionService._ejecutar_operacion(
        db, operacion, item, LoteSyncRequest(dispositivo_id=str(operacion.dispositivo_id))
    )
    assert pasos == (["handler"] if fallo else ["handler", "cierre"])
    assert respuesta["estado"] == ("RECHAZADA" if fallo else "CONFIRMADA")
    db.commit.assert_awaited_once()
