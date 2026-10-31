"""ex2_018 - Devoluciones/cambios offline (CU13)

[EXAMEN-2] Fase 3 (plan, 9.3.3). Los campos de CU13 ya existen desde la Fase 1
(plan, 3.8.3/3.8.4); esta migración agrega los índices de consulta, fija el
default de `es_offline` y garantiza la unicidad de `uuid_offline` por tenant.

Revision ID: ex2_018_devoluciones_offline
Revises: ex2_017
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_018"
down_revision: Union[str, None] = "ex2_017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE solicitudes_devolucion "
        "ALTER COLUMN es_offline SET DEFAULT false"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_solicitudes_devolucion_tenant_estado_sync "
        "ON solicitudes_devolucion (tenant_id, estado_sincronizacion)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_solicitudes_devolucion_tenant_fecha_limite "
        "ON solicitudes_devolucion (tenant_id, fecha_limite_cambio)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_detalles_solicitud_tenant_estado_item "
        "ON detalles_solicitud_devolucion (tenant_id, estado_item)"
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_solicitudes_tenant_uuid_offline "
        "ON solicitudes_devolucion (tenant_id, uuid_offline) "
        "WHERE uuid_offline IS NOT NULL"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_solicitudes_tenant_uuid_offline"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_detalles_solicitud_tenant_estado_item"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_solicitudes_devolucion_tenant_fecha_limite"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_solicitudes_devolucion_tenant_estado_sync"))
    op.execute(sa.text(
        "ALTER TABLE solicitudes_devolucion ALTER COLUMN es_offline DROP DEFAULT"
    ))
