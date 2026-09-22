"""
Tests de integración para el Caso de Uso CU27: Extensión de Cupones (Aplicabilidad).
"""
import pytest
from httpx import AsyncClient
from datetime import datetime, timezone, timedelta


async def _asegurar_producto(client: AsyncClient, admin_headers: dict) -> int:
    res_prods = await client.get("/api/v1/productos/", headers=admin_headers)
    if res_prods.status_code == 200 and len(res_prods.json()) > 0:
        return res_prods.json()[0]["id"]
    
    # Crear categoría
    res_cats = await client.get("/api/v1/categorias/")
    cats = res_cats.json()
    if not cats:
        cat_resp = await client.post("/api/v1/categorias/", headers=admin_headers, json={
            "nombre": "Categoría Cupón",
            "descripcion": "Descripción cupón"
        })
        cat_id = cat_resp.json()["id"]
    else:
        cat_id = cats[0]["id"]

    ts = int(datetime.now().timestamp())
    prod_resp = await client.post("/api/v1/productos/", headers=admin_headers, json={
        "sku": f"PROD-CUP-{ts}",
        "nombre": "Producto Cupón Test",
        "precio": "100.00",
        "categoria_id": cat_id
    })
    return prod_resp.json()["id"]


@pytest.mark.asyncio
async def test_01_cupon_con_aplicabilidad_productos(client: AsyncClient, admin_headers: dict):
    ahora = datetime.now(timezone.utc)
    inicio = (ahora - timedelta(days=1)).isoformat()
    fin = (ahora + timedelta(days=10)).isoformat()

    prod_id = await _asegurar_producto(client, admin_headers)
    non_existent_prod_id = prod_id + 999

    # 1. Crear cupón aplicable solo al producto obtenido
    cupon_data = {
        "codigo": f"SOLO_PROD_{prod_id}",
        "tipo": "PORCENTAJE",
        "valor": 20.0,
        "descripcion": "20% en producto especifico",
        "fecha_inicio": inicio,
        "fecha_fin": fin,
        "producto_ids": [prod_id],
        "categoria_ids": []
    }
    res_crear = await client.post("/api/v1/cupones/", json=cupon_data, headers=admin_headers)
    assert res_crear.status_code == 201
    cupon_id = res_crear.json()["id"]

    # 2. Validar con item aplicable (producto_id, subtotal 100) -> descuento = 20
    req_ok = {
        "codigo": f"SOLO_PROD_{prod_id}",
        "subtotal": 100.0,
        "items": [
            {"producto_id": prod_id, "cantidad": 1, "precio_unitario": 100.0}
        ]
    }
    res_v1 = await client.post("/api/v1/cupones/validar", json=req_ok, headers=admin_headers)
    assert res_v1.status_code == 200
    data_v1 = res_v1.json()
    assert data_v1["valido"] is True
    assert float(data_v1["descuento_calculado"]) == 20.0

    # 3. Validar con item NO aplicable -> valido = False
    req_fail = {
        "codigo": f"SOLO_PROD_{prod_id}",
        "subtotal": 200.0,
        "items": [
            {"producto_id": non_existent_prod_id, "cantidad": 1, "precio_unitario": 200.0}
        ]
    }
    res_v2 = await client.post("/api/v1/cupones/validar", json=req_fail, headers=admin_headers)
    assert res_v2.status_code == 200
    data_v2 = res_v2.json()
    assert data_v2["valido"] is False
    assert "no es aplicable" in data_v2["mensaje"]

    # Limpiar
    await client.delete(f"/api/v1/cupones/{cupon_id}", headers=admin_headers)


@pytest.mark.asyncio
async def test_02_listar_cupones_disponibles_cliente(client: AsyncClient, admin_headers: dict):
    ahora = datetime.now(timezone.utc)
    ts = int(datetime.now().timestamp())
    # Crear cupón disponible
    cupon_data = {
        "codigo": f"CLIENTE_DISP_{ts}",
        "tipo": "MONTO_FIJO",
        "valor": 30.0,
        "fecha_inicio": (ahora - timedelta(days=1)).isoformat(),
        "fecha_fin": (ahora + timedelta(days=20)).isoformat(),
        "estado": "ACTIVO"
    }
    res_c = await client.post("/api/v1/cupones/", json=cupon_data, headers=admin_headers)
    assert res_c.status_code == 201
    cid = res_c.json()["id"]

    # Registrar y loguear cliente
    correo = f"cliente_cupon_{ts}@fashionstore.com"
    contrasena = "Cupon123!"
    res_reg = await client.post(
        "/api/v1/auth/register",
        json={"nombre": "Ana", "apellido": "Lopez", "correo": correo, "contrasena": contrasena, "nit_ci": f"NIT-CUP-{ts}"}
    )
    assert res_reg.status_code == 200, f"Register failed: {res_reg.text}"
    res_log = await client.post("/api/v1/auth/login", json={"correo": correo, "contrasena": contrasena})
    assert res_log.status_code == 200, f"Login failed: {res_log.text}"
    c_headers = {"Authorization": f"Bearer {res_log.json()['access_token']}"}

    # Listar disponibles
    res_disp = await client.get("/api/v1/cupones/disponibles", headers=c_headers)
    assert res_disp.status_code == 200
    disponibles = res_disp.json()
    assert any(c["codigo"] == f"CLIENTE_DISP_{ts}" for c in disponibles)

    # Limpiar
    await client.delete(f"/api/v1/cupones/{cid}", headers=admin_headers)
