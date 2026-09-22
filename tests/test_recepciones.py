"""
Pruebas de Recepción Directa por Proveedor (Opción A - RF06).
Valida: crear recepción suma stock + movimiento RECEPCION + trazabilidad por proveedor.
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_recepcion_proveedor_suma_stock(client: AsyncClient, admin_headers: dict):
    # Ciudad / sucursal
    res_c = await client.get("/api/v1/ciudades/", headers=admin_headers)
    ciudades = res_c.json()
    if not ciudades:
        res_c = await client.post("/api/v1/ciudades/", headers=admin_headers, json={"nombre": "Ciudad Recep Test"})
        assert res_c.status_code == 200
        ciudad_id = res_c.json()["id"]
    else:
        ciudad_id = ciudades[0]["id"]

    res_s = await client.post("/api/v1/sucursales/", headers=admin_headers, json={
        "nombre": "Sucursal Recepción Test",
        "direccion": "Av. Recep 123",
        "telefono": "70000999",
        "ciudad_id": ciudad_id,
        "estado": "ACTIVO"
    })
    assert res_s.status_code == 200
    sucursal_id = res_s.json()["id"]

    # Proveedor
    res_p = await client.post("/api/v1/proveedores/", headers=admin_headers, json={
        "nombre": "Proveedor Recep SA",
        "nit": "999888777",
        "contacto": "Contacto Test",
        "telefono": "70000000",
        "correo": "recep@test.com"
    })
    assert res_p.status_code in (200, 201)
    proveedor_id = res_p.json()["id"]

    # Producto + variante
    res_cat = await client.post("/api/v1/categorias/", headers=admin_headers, json={"nombre": "Cat Recep Test"})
    assert res_cat.status_code == 200
    cat_id = res_cat.json()["id"]

    res_prod = await client.post("/api/v1/productos/", headers=admin_headers, json={
        "sku": "REC-TEST-001",
        "nombre": "Prenda Recep Test",
        "precio": 100.0,
        "categoria_id": cat_id,
        "genero": "UNISEX",
        "proveedor_id": proveedor_id
    })
    assert res_prod.status_code == 200
    prod_id = res_prod.json()["id"]

    res_t = await client.post("/api/v1/tallas/", headers=admin_headers, json={"valor": "M"})
    talla_id = res_t.json()["id"]
    res_co = await client.post("/api/v1/colores/", headers=admin_headers, json={"nombre": "Rojo Recep Test"})
    color_id = res_co.json()["id"]

    res_v = await client.post(f"/api/v1/productos/{prod_id}/variantes", headers=admin_headers, json={
        "talla_id": talla_id,
        "color_id": color_id,
        "sku_variante": "REC-TEST-M-R",
    })
    assert res_v.status_code == 200
    variante_id = res_v.json()["id"]

    # Stock antes
    res_inv_antes = await client.get("/api/v1/inventario/", headers=admin_headers, params={"sucursal_id": sucursal_id, "producto_id": prod_id})
    assert res_inv_antes.status_code == 200
    inv_antes = next((i for i in res_inv_antes.json() if i["variante_producto_id"] == variante_id), None)
    cant_antes = inv_antes["cantidad"] if inv_antes else 0

    # Crear recepción
    res_r = await client.post("/api/v1/recepciones/", headers=admin_headers, json={
        "proveedor_id": proveedor_id,
        "sucursal_id": sucursal_id,
        "nro_factura": "FAC-TEST-001",
        "observaciones": "Ingreso test",
        "items": [{"variante_producto_id": variante_id, "cantidad": 10, "costo_unitario": 45.5}]
    })
    assert res_r.status_code == 200, res_r.text
    recep = res_r.json()
    assert recep["numero"].startswith("REC-")
    assert recep["total_unidades"] == 10
    assert float(recep["total_costo"]) == 455.0
    assert recep["proveedor_id"] == proveedor_id
    assert len(recep["detalles"]) == 1

    # Stock después (+10)
    res_inv_des = await client.get("/api/v1/inventario/", headers=admin_headers, params={"sucursal_id": sucursal_id, "producto_id": prod_id})
    inv_des = next((i for i in res_inv_des.json() if i["variante_producto_id"] == variante_id), None)
    assert inv_des is not None
    assert inv_des["cantidad"] == cant_antes + 10

    # Trazabilidad: historial por proveedor + detalle
    res_hist = await client.get(f"/api/v1/proveedores/{proveedor_id}/recepciones", headers=admin_headers)
    assert res_hist.status_code == 200
    assert any(r["id"] == recep["id"] for r in res_hist.json())

    res_det = await client.get(f"/api/v1/recepciones/{recep['id']}", headers=admin_headers)
    assert res_det.status_code == 200
    assert res_det.json()["numero"] == recep["numero"]

    # Movimiento RECEPCION registrado
    res_mov = await client.get("/api/v1/inventario/movimientos", headers=admin_headers, params={"inventario_id": inv_des["id"], "tipo": "RECEPCION", "limit": 50})
    assert res_mov.status_code == 200
    assert any(m["cantidad"] == 10 for m in res_mov.json())


@pytest.mark.asyncio
async def test_recepcion_rechaza_variante_inexistente(client: AsyncClient, admin_headers: dict):
    res_c = await client.get("/api/v1/ciudades/", headers=admin_headers)
    ciudad_id = res_c.json()[0]["id"]
    res_s = await client.post("/api/v1/sucursales/", headers=admin_headers, json={
        "nombre": "Sucursal Recep Neg", "direccion": "Av N", "telefono": "70000998",
        "ciudad_id": ciudad_id, "estado": "ACTIVO"
    })
    sucursal_id = res_s.json()["id"]
    res_p = await client.post("/api/v1/proveedores/", headers=admin_headers, json={
        "nombre": "Prov Neg", "nit": "111222999", "contacto": "c", "telefono": "1", "correo": "neg@test.com"
    })
    proveedor_id = res_p.json()["id"]

    res_r = await client.post("/api/v1/recepciones/", headers=admin_headers, json={
        "proveedor_id": proveedor_id,
        "sucursal_id": sucursal_id,
        "items": [{"variante_producto_id": 999999999, "cantidad": 5}]
    })
    assert res_r.status_code == 404
