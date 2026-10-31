"""[EXAMEN-2] Aislamiento multitenant — prueba transversal obligatoria.

Un administrador del tenant A no puede leer, editar ni borrar recursos del
tenant B en 20 endpoints representativos del Examen 1 y de la Fase 1
(plan, 9.1.10).
"""
import uuid
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient


def _iso(minutos: int = 0) -> str:
    return (datetime.utcnow() + timedelta(minutes=minutos)).isoformat()


async def _datos_del_tenant_b(client: AsyncClient, tenant_nuevo) -> dict:
    """Crea el tenant B y un juego completo de recursos propios."""
    tenant = await tenant_nuevo(nombre="Tenant B", finalizar=True)
    headers = tenant["headers"]
    sufijo = uuid.uuid4().hex[:6]

    async def _post(ruta: str, cuerpo: dict) -> dict:
        respuesta = await client.post(ruta, json=cuerpo, headers=headers)
        assert respuesta.status_code in (200, 201), f"{ruta}: {respuesta.text}"
        return respuesta.json()

    categoria = await _post("/api/v1/categorias/", {"nombre": f"CatB {sufijo}"})
    temporada = await _post(
        "/api/v1/temporadas/",
        {"nombre": f"TempB {sufijo}", "fecha_inicio": "2026-01-01", "fecha_fin": "2026-12-31"},
    )
    coleccion = await _post(
        "/api/v1/colecciones/",
        {"nombre": f"ColB {sufijo}", "temporada_id": temporada["id"]},
    )
    proveedor = await _post(
        "/api/v1/proveedores/", {"nombre": f"ProvB {sufijo}", "nit": f"PB{sufijo}"}
    )

    ciudades = (await client.get("/api/v1/ciudades/", headers=headers)).json()
    assert ciudades, "El seed debe crear ciudades"
    sucursal = await _post(
        "/api/v1/sucursales/",
        {
            "nombre": f"SucB {sufijo}",
            "direccion": "Calle B 100",
            "telefono": "70111222",
            "ciudad_id": ciudades[0]["id"],
        },
    )
    producto = await _post(
        "/api/v1/productos/",
        {
            "sku": f"SKUB-{sufijo}",
            "nombre": f"ProductoB {sufijo}",
            "precio": 99.9,
            "costo_compra": 40,
            "categoria_id": categoria["id"],
            "temporada_id": temporada["id"],
            "proveedor_id": proveedor["id"],
        },
    )
    cupon = await _post(
        "/api/v1/cupones/",
        {
            "codigo": f"CUPB{sufijo}",
            "tipo": "PORCENTAJE",
            "valor": 10,
            "fecha_inicio": _iso(-60),
            "fecha_fin": _iso(60 * 24 * 30),
        },
    )
    promocion = await _post(
        "/api/v1/promociones/",
        {
            "nombre": f"PromoB {sufijo}",
            "tipo": "PORCENTAJE",
            "valor": 5,
            "fecha_inicio": _iso(-60),
            "fecha_fin": _iso(60 * 24 * 30),
        },
    )
    usuario = await _post(
        "/api/v1/saas/configuracion/usuarios",
        {
            "nombre": "Cajero",
            "apellido": "B",
            "correo": f"cajero{sufijo}@tenantb.com",
            "contrasena": "Temporal123!",
            "rol": "Cajero",
            "sucursal_id": sucursal["id"],
        },
    )
    dispositivo = await _post(
        "/api/v1/offline/dispositivos",
        {"plataforma": "ANDROID", "nombre": f"EquipoB {sufijo}"},
    )

    return {
        "tenant": tenant,
        "sufijo": sufijo,
        "categoria": categoria,
        "temporada": temporada,
        "coleccion": coleccion,
        "proveedor": proveedor,
        "sucursal": sucursal,
        "producto": producto,
        "cupon": cupon,
        "promocion": promocion,
        "usuario": usuario,
        "dispositivo": dispositivo,
    }


@pytest.mark.asyncio
async def test_aislamiento_multitenant_en_20_endpoints(
    client: AsyncClient, tenant_nuevo, admin_headers: dict
):
    b = await _datos_del_tenant_b(client, tenant_nuevo)

    # (método, ruta, cuerpo opcional) — todos apuntan a recursos del tenant B
    objetivos = [
        ("GET", f"/api/v1/productos/{b['producto']['id']}", None),
        ("PUT", f"/api/v1/productos/{b['producto']['id']}", {"nombre": "Hackeado"}),
        ("DELETE", f"/api/v1/productos/{b['producto']['id']}", None),
        ("GET", f"/api/v1/categorias/{b['categoria']['id']}", None),
        ("PUT", f"/api/v1/categorias/{b['categoria']['id']}", {"nombre": "Hackeada"}),
        ("DELETE", f"/api/v1/categorias/{b['categoria']['id']}", None),
        ("GET", f"/api/v1/sucursales/{b['sucursal']['id']}", None),
        ("PUT", f"/api/v1/sucursales/{b['sucursal']['id']}", {"nombre": "Hackeada"}),
        ("DELETE", f"/api/v1/sucursales/{b['sucursal']['id']}", None),
        ("GET", f"/api/v1/usuarios/{b['usuario']['id']}", None),
        ("PUT", f"/api/v1/usuarios/{b['usuario']['id']}", {"nombre": "Hackeado"}),
        ("DELETE", f"/api/v1/usuarios/{b['usuario']['id']}", None),
        ("GET", f"/api/v1/cupones/{b['cupon']['id']}", None),
        ("PUT", f"/api/v1/cupones/{b['cupon']['id']}", {"descripcion": "Hackeado"}),
        ("DELETE", f"/api/v1/cupones/{b['cupon']['id']}", None),
        ("GET", f"/api/v1/promociones/{b['promocion']['id']}", None),
        ("PUT", f"/api/v1/promociones/{b['promocion']['id']}", {"nombre": "Hackeada"}),
        ("GET", f"/api/v1/temporadas/{b['temporada']['id']}", None),
        ("GET", f"/api/v1/colecciones/{b['coleccion']['id']}", None),
        ("GET", f"/api/v1/proveedores/{b['proveedor']['id']}", None),
        ("POST", f"/api/v1/offline/dispositivos/{b['dispositivo']['id']}/revocar", None),
    ]

    for metodo, ruta, cuerpo in objetivos:
        respuesta = await client.request(
            metodo, ruta, json=cuerpo, headers=admin_headers
        )
        assert respuesta.status_code == 404, (
            f"{metodo} {ruta} debía responder 404 para el tenant A, "
            f"respondió {respuesta.status_code}: {respuesta.text}"
        )


@pytest.mark.asyncio
async def test_los_listados_no_mezclan_tenants(
    client: AsyncClient, tenant_nuevo, admin_headers: dict
):
    b = await _datos_del_tenant_b(client, tenant_nuevo)

    productos = (await client.get("/api/v1/productos/", headers=admin_headers)).json()
    skus = {p["sku"] for p in productos}
    assert b["producto"]["sku"] not in skus

    categorias = (await client.get("/api/v1/categorias/", headers=admin_headers)).json()
    assert b["categoria"]["id"] not in {c["id"] for c in categorias}

    sucursales = (await client.get("/api/v1/sucursales/", headers=admin_headers)).json()
    assert b["sucursal"]["id"] not in {s["id"] for s in sucursales}

    bitacora = (await client.get("/api/v1/bitacora/", headers=admin_headers)).json()
    assert all(f"SucB {b['sufijo']}" not in str(evento) for evento in bitacora)

    inventario = (await client.get("/api/v1/inventario/", headers=admin_headers)).json()
    assert all(fila.get("tenant_id") != b["tenant"]["id"] for fila in inventario)


@pytest.mark.asyncio
async def test_cada_tenant_solo_ve_su_propia_suscripcion(
    client: AsyncClient, tenant_nuevo, admin_headers: dict, demo_tenant
):
    b = await _datos_del_tenant_b(client, tenant_nuevo)

    mi_suscripcion_a = await client.get(
        "/api/v1/saas/mi-suscripcion", headers=admin_headers
    )
    mi_suscripcion_b = await client.get(
        "/api/v1/saas/mi-suscripcion", headers=b["tenant"]["headers"]
    )

    assert mi_suscripcion_a.status_code == 200
    assert mi_suscripcion_b.status_code == 200
    assert mi_suscripcion_a.json()["tenant_id"] == demo_tenant.id
    assert mi_suscripcion_b.json()["tenant_id"] == b["tenant"]["id"]


@pytest.mark.asyncio
async def test_el_tenant_b_tampoco_ve_datos_del_tenant_demo(
    client: AsyncClient, tenant_nuevo, admin_headers: dict
):
    b = await _datos_del_tenant_b(client, tenant_nuevo)
    productos_demo = (await client.get("/api/v1/productos/", headers=admin_headers)).json()

    if not productos_demo:
        pytest.skip("El tenant demo no tiene productos para la prueba inversa")

    producto_demo = productos_demo[0]["id"]
    respuesta = await client.get(
        f"/api/v1/productos/{producto_demo}", headers=b["tenant"]["headers"]
    )

    assert respuesta.status_code == 404
