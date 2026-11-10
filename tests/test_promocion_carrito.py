"""
[EXAMEN-2] Tests de integración: la campaña promocional se aplica DE VERDAD
en carrito y checkout (antes solo se mostraba en catálogo).

Reglas probadas:
1. El descuento PORCENTAJE vigente se aplica automáticamente al carrito
   (por producto o por categoría, el mayor % corresponde).
2. No acumulable: aplicar un cupón sobre un carrito con campaña activa -> 400.
3. La orden hereda el descuento de campaña en `descuentos` y el total real.
"""
import pytest
from httpx import AsyncClient
from datetime import datetime, timezone, timedelta


@pytest.mark.asyncio
async def test_promocion_se_aplica_en_carrito_y_orden(client: AsyncClient, admin_auth_tokens: dict):
    ts = int(datetime.now().timestamp())
    headers_admin = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}

    # ---- Cliente ---------------------------------------------------------
    res_reg = await client.post("/api/v1/auth/register", json={
        "nombre": "Camila",
        "apellido": "Test Promo",
        "correo": f"camila_promo_{ts}@test.com",
        "telefono": "70000001",
        "nit_ci": f"CI-{ts}",
        "contrasena": "Cliente123!"
    })
    assert res_reg.status_code == 200, res_reg.text
    headers_cliente = {"Authorization": f"Bearer {res_reg.json()['access_token']}"}

    # ---- Catálogo: 2 categorías, producto EN OFERTA y producto FUERA ------
    res_cat = await client.get("/api/v1/categorias/", headers=headers_admin)
    cats = res_cat.json()
    cat_oferta = cats[0]["id"]
    cat_normal = cats[1]["id"] if len(cats) > 1 else cats[0]["id"]

    res_tallas = await client.get("/api/v1/tallas/", headers=headers_admin)
    talla_id = res_tallas.json()[0]["id"]
    res_colores = await client.get("/api/v1/colores/", headers=headers_admin)
    color_id = res_colores.json()[0]["id"]

    res_prod_oferta = await client.post("/api/v1/productos/", json={
        "sku": f"PROD-PROMO-{ts}",
        "nombre": "Polera Oferta",
        "precio": 100.00,
        "categoria_id": cat_oferta
    }, headers=headers_admin)
    prod_oferta = res_prod_oferta.json()["id"]

    res_var = await client.post(
        f"/api/v1/productos/{prod_oferta}/variantes?cantidad_inicial=30",
        json={
            "producto_id": prod_oferta,
            "talla_id": talla_id,
            "color_id": color_id,
            "sku_variante": f"SKU-PROMO-{ts}",
            "precio_variante": 100.00
        },
        headers=headers_admin
    )
    assert res_var.status_code in (200, 201), res_var.text
    var_promo = res_var.json()["id"]

    res_prod_normal = await client.post("/api/v1/productos/", json={
        "sku": f"PROD-NORM-{ts}",
        "nombre": "Accesorio Sin Oferta",
        "precio": 50.00,
        "categoria_id": cat_normal
    }, headers=headers_admin)
    prod_normal = res_prod_normal.json()["id"]
    res_var2 = await client.post(
        f"/api/v1/productos/{prod_normal}/variantes?cantidad_inicial=30",
        json={
            "producto_id": prod_normal,
            "talla_id": talla_id,
            "color_id": color_id,
            "sku_variante": f"SKU-NORM-{ts}",
            "precio_variante": 50.00
        },
        headers=headers_admin
    )
    var_normal = res_var2.json()["id"]

    # ---- Campaña vigente 50% en la categoría de oferta --------------------
    ahora = datetime.now(timezone.utc)
    res_promo = await client.post("/api/v1/promociones/", json={
        "nombre": f"Campana Carrito {ts}",
        "tipo": "PORCENTAJE",
        "valor": 50.0,
        "fecha_inicio": (ahora - timedelta(minutes=1)).isoformat(),
        "fecha_fin": (ahora + timedelta(days=7)).isoformat(),
        "estado": "ACTIVA",
        "producto_ids": [],
        "categoria_ids": [cat_oferta]
    }, headers=headers_admin)
    assert res_promo.status_code == 201, res_promo.text

    # ---- Carrito: 2x100 (en oferta) + 1x50 (fuera) = Bs. 250 --------------
    res_add = await client.post("/api/v1/carrito/items", json={
        "variante_producto_id": var_promo, "cantidad": 2
    }, headers=headers_cliente)
    assert res_add.status_code == 200, res_add.text
    carrito = res_add.json()
    assert float(carrito["subtotal"]) == 200.00
    # La campaña se descuenta al instante: 50% de 200 = Bs. 100
    assert float(carrito["descuento_promocion"]) == 100.00
    assert float(carrito["descuento_aplicado"]) == 100.00
    assert float(carrito["total"]) == 100.00
    assert carrito["promocion_nombre"]

    res_add2 = await client.post("/api/v1/carrito/items", json={
        "variante_producto_id": var_normal, "cantidad": 1
    }, headers=headers_cliente)
    assert res_add2.status_code == 200
    carrito = res_add2.json()
    assert float(carrito["subtotal"]) == 250.00
    # El descuento solo aplica a los productos de las categorías de la campaña
    assert float(carrito["descuento_promocion"]) == 100.00
    assert float(carrito["total"]) == 150.00

    # ---- No acumulable: cupón sobre carrito con campaña -> 400 ------------
    res_cup = await client.post("/api/v1/carrito/aplicar-cupon", json={
        "codigo": f"CUPON_PROHIBIDO_{ts}"
    }, headers=headers_cliente)
    assert res_cup.status_code == 400, res_cup.text

    # ---- La orden hereda el descuento real de la campaña ------------------
    res_ord = await client.post("/api/v1/ordenes/", json={
        "tipo": "DIGITAL",
        "direccion_envio": "Av. Barrientos 456"
    }, headers=headers_cliente)
    assert res_ord.status_code == 201, res_ord.text
    orden = res_ord.json()
    assert float(orden["descuentos"]) == 100.00
    assert float(orden["total"]) == 150.00
    assert orden["cupon_id"] is None  # no se consumió ningún cupón

    # ---- Cleanup: desactivar la campaña para no filtrar a otros tests -----
    await client.patch(
        f"/api/v1/promociones/{res_promo.json()['id']}/estado",
        json={"estado": "INACTIVA"},
        headers=headers_admin
    )


@pytest.mark.asyncio
async def test_cupon_funciona_sin_campana(client: AsyncClient, admin_auth_tokens: dict):
    """Sin promo aplicable, el flujo clásico de cupón queda intacto."""
    ts = int(datetime.now().timestamp())
    headers_admin = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}

    res_reg = await client.post("/api/v1/auth/register", json={
        "nombre": "Diego",
        "apellido": "Cupon Puro",
        "correo": f"diego_cupon_{ts}@test.com",
        "telefono": "70000002",
        "nit_ci": f"CI-{ts}",
        "contrasena": "Cliente123!"
    })
    assert res_reg.status_code == 200, res_reg.text
    headers_cliente = {"Authorization": f"Bearer {res_reg.json()['access_token']}"}

    cod_cupon = f"CUPON_TEST_{ts}"
    res_cup_create = await client.post("/api/v1/cupones/", json={
        "codigo": cod_cupon,
        "tipo": "PORCENTAJE",
        "valor": 20.00,
        "descripcion": "20% sin campana",
        "fecha_inicio": datetime.now(timezone.utc).isoformat(),
        "fecha_fin": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "usos_maximos": 100,
        "monto_minimo": 10.00,
        "estado": "ACTIVO"
    }, headers=headers_admin)
    assert res_cup_create.status_code == 201, res_cup_create.text

    # Producto en categoría SIN campañas activas (todas las categorías listadas
    # por el seed de tests no tienen promociones vigentes creadas aquí).
    res_cat = await client.get("/api/v1/categorias/", headers=headers_admin)
    cats = res_cat.json()
    cat_normal = cats[-1]["id"]

    res_tallas = await client.get("/api/v1/tallas/", headers=headers_admin)
    talla_id = res_tallas.json()[0]["id"]
    res_colores = await client.get("/api/v1/colores/", headers=headers_admin)
    color_id = res_colores.json()[0]["id"]

    res_prod = await client.post("/api/v1/productos/", json={
        "sku": f"PROD-SINPROMO-{ts}",
        "nombre": "Producto Cupon",
        "precio": 80.00,
        "categoria_id": cat_normal
    }, headers=headers_admin)
    prod_id = res_prod.json()["id"]
    res_var = await client.post(
        f"/api/v1/productos/{prod_id}/variantes?cantidad_inicial=20",
        json={
            "producto_id": prod_id,
            "talla_id": talla_id,
            "color_id": color_id,
            "sku_variante": f"SKU-SINPROMO-{ts}",
            "precio_variante": 80.00
        },
        headers=headers_admin
    )
    assert res_var.status_code in (200, 201), res_var.text
    variante_id = res_var.json()["id"]

    await client.post("/api/v1/carrito/items", json={
        "variante_producto_id": variante_id, "cantidad": 1
    }, headers=headers_cliente)

    res_aplicar = await client.post("/api/v1/carrito/aplicar-cupon", json={
        "codigo": cod_cupon
    }, headers=headers_cliente)
    assert res_aplicar.status_code == 200, res_aplicar.text
    carrito = res_aplicar.json()
    assert float(carrito["descuento_aplicado"]) == 16.00  # 20% de 80
    assert float(carrito["total"]) == 64.00
