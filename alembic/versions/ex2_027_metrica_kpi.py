"""ex2_027 - Métrica vinculada para KPIs en tiempo real

[EXAMEN-2] Agrega la columna `metrica` a `indicadores_kpi`: permite vincular un
KPI a una métrica del negocio (ventas_mes, ticket_promedio,
tasa_conversion_reservas, stock_bajo_minimo, clientes_activos) para que su
`valor_actual` se calcule automáticamente en el dashboard. NULL = valor manual.

Revision ID: ex2_027
Revises: ex2_026
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_027"
down_revision: Union[str, None] = "ex2_026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("indicadores_kpi", sa.Column("metrica", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("indicadores_kpi", "metrica")
