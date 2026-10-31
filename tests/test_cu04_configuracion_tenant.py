"""[EXAMEN-2] CU04 — Configuración inicial del tenant (plan 9.1.10).

Asistente de configuración, límite de sucursales, alta de usuarios,
preferencias offline y paso a OPERATIVO.
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_asistente_reporta_todos_los_pasos_completos(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    respuesta = await client.get(
        "/api/v1/saas/configuracion/asistente", headers=tenant["headers"]
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["tenant_id"] == tenant["id"]
    assert sorted(cuerpo["pasos_completados"]) == ["empresa", "offline", "sucursales", "usuarios"]
    assert cuerpo["pasos_faltantes"] == []
    assert cuerpo["porcentaje"] == 100
    assert cuerpo["puede_finalizar"] is True


@pytest.mark.asyncio
async def test_configuracion_incluye_tenant_offline_y_asistente(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    respuesta = await client.get("/api/v1/saas/configuracion", headers=tenant["headers"])

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["tenant"]["id"] == tenant["id"]
    assert cuerpo["configuracion_offline"]["tenant_id"] == tenant["id"]
    assert cuerpo["configuracion_offline"]["habilitar_carrito_offline"] is True
    assert cuerpo["asistente"]["estado_tenant"] == "OPERATIVO"


@pytest.mark.asyncio
async def test_actualizar_datos_de_empresa(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    respuesta = await client.put(
        "/api/v1/saas/configuracion/empresa",
        json={"razon_social": "Moda Andina Renombrada", "telefono": "76543210"},
        headers=tenant["headers"],
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["razon_social"] == "Moda Andina Renombrada"
    assert respuesta.json()["telefono"] == "76543210"


@pytest.mark.asyncio
async def test_limite_de_sucursales_en_la_configuracion(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    segunda = await client.post(
        "/api/v1/saas/configuracion/sucursales",
        json={"nombre": "Sucursal Centro", "direccion": "Calle 5", "telefono": "71111111"},
        headers=tenant["headers"],
    )
    tercera = await client.post(
        "/api/v1/saas/configuracion/sucursales",
        json={"nombre": "Sucursal Extra", "direccion": "Calle 6", "telefono": "72222222"},
        headers=tenant["headers"],
    )

    assert segunda.status_code == 201, segunda.text
    assert tercera.status_code == 409, tercera.text
    assert tercera.json()["codigo"] == "LIMITE_PLAN_EXCEDIDO"


@pytest.mark.asyncio
async def test_creacion_de_usuarios_del_tenant(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    sucursales = await client.get("/api/v1/sucursales/", headers=tenant["headers"])
    assert sucursales.status_code == 200, sucursales.text
    sucursal_id = sucursales.json()[0]["id"]

    respuesta = await client.post(
        "/api/v1/saas/configuracion/usuarios",
        json={
            "nombre": "Carlos",
            "apellido": "Mamani",
            "correo": f"encargado.{tenant['id']}@example.com",
            "contrasena": "Temporal123!",
            "rol": "Encargado",
            "sucursal_id": sucursal_id,
            "telefono": "70001111",
        },
        headers=tenant["headers"],
    )

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["tenant_id"] == tenant["id"]
    assert cuerpo["rol"] == "Encargado"
    assert cuerpo["debe_cambiar_contrasena"] is True


@pytest.mark.asyncio
async def test_no_se_puede_cambiar_el_tenant_id_desde_el_cuerpo(
    client: AsyncClient, tenant_nuevo, plan_basico, demo_tenant
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    sucursales = await client.get("/api/v1/sucursales/", headers=tenant["headers"])
    sucursal_id = sucursales.json()[0]["id"]
    correo = f"intruso.{tenant['id']}@example.com"

    respuesta = await client.post(
        "/api/v1/saas/configuracion/usuarios",
        json={
            "nombre": "Intruso",
            "apellido": "Prueba",
            "correo": correo,
            "contrasena": "Temporal123!",
            "rol": "Cajero",
            "sucursal_id": sucursal_id,
            "tenant_id": demo_tenant.id,  # intento de fijar otro tenant
        },
        headers=tenant["headers"],
    )

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["tenant_id"] == tenant["id"]


@pytest.mark.asyncio
async def test_correo_de_usuario_duplicado_es_rechazado(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    sucursales = await client.get("/api/v1/sucursales/", headers=tenant["headers"])
    sucursal_id = sucursales.json()[0]["id"]
    correo = f"repetido.{tenant['id']}@example.com"

    primero = await client.post(
        "/api/v1/saas/configuracion/usuarios",
        json={
            "nombre": "Ana",
            "apellido": "Rojas",
            "correo": correo,
            "contrasena": "Temporal123!",
            "rol": "Cajero",
            "sucursal_id": sucursal_id,
        },
        headers=tenant["headers"],
    )
    segundo = await client.post(
        "/api/v1/saas/configuracion/usuarios",
        json={
            "nombre": "Ana",
            "apellido": "Rojas",
            "correo": correo,
            "contrasena": "Temporal123!",
            "rol": "Cajero",
            "sucursal_id": sucursal_id,
        },
        headers=tenant["headers"],
    )

    assert primero.status_code == 201, primero.text
    assert segundo.status_code == 409


@pytest.mark.asyncio
async def test_sucursal_de_otro_tenant_no_es_aceptada(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    ajeno = await tenant_nuevo(plan_id=plan_basico.id, nombre="Ajeno")
    sucursales_ajenas = await client.get(
        "/api/v1/sucursales/", headers=ajeno["headers"]
    )
    assert sucursales_ajenas.status_code == 200, sucursales_ajenas.text
    sucursal_ajena = sucursales_ajenas.json()[0]["id"]

    respuesta = await client.post(
        "/api/v1/saas/configuracion/usuarios",
        json={
            "nombre": "Beto",
            "apellido": "Flores",
            "correo": f"ajeno.{tenant['id']}@example.com",
            "contrasena": "Temporal123!",
            "rol": "Cajero",
            "sucursal_id": sucursal_ajena,
        },
        headers=tenant["headers"],
    )

    assert respuesta.status_code == 404


@pytest.mark.asyncio
async def test_preferencias_offline_del_tenant(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    respuesta = await client.put(
        "/api/v1/saas/configuracion/offline",
        json={
            "descarga_automatica": False,
            "hora_descarga": "03:30",
            "max_horas_sesion_offline": 8,
            "habilitar_carrito_offline": False,
        },
        headers=tenant["headers"],
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["descarga_automatica"] is False
    assert cuerpo["hora_descarga"].startswith("03:30")
    assert cuerpo["max_horas_sesion_offline"] == 8
    assert cuerpo["habilitar_carrito_offline"] is False

    invalida = await client.put(
        "/api/v1/saas/configuracion/offline",
        json={"hora_descarga": "25:99"},
        headers=tenant["headers"],
    )
    assert invalida.status_code == 400


@pytest.mark.asyncio
async def test_paso_a_operativo_exige_configuracion_completa(
    client: AsyncClient, superadmin_headers: dict, superadmin_auth_tokens: dict
):
    # Tenant sin finalizar: su estado debe seguir siendo CONFIGURACION_INICIAL
    tenant = await client.post(
        "/api/v1/saas/tenants",
        json={
            "razon_social": "Sin Finalizar SRL",
            "nit": "NIT-SINFIN-01",
            "direccion_fiscal": "Calle 9",
            "correo_contacto": "contacto@sinfinalizar.com",
            "telefono": "70009999",
            "slug": "sin-finalizar",
            "admin_nombre": "Eva",
            "admin_apellido": "Loza",
            "admin_correo": "admin@sinfinalizar.com",
            "admin_contrasena": "Secreta123!",
        },
        headers=superadmin_headers,
    )
    assert tenant.status_code == 201, tenant.text
    tenant_id = tenant.json()["id"]
    assert tenant.json()["estado"] == "CONFIGURACION_INICIAL"

    login = await client.post(
        "/api/v1/auth/login",
        json={"correo": "admin@sinfinalizar.com", "contrasena": "Secreta123!"},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    finalizacion = await client.post(
        "/api/v1/saas/configuracion/finalizar", headers=headers
    )

    assert finalizacion.status_code == 200, finalizacion.text
    assert finalizacion.json()["estado"] == "OPERATIVO"

    detalle = await client.get(
        f"/api/v1/saas/tenants/{tenant_id}", headers=superadmin_headers
    )
    assert detalle.json()["estado"] == "OPERATIVO"


@pytest.mark.asyncio
async def test_un_encargado_no_accede_a_la_configuracion_del_tenant(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    sucursales = await client.get("/api/v1/sucursales/", headers=tenant["headers"])
    sucursal_id = sucursales.json()[0]["id"]
    correo = f"encargado.rol.{tenant['id']}@example.com"
    creacion = await client.post(
        "/api/v1/saas/configuracion/usuarios",
        json={
            "nombre": "Rosa",
            "apellido": "Vargas",
            "correo": correo,
            "contrasena": "Temporal123!",
            "rol": "Encargado",
            "sucursal_id": sucursal_id,
        },
        headers=tenant["headers"],
    )
    assert creacion.status_code == 201, creacion.text

    login = await client.post(
        "/api/v1/auth/login",
        json={"correo": correo, "contrasena": "Temporal123!"},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    respuesta = await client.get("/api/v1/saas/configuracion", headers=headers)

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "FUNCIONALIDAD_NO_DISPONIBLE"
