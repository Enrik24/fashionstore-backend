"""
Tests de integración para el Caso de Uso CU24: Gestionar Promociones.
"""
import pytest
from httpx import AsyncClient
from datetime import datetime, timezone, timedelta


@pytest.mark.asyncio
async def test_01_crud_promociones_admin(client: AsyncClient, admin_headers: dict):
    ahora = datetime.now(timezone.utc)
    inicio = (ahora - timedelta(days=1)).isoformat()
    fin = (ahora + timedelta(days=10)).isoformat()

    # 1. Crear promoción con advertencia (sin productos ni categorías)
    promo_data = {
        "nombre": "Gran Oferta de Verano",
        "descripcion": "Descuento especial en toda la tienda",
        "tipo": "PORCENTAJE",
        "valor": 20.0,
        "fecha_inicio": inicio,
        "fecha_fin": fin,
        "condiciones": "Aplica a compras seleccionadas",
        "estado": "ACTIVA",
        "producto_ids": [],
        "categoria_ids": []
    }
    res_crear = await client.post("/api/v1/promociones/", json=promo_data, headers=admin_headers)
    assert res_crear.status_code == 201, f"Error: {res_crear.text}"
    created = res_crear.json()
    assert created["nombre"] == "Gran Oferta de Verano"
    assert created["tipo"] == "PORCENTAJE"
    assert created["valor"] == "20.00" or float(created["valor"]) == 20.0
    assert created["advertencia"] is not None
    promo_id = created["id"]

    # 2. Obtener por ID
    res_get = await client.get(f"/api/v1/promociones/{promo_id}", headers=admin_headers)
    assert res_get.status_code == 200
    assert res_get.json()["id"] == promo_id

    # 3. Listar promociones
    res_list = await client.get("/api/v1/promociones/", headers=admin_headers)
    assert res_list.status_code == 200
    items = res_list.json()
    assert any(p["id"] == promo_id for p in items)

    # 4. Actualizar promoción
    res_put = await client.put(
        f"/api/v1/promociones/{promo_id}",
        json={"nombre": "Gran Oferta de Verano 2026", "valor": 25.0},
        headers=admin_headers
    )
    assert res_put.status_code == 200
    assert res_put.json()["nombre"] == "Gran Oferta de Verano 2026"

    # 5. Cambiar estado a INACTIVA
    res_patch = await client.patch(
        f"/api/v1/promociones/{promo_id}/estado",
        json={"estado": "INACTIVA"},
        headers=admin_headers
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["estado"] == "INACTIVA"

    # 6. Eliminar promoción
    res_del = await client.delete(f"/api/v1/promociones/{promo_id}", headers=admin_headers)
    assert res_del.status_code == 200


@pytest.mark.asyncio
async def test_02_validacion_fechas_y_valores(client: AsyncClient, admin_headers: dict):
    ahora = datetime.now(timezone.utc)
    # Fecha fin anterior a inicio -> Error 422
    invalid_data = {
        "nombre": "Promo Fechas Inválidas",
        "tipo": "PORCENTAJE",
        "valor": 15.0,
        "fecha_inicio": (ahora + timedelta(days=10)).isoformat(),
        "fecha_fin": (ahora + timedelta(days=5)).isoformat(),
        "estado": "ACTIVA"
    }
    res = await client.post("/api/v1/promociones/", json=invalid_data, headers=admin_headers)
    assert res.status_code == 422 or res.status_code == 400

    # Porcentaje fuera de rango (>100) -> Error 422
    invalid_percent = {
        "nombre": "Promo Porcentaje Inválido",
        "tipo": "PORCENTAJE",
        "valor": 150.0,
        "fecha_inicio": ahora.isoformat(),
        "fecha_fin": (ahora + timedelta(days=5)).isoformat(),
        "estado": "ACTIVA"
    }
    res_pct = await client.post("/api/v1/promociones/", json=invalid_percent, headers=admin_headers)
    assert res_pct.status_code == 422 or res_pct.status_code == 400


@pytest.mark.asyncio
async def test_03_solapamiento_promociones_activas(client: AsyncClient, admin_headers: dict):
    ahora = datetime.now(timezone.utc)
    inicio = (ahora - timedelta(days=1)).isoformat()
    fin = (ahora + timedelta(days=15)).isoformat()

    # 1. Crear primera promoción activa sobre categoría 1
    p1 = {
        "nombre": "Promo A - Categoria 1",
        "tipo": "PORCENTAJE",
        "valor": 10.0,
        "fecha_inicio": inicio,
        "fecha_fin": fin,
        "estado": "ACTIVA",
        "categoria_ids": [1]
    }
    res1 = await client.post("/api/v1/promociones/", json=p1, headers=admin_headers)
    assert res1.status_code == 201
    id1 = res1.json()["id"]

    # 2. Intentar crear segunda promoción activa que se solape en fechas y en la misma categoría 1 -> 409
    p2 = {
        "nombre": "Promo B - Conflicto Categoria 1",
        "tipo": "MONTO_FIJO",
        "valor": 50.0,
        "fecha_inicio": (ahora + timedelta(days=2)).isoformat(),
        "fecha_fin": (ahora + timedelta(days=12)).isoformat(),
        "estado": "ACTIVA",
        "categoria_ids": [1]
    }
    res2 = await client.post("/api/v1/promociones/", json=p2, headers=admin_headers)
    assert res2.status_code == 409, f"Esperaba 409 de solapamiento pero recibí: {res2.status_code}"

    # Limpiar
    await client.delete(f"/api/v1/promociones/{id1}", headers=admin_headers)


@pytest.mark.asyncio
async def test_04_endpoint_publico_promociones_activas(client: AsyncClient, admin_headers: dict):
    ahora = datetime.now(timezone.utc)
    # Crear una promoción activa vigente
    p = {
        "nombre": "Promo Pública Activa",
        "tipo": "ENVIO_GRATIS",
        "fecha_inicio": (ahora - timedelta(days=2)).isoformat(),
        "fecha_fin": (ahora + timedelta(days=5)).isoformat(),
        "estado": "ACTIVA"
    }
    res_crear = await client.post("/api/v1/promociones/", json=p, headers=admin_headers)
    assert res_crear.status_code == 201
    pid = res_crear.json()["id"]

    # Consultar endpoint público sin token
    res_pub = await client.get("/api/v1/public/promociones/activas")
    assert res_pub.status_code == 200
    activas = res_pub.json()
    assert isinstance(activas, list)
    assert any(item["id"] == pid for item in activas)

    # Limpiar
    await client.delete(f"/api/v1/promociones/{pid}", headers=admin_headers)
