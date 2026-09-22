"""
Tests de integración para el Caso de Uso CU25: Gestionar Productos Favoritos.
"""
import pytest
from httpx import AsyncClient
from datetime import datetime


async def _obtener_cliente_headers(client: AsyncClient) -> dict:
    correo = "cliente_fav@fashionstore.com"
    contrasena = "Cliente123!"
    # Registrar cliente si no existe
    await client.post(
        "/api/v1/auth/register",
        json={
            "nombre": "Sofia",
            "apellido": "Gomez",
            "correo": correo,
            "contrasena": contrasena,
            "telefono": "55551234",
            "nit_ci": "NIT-FAV-001",
            "direccion_envio": "Zona 10, Ciudad de Guatemala"
        }
    )
    # Login
    res_log = await client.post(
        "/api/v1/auth/login",
        json={"correo": correo, "contrasena": contrasena}
    )
    token = res_log.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _asegurar_producto_y_variante(client: AsyncClient, admin_headers: dict) -> tuple[int, int]:
    res_prods = await client.get("/api/v1/productos/", headers=admin_headers)
    if res_prods.status_code == 200 and len(res_prods.json()) > 0:
        for p in res_prods.json():
            res_det = await client.get(f"/api/v1/productos/{p['id']}", headers=admin_headers)
            if res_det.status_code == 200:
                det = res_det.json()
                if det.get("variantes"):
                    return p["id"], det["variantes"][0]["id"]
    
    # Crear categoría si no hay
    res_cats = await client.get("/api/v1/categorias/")
    cats = res_cats.json()
    if not cats:
        cat_resp = await client.post("/api/v1/categorias/", headers=admin_headers, json={
            "nombre": "Categoría Test Fav",
            "descripcion": "Descripción test"
        })
        cat_id = cat_resp.json()["id"]
    else:
        cat_id = cats[0]["id"]

    # Crear producto
    ts = int(datetime.now().timestamp())
    prod_resp = await client.post("/api/v1/productos/", headers=admin_headers, json={
        "sku": f"PROD-FAV-{ts}",
        "nombre": "Producto Favorito Test",
        "precio": "99.99",
        "categoria_id": cat_id
    })
    prod_id = prod_resp.json()["id"]

    # Crear variante
    tallas_resp = await client.get("/api/v1/tallas/")
    talla_id = tallas_resp.json()[0]["id"] if tallas_resp.json() else 1
    colores_resp = await client.get("/api/v1/colores/")
    color_id = colores_resp.json()[0]["id"] if colores_resp.json() else 1

    var_resp = await client.post(
        f"/api/v1/productos/{prod_id}/variantes?cantidad_inicial=10",
        headers=admin_headers,
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku_variante": f"SKU-FAV-{ts}",
            "precio_variante": "99.99"
        }
    )
    var_id = var_resp.json()["id"]
    return prod_id, var_id


@pytest.mark.asyncio
async def test_01_flujo_completo_favoritos(client: AsyncClient, admin_headers: dict):
    # 1. Asegurar producto existente
    producto_id, _ = await _asegurar_producto_y_variante(client, admin_headers)

    # 2. Login cliente
    cliente_headers = await _obtener_cliente_headers(client)

    # 3. Agregar a favoritos (idempotente)
    res_add1 = await client.post(f"/api/v1/favoritos/{producto_id}", headers=cliente_headers)
    assert res_add1.status_code == 200

    # Agregar de nuevo no debe fallar
    res_add2 = await client.post(f"/api/v1/favoritos/{producto_id}", headers=cliente_headers)
    assert res_add2.status_code == 200

    # 4. Listar IDs de favoritos
    res_ids = await client.get("/api/v1/favoritos/ids", headers=cliente_headers)
    assert res_ids.status_code == 200
    fav_ids = res_ids.json()["producto_ids"]
    assert producto_id in fav_ids

    # 5. Listar favoritos con detalle
    res_list = await client.get("/api/v1/favoritos/", headers=cliente_headers)
    assert res_list.status_code == 200
    items = res_list.json()
    assert any(it["producto_id"] == producto_id for it in items)

    # 6. Quitar de favoritos (idempotente)
    res_del = await client.delete(f"/api/v1/favoritos/{producto_id}", headers=cliente_headers)
    assert res_del.status_code == 200

    # Verificar que ya no está en IDs
    res_ids_after = await client.get("/api/v1/favoritos/ids", headers=cliente_headers)
    assert producto_id not in res_ids_after.json()["producto_ids"]


@pytest.mark.asyncio
async def test_02_mover_favorito_al_carrito(client: AsyncClient, admin_headers: dict):
    # Obtener producto y variante
    producto_id, variante_id = await _asegurar_producto_y_variante(client, admin_headers)

    cliente_headers = await _obtener_cliente_headers(client)

    # Agregar a favoritos
    await client.post(f"/api/v1/favoritos/{producto_id}", headers=cliente_headers)

    # Mover al carrito
    req = {
        "variante_producto_id": variante_id,
        "cantidad": 1,
        "quitar_de_favoritos": True
    }
    res_move = await client.post(f"/api/v1/favoritos/{producto_id}/mover-al-carrito", json=req, headers=cliente_headers)
    assert res_move.status_code == 200

    # Verificar que el carrito tiene el item
    res_cart = await client.get("/api/v1/carrito/", headers=cliente_headers)
    assert res_cart.status_code == 200
    cart_items = res_cart.json()["items"]
    assert any(it["variante_producto_id"] == variante_id for it in cart_items)

    # Verificar que se quitó de favoritos
    res_ids = await client.get("/api/v1/favoritos/ids", headers=cliente_headers)
    assert producto_id not in res_ids.json()["producto_ids"]


@pytest.mark.asyncio
async def test_03_favoritos_requiere_rol_cliente(client: AsyncClient, admin_headers: dict):
    # Intentar acceder a /favoritos/ con token de admin -> 403
    res = await client.get("/api/v1/favoritos/", headers=admin_headers)
    assert res.status_code == 403
