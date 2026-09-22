"""add unidad_medida to indicadores_kpi

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('indicadores_kpi', sa.Column('unidad_medida', sa.String(20), nullable=True, server_default='%'))


def downgrade() -> None:
    op.drop_column('indicadores_kpi', 'unidad_medida')
