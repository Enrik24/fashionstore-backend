"""
Pruebas de ámbito de inventario para Encargado de Sucursal.
Valida que un Encargado solo acceda, consulte y modifique el inventario de su sucursal asignada.
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_encargado_inventario_ambito_sucursal(client: AsyncClient, admin_headers: dict):
    # 1. Crear o asegurar dos sucursales
    res_c = await client.get("/api/v1/ciudades/", headers=admin_headers)
    ciudades = res_c.json()
    if not ciudades:
        res_c = await client.post("/api/v1/ciudades/", headers=admin_headers, json={"nombre": "Ciudad Inventario Test"})
        ciudad_id = res_c.json()["id"]
    else:
        ciudad_id = ciudades[0]["id"]

    res_s1 = await client.post("/api/v1/sucursales/", headers=admin_headers, json={
        "nombre": "Sucursal Encargado Alpha",
        "direccion": "Av. Alpha 123",
        "telefono": "70000001",
        "ciudad_id": ciudad_id,
        "estado": "ACTIVO"
    })
    assert res_s1.status_code == 200
    sucursal_1 = res_s1.json()

    res_s2 = await client.post("/api/v1/sucursales/", headers=admin_headers, json={
        "nombre": "Sucursal Encargado Beta",
        "direccion": "Av. Beta 456",
        "telefono": "70000002",
        "ciudad_id": ciudad_id,
        "estado": "ACTIVO"
    })
    assert res_s2.status_code == 200
    sucursal_2 = res_s2.json()

    # 2. Crear categoría, producto y variante
    res_cat = await client.post("/api/v1/categorias/", headers=admin_headers, json={
        "nombre": "Categoría Enc Test",
        "descripcion": "Categoría para prueba"
    })
    assert res_cat.status_code == 200
    cat_id = res_cat.json()["id"]

    res_prod = await client.post("/api/v1/productos/", headers=admin_headers, json={
        "sku": "CAM-DEN-001",
        "nombre": "Camisa Denim Test",
        "descripcion": "Camisa para prueba",
        "precio": 150.0,
        "categoria_id": cat_id,
        "genero": "UNISEX"
    })
    assert res_prod.status_code == 200
    prod_id = res_prod.json()["id"]

    # Crear variante (crea inventario en todas las sucursales activas)
    res_talla = await client.post("/api/v1/tallas/", headers=admin_headers, json={"nombre": "Talla M Test", "valor": "M"})
    talla_id = res_talla.json()["id"]

    res_color = await client.post("/api/v1/colores/", headers=admin_headers, json={"nombre": "Azul Denim Test", "codigo_hex": "#1E3A8A"})
    color_id = res_color.json()["id"]

    res_var = await client.post(f"/api/v1/productos/{prod_id}/variantes", headers=admin_headers, json={
        "talla_id": talla_id,
        "color_id": color_id,
        "sku_variante": "CAM-DEN-M-01",
        "precio_variante": 150.0
    })
    assert res_var.status_code == 200

    # 3. Crear usuario Encargado y asignarle a sucursal 1
    encargado_payload = {
        "nombre": "Mario",
        "apellido": "Encargado",
        "correo": "mario.encargado@fashionstore.com",
        "telefono": "71122334",
        "contrasena": "Encargado123!",
        "estado": "ACTIVO",
        "sucursal_id": sucursal_1["id"]
    }
    res_user = await client.post("/api/v1/users/?rol=Encargado", headers=admin_headers, json=encargado_payload)
    assert res_user.status_code == 200
    usuario_enc = res_user.json()

    # Iniciar sesión como Encargado
    login_resp = await client.post("/api/v1/auth/login", json={
        "correo": "mario.encargado@fashionstore.com",
        "contrasena": "Encargado123!"
    })
    assert login_resp.status_code == 200
    enc_token = login_resp.json()["access_token"]
    enc_headers = {"Authorization": f"Bearer {enc_token}"}

    # 4. Verificar perfil /auth/me del Encargado
    me_resp = await client.get("/api/v1/auth/me", headers=enc_headers)
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["sucursal_id"] == sucursal_1["id"]
    assert me_data["sucursal_nombre"] == sucursal_1["nombre"]

    # 5. Listar inventario como Encargado (GET /api/v1/inventario/)
    # Solo debe retornar inventario perteneciente a sucursal_1
    inv_list_resp = await client.get("/api/v1/inventario/", headers=enc_headers)
    assert inv_list_resp.status_code == 200
    inv_list = inv_list_resp.json()
    assert len(inv_list) > 0
    for inv in inv_list:
        assert inv["sucursal_id"] == sucursal_1["id"]

    # 6. Intentar acceder al inventario de sucursal 2 (debe dar 403 Forbidden)
    res_suc2 = await client.get(f"/api/v1/inventario/sucursal/{sucursal_2['id']}", headers=enc_headers)
    assert res_suc2.status_code == 403

    # 7. Acceder al inventario de su propia sucursal 1 (debe dar 200 OK)
    res_suc1 = await client.get(f"/api/v1/inventario/sucursal/{sucursal_1['id']}", headers=enc_headers)
    assert res_suc1.status_code == 200
    assert all(i["sucursal_id"] == sucursal_1["id"] for i in res_suc1.json())
