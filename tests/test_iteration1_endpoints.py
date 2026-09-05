"""
Tests completos para los endpoints de la Iteración 1:
- Autenticación (Login con admin por defecto, Registro, Refresh)
- Gestión de Usuarios y Roles (Usuarios, Roles, Bitácora)
- Gestión de Catálogo (Ciudades, Sucursales, Categorías, Proveedores, Productos)
- Inventario y Movimientos de Stock
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_01_auth_login_admin_default(client: AsyncClient):
    """Prueba POST /api/v1/auth/login con credenciales del administrador por defecto."""
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "correo": "admin@fashionstore.com",
            "contrasena": "Admin123!"
        }
    )
    assert response.status_code == 200, f"Error: {response.text}"
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"].lower() == "bearer"


@pytest.mark.asyncio
async def test_02_auth_register_cliente(client: AsyncClient):
    """Prueba POST /api/v1/auth/register registrando un nuevo cliente."""
    nuevo_cliente = {
        "nombre": "Carlos",
        "apellido": "Gomez",
        "correo": "carlos.gomez@test.com",
        "telefono": "71234567",
        "nit_ci": "9876543-1A",
        "direccion_envio": "Av. Las Americas 456, Santa Cruz",
        "contrasena": "Cliente123!"
    }
    response = await client.post("/api/v1/auth/register", json=nuevo_cliente)
    assert response.status_code == 200, f"Error: {response.text}"
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"].lower() == "bearer"


@pytest.mark.asyncio
async def test_03_auth_refresh_token(client: AsyncClient, admin_auth_tokens: dict):
    """Prueba POST /api/v1/auth/refresh usando el refresh_token."""
    refresh_token = admin_auth_tokens["refresh_token"]
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200, f"Error: {response.text}"
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.asyncio
async def test_04_gestion_usuarios_listar_y_crear(client: AsyncClient, admin_headers: dict):
    """Prueba GET /api/v1/users/ y POST /api/v1/users/."""
    # Listar usuarios
    response_list = await client.get("/api/v1/users/", headers=admin_headers)
    assert response_list.status_code == 200, f"Error: {response_list.text}"
    usuarios = response_list.json()
    assert isinstance(usuarios, list)
    assert len(usuarios) >= 1

    # Crear nuevo usuario
    nuevo_usuario = {
        "nombre": "Elena",
        "apellido": "Rios",
        "correo": "elena.rios@fashionstore.com",
        "telefono": "78901234",
        "contrasena": "Elena123!",
        "estado": "ACTIVO"
    }
    response_create = await client.post(
        "/api/v1/users/?rol=Administrador",
        headers=admin_headers,
        json=nuevo_usuario
    )
    assert response_create.status_code == 200, f"Error: {response_create.text}"
    usuario_creado = response_create.json()
    assert usuario_creado["correo"] == nuevo_usuario["correo"]
    assert usuario_creado["id"] is not None


@pytest.mark.asyncio
async def test_05_gestion_roles_listar_y_crear(client: AsyncClient, admin_headers: dict):
    """Prueba GET /api/v1/roles/ y POST /api/v1/roles/."""
    # Listar roles
    response_list = await client.get("/api/v1/roles/", headers=admin_headers)
    assert response_list.status_code == 200, f"Error: {response_list.text}"
    roles = response_list.json()
    assert isinstance(roles, list)
    assert any(r["nombre"] == "Administrador" for r in roles)

    # Crear nuevo rol
    nuevo_rol = {
        "nombre": "Supervisor Inventario",
        "descripcion": "Rol encargado de supervisar stocks y movimientos"
    }
    response_create = await client.post("/api/v1/roles/", headers=admin_headers, json=nuevo_rol)
    assert response_create.status_code == 200, f"Error: {response_create.text}"
    rol_creado = response_create.json()
    assert rol_creado["nombre"] == nuevo_rol["nombre"]


@pytest.mark.asyncio
async def test_06_gestion_bitacora(client: AsyncClient, admin_headers: dict):
    """Prueba GET /api/v1/bitacora/."""
    response = await client.get("/api/v1/bitacora/", headers=admin_headers)
    assert response.status_code == 200, f"Error: {response.text}"
    bitacora = response.json()
    assert isinstance(bitacora, list)


@pytest.mark.asyncio
async def test_07_catalogo_ciudades(client: AsyncClient, admin_headers: dict):
    """Prueba GET /api/v1/ciudades/ y POST /api/v1/ciudades/."""
    # Crear ciudad
    nueva_ciudad = {
        "nombre": "Santa Cruz de la Sierra",
        "codigo_postal": "0000",
        "pais": "Bolivia"
    }
    response_create = await client.post("/api/v1/ciudades/", headers=admin_headers, json=nueva_ciudad)
    assert response_create.status_code == 200, f"Error: {response_create.text}"
    ciudad = response_create.json()
    assert ciudad["nombre"] == nueva_ciudad["nombre"]
    assert ciudad["id"] is not None

    # Listar ciudades
    response_list = await client.get("/api/v1/ciudades/", headers=admin_headers)
    assert response_list.status_code == 200, f"Error: {response_list.text}"
    ciudades = response_list.json()
    assert isinstance(ciudades, list)
    assert any(c["nombre"] == nueva_ciudad["nombre"] for c in ciudades)


@pytest.mark.asyncio
async def test_08_catalogo_sucursales(client: AsyncClient, admin_headers: dict):
    """Prueba GET /api/v1/sucursales/ y POST /api/v1/sucursales/."""
    # Crear ciudad previa
    ciudad_resp = await client.post(
        "/api/v1/ciudades/",
        headers=admin_headers,
        json={"nombre": "La Paz", "codigo_postal": "0000", "pais": "Bolivia"}
    )
    ciudad_id = ciudad_resp.json()["id"]

    # Crear sucursal
    nueva_sucursal = {
        "nombre": "Sucursal Central La Paz",
        "direccion": "Av. 16 de Julio #100",
        "telefono": "22123456",
        "horario_atencion": "09:00 - 20:00",
        "estado": "ACTIVO",
        "latitud": -16.5,
        "longitud": -68.15,
        "ciudad_id": ciudad_id
    }
    response_create = await client.post("/api/v1/sucursales/", headers=admin_headers, json=nueva_sucursal)
    assert response_create.status_code == 200, f"Error: {response_create.text}"
    sucursal = response_create.json()
    assert sucursal["nombre"] == nueva_sucursal["nombre"]

    # Listar sucursales
    response_list = await client.get("/api/v1/sucursales/", headers=admin_headers)
    assert response_list.status_code == 200, f"Error: {response_list.text}"
    sucursales = response_list.json()
    assert isinstance(sucursales, list)
    assert any(s["nombre"] == nueva_sucursal["nombre"] for s in sucursales)


@pytest.mark.asyncio
async def test_09_catalogo_categorias(client: AsyncClient, admin_headers: dict):
    """Prueba GET /api/v1/categorias/ y POST /api/v1/categorias/."""
    # Listar categorías (incluye las iniciales del seeder)
    response_list = await client.get("/api/v1/categorias/")
    assert response_list.status_code == 200, f"Error: {response_list.text}"
    categorias = response_list.json()
    assert isinstance(categorias, list)
    assert len(categorias) > 0

    # Crear categoría nueva
    nueva_categoria = {
        "nombre": "Chaquetas y Abrigos Elegantes",
        "descripcion": "Colección de abrigos de invierno y media estación",
        "imagen": "https://img.fashionstore.com/categorias/abrigos.jpg"
    }
    response_create = await client.post("/api/v1/categorias/", headers=admin_headers, json=nueva_categoria)
    assert response_create.status_code == 200, f"Error: {response_create.text}"
    categoria = response_create.json()
    assert categoria["nombre"] == nueva_categoria["nombre"]


@pytest.mark.asyncio
async def test_10_catalogo_proveedores(client: AsyncClient, admin_headers: dict):
    """Prueba GET /api/v1/proveedores/ y POST /api/v1/proveedores/."""
    # Crear proveedor
    nuevo_proveedor = {
        "nombre": "Textiles Internacionales S.A.",
        "nit": "1029384756",
        "contacto": "Roberto Mendez",
        "telefono": "33456789",
        "correo": "contacto@textilesinter.com",
        "direccion": "Parque Industrial Mz. 5"
    }
    response_create = await client.post("/api/v1/proveedores/", headers=admin_headers, json=nuevo_proveedor)
    assert response_create.status_code == 200, f"Error: {response_create.text}"
    proveedor = response_create.json()
    assert proveedor["nombre"] == nuevo_proveedor["nombre"]

    # Listar proveedores
    response_list = await client.get("/api/v1/proveedores/", headers=admin_headers)
    assert response_list.status_code == 200, f"Error: {response_list.text}"
    proveedores = response_list.json()
    assert isinstance(proveedores, list)
    assert any(p["nombre"] == nuevo_proveedor["nombre"] for p in proveedores)


@pytest.mark.asyncio
async def test_11_catalogo_productos(client: AsyncClient, admin_headers: dict):
    """Prueba GET /api/v1/productos/ y POST /api/v1/productos/."""
    # Obtener categoría existente
    cats_resp = await client.get("/api/v1/categorias/")
    categoria_id = cats_resp.json()[0]["id"]

    # Crear producto
    nuevo_producto = {
        "sku": "POL-TEST-001",
        "nombre": "Polera Premium Algodón Pima",
        "descripcion": "Polera 100% algodón pima con corte moderno",
        "precio": "129.50",
        "imagenes": ["https://img.fashionstore.com/p1.jpg"],
        "estado": "ACTIVO",
        "categoria_id": categoria_id
    }
    response_create = await client.post("/api/v1/productos/", headers=admin_headers, json=nuevo_producto)
    assert response_create.status_code == 200, f"Error: {response_create.text}"
    producto = response_create.json()
    assert producto["sku"] == nuevo_producto["sku"]
    assert producto["nombre"] == nuevo_producto["nombre"]

    # Listar productos
    response_list = await client.get("/api/v1/productos/", headers=admin_headers)
    assert response_list.status_code == 200, f"Error: {response_list.text}"
    productos = response_list.json()
    assert isinstance(productos, list)
    assert any(p["sku"] == nuevo_producto["sku"] for p in productos)


@pytest.mark.asyncio
async def test_12_inventario_y_movimientos(client: AsyncClient, admin_headers: dict):
    """Prueba GET /api/v1/inventario/ y POST /api/v1/inventario/movimientos."""
    # 1. Crear ciudad y sucursal
    ciudad_resp = await client.post(
        "/api/v1/ciudades/",
        headers=admin_headers,
        json={"nombre": "Cochabamba", "codigo_postal": "0000", "pais": "Bolivia"}
    )
    ciudad_id = ciudad_resp.json()["id"]

    sucursal_resp = await client.post(
        "/api/v1/sucursales/",
        headers=admin_headers,
        json={
            "nombre": "Sucursal Cochabamba Plaza",
            "direccion": "Av. Heroinas 123",
            "telefono": "44123456",
            "ciudad_id": ciudad_id
        }
    )
    assert sucursal_resp.status_code == 200

    # 2. Crear producto
    cats_resp = await client.get("/api/v1/categorias/")
    categoria_id = cats_resp.json()[0]["id"]

    prod_resp = await client.post(
        "/api/v1/productos/",
        headers=admin_headers,
        json={
            "sku": "CAM-INV-001",
            "nombre": "Camisa Denim Casual",
            "descripcion": "Camisa de mezclilla",
            "precio": "199.00",
            "categoria_id": categoria_id
        }
    )
    assert prod_resp.status_code == 200
    producto_id = prod_resp.json()["id"]

    # 3. Obtener tallas y colores existentes
    tallas_resp = await client.get("/api/v1/tallas/")
    talla_id = tallas_resp.json()[0]["id"]

    colores_resp = await client.get("/api/v1/colores/")
    color_id = colores_resp.json()[0]["id"]

    # 4. Crear variante con cantidad inicial en sucursales
    variante_resp = await client.post(
        f"/api/v1/productos/{producto_id}/variantes?cantidad_inicial=20",
        headers=admin_headers,
        json={
            "talla_id": talla_id,
            "color_id": color_id,
            "sku_variante": "CAM-INV-001-M-AZUL",
            "precio_variante": "199.00"
        }
    )
    assert variante_resp.status_code == 200, f"Error variante: {variante_resp.text}"

    # 5. Listar inventario (GET /api/v1/inventario/)
    inv_list_resp = await client.get("/api/v1/inventario/", headers=admin_headers)
    assert inv_list_resp.status_code == 200, f"Error inventario: {inv_list_resp.text}"
    inventarios = inv_list_resp.json()
    assert isinstance(inventarios, list)
    assert len(inventarios) >= 1
    inventario_item = inventarios[0]
    inventario_id = inventario_item["id"]
    assert inventario_item["cantidad"] >= 20

    # 6. Registrar movimiento de inventario (POST /api/v1/inventario/movimientos)
    movimiento_data = {
        "inventario_id": inventario_id,
        "tipo": "RECEPCION",
        "cantidad": 10,
        "motivo": "Ingreso de nuevo lote desde proveedor"
    }
    mov_resp = await client.post(
        "/api/v1/inventario/movimientos",
        headers=admin_headers,
        json=movimiento_data
    )
    assert mov_resp.status_code == 200, f"Error movimiento: {mov_resp.text}"
    movimiento = mov_resp.json()
    assert movimiento["inventario_id"] == inventario_id
    assert movimiento["tipo"] == "RECEPCION"
    assert movimiento["cantidad"] == 10
    assert movimiento["id"] is not None
