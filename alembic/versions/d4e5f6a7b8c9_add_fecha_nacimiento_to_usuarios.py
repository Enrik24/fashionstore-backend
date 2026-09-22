"""add fecha_nacimiento to usuarios

Revision ID: d4e5f6a7b8c9
Revises: cu24_cu28_001
Create Date: 2026-09-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'cu24_cu28_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('usuarios', sa.Column('fecha_nacimiento', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('usuarios', 'fecha_nacimiento')
