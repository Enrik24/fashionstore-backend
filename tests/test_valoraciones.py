"""
Tests de integración para el Caso de Uso CU26: Valorar Productos.
"""
import pytest
import uuid
from httpx import AsyncClient


def _uid():
    return uuid.uuid4().hex[:8]


async def _asegurar_producto_con_stock(client: AsyncClient, admin_headers: dict) -> tuple[int, int]:
    res_prods = await client.get("/api/v1/productos/", headers=admin_headers)
    if res_prods.status_code == 200 and len(res_prods.json()) > 0:
        for p in res_prods.json():
            res_det = await client.get(f"/api/v1/productos/{p['id']}", headers=admin_headers)
            if res_det.status_code == 200:
                det = res_det.json()
                if det.get("variantes"):
                    return p["id"], det["variantes"][0]["id"]
    
    # Crear categoría
    res_cats = await client.get("/api/v1/categorias/")
    cats = res_cats.json()
    if not cats:
        cat_resp = await client.post("/api/v1/categorias/", headers=admin_headers, json={
            "nombre": "Categoría Val",
            "descripcion": "Descripción test"
        })
        cat_id = cat_resp.json()["id"]
    else:
        cat_id = cats[0]["id"]

    uid = _uid()
    prod_resp = await client.post("/api/v1/productos/", headers=admin_headers, json={
        "sku": f"PROD-VAL-{uid}",
        "nombre": "Producto Valoración Test",
        "precio": "150.00",
        "categoria_id": cat_id
    })
    prod_id = prod_resp.json()["id"]

    tallas_resp = await client.get("/api/v1/tallas/")
    talla_id = tallas_resp.json()[0]["id"] if tallas_resp.json() else 1
    colores_resp = await client.get("/api/v1/colores/")
    color_id = colores_resp.json()[0]["id"] if colores_resp.json() else 1

    var_resp = await client.post(
        f"/api/v1/productos/{prod_id}/variantes?cantidad_inicial=20",
        headers=admin_headers,
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku_variante": f"SKU-VAL-{uid}",
            "precio_variante": "150.00"
        }
    )
    return prod_id, var_resp.json()["id"]


async def _crear_cliente_con_compra(client: AsyncClient, admin_headers: dict) -> tuple[dict, int, int]:
    """Crea un cliente, realiza una compra y la marca como PAGADO para habilitar valoraciones."""
    uid = _uid()
    correo = f"crev_{uid}@test.com"
    contrasena = "Review123!"

    # 1. Registrar
    res_reg = await client.post(
        "/api/v1/auth/register",
        json={
            "nombre": "Carlos",
            "apellido": "Ramirez",
            "correo": correo,
            "contrasena": contrasena,
            "telefono": "55554321",
            "nit_ci": f"NCI-{uid}",
            "direccion_envio": "Zona 14, Guatemala"
        }
    )
    assert res_reg.status_code == 200, f"Register failed: {res_reg.text}"

    # 2. Login
    res_log = await client.post("/api/v1/auth/login", json={"correo": correo, "contrasena": contrasena})
    assert res_log.status_code == 200, f"Login failed: {res_log.text}"
    token = res_log.json()["access_token"]
    cliente_headers = {"Authorization": f"Bearer {token}"}

    # 3. Obtener producto con variante
    producto_id, variante_id = await _asegurar_producto_con_stock(client, admin_headers)

    # 4. Agregar al carrito y crear orden
    await client.post(
        "/api/v1/carrito/items",
        json={"variante_producto_id": variante_id, "cantidad": 1},
        headers=cliente_headers
    )
    res_orden = await client.post(
        "/api/v1/ordenes/",
        json={"tipo": "DIGITAL", "direccion_envio": "Zona 14, Guatemala"},
        headers=cliente_headers
    )
    assert res_orden.status_code in (200, 201), f"Create order failed: {res_orden.text}"
    orden_id = res_orden.json()["id"]

    # 5. Admin cambia estado a PAGADO
    await client.patch(
        f"/api/v1/ordenes/{orden_id}/estado",
        json={"estado": "PAGADO"},
        headers=admin_headers
    )

    return cliente_headers, producto_id, orden_id


@pytest.mark.asyncio
async def test_01_flujo_valoracion_con_compra(client: AsyncClient, admin_headers: dict):
    cliente_headers, producto_id, orden_id = await _crear_cliente_con_compra(client, admin_headers)

    # 1. Verificar puede_valorar
    res_puede = await client.get(f"/api/v1/productos/{producto_id}/puede-valorar", headers=cliente_headers)
    assert res_puede.status_code == 200
    assert res_puede.json()["puede_valorar"] is True

    # 2. Crear valoración con 5 estrellas
    rev_data = {
        "puntuacion": 5,
        "comentario": "Excelente calidad de tela y acabado."
    }
    res_rev = await client.post(f"/api/v1/productos/{producto_id}/valoraciones", json=rev_data, headers=cliente_headers)
    assert res_rev.status_code == 201
    created_rev = res_rev.json()
    assert created_rev["puntuacion"] == 5
    assert "Carlos R." in created_rev["cliente_nombre"]
    valoracion_id = created_rev["id"]

    # 3. Intentar crear valoración duplicada -> 409
    res_dup = await client.post(f"/api/v1/productos/{producto_id}/valoraciones", json=rev_data, headers=cliente_headers)
    assert res_dup.status_code == 409

    # 4. Verificar que el producto actualizó su promedio y total
    res_prod = await client.get(f"/api/v1/productos/{producto_id}", headers=admin_headers)
    prod_data = res_prod.json()
    assert float(prod_data["promedio_valoracion"]) >= 4.0
    assert prod_data["total_valoraciones"] >= 1

    # 5. Editar la valoración propia a 4 estrellas
    res_edit = await client.put(
        f"/api/v1/valoraciones/{valoracion_id}",
        json={"puntuacion": 4, "comentario": "Muy buena prenda, aunque tardó un poco el envío."},
        headers=cliente_headers
    )
    assert res_edit.status_code == 200
    assert res_edit.json()["puntuacion"] == 4

    # 6. Consultar mi valoración
    res_mi = await client.get(f"/api/v1/productos/{producto_id}/mi-valoracion", headers=cliente_headers)
    assert res_mi.status_code == 200
    assert res_mi.json()["puntuacion"] == 4

    # 7. Listar valoraciones públicas
    res_pub = await client.get(f"/api/v1/productos/{producto_id}/valoraciones")
    assert res_pub.status_code == 200
    assert any(v["id"] == valoracion_id for v in res_pub.json())


@pytest.mark.asyncio
async def test_02_valorar_sin_compra_rechazado(client: AsyncClient, admin_headers: dict):
    # Cliente sin compras
    uid = _uid()
    correo = f"cnocomp_{uid}@test.com"
    contrasena = "SinCompra123!"
    res_reg = await client.post(
        "/api/v1/auth/register",
        json={
            "nombre": "Elena",
            "apellido": "Perez",
            "correo": correo,
            "contrasena": contrasena,
            "telefono": "55559999",
            "nit_ci": f"NCI-{uid}"
        }
    )
    assert res_reg.status_code == 200, f"Register failed: {res_reg.text}"
    res_log = await client.post("/api/v1/auth/login", json={"correo": correo, "contrasena": contrasena})
    assert res_log.status_code == 200, f"Login failed: {res_log.text}"
    headers = {"Authorization": f"Bearer {res_log.json()['access_token']}"}

    prod_id, _ = await _asegurar_producto_con_stock(client, admin_headers)

    # Intentar valorar producto sin haberlo comprado -> 403
    res_val = await client.post(
        f"/api/v1/productos/{prod_id}/valoraciones",
        json={"puntuacion": 5, "comentario": "Me parece bonito"},
        headers=headers
    )
    assert res_val.status_code == 403


@pytest.mark.asyncio
async def test_03_moderacion_palabras_prohibidas(client: AsyncClient, admin_headers: dict):
    cliente_headers, producto_id, orden_id = await _crear_cliente_con_compra(client, admin_headers)

    # Enviar comentario con palabra prohibida "estafa"
    res_bad = await client.post(
        f"/api/v1/productos/{producto_id}/valoraciones",
        json={"puntuacion": 1, "comentario": "Esto es una estafa total, pésimo servicio"},
        headers=cliente_headers
    )
    assert res_bad.status_code == 201
    # Debe quedar en estado PENDIENTE_MODERACION
    assert res_bad.json()["estado"] == "PENDIENTE_MODERACION"
