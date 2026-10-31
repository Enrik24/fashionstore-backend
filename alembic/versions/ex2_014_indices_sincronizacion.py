"""ex2_014 - Indices de sincronización y UUID offline

[EXAMEN-2] Fase 2 (plan, 9.2.3). Cierra la fase con los índices de cola por
dispositivo/secuencia, la unicidad de los UUID offline en ventas, reservas y
devoluciones, y el valor histórico `NO_APLICA` para los registros anteriores a
la operación offline. Todo es idempotente porque las tablas ya existen.

Revision ID: ex2_014_indices_sincronizacion
Revises: ex2_013
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_014"
down_revision: Union[str, None] = "ex2_013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------
    # Cola de sincronización: búsqueda por dispositivo y secuencia
    # ------------------------------------------------------------
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_operaciones_offline_tenant_dispositivo_secuencia "
        "ON operaciones_offline (tenant_id, dispositivo_id, secuencia_local)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_operaciones_offline_dispositivo_secuencia "
        "ON operaciones_offline (dispositivo_id, secuencia_local)"
    ))

    # ------------------------------------------------------------
    # Unicidad de los UUID generados offline (RN-005)
    # ------------------------------------------------------------
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ventas_presenciales_tenant_uuid_offline "
        "ON ventas_presenciales (tenant_id, uuid_offline) "
        "WHERE uuid_offline IS NOT NULL"
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_reservas_tenant_uuid_reserva "
        "ON reservas (tenant_id, uuid_reserva) "
        "WHERE uuid_reserva IS NOT NULL"
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_solicitudes_tenant_uuid_offline "
        "ON solicitudes_devolucion (tenant_id, uuid_offline) "
        "WHERE uuid_offline IS NOT NULL"
    ))

    # ------------------------------------------------------------
    # Histórico: los registros previos no participan de la sync
    # ------------------------------------------------------------
    inspector = sa.inspect(op.get_bind())
    for tabla in (
        "carritos",
        "ordenes",
        "ventas_presenciales",
        "reservas",
        "solicitudes_devolucion",
    ):
        columnas = {c["name"] for c in inspector.get_columns(tabla)}
        if "estado_sincronizacion" not in columnas:
            continue
        op.execute(sa.text(
            f"ALTER TABLE {tabla} ALTER COLUMN estado_sincronizacion "
            "SET DEFAULT 'NO_APLICA'"
        ))
        op.execute(sa.text(
            f"UPDATE {tabla} SET estado_sincronizacion = 'NO_APLICA' "
            "WHERE estado_sincronizacion IS NULL"
        ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_solicitudes_tenant_uuid_offline"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_reservas_tenant_uuid_reserva"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_ventas_presenciales_tenant_uuid_offline"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_operaciones_offline_dispositivo_secuencia"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_operaciones_offline_tenant_dispositivo_secuencia"))
