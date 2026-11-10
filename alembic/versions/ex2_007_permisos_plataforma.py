"""ex2_007 - Permisos de plataforma y rol Super Administrador

[EXAMEN-2] Fase 1 (plan, 9.1.9). Crea los permisos nuevos, el rol de
plataforma y sus asociaciones. Es idempotente por nombre.

Revision ID: ex2_007_permisos_plataforma
Revises: ex2_006_unicidad_compuesta
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_007"
down_revision: Union[str, None] = "ex2_006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NUEVOS_PERMISOS = [
    ("gestionar_tenants", "Permiso para gestionar empresas tenant"),
    ("gestionar_planes", "Permiso para gestionar planes"),
    ("gestionar_suscripciones", "Permiso para gestionar suscripciones"),
    ("configurar_tenant", "Permiso para configurar el entorno del tenant"),
    ("sincronizar_offline", "Permiso para sincronizar operaciones offline"),
    ("resolver_conflictos", "Permiso para resolver conflictos de sincronización"),
    ("gestionar_traspasos", "Permiso para gestionar traspasos"),
    ("usar_reportes_ia", "Permiso para usar reportes con IA"),
    ("entrenar_modelos", "Permiso para entrenar modelos de ML"),
    ("exportar_reportes", "Permiso para exportar reportes"),
    ("operar_offline", "Permiso para operar en modo offline"),
    ("gestionar_pagos", "Permiso para gestionar pagos"),
    ("revocar_dispositivos", "Permiso para revocar dispositivos"),
]

PERMISOS_PLATAFORMA = {
    "gestionar_tenants", "gestionar_planes", "gestionar_suscripciones",
    "entrenar_modelos",
}


def _asociar(rol: str, permisos_sql: str) -> None:
    op.execute(
        sa.text(
            f"""
            INSERT INTO roles_permisos (rol_id, permiso_id)
            SELECT r.id, p.id
            FROM roles r, permisos p
            WHERE r.nombre = '{rol}' AND p.nombre IN ({permisos_sql})
              AND NOT EXISTS (
                  SELECT 1 FROM roles_permisos rp
                  WHERE rp.rol_id = r.id AND rp.permiso_id = p.id
              )
            """
        )
    )


def upgrade() -> None:
    for nombre, descripcion in NUEVOS_PERMISOS:
        op.execute(
            sa.text(
                """
                INSERT INTO permisos (nombre, descripcion)
                SELECT :nombre, :descripcion
                WHERE NOT EXISTS (SELECT 1 FROM permisos WHERE nombre = :nombre)
                """
            ).bindparams(nombre=nombre, descripcion=descripcion)
        )

    # Rol de plataforma
    op.execute(
        sa.text(
            """
            INSERT INTO roles (nombre, descripcion, tenant_id)
            SELECT 'Super Administrador',
                   'Super Administrador de la plataforma SaaS', NULL
            WHERE NOT EXISTS (
                SELECT 1 FROM roles WHERE nombre = 'Super Administrador'
            )
            """
        )
    )
    op.execute(
        sa.text(
            "UPDATE roles SET tenant_id = NULL WHERE nombre = 'Super Administrador'"
        )
    )

    todos = "'" + "', '".join(nombre for nombre, _ in NUEVOS_PERMISOS) + "'"
    _asociar("Super Administrador", todos)
    # El superadministrador también conserva los permisos base del Examen 1
    op.execute(
        sa.text(
            """
            INSERT INTO roles_permisos (rol_id, permiso_id)
            SELECT r.id, p.id
            FROM roles r, permisos p
            WHERE r.nombre = 'Super Administrador'
              AND NOT EXISTS (
                  SELECT 1 FROM roles_permisos rp
                  WHERE rp.rol_id = r.id AND rp.permiso_id = p.id
              )
            """
        )
    )

    # Permisos de tenant (todos salvo los exclusivos de plataforma)
    tenant = ", ".join(
        f"'{nombre}'"
        for nombre, _ in NUEVOS_PERMISOS
        if nombre not in PERMISOS_PLATAFORMA
    )
    _asociar("Administrador", tenant)
    _asociar("Encargado", "'sincronizar_offline', 'operar_offline'")
    _asociar(
        "Cajero",
        "'sincronizar_offline', 'operar_offline', 'gestionar_pagos'",
    )


def downgrade() -> None:
    nombres = ", ".join(f"'{nombre}'" for nombre, _ in NUEVOS_PERMISOS)
    op.execute(
        sa.text(
            f"""
            DELETE FROM roles_permisos rp
            USING permisos p
            WHERE rp.permiso_id = p.id AND p.nombre IN ({nombres})
            """
        )
    )
    op.execute(sa.text(f"DELETE FROM permisos WHERE nombre IN ({nombres})"))

