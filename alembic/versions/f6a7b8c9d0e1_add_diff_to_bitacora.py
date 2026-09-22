"""add registro_id y valores anterior/nuevo a bitacora

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bitacora', sa.Column('registro_id', sa.Integer(), nullable=True))
    op.add_column('bitacora', sa.Column('valores_anteriores', sa.JSON(), nullable=True))
    op.add_column('bitacora', sa.Column('valores_nuevos', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('bitacora', 'valores_nuevos')
    op.drop_column('bitacora', 'valores_anteriores')
    op.drop_column('bitacora', 'registro_id')
