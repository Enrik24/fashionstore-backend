"""[EXAMEN-2] CU17 — Sesión offline (plan 9.1.10).

Registro de PIN, bloqueo por intentos, token de propósito `offline`,
dispositivo revocado, cierre con instrucción de borrado, tenant suspendido
y usuario desactivado (RN-019 / RNF-13 / RNF-17).
"""
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_usuarios.models import EstadoUsuario, Usuario
from app.apps.operaciones_offline.models import DispositivoSincronizacion
from app.apps.operaciones_offline.services import (
    DispositivoService,
    SesionOfflineService,
)
from app.security import create_offline_token, decode_token

PIN_VALIDO = "135790"
PIN_TRIVIAL = "111111"


async def _preparar_dispositivo(cliente: AsyncClient, tenant: dict, nombre: str = "POS") -> dict:
    """Registra dispositivo + configura PIN del administrador del tenant."""
    dispositivo = await cliente.post(
        "/api/v1/offline/dispositivos",
        json={"plataforma": "ANDROID", "nombre": nombre},
        headers=tenant["headers"],
    )
    assert dispositivo.status_code == 201, dispositivo.text

    pin = await cliente.post(
        "/api/v1/offline/sesion/pin",
        json={
            "contrasena_actual": tenant["payload"]["admin_contrasena"],
            "pin": PIN_VALIDO,
        },
        headers=tenant["headers"],
    )
    assert pin.status_code == 200, pin.text
    return dispositivo.json()


@pytest.mark.asyncio
async def test_registro_de_pin_y_sesion_offline(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)

    respuesta = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={
            "dispositivo_id": dispositivo["id"],
            "pin": PIN_VALIDO,
            "tenant_id": tenant["id"],
        },
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["tenant_id"] == tenant["id"]
    assert cuerpo["dispositivo_id"] == dispositivo["id"]
    assert "Administrador" in cuerpo["roles"]
    assert cuerpo["configuracion"]["tenant_id"] == tenant["id"]
    assert cuerpo["expira_en"] is not None

    payload = decode_token(cuerpo["token_offline"])
    assert payload["type"] == "offline"
    assert payload["proposito"] == "offline"
    assert payload["tenant_id"] == tenant["id"]
    assert payload["dispositivo_id"] == dispositivo["id"]

    # RNF-13: el PIN nunca se devuelve por API
    assert "pin" not in cuerpo
    assert PIN_VALIDO not in respuesta.text


@pytest.mark.asyncio
async def test_el_pin_se_guarda_hasheado(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    await _preparar_dispositivo(client, tenant)

    resultado = await db_session.execute(
        select(Usuario).where(Usuario.correo == tenant["payload"]["admin_correo"])
    )
    usuario = resultado.scalar_one()

    assert usuario.offline_pin_hash is not None
    assert usuario.offline_pin_hash != PIN_VALIDO
    assert len(usuario.offline_pin_hash) > 20


@pytest.mark.asyncio
async def test_pin_incorrecto_incrementa_intentos(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)

    respuesta = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": "000111"},
    )

    assert respuesta.status_code == 401, respuesta.text
    assert respuesta.json()["codigo"] == "PIN_INCORRECTO"

    estado = await client.get(
        "/api/v1/offline/sesion/estado",
        headers={**tenant["headers"], "X-Device-ID": dispositivo["id"]},
    )
    assert estado.status_code == 200, estado.text
    assert estado.json()["intentos_restantes"] == 4


@pytest.mark.asyncio
async def test_bloqueo_al_quinto_intento_fallido(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)

    for intento in range(4):
        respuesta = await client.post(
            "/api/v1/offline/sesion/iniciar",
            json={"dispositivo_id": dispositivo["id"], "pin": "000111"},
        )
        assert respuesta.status_code == 401, f"intento {intento + 1}: {respuesta.text}"

    quinto = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": "000111"},
    )
    bloqueado = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": PIN_VALIDO},
    )

    assert quinto.status_code == 423, quinto.text
    assert quinto.json()["codigo"] == "PIN_BLOQUEADO"
    assert bloqueado.status_code == 423, bloqueado.text

    # Se libera el bloqueo en memoria para no afectar a otros tests
    resultado = await db_session.execute(
        select(Usuario).where(Usuario.correo == tenant["payload"]["admin_correo"])
    )
    usuario = resultado.scalar_one()
    SesionOfflineService.reiniciar_intentos(usuario.id, dispositivo["id"])

    liberado = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": PIN_VALIDO},
    )
    assert liberado.status_code == 200, liberado.text


@pytest.mark.asyncio
async def test_pin_con_longitud_insuficiente_o_trivial(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)

    corto = await client.post(
        "/api/v1/offline/sesion/pin",
        json={"contrasena_actual": tenant["payload"]["admin_contrasena"], "pin": "123"},
        headers=tenant["headers"],
    )
    trivial = await client.post(
        "/api/v1/offline/sesion/pin",
        json={
            "contrasena_actual": tenant["payload"]["admin_contrasena"],
            "pin": PIN_TRIVIAL,
        },
        headers=tenant["headers"],
    )
    sin_contrasena = await client.post(
        "/api/v1/offline/sesion/pin",
        json={"contrasena_actual": "incorrecta", "pin": PIN_VALIDO},
        headers=tenant["headers"],
    )

    assert corto.status_code == 422
    assert trivial.status_code == 400
    assert sin_contrasena.status_code == 400


@pytest.mark.asyncio
async def test_token_offline_no_sirve_para_endpoints_en_linea(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)
    sesion = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": PIN_VALIDO},
    )
    token = sesion.json()["token_offline"]

    respuesta = await client.get(
        "/api/v1/saas/configuracion",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert respuesta.status_code == 401
    assert "en línea" in respuesta.json()["detail"]


@pytest.mark.asyncio
async def test_token_offline_vencido_es_rechazado(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)
    sesion = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": PIN_VALIDO},
    )

    vencido = create_offline_token(
        {
            "sub": str(sesion.json()["usuario_id"]),
            "tenant_id": tenant["id"],
            "dispositivo_id": dispositivo["id"],
            "roles": ["Administrador"],
        },
        expires_delta=timedelta(minutes=-5),
    )

    respuesta = await client.post(
        "/api/v1/offline/sesion/renovar", json={"token_offline": vencido}
    )

    assert respuesta.status_code in (401, 403), respuesta.text


@pytest.mark.asyncio
async def test_renovacion_de_sesion_offline(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)
    sesion = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": PIN_VALIDO},
    )

    respuesta = await client.post(
        "/api/v1/offline/sesion/renovar",
        json={"token_offline": sesion.json()["token_offline"]},
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["dispositivo_id"] == dispositivo["id"]
    assert cuerpo["tenant_id"] == tenant["id"]
    assert cuerpo["token_offline"] != sesion.json()["token_offline"]


@pytest.mark.asyncio
async def test_sesion_offline_vencida_en_el_limite_exacto(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)

    resultado = await db_session.execute(
        select(Usuario).where(Usuario.correo == tenant["payload"]["admin_correo"])
    )
    usuario = resultado.scalar_one()

    iniciada = datetime.utcnow() - timedelta(hours=12)
    token = create_offline_token(
        {
            "sub": str(usuario.id),
            "correo": usuario.correo,
            "tenant_id": tenant["id"],
            "dispositivo_id": dispositivo["id"],
            "roles": ["Administrador"],
            "sesion_iniciada": iniciada.isoformat(),
        },
        expires_delta=timedelta(minutes=30),
    )

    respuesta = await client.post(
        "/api/v1/offline/sesion/renovar", json={"token_offline": token}
    )

    assert respuesta.status_code == 403, respuesta.text
    assert respuesta.json()["codigo"] == "SESION_OFFLINE_VENCIDA"


@pytest.mark.asyncio
async def test_dispositivo_revocado_no_puede_iniciar_sesion(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)

    revocacion = await client.post(
        f"/api/v1/offline/dispositivos/{dispositivo['id']}/revocar",
        json={"motivo": "Pérdida del equipo"},
        headers=tenant["headers"],
    )
    assert revocacion.status_code == 200, revocacion.text
    assert revocacion.json()["activo"] is False

    respuesta = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": PIN_VALIDO},
    )

    assert respuesta.status_code == 403, respuesta.text
    assert respuesta.json()["codigo"] == "DISPOSITIVO_REVOCADO"


@pytest.mark.asyncio
async def test_cierre_de_sesion_con_instruccion_de_borrado(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)
    sesion = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": PIN_VALIDO},
    )

    respuesta = await client.post(
        "/api/v1/offline/sesion/cerrar",
        headers={"Authorization": f"Bearer {sesion.json()['token_offline']}"},
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["cerrada"] is True
    instruccion = cuerpo["instruccion_borrado_local"]
    assert instruccion["tenant_id"] == tenant["id"]
    assert "token_offline" in instruccion["eliminar"]
    assert "pin_local" in instruccion["eliminar"]


@pytest.mark.asyncio
async def test_tenant_suspendido_no_inicia_sesion_offline(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)
    detalle = (
        await client.get(
            f"/api/v1/saas/tenants/{tenant['id']}", headers=superadmin_headers
        )
    ).json()
    await client.post(
        f"/api/v1/saas/suscripciones/{detalle['suscripcion']['id']}/suspender",
        headers=superadmin_headers,
    )

    respuesta = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": PIN_VALIDO},
    )

    assert respuesta.status_code == 403, respuesta.text


@pytest.mark.asyncio
async def test_usuario_desactivado_no_inicia_sesion_offline(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)

    resultado = await db_session.execute(
        select(Usuario).where(Usuario.correo == tenant["payload"]["admin_correo"])
    )
    usuario = resultado.scalar_one()
    usuario.estado = EstadoUsuario.INACTIVO
    await db_session.commit()

    respuesta = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={"dispositivo_id": dispositivo["id"], "pin": PIN_VALIDO},
    )

    assert respuesta.status_code == 403, respuesta.text
    assert respuesta.json()["codigo"] == "SESION_OFFLINE_VENCIDA"


@pytest.mark.asyncio
async def test_dispositivo_de_otro_tenant_es_rechazado(
    client: AsyncClient, tenant_nuevo, plan_profesional, demo_tenant
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)

    respuesta = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={
            "dispositivo_id": dispositivo["id"],
            "pin": PIN_VALIDO,
            "tenant_id": demo_tenant.id,
        },
    )

    assert respuesta.status_code == 404, respuesta.text


@pytest.mark.asyncio
async def test_pin_de_otro_usuario_es_rechazado(
    client: AsyncClient, tenant_nuevo, plan_profesional, admin_auth_tokens
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    dispositivo = await _preparar_dispositivo(client, tenant)
    otro_usuario = admin_auth_tokens.get("usuario_id") or 1

    respuesta = await client.post(
        "/api/v1/offline/sesion/iniciar",
        json={
            "dispositivo_id": dispositivo["id"],
            "pin": PIN_VALIDO,
            "usuario_id": otro_usuario,
        },
    )

    assert respuesta.status_code == 404, respuesta.text


@pytest.mark.asyncio
async def test_revocacion_automatica_por_inactividad(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    """CU17/RN-019: el job revoca solo los equipos sin conexión > 90 días."""
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    viejo = await _preparar_dispositivo(client, tenant, nombre="POS viejo")
    reciente = await _preparar_dispositivo(client, tenant, nombre="POS nuevo")

    dispositivos = {}
    for datos, dias in ((viejo, 91), (reciente, 10)):
        dispositivo = (await db_session.execute(
            select(DispositivoSincronizacion).where(
                DispositivoSincronizacion.id == UUID(datos["id"])
            )
        )).scalar_one()
        dispositivo.ultima_conexion = datetime.utcnow() - timedelta(days=dias)
        dispositivos[datos["id"]] = dispositivo
    await db_session.commit()

    assert await DispositivoService.revocar_por_inactividad(db_session) == 1

    async def _leer(device_id):
        return (await db_session.execute(
            select(DispositivoSincronizacion).where(
                DispositivoSincronizacion.id == UUID(device_id)
            )
        )).scalar_one()

    viejo_db = await _leer(viejo["id"])
    reciente_db = await _leer(reciente["id"])
    assert viejo_db.activo is False
    assert viejo_db.revocado_en is not None
    assert "inactividad" in viejo_db.motivo_revocacion
    assert reciente_db.activo is True
    assert reciente_db.revocado_en is None


@pytest.mark.asyncio
async def test_marcar_dispositivo_perdido(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    """CU17: atajo de revocación para equipos perdidos o robados."""
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    datos = await _preparar_dispositivo(client, tenant, nombre="POS perdido")

    dispositivo = (await db_session.execute(
        select(DispositivoSincronizacion).where(
            DispositivoSincronizacion.id == UUID(datos["id"])
        )
    )).scalar_one()

    revocado = await DispositivoService.marcar_dispositivo_perdido(
        db_session, datos["id"], dispositivo.usuario_id, int(tenant["id"])
    )

    assert revocado.activo is False
    assert revocado.motivo_revocacion == "Dispositivo perdido o robado"
    assert revocado.revocado_por_id == dispositivo.usuario_id
