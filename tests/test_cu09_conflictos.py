"""[EXAMEN-2] CU09 — Detección y resolución de conflictos (plan 9.2.10).

Cubre los conflictos de stock, de precio (umbral del 5 %), de datos maestros,
tenant suspendido, orden expirada, pago ya capturado y pago duplicado, además
de la resolución manual/automática y el log persistido.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select, update

from app.apps.gestion_catalogo.models import Inventario, Producto, VarianteProducto
from app.apps.gestion_saas.models import EstadoTenant, Tenant
from app.apps.gestion_ventas.models import EstadoOrden, Orden, TipoOrden
from app.apps.operaciones_offline.conflicto_service import ConflictoService
from app.apps.operaciones_offline.models import (
    EstadoSincronizacion,
    EstrategiaResolucion,
    LogConflicto,
    OperacionOffline,
    OrigenOperacion,
    TipoConflicto,
)
from tests.conftest import operacion_payload


def _reserva_payload(catalogo: dict, cliente: dict, cantidad: int = 1) -> dict:
    return {
        "cliente_id": cliente["id"],
        "sucursal_id": catalogo["sucursal_id"],
        "detalles": [
            {"variante_producto_id": catalogo["variante_id"], "cantidad": cantidad}
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


async def _operacion(db_session, uuid_operacion: str) -> OperacionOffline:
    return (await db_session.execute(
        select(OperacionOffline).where(OperacionOffline.uuid_operacion == uuid_operacion)
    )).scalar_one()


async def _escalar_a_manual(db_session, tenant: dict, operacion: OperacionOffline) -> LogConflicto:
    operacion.estado_sincronizacion = EstadoSincronizacion.CONFLICTO
    operacion.requiere_revision = True
    operacion.motivo_rechazo = TipoConflicto.STOCK_INSUFICIENTE.value
    conflicto = LogConflicto(
        tenant_id=int(tenant["id"]),
        operacion_offline_id=operacion.id,
        tipo_conflicto=TipoConflicto.STOCK_INSUFICIENTE.value,
        detalle_json={"origen": "prueba"},
        resuelto=False,
        estrategia_resolucion=EstrategiaResolucion.MANUAL.value,
    )
    db_session.add(conflicto)
    await db_session.commit()
    await db_session.refresh(conflicto)
    return conflicto


async def test_conflicto_de_stock_se_persiste_y_queda_auto_resuelto(
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

    uuid_operacion = str(uuid4())
    lote = await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=uuid_operacion,
                tipo_operacion="RESERVA",
                payload=_reserva_payload(catalogo, cliente, cantidad=3),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    resultado = lote["resultados"][0]
    assert resultado["estado"] == "RECHAZADA"
    assert resultado["datos_resultado"]["faltantes"][0]["faltante"] == 2

    conflicto = (await db_session.execute(
        select(LogConflicto).where(LogConflicto.operacion_offline_id == (
            await _operacion(db_session, uuid_operacion)
        ).id)
    )).scalar_one()
    assert conflicto.tenant_id == tenant["id"]
    assert conflicto.tipo_conflicto == TipoConflicto.STOCK_INSUFICIENTE.value
    assert conflicto.resuelto is True
    assert conflicto.resolucion == "RECHAZO"
    assert conflicto.estrategia_resolucion == EstrategiaResolucion.RECHAZO.value
    assert conflicto.fecha_resolucion is not None
    assert conflicto.detalle_json["detalle"]["faltantes"][0]["disponible"] == 1


async def test_conflicto_de_producto_inactivo_se_persiste(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=5, activo=False)
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
                payload=_reserva_payload(catalogo, cliente),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    operacion = await _operacion(db_session, uuid_operacion)
    conflicto = (await db_session.execute(
        select(LogConflicto).where(LogConflicto.operacion_offline_id == operacion.id)
    )).scalar_one()
    assert conflicto.tipo_conflicto == TipoConflicto.PRODUCTO_INACTIVO.value
    assert conflicto.resuelto is True


async def test_listado_y_conteo_de_conflictos_pendientes(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=0)
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
                payload=_reserva_payload(catalogo, cliente),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    operacion = await _operacion(db_session, uuid_operacion)
    conflicto = await _escalar_a_manual(db_session, tenant, operacion)

    listado = await client.get(
        "/api/v1/offline/conflictos?resuelto=false",
        headers=dispositivo["headers"],
    )
    assert listado.status_code == 200, listado.text
    ids = {c["id"] for c in listado.json()}
    assert conflicto.id in ids

    conteo = await client.get(
        "/api/v1/offline/conflictos/pendientes/count",
        headers=dispositivo["headers"],
    )
    assert conteo.status_code == 200, conteo.text
    assert conteo.json()["pendientes"] >= 1

    detalle = await client.get(
        f"/api/v1/offline/conflictos/{conflicto.id}",
        headers=dispositivo["headers"],
    )
    assert detalle.status_code == 200, detalle.text
    cuerpo = detalle.json()
    assert cuerpo["uuid_operacion"] == uuid_operacion
    assert cuerpo["resuelto"] is False
    assert cuerpo["estrategia_resolucion"] == "MANUAL"


async def test_resolucion_manual_rechazar(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=0)
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
                payload=_reserva_payload(catalogo, cliente),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    conflicto = await _escalar_a_manual(
        db_session, tenant, await _operacion(db_session, uuid_operacion)
    )

    respuesta = await client.post(
        f"/api/v1/offline/conflictos/{conflicto.id}/resolver",
        json={"decision": "RECHAZAR", "observacion": "Cliente desistió"},
        headers=dispositivo["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["resuelto"] is True
    assert cuerpo["resolucion"] == "RECHAZAR"
    assert cuerpo["resuelto_por"] is not None
    assert cuerpo["observaciones"] == "Cliente desistió"

    operacion = await _operacion(db_session, uuid_operacion)
    await db_session.refresh(operacion)
    assert operacion.estado_sincronizacion == EstadoSincronizacion.RECHAZADA
    assert operacion.requiere_revision is False

    repetido = await client.post(
        f"/api/v1/offline/conflictos/{conflicto.id}/resolver",
        json={"decision": "CONFIRMAR"},
        headers=dispositivo["headers"],
    )
    assert repetido.status_code == 409, repetido.text


async def test_resolucion_manual_confirmar_reintenta_y_confirma(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=0)
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
                payload=_reserva_payload(catalogo, cliente),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    conflicto = await _escalar_a_manual(
        db_session, tenant, await _operacion(db_session, uuid_operacion)
    )

    # El administrador repone stock antes de confirmar.
    await db_session.execute(
        update(Inventario)
        .where(Inventario.variante_producto_id == catalogo["variante_id"])
        .values(cantidad=5)
    )
    await db_session.commit()

    respuesta = await client.post(
        f"/api/v1/offline/conflictos/{conflicto.id}/resolver",
        json={"decision": "CONFIRMAR", "observacion": "Stock repuesto"},
        headers=dispositivo["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["resolucion"] == "CONFIRMAR"

    operacion = await _operacion(db_session, uuid_operacion)
    await db_session.refresh(operacion)
    assert operacion.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA
    assert operacion.requiere_revision is False


async def test_resolucion_automatica_marca_el_log(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=0)
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
                payload=_reserva_payload(catalogo, cliente),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    operacion = await _operacion(db_session, uuid_operacion)

    conflicto = await ConflictoService.registrar_conflicto(
        db_session,
        operacion,
        {
            "tipo_conflicto": TipoConflicto.PRECIO_DESACTUALIZADO.value,
            "detalle": {"precio_local": 101.0, "precio_central": 100.0},
        },
    )
    assert conflicto.estrategia_resolucion == EstrategiaResolucion.AUTOMATICA.value

    resuelto = await ConflictoService.resolver_automaticamente(
        db_session, conflicto, observacion="Se aplicó el precio central"
    )
    await db_session.commit()
    await db_session.refresh(resuelto)
    assert resuelto.resuelto is True
    assert resuelto.resolucion == "AUTOMATICA"
    assert resuelto.fecha_resolucion is not None
    assert resuelto.observaciones == "Se aplicó el precio central"


async def test_umbral_de_precio_del_cinco_por_ciento():
    central = Decimal("100.00")
    # 4 % -> resoluble
    assert ConflictoService.evaluar_umbral_precio(Decimal("4.00"), central) is True
    # Exactamente 5 % -> resoluble (el umbral es inclusivo)
    assert ConflictoService.evaluar_umbral_precio(Decimal("5.00"), central) is True
    # 5,01 % -> revisión manual
    assert ConflictoService.evaluar_umbral_precio(Decimal("5.01"), central) is False
    # Precio local por debajo del central en más del 5 %
    assert ConflictoService.evaluar_umbral_precio(Decimal("-6.00"), central) is False


async def test_detectar_precio_marca_revision_solo_sobre_el_umbral():
    precios = {1: Decimal("100.00"), 2: Decimal("200.00")}

    dentro = ConflictoService.detectar_precio(
        [
            {"variante_producto_id": 1, "cantidad": 2, "precio_unitario_local": "104.00"},
            {"variante_producto_id": 2, "cantidad": 1, "precio_unitario_local": "190.00"},
        ],
        precios,
    )
    assert dentro["hay_cambio"] is True
    assert dentro["requiere_revision"] is False
    assert dentro["total_local"] == Decimal("398.00")
    assert dentro["total_central"] == Decimal("400.00")
    assert all(d["resoluble_automaticamente"] for d in dentro["diferencias"])

    fuera = ConflictoService.detectar_precio(
        [{"variante_producto_id": 1, "cantidad": 1, "precio_unitario_local": "112.00"}],
        precios,
    )
    assert fuera["requiere_revision"] is True
    assert fuera["diferencias"][0]["diferencia_porcentaje"] == 0.12
    # RN-007: el precio central es el que se aplica.
    ajustadas = ConflictoService.aplicar_rn007(fuera["diferencias"])
    assert ajustadas[0]["precio_aplicado"] == 100.0


async def test_datos_maestros_detecta_variante_inactiva_y_datos_ajenos(
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Maestros A")
    catalogo_a = await catalogo_tenant(tenant_a, stock=5, activo=False)
    cliente_a = await cliente_tenant(tenant_a)

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Maestros B")
    catalogo_b = await catalogo_tenant(tenant_b, stock=5)
    cliente_b = await cliente_tenant(tenant_b)

    conflictos = await ConflictoService.detectar_datos_maestros(
        db_session,
        int(tenant_a["id"]),
        [catalogo_a["variante_id"], catalogo_b["variante_id"]],
        cliente_id=cliente_b["id"],
        sucursal_id=catalogo_b["sucursal_id"],
    )
    tipos = {c["tipo_conflicto"] for c in conflictos}
    assert TipoConflicto.PRODUCTO_INACTIVO.value in tipos
    assert TipoConflicto.VARIANTE_INEXISTENTE.value in tipos
    assert TipoConflicto.SUCURSAL_INVALIDA.value in tipos

    # Una variante inexistente también se reporta.
    inexistentes = await ConflictoService.detectar_datos_maestros(
        db_session, int(tenant_a["id"]), [999999]
    )
    assert inexistentes[0]["tipo_conflicto"] == TipoConflicto.VARIANTE_INEXISTENTE.value

    # La variante del cliente correcto no genera conflicto de producto.
    solo_cliente = await ConflictoService.detectar_datos_maestros(
        db_session, int(tenant_a["id"]), [], cliente_id=cliente_a["id"]
    )
    assert solo_cliente == []


async def test_tenant_suspendido_y_orden_expirada(
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant)
    cliente = await cliente_tenant(tenant)

    entidad = await db_session.get(Tenant, int(tenant["id"]))
    assert ConflictoService.detectar_tenant_suspendido(entidad) == []

    entidad.estado = EstadoTenant.SUSPENDIDO
    suspendido = ConflictoService.detectar_tenant_suspendido(entidad)
    assert suspendido[0]["tipo_conflicto"] == TipoConflicto.TENANT_SUSPENDIDO.value
    assert suspendido[0]["estado_tenant"] == "SUSPENDIDO"
    assert suspendido[0]["orden_sugerida"] == EstadoOrden.PENDIENTE_REGULARIZACION.value

    orden = Orden(
        numero_orden=f"ORD-{uuid4().hex[:10]}",
        cliente_id=cliente["id"],
        sucursal_id=catalogo["sucursal_id"],
        tipo=TipoOrden.DIGITAL,
        estado=EstadoOrden.PENDIENTE_PAGO_DIFERIDO,
        total=Decimal("100.00"),
        tenant_id=int(tenant["id"]),
        uuid_orden=uuid4(),
        origen=OrigenOperacion.WEB,
        es_offline=False,
        estado_sincronizacion=EstadoSincronizacion.NO_APLICA,
        expira_en=datetime.now(timezone.utc) - timedelta(hours=1),
        moneda="BOB",
    )
    db_session.add(orden)
    await db_session.commit()
    await db_session.refresh(orden)

    expirada = ConflictoService.detectar_orden_expirada(orden)
    assert expirada[0]["tipo_conflicto"] == TipoConflicto.ORDEN_EXPIRADA.value
    assert expirada[0]["orden_id"] == orden.id

    orden.expira_en = datetime.now(timezone.utc) + timedelta(hours=1)
    assert ConflictoService.detectar_orden_expirada(orden) == []


async def test_pago_ya_capturado_y_pago_duplicado():
    capturado = ConflictoService.detectar_pago_ya_capturado("CONFIRMADO", Decimal("150.00"))
    assert capturado[0]["tipo_conflicto"] == TipoConflicto.PAGO_YA_CAPTURADO.value
    assert capturado[0]["monto"] == 150.0

    assert ConflictoService.detectar_pago_ya_capturado("PENDIENTE") == []
    assert ConflictoService.detectar_pago_ya_capturado(None) == []

    pago = SimpleNamespace(id=7, uuid_pago=uuid4(), estado="REQUIERE_ACCION_CLIENTE")
    duplicado = ConflictoService.detectar_pago_duplicado(pago)
    assert duplicado[0]["tipo_conflicto"] == TipoConflicto.PAGO_DUPLICADO.value
    assert duplicado[0]["pago_id"] == 7
    assert duplicado[0]["estado"] == "REQUIERE_ACCION_CLIENTE"
    assert ConflictoService.detectar_pago_duplicado(None) == []

    clasificado = ConflictoService.clasificar_conflicto(duplicado[0])
    assert clasificado["estrategia_resolucion"] == EstrategiaResolucion.AUTOMATICA.value

    severo = ConflictoService.clasificar_conflicto(capturado[0])
    assert severo["severidad"] == "ALTA"
    assert severo["estrategia_resolucion"] == EstrategiaResolucion.MANUAL.value
