"""ex2_019 - Marca de datos sintéticos (RF-023)

[EXAMEN-2] Fase 3 (plan, 9.3.3 y 9.3.7). Agrega `es_sintetico` a ventas
(`ordenes`), clientes y movimientos de inventario para poder generar datasets
de demostración y eliminarlos sin tocar datos reales.

Revision ID: ex2_019_datos_sinteticos
Revises: ex2_018
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_019"
down_revision: Union[str, None] = "ex2_018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLAS = (
    ("ordenes", "ix_ordenes_tenant_es_sintetico"),
    ("clientes", "ix_clientes_tenant_es_sintetico"),
    ("movimientos_inventario", "ix_movimientos_tenant_es_sintetico"),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for tabla, indice in _TABLAS:
        columnas = {col["name"] for col in inspector.get_columns(tabla)}
        if "es_sintetico" not in columnas:
            op.add_column(
                tabla,
                sa.Column(
                    "es_sintetico",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )
        op.execute(sa.text(
            f"CREATE INDEX IF NOT EXISTS {indice} ON {tabla} (tenant_id, es_sintetico)"
        ))


def downgrade() -> None:
    for tabla, indice in _TABLAS:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {indice}"))
        op.execute(sa.text(f"ALTER TABLE {tabla} DROP COLUMN IF EXISTS es_sintetico"))
