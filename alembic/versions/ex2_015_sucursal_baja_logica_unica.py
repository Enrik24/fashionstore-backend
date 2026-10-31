"""ex2_015 - Unicidad de sucursales solo entre las activas

[EXAMEN-2] Fase 2.5. Sustituye el único `(tenant_id, nombre)` por un índice único
parcial que solo considera las sucursales activas (`deleted_at IS NULL`), para que
un nombre quede libre al dar de baja y para que la baja lógica no choque con la
restricción del Examen 1.

Revision ID: ex2_015_sucursal_baja_logica_unica
Revises: ex2_014
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_015"
down_revision: Union[str, None] = "ex2_014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Puede existir como constraint (migración ex2_006) o como índice (create_all)
    op.execute(sa.text(
        "ALTER TABLE sucursales DROP CONSTRAINT IF EXISTS uq_sucursales_tenant_nombre"
    ))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_sucursales_tenant_nombre"))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sucursales_tenant_nombre_activas "
        "ON sucursales (tenant_id, nombre) WHERE deleted_at IS NULL"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_sucursales_tenant_nombre_activas"))
    op.execute(sa.text(
        "ALTER TABLE sucursales ADD CONSTRAINT uq_sucursales_tenant_nombre "
        "UNIQUE (tenant_id, nombre)"
    ))
