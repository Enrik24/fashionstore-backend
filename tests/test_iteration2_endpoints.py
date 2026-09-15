"""
Tests completos para los endpoints de la Iteración 2:
- Catálogo Público y Disponibilidad (CU13, CU15)
- Cupones de Descuento (CU18)
- Carrito de Compras (CU10)
- Órdenes y Comprobantes (CU11, CU12)
- Ventas Presenciales en Caja (CU09, CU14)
- Reservas de Prendas en Sucursal (CU16, CU17)
- Pasarelas de Pago Digital Stripe / PayPal (CU12)
"""
import pytest
from httpx import AsyncClient
from datetime import datetime, timedelta, timezone


@pytest.mark.asyncio
async def test_01_public_catalog_endpoints(client: AsyncClient):
    """Prueba los endpoints públicos del catálogo sin requerir autenticación."""
    # 1. Catálogo público
    res = await client.get("/api/v1/public/catalogo")
    assert res.status_code == 200, f"Error: {res.text}"
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert "pagina" in data

    # 2. Búsqueda de productos
    res_b = await client.get("/api/v1/public/productos/buscar?q=Camisa")
    assert res_b.status_code == 200

    # 3. Productos populares
    res_p = await client.get("/api/v1/public/productos/populares?limit=5")
    assert res_p.status_code == 200
    assert isinstance(res_p.json(), list)


@pytest.mark.asyncio
async def test_02_disponibilidad_por_sucursal(client: AsyncClient, admin_auth_tokens: dict):
    """Prueba el endpoint público de disponibilidad por sucursal (CU10).

    Verifica que la respuesta tenga la estructura ``DisponibilidadProductoResponse``
    que el frontend espera: objeto con ``disponibilidad`` (lista agregada por
    sucursal) en lugar de un arreglo plano.
    """
    headers = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}

    # 1. Crear ciudad y sucursal
    res_ciud = await client.post(
        "/api/v1/ciudades/",
        json={"nombre": f"Ciudad Disp {int(datetime.now().timestamp())}"},
        headers=headers
    )
    assert res_ciud.status_code in (200, 201), f"Error: {res_ciud.text}"
    ciudad_id = res_ciud.json()["id"]

    res_suc = await client.post("/api/v1/sucursales/", json={
        "nombre": f"Sucursal Norte {int(datetime.now().timestamp())}",
        "direccion": "Calle 10-20",
        "telefono": "22334455",
        "ciudad_id": ciudad_id
    }, headers=headers)
    assert res_suc.status_code in (200, 201), f"Error: {res_suc.text}"

    # 2. Crear producto y variante con stock inicial (crea inventario en toda sucursal activa)
    res_cat = await client.get("/api/v1/categorias/", headers=headers)
    categoria_id = res_cat.json()[0]["id"]

    sku_prod = f"PROD-DISP-{int(datetime.now().timestamp())}"
    res_prod = await client.post("/api/v1/productos/", json={
        "sku": sku_prod,
        "nombre": "Chaqueta Aviadora",
        "descripcion": "Chaqueta de cuero sintético",
        "precio": 350.00,
        "categoria_id": categoria_id
    }, headers=headers)
    assert res_prod.status_code in (200, 201), f"Error: {res_prod.text}"
    producto_id = res_prod.json()["id"]

    res_tallas = await client.get("/api/v1/tallas/", headers=headers)
    talla_id = res_tallas.json()[0]["id"]

    res_colores = await client.get("/api/v1/colores/", headers=headers)
    color_id = res_colores.json()[0]["id"]

    res_var = await client.post(
        f"/api/v1/productos/{producto_id}/variantes?cantidad_inicial=25",
        json={
            "producto_id": producto_id,
            "talla_id": talla_id,
            "color_id": color_id,
            "sku_variante": f"SKU-VAR-DISP-{int(datetime.now().timestamp())}",
            "precio_variante": 350.00
        },
        headers=headers
    )
    assert res_var.status_code in (200, 201), f"Error: {res_var.text}"

    # 3. Consultar disponibilidad pública
    res_disp = await client.get(f"/api/v1/public/disponibilidad/{producto_id}")
    assert res_disp.status_code == 200, f"Error: {res_disp.text}"
    data = res_disp.json()

    # Estructura esperada por el frontend (DisponibilidadProductoResponse)
    assert data["producto_id"] == producto_id
    assert data["producto_nombre"] == "Chaqueta Aviadora"
    assert data["sku"] == sku_prod
    assert isinstance(data["disponibilidad"], list)
    assert len(data["disponibilidad"]) >= 1

    first = data["disponibilidad"][0]
    assert "sucursal_id" in first
    assert "sucursal_nombre" in first
    assert "cantidad_disponible" in first
    assert "cantidad_reservada" in first
    assert first["estado"] in {"DISPONIBLE", "RESERVADO", "AGOTADO"}

    # La sucursal creada en el test debe tener las 25 unidades
    assert any(e["cantidad_disponible"] == 25 for e in data["disponibilidad"])

    # 4. Filtrar por talla y color (CU10): responde con esos valores
    res_filt = await client.get(
        f"/api/v1/public/disponibilidad/{producto_id}",
        params={"talla_id": talla_id, "color_id": color_id}
    )
    assert res_filt.status_code == 200, f"Error: {res_filt.text}"
    filt_data = res_filt.json()
    assert filt_data["talla"] is not None
    assert filt_data["color"] is not None
    assert len(filt_data["disponibilidad"]) >= 1


@pytest.mark.asyncio
async def test_02_cupones_flujo_completo(client: AsyncClient, admin_auth_tokens: dict):
    """Prueba creación, listado y validación de cupones de descuento."""
    headers = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}
    
    ahora = datetime.now(timezone.utc)
    codigo = f"DESC20_{int(datetime.now().timestamp())}"
    cupon_data = {
        "codigo": codigo,
        "tipo": "PORCENTAJE",
        "valor": 20.00,
        "descripcion": "20% de descuento en compras mayores a $50",
        "fecha_inicio": ahora.isoformat(),
        "fecha_fin": (ahora + timedelta(days=30)).isoformat(),
        "usos_maximos": 100,
        "monto_minimo": 50.00,
        "estado": "ACTIVO"
    }
    
    # 1. Crear cupón (Admin)
    res = await client.post("/api/v1/cupones/", json=cupon_data, headers=headers)
    assert res.status_code == 201, f"Error: {res.text}"
    cupon = res.json()
    assert cupon["codigo"] == codigo

    # 2. Listar cupones (Admin)
    res_l = await client.get("/api/v1/cupones/", headers=headers)
    assert res_l.status_code == 200
    assert any(c["codigo"] == codigo for c in res_l.json())

    # 3. Validar cupón con subtotal suficiente
    res_v = await client.post(
        "/api/v1/cupones/validar?subtotal=100.00",
        json={"codigo": codigo},
        headers=headers
    )
    assert res_v.status_code == 200
    val_data = res_v.json()
    assert val_data["valido"] is True
    assert float(val_data["descuento_calculado"]) == 20.00


@pytest.mark.asyncio
async def test_03_carrito_y_orden_flujo(client: AsyncClient, admin_auth_tokens: dict):
    """Prueba flujo completo de carrito de compras, cupón y creación de orden."""
    headers_admin = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}

    # 1. Registrar un nuevo cliente para este test
    cliente_payload = {
        "nombre": "Mariana",
        "apellido": "Rios",
        "correo": f"mariana_{int(datetime.now().timestamp())}@test.com",
        "telefono": "72345678",
        "nit_ci": f"CI-{int(datetime.now().timestamp())}",
        "direccion_envio": "Zona 10, Ciudad de Guatemala",
        "contrasena": "Cliente123!"
    }
    res_reg = await client.post("/api/v1/auth/register", json=cliente_payload)
    assert res_reg.status_code == 200, f"Error registro: {res_reg.text}"
    token_cliente = res_reg.json()["access_token"]
    headers_cliente = {"Authorization": f"Bearer {token_cliente}"}

    # 2. Crear cupón específico para el test
    cupon_cod = f"CUPON_TEST3_{int(datetime.now().timestamp())}"
    res_cup_create = await client.post("/api/v1/cupones/", json={
        "codigo": cupon_cod,
        "tipo": "PORCENTAJE",
        "valor": 20.00,
        "descripcion": "20% desc",
        "fecha_inicio": datetime.now(timezone.utc).isoformat(),
        "fecha_fin": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "usos_maximos": 100,
        "monto_minimo": 10.00,
        "estado": "ACTIVO"
    }, headers=headers_admin)
    assert res_cup_create.status_code == 201

    # 3. Crear datos de catálogo como Admin: Ciudad, Sucursal, Categoria, Producto, Variante
    res_ciud = await client.post("/api/v1/ciudades/", json={"nombre": f"Ciudad_{int(datetime.now().timestamp())}"}, headers=headers_admin)
    ciudad_id = res_ciud.json()["id"]

    res_suc = await client.post("/api/v1/sucursales/", json={
        "nombre": f"Sucursal Centro {int(datetime.now().timestamp())}",
        "direccion": "Avenida Reforma 10-00",
        "telefono": "22334455",
        "ciudad_id": ciudad_id
    }, headers=headers_admin)
    sucursal_id = res_suc.json()["id"]

    res_cat = await client.get("/api/v1/categorias/", headers=headers_admin)
    categoria_id = res_cat.json()[0]["id"]

    res_prod = await client.post("/api/v1/productos/", json={
        "sku": f"PROD-CART-{int(datetime.now().timestamp())}",
        "nombre": "Vestido Elegante",
        "descripcion": "Vestido para eventos formales",
        "precio": 120.00,
        "categoria_id": categoria_id
    }, headers=headers_admin)
    producto_id = res_prod.json()["id"]

    res_tallas = await client.get("/api/v1/tallas/", headers=headers_admin)
    talla_id = res_tallas.json()[0]["id"]

    res_colores = await client.get("/api/v1/colores/", headers=headers_admin)
    color_id = res_colores.json()[0]["id"]

    res_var = await client.post(
        f"/api/v1/productos/{producto_id}/variantes?cantidad_inicial=30",
        json={
            "producto_id": producto_id,
            "talla_id": talla_id,
            "color_id": color_id,
            "sku_variante": f"SKU-VAR-{int(datetime.now().timestamp())}",
            "precio_variante": 120.00
        },
        headers=headers_admin
    )
    variante_id = res_var.json()["id"]

    # 4. Operaciones de Carrito (Cliente)
    # 4.1 Obtener carrito inicial (vacío)
    res_car = await client.get("/api/v1/carrito/", headers=headers_cliente)
    assert res_car.status_code == 200
    assert len(res_car.json()["items"]) == 0

    # 4.2 Agregar item al carrito
    res_add = await client.post("/api/v1/carrito/items", json={
        "variante_producto_id": variante_id,
        "cantidad": 2
    }, headers=headers_cliente)
    assert res_add.status_code == 200
    carrito_data = res_add.json()
    assert len(carrito_data["items"]) == 1
    item_id = carrito_data["items"][0]["id"]
    assert float(carrito_data["subtotal"]) == 240.00

    # 4.3 Actualizar cantidad
    res_upd = await client.put(f"/api/v1/carrito/items/{item_id}", json={"cantidad": 1}, headers=headers_cliente)
    assert res_upd.status_code == 200
    assert float(res_upd.json()["subtotal"]) == 120.00

    # 4.4 Aplicar cupón existente
    res_cup = await client.post("/api/v1/carrito/aplicar-cupon", json={"codigo": cupon_cod}, headers=headers_cliente)
    assert res_cup.status_code == 200
    assert float(res_cup.json()["descuento_aplicado"]) == 24.00  # 20% de 120 = 24.00
    assert float(res_cup.json()["total"]) == 96.00

    # 5. Crear orden a partir del carrito
    res_ord = await client.post("/api/v1/ordenes/", json={
        "direccion_envio": "Av. Las Americas 123",
        "tipo": "DIGITAL"
    }, headers=headers_cliente)
    assert res_ord.status_code == 201, f"Error creando orden: {res_ord.text}"
    orden_data = res_ord.json()
    assert orden_data["estado"] == "PENDIENTE_PAGO"
    assert float(orden_data["total"]) == 96.00
    orden_id = orden_data["id"]

    # 6. Listar mis órdenes
    res_mord = await client.get("/api/v1/ordenes/", headers=headers_cliente)
    assert res_mord.status_code == 200
    assert len(res_mord.json()) >= 1

    # 7. Obtener comprobante
    res_comp = await client.get(f"/api/v1/ordenes/{orden_id}/comprobante", headers=headers_cliente)
    assert res_comp.status_code == 200
    assert res_comp.json()["orden_id"] == orden_id


@pytest.mark.asyncio
async def test_04_ventas_presenciales_cajero(client: AsyncClient, admin_auth_tokens: dict):
    """Prueba el registro de ventas presenciales por un cajero/administrador."""
    headers = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}

    # Obtener una sucursal existente
    res_suc = await client.get("/api/v1/sucursales/", headers=headers)
    sucursal_id = res_suc.json()[0]["id"]

    # Crear producto y variante para venta directa
    res_cat = await client.get("/api/v1/categorias/", headers=headers)
    cat_id = res_cat.json()[0]["id"]
    res_tallas = await client.get("/api/v1/tallas/", headers=headers)
    talla_id = res_tallas.json()[0]["id"]
    res_colores = await client.get("/api/v1/colores/", headers=headers)
    color_id = res_colores.json()[0]["id"]

    res_prod = await client.post("/api/v1/productos/", json={
        "sku": f"PROD-POS-{int(datetime.now().timestamp())}",
        "nombre": "Pantalón Casual",
        "precio": 80.00,
        "categoria_id": cat_id
    }, headers=headers)
    producto_id = res_prod.json()["id"]

    res_var = await client.post(
        f"/api/v1/productos/{producto_id}/variantes?cantidad_inicial=20",
        json={
            "producto_id": producto_id,
            "talla_id": talla_id,
            "color_id": color_id,
            "sku_variante": f"SKU-POS-{int(datetime.now().timestamp())}",
            "precio_variante": 80.00
        },
        headers=headers
    )
    variante_id = res_var.json()["id"]

    # Realizar venta presencial
    venta_payload = {
        "sucursal_id": sucursal_id,
        "nit_ci_cliente": "CF",
        "metodo_pago": "EFECTIVO",
        "detalles": [
            {
                "variante_producto_id": variante_id,
                "cantidad": 2,
                "precio_unitario": 80.00
            }
        ]
    }
    res_v = await client.post("/api/v1/ventas-presenciales/", json=venta_payload, headers=headers)
    assert res_v.status_code == 201, f"Error en venta presencial: {res_v.text}"
    venta_data = res_v.json()
    assert venta_data["metodo_pago"] == "EFECTIVO"
    assert venta_data["orden"]["estado"] == "PAGADO"
    assert float(venta_data["orden"]["total"]) == 160.00


@pytest.mark.asyncio
async def test_05_reservas_de_prendas(client: AsyncClient, admin_auth_tokens: dict):
    """Prueba flujo completo de reservas: creación, preparación, y completar prueba."""
    headers_admin = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}

    # Registrar cliente
    cliente_payload = {
        "nombre": "Sofia",
        "apellido": "Lara",
        "correo": f"sofia_{int(datetime.now().timestamp())}@test.com",
        "telefono": "74567890",
        "nit_ci": f"CI-RES-{int(datetime.now().timestamp())}",
        "direccion_envio": "Zona 14, Guatemala",
        "contrasena": "Cliente123!"
    }
    res_reg = await client.post("/api/v1/auth/register", json=cliente_payload)
    token_cliente = res_reg.json()["access_token"]
    headers_cliente = {"Authorization": f"Bearer {token_cliente}"}

    # Crear sucursal y variante con stock
    res_suc = await client.get("/api/v1/sucursales/", headers=headers_admin)
    sucursal_id = res_suc.json()[0]["id"]
    res_cat = await client.get("/api/v1/categorias/", headers=headers_admin)
    res_tallas = await client.get("/api/v1/tallas/", headers=headers_admin)
    res_colores = await client.get("/api/v1/colores/", headers=headers_admin)

    res_prod = await client.post("/api/v1/productos/", json={
        "sku": f"PROD-RES-{int(datetime.now().timestamp())}",
        "nombre": "Chaqueta de Cuero",
        "precio": 150.00,
        "categoria_id": res_cat.json()[0]["id"]
    }, headers=headers_admin)
    producto_id = res_prod.json()["id"]

    res_var = await client.post(
        f"/api/v1/productos/{producto_id}/variantes?cantidad_inicial=10",
        json={
            "producto_id": producto_id,
            "talla_id": res_tallas.json()[0]["id"],
            "color_id": res_colores.json()[0]["id"],
            "sku_variante": f"SKU-RES-{int(datetime.now().timestamp())}",
            "precio_variante": 150.00
        },
        headers=headers_admin
    )
    variante_id = res_var.json()["id"]

    # 1. Crear reserva (Cliente)
    res_res = await client.post("/api/v1/reservas/", json={
        "sucursal_id": sucursal_id,
        "fecha_reserva": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "notas": "Deseo probar la chaqueta antes de comprar",
        "detalles": [{"variante_producto_id": variante_id, "cantidad": 1}]
    }, headers=headers_cliente)
    assert res_res.status_code == 201, f"Error creando reserva: {res_res.text}"
    reserva = res_res.json()
    reserva_id = reserva["id"]
    detalle_id = reserva["detalles"][0]["id"]
    assert reserva["estado"] == "PENDIENTE"

    # 2. Listar reservas de la sucursal (Admin/Encargado)
    res_suc_res = await client.get(f"/api/v1/reservas/sucursal/{sucursal_id}", headers=headers_admin)
    assert res_suc_res.status_code == 200

    # 3. Completar reserva (Cajero) - El cliente se lleva la prenda
    res_comp = await client.post(f"/api/v1/reservas/{reserva_id}/completar", json={
        "items": [{"detalle_reserva_id": detalle_id, "comprado": True}],
        "metodo_pago": "TARJETA_CREDITO"
    }, headers=headers_admin)
    assert res_comp.status_code == 200
    comp_data = res_comp.json()
    assert comp_data["items_comprados"] == 1
    assert comp_data["orden_id"] is not None


@pytest.mark.asyncio
async def test_06_pagos_digitales_endpoints(client: AsyncClient, admin_auth_tokens: dict):
    """Prueba los endpoints de inicio de sesión de pago y transacciones."""
    headers_admin = {"Authorization": f"Bearer {admin_auth_tokens['access_token']}"}
    
    # 1. Obtener órdenes existentes
    res_ord = await client.get("/api/v1/ordenes/", headers=headers_admin)
    assert res_ord.status_code == 200
    ordenes = res_ord.json()
    assert isinstance(ordenes, list)
    if ordenes:
        orden_id = ordenes[0]["id"]
        # Historial de transacciones de la orden
        res_tx = await client.get(f"/api/v1/pagos/transacciones/orden/{orden_id}", headers=headers_admin)
        assert res_tx.status_code == 200
        assert isinstance(res_tx.json(), list)
