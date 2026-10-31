"""[EXAMEN-2] Fase 2.5 — Coherencia de sucursales (multitenant).

Cubre C1 (límite de plan), C2 (deduplicación por tenant), C3 (alias CU04),
C4 (guardas scoped), C5 (baja lógica) y C6 (seed a dos sucursales).
Plan de referencia: `plan-implementacion-sucursales-examen2.md` (v1.3).
"""
import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_catalogo.models import Sucursal


def _payload(nombre: str, ciudad_id: int | None = None, **extra) -> dict:
    datos = {
        "nombre": nombre,
        "direccion": "Calle de prueba 123",
        "telefono": "70000000",
    }
    if ciudad_id is not None:
        datos["ciudad_id"] = ciudad_id
    datos.update(extra)
    return datos


async def _ciudad(client: AsyncClient, headers: dict, nombre: str | None = None) -> int:
    respuesta = await client.get("/api/v1/ciudades/", headers=headers)
    assert respuesta.status_code == 200, respuesta.text
    ciudades = respuesta.json()
    if ciudades and nombre is None:
        return ciudades[0]["id"]
    creada = await client.post(
        "/api/v1/ciudades/",
        json={"nombre": nombre or f"Ciudad {uuid.uuid4().hex[:8]}"},
        headers=headers,
    )
    assert creada.status_code == 200, creada.text
    return creada.json()["id"]


async def _crear_sucursal(
    client: AsyncClient, headers: dict, nombre: str, ciudad_id: int | None = None
):
    if ciudad_id is None:
        ciudad_id = await _ciudad(client, headers)
    return await client.post(
        "/api/v1/sucursales/", json=_payload(nombre, ciudad_id), headers=headers
    )


async def _crear_usuario(
    client: AsyncClient, tenant: dict, rol: str, sucursal_id: int
) -> dict:
    correo = f"{rol.lower()}.{uuid.uuid4().hex[:8]}@example.com"
    creado = await client.post(
        "/api/v1/saas/configuracion/usuarios",
        json={
            "nombre": "Usuario",
            "apellido": "Prueba",
            "correo": correo,
            "contrasena": "Temporal123!",
            "rol": rol,
            "sucursal_id": sucursal_id,
        },
        headers=tenant["headers"],
    )
    assert creado.status_code == 201, creado.text
    login = await client.post(
        "/api/v1/auth/login",
        json={"correo": correo, "contrasena": "Temporal123!"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _sucursal_matriz(client: AsyncClient, tenant: dict) -> dict:
    respuesta = await client.get("/api/v1/sucursales/", headers=tenant["headers"])
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json(), "El tenant debe nacer con la sucursal Matriz"
    return respuesta.json()[0]


# ============================================================
# C1 — Límite del plan (pruebas 1 y 2)
# ============================================================

@pytest.mark.asyncio
async def test_01_crear_sucursal_por_crud_respeta_plan_basico(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    respuesta = await _crear_sucursal(client, tenant["headers"], "Sucursal Límite Uno")

    assert respuesta.status_code in (200, 201), respuesta.text
    assert respuesta.json()["nombre"] == "Sucursal Límite Uno"
    listado = await client.get("/api/v1/sucursales/", headers=tenant["headers"])
    assert respuesta.json()["id"] in {s["id"] for s in listado.json()}


@pytest.mark.asyncio
async def test_02_superar_max_sucursales_devuelve_409(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    ciudad = await _ciudad(client, tenant["headers"])

    segunda = await _crear_sucursal(
        client, tenant["headers"], "Sucursal Límite Dos", ciudad
    )
    tercera = await _crear_sucursal(
        client, tenant["headers"], "Sucursal Límite Tres", ciudad
    )

    assert segunda.status_code in (200, 201), segunda.text
    assert tercera.status_code == 409, tercera.text
    assert tercera.json()["codigo"] == "LIMITE_PLAN_EXCEDIDO"


# ============================================================
# C2 — Deduplicación por (tenant_id, nombre) (pruebas 3 y 4)
# ============================================================

@pytest.mark.asyncio
async def test_03_homonimo_en_otra_ciudad_es_rechazado(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    ciudad_a = await _ciudad(client, tenant["headers"])
    ciudad_b = await _ciudad(
        client, tenant["headers"], f"Ciudad B {uuid.uuid4().hex[:6]}"
    )

    primera = await _crear_sucursal(
        client, tenant["headers"], "Sucursal Homónima", ciudad_a
    )
    repetida = await _crear_sucursal(
        client, tenant["headers"], "Sucursal Homónima", ciudad_b
    )

    assert primera.status_code in (200, 201), primera.text
    assert repetida.status_code == 409, repetida.text
    assert "nombre" in repetida.json()["detail"].lower()


@pytest.mark.asyncio
async def test_04_mismo_nombre_en_otro_tenant_es_valido(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id)
    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Tenant B")
    ciudad = await _ciudad(client, tenant_a["headers"])
    nombre = "Sucursal Compartida"

    en_a = await _crear_sucursal(client, tenant_a["headers"], nombre, ciudad)
    en_b = await _crear_sucursal(client, tenant_b["headers"], nombre, ciudad)

    assert en_a.status_code in (200, 201), en_a.text
    assert en_b.status_code in (200, 201), en_b.text
    assert en_a.json()["id"] != en_b.json()["id"]


# ============================================================
# C5 — Baja lógica (pruebas 5 a 9)
# ============================================================

@pytest.mark.asyncio
async def test_05_delete_logico_y_get_devuelve_404(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    creada = await _crear_sucursal(client, tenant["headers"], "Sucursal Baja")
    assert creada.status_code in (200, 201), creada.text
    sucursal_id = creada.json()["id"]

    borrada = await client.delete(
        f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
    )
    consultada = await client.get(
        f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
    )

    assert borrada.status_code == 200, borrada.text
    assert consultada.status_code == 404, consultada.text


@pytest.mark.asyncio
async def test_06_el_listado_oculta_las_bajas_por_defecto(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    creada = await _crear_sucursal(client, tenant["headers"], "Sucursal Ocultable")
    assert creada.status_code in (200, 201), creada.text
    sucursal_id = creada.json()["id"]
    assert (
        await client.delete(
            f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
        )
    ).status_code == 200

    listado = await client.get("/api/v1/sucursales/", headers=tenant["headers"])

    assert listado.status_code == 200, listado.text
    assert sucursal_id not in {s["id"] for s in listado.json()}


@pytest.mark.asyncio
async def test_07_incluir_bajas_muestra_la_baja_al_administrador(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    creada = await _crear_sucursal(client, tenant["headers"], "Sucursal Visible Baja")
    assert creada.status_code in (200, 201), creada.text
    sucursal_id = creada.json()["id"]
    assert (
        await client.delete(
            f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
        )
    ).status_code == 200

    listado = await client.get(
        "/api/v1/sucursales/?incluir_bajas=true", headers=tenant["headers"]
    )

    assert listado.status_code == 200, listado.text
    assert sucursal_id in {s["id"] for s in listado.json()}


@pytest.mark.asyncio
async def test_08_el_nombre_se_reutiliza_tras_la_baja(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    nombre = "Sucursal Reutilizable"
    primera = await _crear_sucursal(client, tenant["headers"], nombre)
    assert primera.status_code in (200, 201), primera.text
    assert (
        await client.delete(
            f"/api/v1/sucursales/{primera.json()['id']}", headers=tenant["headers"]
        )
    ).status_code == 200

    segunda = await _crear_sucursal(client, tenant["headers"], nombre)

    assert segunda.status_code in (200, 201), segunda.text
    assert segunda.json()["id"] != primera.json()["id"]


@pytest.mark.asyncio
async def test_09_la_baja_libera_cupo_del_plan(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    ciudad = await _ciudad(client, tenant["headers"])

    primera = await _crear_sucursal(client, tenant["headers"], "Sucursal Cupo A", ciudad)
    assert primera.status_code in (200, 201), primera.text

    bloqueada = await _crear_sucursal(
        client, tenant["headers"], "Sucursal Cupo B", ciudad
    )
    assert bloqueada.status_code == 409, bloqueada.text

    assert (
        await client.delete(
            f"/api/v1/sucursales/{primera.json()['id']}", headers=tenant["headers"]
        )
    ).status_code == 200
    liberada = await _crear_sucursal(
        client, tenant["headers"], "Sucursal Cupo B", ciudad
    )

    assert liberada.status_code in (200, 201), liberada.text


# ============================================================
# C4 — Guardas scoped por tenant (pruebas 10 a 12)
# ============================================================

@pytest.mark.asyncio
async def test_10_super_admin_puede_crear_con_x_tenant_id(
    client: AsyncClient, tenant_nuevo, plan_profesional, superadmin_headers
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    ciudad = await _ciudad(client, tenant["headers"])
    headers = {**superadmin_headers, "X-Tenant-ID": str(tenant["id"])}

    respuesta = await client.post(
        "/api/v1/sucursales/",
        json=_payload("Sucursal Super Admin", ciudad),
        headers=headers,
    )

    assert respuesta.status_code in (200, 201), respuesta.text
    assert respuesta.json()["nombre"] == "Sucursal Super Admin"
    listado = await client.get("/api/v1/sucursales/", headers=tenant["headers"])
    assert respuesta.json()["id"] in {s["id"] for s in listado.json()}


@pytest.mark.asyncio
async def test_11_super_admin_sin_x_tenant_id_no_crea(
    client: AsyncClient, tenant_nuevo, plan_profesional, superadmin_headers
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    ciudad = await _ciudad(client, tenant["headers"])

    respuesta = await client.post(
        "/api/v1/sucursales/",
        json=_payload("Sucursal Sin Cabecera", ciudad),
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 404, respuesta.text
    assert respuesta.json()["codigo"] == "RECURSO_NO_ENCONTRADO"


@pytest.mark.asyncio
async def test_12_encargado_y_cajero_no_pueden_crear_sucursales(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    matriz = await _sucursal_matriz(client, tenant)
    ciudad = await _ciudad(client, tenant["headers"])

    for rol in ("Encargado", "Cajero"):
        headers = await _crear_usuario(client, tenant, rol, matriz["id"])
        respuesta = await client.post(
            "/api/v1/sucursales/",
            json=_payload(f"Sucursal {rol} {uuid.uuid4().hex[:4]}", ciudad),
            headers=headers,
        )
        assert respuesta.status_code == 403, respuesta.text
        assert respuesta.json()["codigo"] == "FUNCIONALIDAD_NO_DISPONIBLE"


# ============================================================
# C5 — Reglas de borde (pruebas 13 a 20)
# ============================================================

@pytest.mark.asyncio
async def test_13_delete_dos_veces_devuelve_404_sin_500(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    creada = await _crear_sucursal(client, tenant["headers"], "Sucursal Doble Baja")
    assert creada.status_code in (200, 201), creada.text
    sucursal_id = creada.json()["id"]

    primera = await client.delete(
        f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
    )
    segunda = await client.delete(
        f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
    )

    assert primera.status_code == 200, primera.text
    assert segunda.status_code == 404, segunda.text


@pytest.mark.asyncio
async def test_14_no_se_puede_dar_de_baja_la_ultima_sucursal_activa(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    matriz = await _sucursal_matriz(client, tenant)

    respuesta = await client.delete(
        f"/api/v1/sucursales/{matriz['id']}", headers=tenant["headers"]
    )
    sigue = await client.get(
        f"/api/v1/sucursales/{matriz['id']}", headers=tenant["headers"]
    )

    assert respuesta.status_code == 409, respuesta.text
    assert sigue.status_code == 200, sigue.text


@pytest.mark.asyncio
async def test_15_no_se_puede_dar_de_baja_con_inventario_activo(
    client: AsyncClient, tenant_nuevo, plan_profesional, catalogo_tenant
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant)

    respuesta = await client.delete(
        f"/api/v1/sucursales/{catalogo['sucursal_id']}", headers=tenant["headers"]
    )

    assert respuesta.status_code == 409, respuesta.text
    assert "inventario" in respuesta.json()["detail"].lower()


@pytest.mark.asyncio
async def test_16_la_baja_actualiza_updated_at(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    creada = await _crear_sucursal(client, tenant["headers"], "Sucursal Incremental")
    assert creada.status_code in (200, 201), creada.text
    sucursal_id = creada.json()["id"]

    antes = (await db_session.execute(
        select(Sucursal.updated_at).where(Sucursal.id == sucursal_id)
    )).scalar_one()
    await asyncio.sleep(0.05)
    borrada = await client.delete(
        f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
    )
    db_session.expire_all()
    despues = (await db_session.execute(
        select(Sucursal.updated_at).where(Sucursal.id == sucursal_id)
    )).scalar_one()

    assert borrada.status_code == 200, borrada.text
    assert antes is not None and despues is not None
    assert despues > antes


@pytest.mark.asyncio
async def test_17_put_sobre_una_sucursal_dada_de_baja_devuelve_404(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    creada = await _crear_sucursal(client, tenant["headers"], "Sucursal No Editable")
    assert creada.status_code in (200, 201), creada.text
    sucursal_id = creada.json()["id"]
    assert (
        await client.delete(
            f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
        )
    ).status_code == 200

    respuesta = await client.put(
        f"/api/v1/sucursales/{sucursal_id}",
        json={"nombre": "Intento de edición"},
        headers=tenant["headers"],
    )

    assert respuesta.status_code == 404, respuesta.text


@pytest.mark.asyncio
async def test_18_incluir_bajas_se_ignora_para_encargado_y_anonimo(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    matriz = await _sucursal_matriz(client, tenant)
    encargado = await _crear_usuario(client, tenant, "Encargado", matriz["id"])
    creada = await _crear_sucursal(client, tenant["headers"], "Sucursal Baja Privada")
    assert creada.status_code in (200, 201), creada.text
    sucursal_id = creada.json()["id"]
    assert (
        await client.delete(
            f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
        )
    ).status_code == 200

    con_encargado = await client.get(
        "/api/v1/sucursales/?incluir_bajas=true", headers=encargado
    )
    anonimo = await client.get("/api/v1/sucursales/?incluir_bajas=true")

    assert con_encargado.status_code == 200, con_encargado.text
    assert sucursal_id not in {s["id"] for s in con_encargado.json()}
    assert anonimo.status_code == 200, anonimo.text
    assert sucursal_id not in {s["id"] for s in anonimo.json()}


@pytest.mark.asyncio
async def test_19_el_paquete_offline_rechaza_una_sucursal_dada_de_baja(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    creada = await _crear_sucursal(client, tenant["headers"], "Sucursal Sin Paquete")
    assert creada.status_code in (200, 201), creada.text
    sucursal_id = creada.json()["id"]
    assert (
        await client.delete(
            f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
        )
    ).status_code == 200

    respuesta = await client.get(
        f"/api/v1/offline/paquete?sucursal_id={sucursal_id}",
        headers=tenant["headers"],
    )

    assert respuesta.status_code == 404, respuesta.text


@pytest.mark.asyncio
async def test_20_la_fila_sobrevive_con_deleted_at_y_mismo_id(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    creada = await _crear_sucursal(client, tenant["headers"], "Sucursal Historial")
    assert creada.status_code in (200, 201), creada.text
    sucursal_id = creada.json()["id"]
    assert (
        await client.delete(
            f"/api/v1/sucursales/{sucursal_id}", headers=tenant["headers"]
        )
    ).status_code == 200

    fila = (await db_session.execute(
        select(Sucursal.id, Sucursal.deleted_at).where(Sucursal.id == sucursal_id)
    )).one_or_none()

    assert fila is not None, "La baja lógica no debe borrar la fila"
    assert fila.id == sucursal_id
    assert fila.deleted_at is not None
