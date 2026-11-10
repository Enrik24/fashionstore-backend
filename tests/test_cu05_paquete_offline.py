"""[EXAMEN-2] CU05 — Paquete de datos offline (plan 9.2.10).

Verifica contenido del paquete, aislamiento por tenant, versión/hash, descarga
incremental, clientes frecuentes y auditoría de la descarga.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_catalogo.models import EstadoSucursal, Inventario, Sucursal
from app.apps.gestion_ventas.models import EstadoOrden, Orden, TipoOrden
from app.apps.gestion_usuarios.models import Bitacora
from app.apps.operaciones_offline.models import (
    EstadoSincronizacion,
    OrigenOperacion,
    PuntoSincronizacion,
)


async def _crear_orden(db_session, tenant_id: int, cliente_id: int, sucursal_id: int):
    """Inserta una orden histórica para que el cliente figure como frecuente."""
    orden = Orden(
        numero_orden=f"ORD-{uuid4().hex[:10]}",
        cliente_id=cliente_id,
        sucursal_id=sucursal_id,
        tipo=TipoOrden.PRESENCIAL,
        estado=EstadoOrden.PAGADO,
        total=Decimal("100.00"),
        impuestos=Decimal("0.00"),
        descuentos=Decimal("0.00"),
        tenant_id=tenant_id,
        uuid_orden=uuid4().hex,
        origen=OrigenOperacion.WEB,
        es_offline=False,
        estado_sincronizacion=EstadoSincronizacion.NO_APLICA,
        moneda="BOB",
    )
    db_session.add(orden)
    await db_session.commit()
    await db_session.refresh(orden)
    return orden


async def test_paquete_contiene_solo_datos_del_tenant(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    db_session,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Paquete A")
    catalogo_a = await catalogo_tenant(tenant_a, precio="120.00", stock=7)

    # Segunda sucursal activa del mismo tenant para comprobar el alcance
    # multi-sucursal cuando el cliente omite `sucursal_id`.
    sucursal_original = await db_session.get(Sucursal, catalogo_a["sucursal_id"])
    sucursal_segunda = Sucursal(
        nombre="Sucursal CU05 2",
        direccion="Av. Segunda 123",
        telefono="70000001",
        estado=EstadoSucursal.ACTIVO,
        ciudad_id=sucursal_original.ciudad_id,
        tenant_id=int(tenant_a["id"]),
    )
    db_session.add(sucursal_segunda)
    await db_session.flush()
    db_session.add(
        Inventario(
            variante_producto_id=catalogo_a["variante_id"],
            sucursal_id=sucursal_segunda.id,
            cantidad=3,
            cantidad_reservada=0,
            stock_minimo=0,
            tenant_id=int(tenant_a["id"]),
        )
    )
    await db_session.commit()

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Paquete B")
    catalogo_b = await catalogo_tenant(tenant_b, precio="999.00", stock=99)

    respuesta = await client.get(
        f"/api/v1/offline/paquete?sucursal_id={catalogo_a['sucursal_id']}",
        headers=tenant_a["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    paquete = respuesta.json()

    assert paquete["tenant_id"] == tenant_a["id"]
    assert paquete["sucursal_id"] == catalogo_a["sucursal_id"]
    assert paquete["version"]
    assert paquete["hash_paquete"]

    skus = {p["sku"] for p in paquete["catalogo"]}
    assert catalogo_a["sku"] in skus
    assert catalogo_b["sku"] not in skus

    variantes = {v["id"]: v for v in paquete["variantes"]}
    assert catalogo_a["variante_id"] in variantes
    assert catalogo_b["variante_id"] not in variantes
    assert float(variantes[catalogo_a["variante_id"]]["precio"]) == 120.0

    stock_explicito = {
        (s["variante_producto_id"], s["sucursal_id"]): s
        for s in paquete["stock"]
    }
    assert stock_explicito[
        (catalogo_a["variante_id"], catalogo_a["sucursal_id"])
    ]["cantidad"] == 7
    assert (
        catalogo_a["variante_id"],
        sucursal_segunda.id,
    ) not in stock_explicito
    assert catalogo_b["variante_id"] not in {
        variante_id for variante_id, _ in stock_explicito
    }

    # Sin sucursal explícita el paquete contiene el stock de todas las
    # sucursales activas del tenant, no solo de una sucursal elegida por orden.
    respuesta_todas = await client.get(
        "/api/v1/offline/paquete",
        headers=tenant_a["headers"],
    )
    assert respuesta_todas.status_code == 200, respuesta_todas.text
    paquete_todas = respuesta_todas.json()
    assert paquete_todas["sucursal_id"] is None
    stock_todas = {
        (s["variante_producto_id"], s["sucursal_id"]): s
        for s in paquete_todas["stock"]
    }
    assert stock_todas[
        (catalogo_a["variante_id"], catalogo_a["sucursal_id"])
    ]["cantidad"] == 7
    assert stock_todas[
        (catalogo_a["variante_id"], sucursal_segunda.id)
    ]["cantidad"] == 3
    assert catalogo_b["variante_id"] not in {
        variante_id for variante_id, _ in stock_todas
    }

    assert paquete["configuracion_offline"]["incluir_clientes"] is True
    assert "impuestos" in paquete
    assert "stripe" in paquete
    assert "roles" in paquete["permisos"]
    assert "permisos" in paquete["permisos"]

    # El paquete de B no filtra datos de A.
    respuesta_b = await client.get(
        f"/api/v1/offline/paquete?sucursal_id={catalogo_b['sucursal_id']}",
        headers=tenant_b["headers"],
    )
    paquete_b = respuesta_b.json()
    assert {p["sku"] for p in paquete_b["catalogo"]} == {catalogo_b["sku"]}


async def test_version_y_hash_son_coherentes(
    client: AsyncClient, tenant_nuevo, plan_profesional, catalogo_tenant
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    await catalogo_tenant(tenant)

    version = await client.get("/api/v1/offline/version", headers=tenant["headers"])
    assert version.status_code == 200, version.text
    datos_version = version.json()

    paquete = await client.get("/api/v1/offline/paquete", headers=tenant["headers"])
    assert paquete.status_code == 200, paquete.text
    datos_paquete = paquete.json()

    assert datos_version["version"] == datos_paquete["version"]
    assert datos_version["hash_paquete"] == datos_version["version"]
    assert len(datos_paquete["hash_paquete"]) >= 16


async def test_version_cambia_al_actualizar_el_catalogo(
    client: AsyncClient, tenant_nuevo, plan_profesional, catalogo_tenant, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00")

    antes = (await client.get("/api/v1/offline/version", headers=tenant["headers"])).json()

    from app.apps.gestion_catalogo.models import VarianteProducto

    variante = await db_session.get(VarianteProducto, catalogo["variante_id"])
    variante.precio_variante = Decimal("150.00")
    await db_session.commit()

    despues = (await client.get("/api/v1/offline/version", headers=tenant["headers"])).json()
    assert despues["version"] != antes["version"]


async def test_descarga_incremental_solo_trae_cambios(
    client: AsyncClient, tenant_nuevo, plan_profesional, catalogo_tenant, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo_1 = await catalogo_tenant(tenant, precio="100.00")
    catalogo_2 = await catalogo_tenant(tenant, precio="200.00")

    # Se fijan fechas explícitas para que el delta no dependa del reloj.
    from sqlalchemy import update

    from app.apps.gestion_catalogo.models import VarianteProducto

    await db_session.execute(
        update(VarianteProducto)
        .where(VarianteProducto.id == catalogo_1["variante_id"])
        .values(updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    )
    await db_session.execute(
        update(VarianteProducto)
        .where(VarianteProducto.id == catalogo_2["variante_id"])
        .values(updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    )
    await db_session.commit()

    fechas = (await db_session.execute(
        select(VarianteProducto.id, VarianteProducto.updated_at).where(
            VarianteProducto.id.in_([catalogo_1["variante_id"], catalogo_2["variante_id"]])
        )
    )).all()
    assert {f[0]: f[1].year for f in fechas} == {
        catalogo_1["variante_id"]: 2026,
        catalogo_2["variante_id"]: 2024,
    }

    desde = datetime(2025, 1, 1)

    delta = await client.get(
        f"/api/v1/offline/catalogo?desde_version={desde.isoformat()}",
        headers=tenant["headers"],
    )
    assert delta.status_code == 200, delta.text
    cuerpo = delta.json()

    ids = {v["id"] for v in cuerpo["variantes"]}
    assert catalogo_1["variante_id"] in ids
    assert catalogo_2["variante_id"] not in ids

    # Una versión desconocida fuerza la descarga completa.
    completa = await client.get(
        "/api/v1/offline/catalogo?desde_version=no-es-una-fecha",
        headers=tenant["headers"],
    )
    assert completa.status_code == 200
    assert len(completa.json()["variantes"]) >= 2


async def test_clientes_frecuentes_solo_del_tenant(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    db_session,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Clientes A")
    catalogo_a = await catalogo_tenant(tenant_a)
    cliente_a = await cliente_tenant(tenant_a)
    await _crear_orden(db_session, tenant_a["id"], cliente_a["id"], catalogo_a["sucursal_id"])

    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Clientes B")
    catalogo_b = await catalogo_tenant(tenant_b)
    cliente_b = await cliente_tenant(tenant_b)
    await _crear_orden(db_session, tenant_b["id"], cliente_b["id"], catalogo_b["sucursal_id"])

    respuesta = await client.get("/api/v1/offline/clientes", headers=tenant_a["headers"])
    assert respuesta.status_code == 200, respuesta.text
    clientes = respuesta.json()
    ids = {c["id"] for c in clientes}
    assert cliente_a["id"] in ids
    assert cliente_b["id"] not in ids
    assert all(c["compras"] >= 1 for c in clientes)


async def test_registrar_descarga_audita_y_actualiza_punto(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    await catalogo_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    respuesta = await client.post(
        "/api/v1/offline/descargas/registrar",
        json={
            "dispositivo_id": dispositivo["id"],
            "version": "v-prueba-001",
            "hash_paquete": "hash-abc",
            "tamano_bytes": 2048,
            "completo": True,
        },
        headers=dispositivo["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["registrada"] is True
    assert cuerpo["cursor_catalogo"] == "v-prueba-001"
    assert cuerpo["ultima_descarga_completa"] is not None

    punto = (await db_session.execute(
        select(PuntoSincronizacion).where(
            PuntoSincronizacion.tenant_id == tenant["id"],
            PuntoSincronizacion.dispositivo_id == dispositivo["id"],
        )
    )).scalar_one_or_none()
    assert punto is not None
    assert punto.cursor_catalogo == "v-prueba-001"

    auditoria = (await db_session.execute(
        select(Bitacora).where(
            Bitacora.tenant_id == tenant["id"],
            Bitacora.accion == "DESCARGA_OFFLINE",
        )
    )).scalars().all()
    assert auditoria, "La descarga debe quedar registrada en bitácora"
