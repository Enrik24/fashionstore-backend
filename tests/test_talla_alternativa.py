"""[EXAMEN-2] Fase D — talla mayor/menor sin stock y notificaciones (RF-034/RF-037)."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_catalogo.models import (
    Categoria,
    Color,
    EstadoProducto,
    Inventario,
    Producto,
    Talla,
    VarianteProducto,
)
from app.apps.gestion_ventas.models import (
    CanalNotificacion,
    EstadoNotificacion,
    NotificacionCliente,
)
from app.services.notificacion_service import NotificacionService
from tests._fase3_helpers import crear_sucursal, crear_venta, fijar_inventario


def _sufijo() -> str:
    return uuid.uuid4().hex[:8]


ORDENES = {"XS": 10, "S": 20, "M": 30, "L": 40, "XL": 50}


async def _producto_con_tallas(
    db_session, tenant: dict, tallas: dict[str, int]
) -> dict:
    """Crea un producto con una variante por talla y su stock en la sucursal."""
    tenant_id = int(tenant["id"])
    sufijo = _sufijo()
    ciudad_id = tenant.get("ciudad_id")
    if ciudad_id is None:
        from app.apps.gestion_catalogo.models import Ciudad

        ciudad_id = (await db_session.execute(select(Ciudad.id))).scalars().first()

    from app.apps.gestion_catalogo.models import Sucursal

    sucursal = Sucursal(
        nombre=f"Sucursal {sufijo}",
        direccion="Calle Alternativa 123",
        telefono="70000003",
        ciudad_id=ciudad_id,
        tenant_id=tenant_id,
    )
    categoria = Categoria(nombre=f"Cat {sufijo}", tenant_id=tenant_id)
    color = Color(nombre=f"Color {sufijo}", codigo_hex="#556677", tenant_id=tenant_id)
    db_session.add_all([sucursal, categoria, color])
    await db_session.flush()

    producto = Producto(
        sku=f"SKU-{sufijo}",
        nombre=f"Producto {sufijo}",
        precio=Decimal("100.00"),
        costo_compra=Decimal("50.00"),
        estado=EstadoProducto.ACTIVO,
        categoria_id=categoria.id,
        tenant_id=tenant_id,
    )
    db_session.add(producto)
    await db_session.flush()

    variantes: dict[str, int] = {}
    for talla_valor, stock in tallas.items():
        talla = Talla(
            valor=talla_valor,
            tipo="ropa",
            tenant_id=tenant_id,
            orden=ORDENES.get(talla_valor),
        )
        db_session.add(talla)
        await db_session.flush()
        variante = VarianteProducto(
            producto_id=producto.id,
            talla_id=talla.id,
            color_id=color.id,
            sku_variante=f"{producto.sku}-{talla_valor}",
            precio_variante=Decimal("100.00"),
            tenant_id=tenant_id,
        )
        db_session.add(variante)
        await db_session.flush()
        fijar = await db_session.execute(
            select(Inventario).where(
                Inventario.tenant_id == tenant_id,
                Inventario.variante_producto_id == variante.id,
                Inventario.sucursal_id == sucursal.id,
            )
        )
        if fijar.scalar_one_or_none() is None:
            db_session.add(Inventario(
                variante_producto_id=variante.id,
                sucursal_id=sucursal.id,
                cantidad=stock,
                cantidad_reservada=0,
                tenant_id=tenant_id,
            ))
        variantes[talla_valor] = variante.id
    await db_session.commit()
    return {
        "producto_id": producto.id,
        "sucursal_id": sucursal.id,
        "variantes": variantes,
        "categoria_id": categoria.id,
        "color_id": color.id,
    }


@pytest.mark.asyncio
async def test_orden_de_talla_poblado(db_session):
    tallas = (await db_session.execute(
        select(Talla).where(Talla.orden.is_not(None)).limit(50)
    )).scalars().all()
    assert tallas, "El seed debe poblar tallas.orden"
    mapa = {talla.valor.upper(): talla.orden for talla in tallas}
    if "S" in mapa:
        assert mapa["S"] == 20
    if "M" in mapa:
        assert mapa["M"] == 30
    if "L" in mapa:
        assert mapa["L"] == 40
    if "34" in mapa:
        assert mapa["34"] == 340


@pytest.mark.asyncio
async def test_alternativa_mayor_y_menor_en_la_misma_sucursal(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    datos = await _producto_con_tallas(db_session, tenant, {"S": 4, "M": 0, "L": 3})

    mayor = await client.get(
        f"/api/v1/devoluciones/alternativas-talla/{datos['variantes']['M']}"
        f"?sucursal_id={datos['sucursal_id']}&direccion=MAYOR",
        headers=tenant["headers"],
    )
    assert mayor.status_code == 200, mayor.text
    cuerpo = mayor.json()
    assert cuerpo["encontrada"] is True
    assert cuerpo["talla"] == "L"
    assert cuerpo["stock"] == 3
    assert cuerpo["variante_id"] == datos["variantes"]["L"]

    menor = await client.get(
        f"/api/v1/devoluciones/alternativas-talla/{datos['variantes']['M']}"
        f"?sucursal_id={datos['sucursal_id']}&direccion=MENOR",
        headers=tenant["headers"],
    )
    assert menor.status_code == 200
    assert menor.json()["talla"] == "S"
    assert menor.json()["stock"] == 4


@pytest.mark.asyncio
async def test_sin_alternativa_cuando_no_hay_stock(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    datos = await _producto_con_tallas(db_session, tenant, {"M": 0, "L": 0})

    respuesta = await client.get(
        f"/api/v1/devoluciones/alternativas-talla/{datos['variantes']['M']}"
        f"?sucursal_id={datos['sucursal_id']}&direccion=MAYOR",
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["encontrada"] is False
    assert respuesta.json()["variante_id"] is None


@pytest.mark.asyncio
async def test_alternativa_en_otra_sucursal(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    datos = await _producto_con_tallas(db_session, tenant, {"M": 0, "L": 0})
    otra = await crear_sucursal(db_session, tenant)
    await fijar_inventario(
        db_session, tenant, otra["id"], datos["variantes"]["L"], 6
    )

    respuesta = await client.get(
        f"/api/v1/devoluciones/alternativas-talla/{datos['variantes']['M']}"
        f"?sucursal_id={datos['sucursal_id']}&direccion=MAYOR",
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["encontrada"] is True
    assert cuerpo["sucursal_id"] == otra["id"]
    assert cuerpo["stock"] == 6


@pytest.mark.asyncio
async def test_notificacion_persistida_reintentable_y_leida(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional, cliente_tenant
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    cliente = await cliente_tenant(tenant)

    filas = await NotificacionService.crear(
        db_session,
        tenant_id=int(tenant["id"]),
        cliente_id=cliente["id"],
        usuario_id=None,
        tipo="REPOSICION",
        asunto="Tu reposición",
        mensaje="Registramos tu reposición correctamente.",
        referencia=("solicitud_devolucion", 9999),
    )
    await db_session.commit()
    assert filas
    assert filas[0].estado in (EstadoNotificacion.PENDIENTE, EstadoNotificacion.ENVIADA)
    assert filas[0].canal == CanalNotificacion.CORREO

    reintento = await NotificacionService.reintentar(db_session, filas[0].id)
    assert reintento.estado in (EstadoNotificacion.PENDIENTE, EstadoNotificacion.ENVIADA)

    leida = await client.patch(
        f"/api/v1/notificaciones/{filas[0].id}/leida", headers=tenant["headers"]
    )
    assert leida.status_code == 200, leida.text
    assert leida.json()["leida"] is True

    listado = await client.get(
        f"/api/v1/notificaciones/mis-notificaciones?cliente_id={cliente['id']}",
        headers=tenant["headers"],
    )
    assert listado.status_code == 200
    assert any(n["id"] == filas[0].id for n in listado.json()["notificaciones"])


@pytest.mark.asyncio
async def test_reposicion_sin_talla_mayor_queda_sin_alternativa(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=5)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(db_session, tenant, catalogo, cliente["id"], precio="100.00")

    # Variante hermana sin stock: no hay alternativa disponible.
    sufijo = _sufijo()
    from app.apps.gestion_catalogo.models import Color

    color = Color(nombre=f"C{sufijo}", tenant_id=int(tenant["id"]))
    db_session.add(color)
    await db_session.flush()
    hermana = VarianteProducto(
        producto_id=catalogo["producto_id"],
        talla_id=catalogo["talla_id"],
        color_id=color.id,
        sku_variante=f"AGOT-{sufijo}",
        precio_variante=Decimal("100.00"),
        tenant_id=int(tenant["id"]),
    )
    db_session.add(hermana)
    await db_session.commit()
    await db_session.refresh(hermana)
    await fijar_inventario(db_session, tenant, catalogo["sucursal_id"], hermana.id, 0)

    respuesta = await client.post(
        "/api/v1/devoluciones/reposicion",
        json={
            "orden_id": venta["orden_id"],
            "motivo": "TALLA_INCORRECTA",
            "aceptar_sin_alternativa": True,
            "items": [
                {
                    "detalle_orden_id": venta["detalle_id"],
                    "cantidad": 1,
                    "variante_cambio_id": hermana.id,
                }
            ],
        },
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["estado"] == "SIN_ALTERNATIVA"

    notificaciones = (await db_session.execute(
        select(NotificacionCliente).where(
            NotificacionCliente.tenant_id == int(tenant["id"]),
            NotificacionCliente.tipo == "SIN_ALTERNATIVA",
        )
    )).scalars().all()
    assert notificaciones, "Debe quedar registrada la notificación al cliente"
