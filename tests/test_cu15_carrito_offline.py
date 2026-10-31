"""[EXAMEN-2] CU15 — Carrito offline (plan 9.2.10).

Verifica alta y recuperación, persistencia entre sesiones, recálculo central de
precios, stock referencial, rechazo de cupones sin conexión, carrito
deshabilitado por el tenant, actualización de ítems y conversión a orden con
pago diferido.
"""
from decimal import Decimal
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_catalogo.models import Inventario
from app.apps.gestion_ventas.models import (
    Carrito,
    EstadoCarrito,
    EstadoOrden,
    ItemCarrito,
    Orden,
)
from app.apps.operaciones_offline.models import ConfiguracionOffline
from tests.conftest import operacion_payload


def _payload(catalogo: dict, uuid_carrito: str | None = None, cantidad: int = 1, **extra) -> dict:
    cuerpo = {
        "uuid_carrito": uuid_carrito or str(uuid4()),
        "sucursal_id": catalogo["sucursal_id"],
        "items": [
            {
                "variante_producto_id": catalogo["variante_id"],
                "cantidad": cantidad,
            }
        ],
    }
    cuerpo.update(extra)
    return cuerpo


async def _crear_carrito(client: AsyncClient, dispositivo: dict, cuerpo: dict):
    return await client.post(
        "/api/v1/carrito/offline",
        json=cuerpo,
        headers=dispositivo["headers"],
    )


async def test_alta_y_recuperacion_del_carrito(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="120.00", stock=8)
    dispositivo = await dispositivo_offline(tenant)

    uuid_carrito = str(uuid4())
    alta = await _crear_carrito(
        client, dispositivo, _payload(catalogo, uuid_carrito, cantidad=2)
    )
    assert alta.status_code == 201, alta.text
    carrito = alta.json()
    assert carrito["uuid_carrito"] == uuid_carrito
    assert carrito["estado"] == "ACTIVO"
    assert carrito["cliente_id"] is None
    assert carrito["dispositivo_id"] == dispositivo["id"]
    assert carrito["moneda"] == "BOB"
    assert carrito["requiere_aceptacion"] is False
    assert Decimal(str(carrito["subtotal"])) == Decimal("240.00")
    assert Decimal(str(carrito["total"])) == Decimal("240.00")
    assert len(carrito["items"]) == 1
    item = carrito["items"][0]
    assert item["variante_producto_id"] == catalogo["variante_id"]
    assert item["cantidad"] == 2
    assert Decimal(str(item["precio_unitario"])) == Decimal("120.00")
    assert item["stock_central"] == 8

    recuperado = await client.get(
        f"/api/v1/carrito/offline/{uuid_carrito}",
        headers=dispositivo["headers"],
    )
    assert recuperado.status_code == 200, recuperado.text
    assert recuperado.json()["carrito_id"] == carrito["carrito_id"]

    pendientes = await client.get(
        "/api/v1/carrito/offline/pendientes",
        headers=dispositivo["headers"],
    )
    assert pendientes.status_code == 200, pendientes.text
    assert uuid_carrito in {c["uuid_carrito"] for c in pendientes.json()}


async def test_persistencia_entre_sesiones_y_dispositivos(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=5)
    dispositivo_a = await dispositivo_offline(tenant, nombre="Caja 1")
    dispositivo_b = await dispositivo_offline(tenant, nombre="Caja 2")

    uuid_carrito = str(uuid4())
    creado = await _crear_carrito(client, dispositivo_a, _payload(catalogo, uuid_carrito))
    assert creado.status_code == 201, creado.text

    # Otro dispositivo del mismo tenant recupera el carrito por su UUID.
    recuperado = await client.get(
        f"/api/v1/carrito/offline/{uuid_carrito}",
        headers=dispositivo_b["headers"],
    )
    assert recuperado.status_code == 200, recuperado.text
    assert recuperado.json()["items"][0]["cantidad"] == 1

    # Y con el token online del administrador también se consulta.
    online = await client.get(
        f"/api/v1/carrito/offline/{uuid_carrito}",
        headers=tenant["headers"],
    )
    assert online.status_code == 200, online.text


async def test_recalculo_central_de_precios(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=5)
    dispositivo = await dispositivo_offline(tenant)

    cuerpo = _payload(catalogo, cantidad=2)
    cuerpo["items"][0]["precio_unitario_local"] = "90.00"
    carrito = (await _crear_carrito(client, dispositivo, cuerpo)).json()

    assert carrito["requiere_aceptacion"] is True
    assert Decimal(str(carrito["subtotal"])) == Decimal("200.00")
    assert Decimal(str(carrito["total"])) == Decimal("200.00")
    assert Decimal(str(carrito["items"][0]["precio_unitario"])) == Decimal("100.00")
    assert Decimal(str(carrito["items"][0]["precio_unitario_local"])) == Decimal("90.00")


async def test_stock_referencial_local_y_central(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=10)
    dispositivo = await dispositivo_offline(tenant)

    cuerpo = _payload(catalogo)
    cuerpo["items"][0]["stock_local_conocido"] = 2
    carrito = (await _crear_carrito(client, dispositivo, cuerpo)).json()

    item = carrito["items"][0]
    assert item["stock_local_conocido"] == 2
    assert item["stock_central"] == 10


async def test_cupon_rechazado_offline(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    respuesta = await _crear_carrito(
        client,
        dispositivo,
        _payload(catalogo, cupon_codigo="DESCUENTO10"),
    )
    assert respuesta.status_code == 400, respuesta.text
    cuerpo = respuesta.json()
    assert "cupón" in cuerpo["detail"].lower() or "conexión" in cuerpo["detail"].lower()


async def test_carrito_deshabilitado_por_el_tenant(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    config = (await db_session.execute(
        select(ConfiguracionOffline).where(ConfiguracionOffline.tenant_id == tenant["id"])
    )).scalar_one()
    config.habilitar_carrito_offline = False
    await db_session.commit()

    respuesta = await _crear_carrito(client, dispositivo, _payload(catalogo))
    assert respuesta.status_code == 409, respuesta.text
    assert respuesta.json()["codigo"] == "CARRITO_NO_HABILITADO"

    # El listado y la consulta siguen disponibles; solo se bloquea el alta.
    pendientes = await client.get(
        "/api/v1/carrito/offline/pendientes",
        headers=dispositivo["headers"],
    )
    assert pendientes.status_code == 200, pendientes.text


async def test_actualizar_y_quitar_items(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="50.00", stock=10)
    dispositivo = await dispositivo_offline(tenant)

    uuid_carrito = str(uuid4())
    creado = (await _crear_carrito(client, dispositivo, _payload(catalogo, uuid_carrito))).json()

    actualizado = await client.put(
        f"/api/v1/carrito/offline/{uuid_carrito}/items",
        json={"items": [{"variante_producto_id": catalogo["variante_id"], "cantidad": 4}]},
        headers=dispositivo["headers"],
    )
    assert actualizado.status_code == 200, actualizado.text
    cuerpo = actualizado.json()
    assert cuerpo["items"][0]["cantidad"] == 4
    assert Decimal(str(cuerpo["total"])) == Decimal("200.00")

    item_id = cuerpo["items"][0]["id"]
    quitado = await client.delete(
        f"/api/v1/carrito/offline/{uuid_carrito}/items/{item_id}",
        headers=dispositivo["headers"],
    )
    assert quitado.status_code == 200, quitado.text
    assert quitado.json()["items"] == []
    assert Decimal(str(quitado.json()["total"])) == Decimal("0.00")

    restantes = (await db_session.execute(
        select(ItemCarrito).where(ItemCarrito.carrito_id == creado["carrito_id"])
    )).scalars().all()
    assert restantes == []


async def test_validar_carrito_detecta_diferencias_y_stock(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=3)
    dispositivo = await dispositivo_offline(tenant)

    uuid_carrito = str(uuid4())
    cuerpo = _payload(catalogo, uuid_carrito, cantidad=2)
    cuerpo["items"][0]["precio_unitario_local"] = "95.00"
    await _crear_carrito(client, dispositivo, cuerpo)

    validacion = await client.post(
        f"/api/v1/carrito/offline/{uuid_carrito}/validar?cupon_codigo=VERANO",
        headers=dispositivo["headers"],
    )
    assert validacion.status_code == 200, validacion.text
    datos = validacion.json()
    assert datos["requiere_aceptacion"] is True
    assert datos["stock_suficiente"] is True
    assert Decimal(str(datos["subtotal_local"])) == Decimal("190.00")
    assert Decimal(str(datos["subtotal_servidor"])) == Decimal("200.00")
    assert Decimal(str(datos["diferencia"])) == Decimal("-10.00")
    assert datos["diferencias"][0]["precio_central"] == 100.0
    assert datos["cupon"]["aplicable"] is False
    assert "conexión" in datos["cupon"]["motivo"].lower()

    # Con más cantidad que el stock central, la validación lo reporta.
    uuid_sin_stock = str(uuid4())
    await _crear_carrito(
        client, dispositivo, _payload(catalogo, uuid_sin_stock, cantidad=10)
    )
    sin_stock = await client.post(
        f"/api/v1/carrito/offline/{uuid_sin_stock}/validar",
        headers=dispositivo["headers"],
    )
    assert sin_stock.status_code == 200, sin_stock.text
    assert sin_stock.json()["stock_suficiente"] is False
    assert sin_stock.json()["faltantes"][0]["faltante"] == 7


async def test_conversion_del_carrito_a_orden_con_pago_diferido(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="80.00", stock=10)
    dispositivo = await dispositivo_offline(tenant)

    uuid_carrito = str(uuid4())
    await _crear_carrito(client, dispositivo, _payload(catalogo, uuid_carrito, cantidad=2))

    conversion = await client.post(
        f"/api/v1/carrito/offline/{uuid_carrito}/convertir",
        json={"direccion_envio": "Calle Falsa 123"},
        headers=dispositivo["headers"],
    )
    assert conversion.status_code == 201, conversion.text
    orden_datos = conversion.json()
    assert orden_datos["estado"] == EstadoOrden.PENDIENTE_PAGO_DIFERIDO.value
    assert orden_datos["stock_reservado"] is True
    assert Decimal(str(orden_datos["total"])) == Decimal("160.00")
    assert orden_datos["expira_en"] is not None

    orden = await db_session.get(Orden, orden_datos["orden_id"])
    assert orden.tenant_id == tenant["id"]
    assert orden.direccion_envio == "Calle Falsa 123"
    assert orden.uuid_orden is not None

    carrito = (await db_session.execute(
        select(Carrito).where(Carrito.uuid_carrito == uuid_carrito)
    )).scalar_one()
    assert carrito.estado == EstadoCarrito.CONVERTIDO

    inventario = (await db_session.execute(
        select(Inventario).where(Inventario.variante_producto_id == catalogo["variante_id"])
    )).scalar_one()
    assert inventario.cantidad_reservada == 2

    repetida = await client.post(
        f"/api/v1/carrito/offline/{uuid_carrito}/convertir",
        json={},
        headers=dispositivo["headers"],
    )
    assert repetida.status_code == 409, repetida.text


async def test_conversion_exige_aceptar_el_nuevo_total(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    dispositivo = await dispositivo_offline(tenant)

    uuid_carrito = str(uuid4())
    cuerpo = _payload(catalogo, uuid_carrito, cantidad=1)
    cuerpo["items"][0]["precio_unitario_local"] = "80.00"
    await _crear_carrito(client, dispositivo, cuerpo)

    rechazo = await client.post(
        f"/api/v1/carrito/offline/{uuid_carrito}/convertir",
        json={"acepta_nuevo_total": False},
        headers=dispositivo["headers"],
    )
    assert rechazo.status_code == 409, rechazo.text
    datos = rechazo.json()
    assert datos["codigo"] == "PRECIO_DESACTUALIZADO"
    assert datos["datos"]["total_anterior"] == 80.0
    assert datos["datos"]["total_nuevo"] == 100.0
    assert datos["datos"]["requiere_aceptacion"] is True

    aceptado = await client.post(
        f"/api/v1/carrito/offline/{uuid_carrito}/convertir",
        json={"acepta_nuevo_total": True},
        headers=dispositivo["headers"],
    )
    assert aceptado.status_code == 201, aceptado.text
    assert Decimal(str(aceptado.json()["total"])) == Decimal("100.00")


async def test_carrito_no_se_filtra_entre_tenants(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Carrito A")
    catalogo_a = await catalogo_tenant(tenant_a)
    dispositivo_a = await dispositivo_offline(tenant_a)

    uuid_carrito = str(uuid4())
    await _crear_carrito(client, dispositivo_a, _payload(catalogo_a, uuid_carrito))

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Carrito B")
    ajeno = await client.get(
        f"/api/v1/carrito/offline/{uuid_carrito}",
        headers=tenant_b["headers"],
    )
    assert ajeno.status_code == 404, ajeno.text
