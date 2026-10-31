"""[EXAMEN-2] CU08 — Motor de sincronización por lotes (plan 9.2.10).

Verifica lotes mixtos, orden FIFO, duplicados, reintentos manuales, errores
críticos, resumen, lote vacío y lote sobredimensionado.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select, update

from app.apps.gestion_catalogo.models import Inventario, VarianteProducto
from app.apps.gestion_ventas.models import Carrito, Orden, Reserva
from app.apps.operaciones_offline import registro_manejadores as registro
from app.apps.operaciones_offline.handlers import manejar_carrito
from app.apps.operaciones_offline.models import (
    EstadoSincronizacion,
    OperacionOffline,
)
from app.apps.operaciones_offline.sincronizacion_service import SincronizacionService
from tests.conftest import operacion_payload


def _reserva_payload(catalogo: dict, cliente: dict, cantidad: int = 1) -> dict:
    return {
        "cliente_id": cliente["id"],
        "sucursal_id": catalogo["sucursal_id"],
        "detalles": [
            {"variante_producto_id": catalogo["variante_id"], "cantidad": cantidad}
        ],
    }


def _venta_payload(catalogo: dict, cantidad: int = 1) -> dict:
    return {
        "sucursal_id": catalogo["sucursal_id"],
        "metodo_pago": "EFECTIVO",
        "detalles": [
            {"variante_producto_id": catalogo["variante_id"], "cantidad": cantidad}
        ],
    }


def _carrito_payload(catalogo: dict, cantidad: int = 1) -> dict:
    return {
        "uuid_carrito": str(uuid4()),
        "items": [
            {"variante_producto_id": catalogo["variante_id"], "cantidad": cantidad}
        ],
    }


async def _sync(client: AsyncClient, dispositivo: dict, operaciones: list[dict]):
    return await client.post(
        "/api/v1/offline/sync",
        json={"operaciones": operaciones},
        headers=dispositivo["headers"],
    )


async def test_lote_mixto_se_procesa_en_orden_fifo(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=20)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    uuid_reserva = str(uuid4())
    uuid_venta = str(uuid4())
    uuid_carrito = str(uuid4())

    # Se envían desordenadas: 3, 1, 2.
    lote = [
        operacion_payload(
            uuid_operacion=uuid_carrito,
            tipo_operacion="CARRITO",
            payload=_carrito_payload(catalogo),
            secuencia_local=3,
            sucursal_id=catalogo["sucursal_id"],
        ),
        operacion_payload(
            uuid_operacion=uuid_reserva,
            tipo_operacion="RESERVA",
            payload=_reserva_payload(catalogo, cliente),
            secuencia_local=1,
            sucursal_id=catalogo["sucursal_id"],
        ),
        operacion_payload(
            uuid_operacion=uuid_venta,
            tipo_operacion="VENTA_CONTADO",
            payload=_venta_payload(catalogo),
            secuencia_local=2,
            sucursal_id=catalogo["sucursal_id"],
        ),
    ]

    respuesta = await _sync(client, dispositivo, lote)
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()

    assert [r["uuid_operacion"] for r in cuerpo["resultados"]] == [
        uuid_reserva,
        uuid_venta,
        uuid_carrito,
    ]
    assert all(r["estado"] == "CONFIRMADA" for r in cuerpo["resultados"]), [
        (r["uuid_operacion"], r["estado"], r["motivo"], r["datos_resultado"])
        for r in cuerpo["resultados"]
    ]
    assert cuerpo["recibidas"] == 3
    assert cuerpo["procesadas"] == 3
    assert cuerpo["resumen"] == {"CONFIRMADA": 3}
    assert cuerpo["lote_id"]

    assert len((await db_session.execute(
        select(Reserva).where(Reserva.tenant_id == tenant["id"])
    )).scalars().all()) == 1
    assert len((await db_session.execute(
        select(Orden).where(Orden.tenant_id == tenant["id"])
    )).scalars().all()) == 1
    assert len((await db_session.execute(
        select(Carrito).where(Carrito.tenant_id == tenant["id"])
    )).scalars().all()) == 1


async def test_error_de_negocio_no_bloquea_al_resto_del_lote(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=1)
    dispositivo = await dispositivo_offline(tenant)

    uuid_rechazada = str(uuid4())
    uuid_valida = str(uuid4())

    respuesta = await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=uuid_rechazada,
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, cantidad=5),
                secuencia_local=1,
            ),
            operacion_payload(
                uuid_operacion=uuid_valida,
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, cantidad=1),
                secuencia_local=2,
            ),
        ],
    )
    cuerpo = respuesta.json()
    por_uuid = {r["uuid_operacion"]: r for r in cuerpo["resultados"]}
    assert por_uuid[uuid_rechazada]["estado"] == "RECHAZADA"
    assert por_uuid[uuid_valida]["estado"] == "CONFIRMADA"
    assert cuerpo["resumen"] == {"RECHAZADA": 1, "CONFIRMADA": 1}

    inventario = (await db_session.execute(
        select(Inventario).where(Inventario.variante_producto_id == catalogo["variante_id"])
    )).scalar_one()
    assert inventario.cantidad == 0


async def test_reenvio_del_mismo_lote_devuelve_duplicadas(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    operaciones = [
        operacion_payload(
            uuid_operacion=str(uuid4()),
            tipo_operacion="RESERVA",
            payload=_reserva_payload(catalogo, cliente),
            secuencia_local=1,
        ),
        operacion_payload(
            uuid_operacion=str(uuid4()),
            tipo_operacion="VENTA_CONTADO",
            payload=_venta_payload(catalogo),
            secuencia_local=2,
        ),
    ]

    primera = (await _sync(client, dispositivo, operaciones)).json()
    segunda = (await _sync(client, dispositivo, operaciones)).json()

    assert primera["resumen"] == {"CONFIRMADA": 2}
    assert segunda["resumen"] == {"DUPLICADA": 2}
    assert all(r["codigo_http_sugerido"] == 200 for r in segunda["resultados"])

    assert len((await db_session.execute(
        select(Reserva).where(Reserva.tenant_id == tenant["id"])
    )).scalars().all()) == 1
    assert len((await db_session.execute(
        select(Orden).where(Orden.tenant_id == tenant["id"])
    )).scalars().all()) == 1

    inventario = (await db_session.execute(
        select(Inventario).where(Inventario.variante_producto_id == catalogo["variante_id"])
    )).scalar_one()
    # Solo se descontó/reservó en el primer envío.
    assert inventario.cantidad == 9
    assert inventario.cantidad_reservada == 1


async def test_reintento_manual_tras_reponer_stock(
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
    respuesta = await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=uuid_operacion,
                tipo_operacion="RESERVA",
                payload=_reserva_payload(catalogo, cliente),
                secuencia_local=1,
            )
        ],
    )
    assert respuesta.json()["resultados"][0]["estado"] == "RECHAZADA"

    # El tendero repone stock y reintenta la operación desde la bandeja.
    await db_session.execute(
        update(Inventario)
        .where(Inventario.variante_producto_id == catalogo["variante_id"])
        .values(cantidad=5)
    )
    await db_session.commit()

    reintento = await client.post(
        f"/api/v1/offline/operaciones/{uuid_operacion}/reintentar",
        headers=dispositivo["headers"],
    )
    assert reintento.status_code == 200, reintento.text
    assert reintento.json()["estado"] == "CONFIRMADA"

    operacion = (await db_session.execute(
        select(OperacionOffline).where(OperacionOffline.uuid_operacion == uuid_operacion)
    )).scalar_one()
    await db_session.refresh(operacion)
    assert operacion.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA
    assert operacion.intentos == 2

    # Una operación confirmada ya no se reintenta.
    otra_vez = await client.post(
        f"/api/v1/offline/operaciones/{uuid_operacion}/reintentar",
        headers=dispositivo["headers"],
    )
    assert otra_vez.status_code == 409, otra_vez.text


async def test_error_critico_cuando_falta_el_manejador(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=5)
    dispositivo = await dispositivo_offline(tenant)

    uuid_operacion = str(uuid4())
    registro._REGISTRO.pop("CARRITO", None)
    try:
        respuesta = await _sync(
            client,
            dispositivo,
            [
                operacion_payload(
                    uuid_operacion=uuid_operacion,
                    tipo_operacion="CARRITO",
                    payload=_carrito_payload(catalogo),
                    secuencia_local=1,
                )
            ],
        )
    finally:
        registro.registrar_manejador("CARRITO", manejar_carrito)

    assert respuesta.status_code == 200, respuesta.text
    resultado = respuesta.json()["resultados"][0]
    assert resultado["estado"] == "ERROR_CRITICO"
    assert resultado["motivo"] == "MANEJADOR_NO_REGISTRADO"
    assert resultado["reintentar"] is False
    assert resultado["codigo_http_sugerido"] == 409

    operacion = (await db_session.execute(
        select(OperacionOffline).where(OperacionOffline.uuid_operacion == uuid_operacion)
    )).scalar_one()
    assert operacion.estado_sincronizacion == EstadoSincronizacion.ERROR_CRITICO
    assert "CARRITO" in operacion.error_detalle


async def test_lote_vacio_es_valido(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    await catalogo_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    respuesta = await _sync(client, dispositivo, [])
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["recibidas"] == 0
    assert cuerpo["procesadas"] == 0
    assert cuerpo["resultados"] == []
    assert cuerpo["resumen"] == {}


async def test_lote_sobredimensionado_es_rechazado(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=100)
    dispositivo = await dispositivo_offline(tenant)

    operaciones = [
        operacion_payload(
            uuid_operacion=str(uuid4()),
            tipo_operacion="VENTA_CONTADO",
            payload=_venta_payload(catalogo),
            secuencia_local=indice,
        )
        for indice in range(51)
    ]

    respuesta = await _sync(client, dispositivo, operaciones)
    assert respuesta.status_code == 400, respuesta.text
    assert "máximo" in respuesta.json()["detail"]


async def test_tipo_de_operacion_no_permitido(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    respuesta = await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid4()),
                tipo_operacion="BORRAR_TODO",
                payload=_venta_payload(catalogo),
            )
        ],
    )
    assert respuesta.status_code == 400, respuesta.text
    assert "tipo_operacion" in respuesta.json()["detail"]


async def test_resumen_y_consulta_de_operaciones(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=2)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    uuid_ok = str(uuid4())
    uuid_rechazada = str(uuid4())
    await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=uuid_ok,
                tipo_operacion="RESERVA",
                payload=_reserva_payload(catalogo, cliente),
                secuencia_local=1,
            ),
            operacion_payload(
                uuid_operacion=uuid_rechazada,
                tipo_operacion="RESERVA",
                payload=_reserva_payload(catalogo, cliente, cantidad=10),
                secuencia_local=2,
            ),
        ],
    )

    resumen = await client.get(
        "/api/v1/offline/resumen",
        headers=dispositivo["headers"],
    )
    assert resumen.status_code == 200, resumen.text
    cuerpo = resumen.json()
    assert cuerpo["tenant_id"] == tenant["id"]
    assert cuerpo["confirmadas"] >= 1
    assert cuerpo["rechazadas"] >= 1
    assert cuerpo["total"] >= 2

    listado = await client.get(
        "/api/v1/offline/operaciones?estado=RECHAZADA",
        headers=dispositivo["headers"],
    )
    assert listado.status_code == 200, listado.text
    uuids = {o["uuid_operacion"] for o in listado.json()}
    assert uuid_rechazada in uuids
    assert uuid_ok not in uuids

    detalle = await client.get(
        f"/api/v1/offline/operaciones/{uuid_ok}",
        headers=dispositivo["headers"],
    )
    assert detalle.status_code == 200, detalle.text
    assert detalle.json()["estado_sincronizacion"] == "CONFIRMADA"

    # Otra tenant no ve la operación.
    otro = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Otro tenant")
    ajeno = await client.get(
        f"/api/v1/offline/operaciones/{uuid_ok}",
        headers=otro["headers"],
    )
    assert ajeno.status_code == 404, ajeno.text


async def test_backoff_exponencial_con_tope():
    assert SincronizacionService.calcular_backoff(0) == 5
    assert SincronizacionService.calcular_backoff(1) == 10
    assert SincronizacionService.calcular_backoff(2) == 20
    # Con base 5 y tope de 300 s.
    assert SincronizacionService.calcular_backoff(20) == 300


async def test_operacion_antigua_no_se_procesa(
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

    respuesta = await _sync(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid4()),
                tipo_operacion="RESERVA",
                payload=_reserva_payload(catalogo, cliente),
                fecha_registro_local=datetime.utcnow() - timedelta(days=45),
            )
        ],
    )
    assert respuesta.status_code == 400, respuesta.text
    assert "antigüedad" in respuesta.json()["detail"].lower()
