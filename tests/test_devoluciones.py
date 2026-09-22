"""
Tests de integración para el Caso de Uso CU28: Gestionar Devoluciones y Cambios.
"""
import pytest
import uuid
from httpx import AsyncClient
from datetime import datetime


def _uid():
    return uuid.uuid4().hex[:8]


async def _asegurar_producto_con_dos_variantes(client: AsyncClient, admin_headers: dict) -> tuple[int, int, int]:
    # Crear o buscar categoría
    res_cats = await client.get("/api/v1/categorias/")
    cats = res_cats.json()
    if not cats:
        cat_resp = await client.post("/api/v1/categorias/", headers=admin_headers, json={
            "nombre": "Categoría Devoluciones",
            "descripcion": "Descripción test"
        })
        cat_id = cat_resp.json()["id"]
    else:
        cat_id = cats[0]["id"]

    uid = _uid()
    prod_resp = await client.post("/api/v1/productos/", headers=admin_headers, json={
        "sku": f"PROD-DEV-{uid}",
        "nombre": "Producto Devolución Test",
        "precio": "120.00",
        "categoria_id": cat_id
    })
    prod_id = prod_resp.json()["id"]

    tallas_resp = await client.get("/api/v1/tallas/")
    tallas = tallas_resp.json()
    talla1 = tallas[0]["id"] if tallas else 1
    talla2 = tallas[1]["id"] if len(tallas) > 1 else talla1

    colores_resp = await client.get("/api/v1/colores/")
    colores = colores_resp.json()
    color1 = colores[0]["id"] if colores else 1
    color2 = colores[1]["id"] if len(colores) > 1 else color1

    v1_resp = await client.post(
        f"/api/v1/productos/{prod_id}/variantes?cantidad_inicial=20",
        headers=admin_headers,
        json={
            "talla_id": talla1,
            "color_id": color1,
            "sku_variante": f"SKU-DEV-1-{uid}",
            "precio_variante": "120.00"
        }
    )
    v1_id = v1_resp.json()["id"]

    v2_resp = await client.post(
        f"/api/v1/productos/{prod_id}/variantes?cantidad_inicial=20",
        headers=admin_headers,
        json={
            "talla_id": talla2,
            "color_id": color2,
            "sku_variante": f"SKU-DEV-2-{uid}",
            "precio_variante": "120.00"
        }
    )
    v2_id = v2_resp.json()["id"]

    return prod_id, v1_id, v2_id


async def _crear_orden_para_devolucion(client: AsyncClient, admin_headers: dict) -> tuple[dict, int, int, int]:
    """Crea un cliente, realiza una compra y la marca como PAGADO."""
    uid = _uid()
    correo = f"cdev_{uid}@test.com"
    contrasena = "Dev12345!"

    # 1. Registrar
    res_reg = await client.post(
        "/api/v1/auth/register",
        json={
            "nombre": "Mario",
            "apellido": "Bros",
            "correo": correo,
            "contrasena": contrasena,
            "telefono": "55558888",
            "nit_ci": f"NCI-{uid}",
            "direccion_envio": "Zona 1, Guatemala"
        }
    )
    assert res_reg.status_code == 200, f"Register failed: {res_reg.text}"

    # 2. Login
    res_log = await client.post("/api/v1/auth/login", json={"correo": correo, "contrasena": contrasena})
    assert res_log.status_code == 200, f"Login failed: {res_log.text}"
    token = res_log.json()["access_token"]
    cliente_headers = {"Authorization": f"Bearer {token}"}

    # 3. Obtener producto y dos variantes para cambios
    _, variante1_id, variante2_id = await _asegurar_producto_con_dos_variantes(client, admin_headers)

    # 4. Comprar variante 1
    await client.post("/api/v1/carrito/items", json={"variante_producto_id": variante1_id, "cantidad": 2}, headers=cliente_headers)
    res_orden = await client.post("/api/v1/ordenes/", json={"tipo": "DIGITAL", "direccion_envio": "Zona 1, Guatemala"}, headers=cliente_headers)
    assert res_orden.status_code in (200, 201), f"Create order failed: {res_orden.text}"
    orden_id = res_orden.json()["id"]
    detalle_orden_id = res_orden.json()["detalles"][0]["id"]

    # 5. Pagar orden
    await client.patch(f"/api/v1/ordenes/{orden_id}/estado", json={"estado": "PAGADO"}, headers=admin_headers)

    return cliente_headers, orden_id, detalle_orden_id, variante2_id


@pytest.mark.asyncio
async def test_01_crear_solicitud_devolucion_y_rechazar(client: AsyncClient, admin_headers: dict):
    cliente_headers, orden_id, detalle_orden_id, variante2_id = await _crear_orden_para_devolucion(client, admin_headers)

    # 1. Crear solicitud de DEVOLUCIÓN
    sol_data = {
        "orden_id": orden_id,
        "tipo": "DEVOLUCION",
        "motivo": "TALLA_INCORRECTA",
        "motivo_detalle": "Me quedó demasiado grande",
        "items": [
            {"detalle_orden_id": detalle_orden_id, "cantidad": 1}
        ]
    }
    res_sol = await client.post("/api/v1/devoluciones/", json=sol_data, headers=cliente_headers)
    assert res_sol.status_code == 201, f"Error: {res_sol.text}"
    solicitud = res_sol.json()
    assert solicitud["numero_solicitud"].startswith("DEV-")
    assert solicitud["estado"] == "PENDIENTE"
    sol_id = solicitud["id"]

    # 2. Intento de crear duplicado sobre el mismo detalle -> 409
    res_dup = await client.post("/api/v1/devoluciones/", json=sol_data, headers=cliente_headers)
    assert res_dup.status_code == 409

    # 3. Listar mis solicitudes
    res_mis = await client.get("/api/v1/devoluciones/mis-solicitudes", headers=cliente_headers)
    assert res_mis.status_code == 200
    assert any(s["id"] == sol_id for s in res_mis.json())

    # 4. Listar para staff
    res_staff = await client.get("/api/v1/devoluciones/", headers=admin_headers)
    assert res_staff.status_code == 200
    assert any(s["id"] == sol_id for s in res_staff.json())

    # 5. Rechazar como staff (con observaciones)
    rev_data = {
        "accion": "RECHAZAR",
        "observaciones": "Prenda entregada sin etiquetas originales y con signos de uso"
    }
    res_rev = await client.patch(f"/api/v1/devoluciones/{sol_id}/revisar", json=rev_data, headers=admin_headers)
    assert res_rev.status_code == 200
    assert res_rev.json()["estado"] == "RECHAZADA"
    assert res_rev.json()["observaciones_staff"] == rev_data["observaciones"]


@pytest.mark.asyncio
async def test_02_flujo_aprobar_cambio_prenda(client: AsyncClient, admin_headers: dict):
    # Crear nueva compra
    cliente_headers, orden_id, detalle_orden_id, variante_cambio_id = await _crear_orden_para_devolucion(client, admin_headers)

    # 1. Crear solicitud de CAMBIO
    sol_data = {
        "orden_id": orden_id,
        "tipo": "CAMBIO",
        "motivo": "COLOR_INCORRECTO",
        "motivo_detalle": "Deseo cambiar de color",
        "items": [
            {
                "detalle_orden_id": detalle_orden_id,
                "cantidad": 1,
                "variante_cambio_id": variante_cambio_id
            }
        ]
    }
    res_sol = await client.post("/api/v1/devoluciones/", json=sol_data, headers=cliente_headers)
    assert res_sol.status_code == 201
    sol_id = res_sol.json()["id"]

    # 2. Aprobar cambio como staff
    rev_data = {
        "accion": "APROBAR",
        "observaciones": "Prenda en perfecto estado con etiquetas"
    }
    res_rev = await client.patch(f"/api/v1/devoluciones/{sol_id}/revisar", json=rev_data, headers=admin_headers)
    assert res_rev.status_code == 200
    resuelto = res_rev.json()
    assert resuelto["estado"] == "COMPLETADA"


@pytest.mark.asyncio
async def test_03_flujo_aprobar_devolucion_reintegro(client: AsyncClient, admin_headers: dict):
    cliente_headers, orden_id, detalle_orden_id, _ = await _crear_orden_para_devolucion(client, admin_headers)

    # 1. Crear solicitud de DEVOLUCIÓN
    sol_data = {
        "orden_id": orden_id,
        "tipo": "DEVOLUCION",
        "motivo": "DEFECTO_FABRICA",
        "motivo_detalle": "Costura deshilachada en la manga",
        "items": [
            {"detalle_orden_id": detalle_orden_id, "cantidad": 1}
        ]
    }
    res_sol = await client.post("/api/v1/devoluciones/", json=sol_data, headers=cliente_headers)
    assert res_sol.status_code == 201
    sol_id = res_sol.json()["id"]

    # 2. Aprobar devolución
    res_rev = await client.patch(
        f"/api/v1/devoluciones/{sol_id}/revisar",
        json={"accion": "APROBAR", "observaciones": "Defecto verificado, procede reembolso"},
        headers=admin_headers
    )
    assert res_rev.status_code == 200
    assert res_rev.json()["estado"] in ["COMPLETADA", "PENDIENTE_REEMBOLSO"]
