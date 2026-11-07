"""[EXAMEN-2] Fase B — plan Empresarial, límites y respaldos (RF-035 / RNF-19)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_saas.backup_service import BackupService
from app.apps.gestion_saas.models import Backup, EstadoBackup, Plan
from app.apps.gestion_saas.services import SuscripcionService
from app.config import settings
from app.exceptions import LimitePlanExcedido


def _sufijo() -> str:
    return uuid.uuid4().hex[:8]


@pytest.mark.asyncio
async def test_plan_empresarial_sembrado(db_session):
    plan = (await db_session.execute(
        select(Plan).where(Plan.nombre == "Empresarial")
    )).scalar_one_or_none()
    assert plan is not None, "El seed debe crear el plan Empresarial"
    assert plan.max_sucursales == 50
    assert plan.max_usuarios == 200
    assert plan.max_productos is None  # ilimitado
    assert plan.backup_automatico is True
    assert plan.backup_frecuencia_horas == 6
    assert plan.backup_retencion_dias == 365
    assert plan.permite_restauracion is True


@pytest.mark.asyncio
async def test_limite_de_usuarios_por_plan(db_session, tenant_nuevo, plan_basico):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    plan = await db_session.get(Plan, plan_basico.id)
    original = plan.max_usuarios
    plan.max_usuarios = 1  # el tenant ya tiene su administrador
    await db_session.commit()
    try:
        with pytest.raises(LimitePlanExcedido):
            await SuscripcionService.verificar_limite_usuarios(db_session, tenant["id"])
    finally:
        plan.max_usuarios = original
        await db_session.commit()


@pytest.mark.asyncio
async def test_limite_de_productos_y_plan_empresarial_ilimitado(
    client: AsyncClient, db_session, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    plan = await db_session.get(Plan, plan_basico.id)
    original = plan.max_productos
    plan.max_productos = 1
    await db_session.commit()
    try:
        primero = await client.post(
            "/api/v1/productos/",
            json={"sku": f"SKU-{_sufijo()}", "nombre": "Producto 1", "precio": "10.00"},
            headers=tenant["headers"],
        )
        assert primero.status_code in (200, 201), primero.text
        segundo = await client.post(
            "/api/v1/productos/",
            json={"sku": f"SKU-{_sufijo()}", "nombre": "Producto 2", "precio": "10.00"},
            headers=tenant["headers"],
        )
        assert segundo.status_code == 409, segundo.text
    finally:
        plan.max_productos = original
        await db_session.commit()

    # Empresarial: max_productos = NULL no bloquea.
    plan_empresarial = (await db_session.execute(
        select(Plan).where(Plan.nombre == "Empresarial")
    )).scalar_one()
    assert plan_empresarial.max_productos is None


@pytest.mark.asyncio
async def test_backup_manual_y_limite_mensual(
    client: AsyncClient, tenant_nuevo, plan_basico, plan_profesional
):
    # Básico: 0 respaldos manuales.
    basico = await tenant_nuevo(plan_id=plan_basico.id)
    bloqueado = await client.post("/api/v1/saas/backups", headers=basico["headers"])
    assert bloqueado.status_code == 409, bloqueado.text

    # Profesional: 2 manuales por mes.
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    primero = await client.post("/api/v1/saas/backups", headers=tenant["headers"])
    assert primero.status_code == 201, primero.text
    backup = primero.json()
    assert backup["estado"] == "COMPLETADO"
    assert backup["tamano_bytes"] > 0
    assert len(backup["hash_sha256"]) == 64
    assert backup["tipo"] == "MANUAL"

    segundo = await client.post("/api/v1/saas/backups", headers=tenant["headers"])
    assert segundo.status_code == 201, segundo.text

    tercero = await client.post("/api/v1/saas/backups", headers=tenant["headers"])
    assert tercero.status_code == 409

    listado = await client.get("/api/v1/saas/backups", headers=tenant["headers"])
    assert listado.status_code == 200
    assert len(listado.json()) == 2

    uso = await client.get("/api/v1/saas/backups/uso", headers=tenant["headers"])
    assert uso.status_code == 200
    assert uso.json()["backups_mes_actual"] == 2
    assert uso.json()["restantes_mes_actual"] == 0

    detalle = await client.get(
        f"/api/v1/saas/backups/{backup['id']}", headers=tenant["headers"]
    )
    assert detalle.status_code == 200


@pytest.mark.asyncio
async def test_backups_aislados_por_tenant(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    otro = await tenant_nuevo(plan_id=plan_profesional.id)
    backup = (await client.post(
        "/api/v1/saas/backups", headers=tenant["headers"]
    )).json()

    ajeno = await client.get(
        f"/api/v1/saas/backups/{backup['id']}", headers=otro["headers"]
    )
    assert ajeno.status_code == 404
    assert (await client.get(
        "/api/v1/saas/backups", headers=otro["headers"]
    )).json() == []


@pytest.mark.asyncio
async def test_retencion_marca_expirado(db_session, tenant_nuevo, plan_profesional):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    backup = await BackupService.crear_manual(db_session, int(tenant["id"]), None)
    assert backup.estado == EstadoBackup.COMPLETADO

    backup.retencion_hasta = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()
    expirados = await BackupService.aplicar_retencion(db_session)
    assert expirados >= 1
    await db_session.refresh(backup)
    assert backup.estado == EstadoBackup.EXPIRADO


@pytest.mark.asyncio
async def test_restauracion_bloqueada_por_plan(
    client: AsyncClient, db_session, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    backup = (await client.post(
        "/api/v1/saas/backups", headers=tenant["headers"]
    )).json()
    respuesta = await client.post(
        f"/api/v1/saas/backups/{backup['id']}/restaurar",
        json={"motivo": "Prueba"},
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 409
    assert "no permite" in respuesta.text


@pytest.mark.asyncio
async def test_restauracion_empresarial_con_flag_y_mock(
    client: AsyncClient, db_session, tenant_nuevo, monkeypatch
):
    plan_empresarial = (await db_session.execute(
        select(Plan).where(Plan.nombre == "Empresarial")
    )).scalar_one()
    tenant = await tenant_nuevo(plan_id=plan_empresarial.id)
    backup = (await client.post(
        "/api/v1/saas/backups", headers=tenant["headers"]
    )).json()

    if backup.get("estado") != "COMPLETADO":
        pytest.skip("El respaldo no se pudo completar en este entorno")

    async def _sin_efecto(db, backup, restauracion):
        return None

    monkeypatch.setattr(BackupService, "_ejecutar_restauracion", staticmethod(_sin_efecto))
    monkeypatch.setattr(settings, "BACKUP_PERMITE_RESTAURACION", True)

    respuesta = await client.post(
        f"/api/v1/saas/backups/{backup['id']}/restaurar",
        json={"motivo": "Restauración de prueba"},
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["estado"] == "COMPLETADA"

    historial = await client.get(
        "/api/v1/saas/restauraciones", headers=tenant["headers"]
    )
    assert historial.status_code == 200
    assert len(historial.json()) == 1
