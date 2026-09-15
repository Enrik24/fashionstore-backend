"""add preferencias to cliente

Revision ID: b5c6d7e8f9g0
Revises: f3894f381e68
Create Date: 2026-09-14 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = 'b5c6d7e8f9g0'
down_revision: Union[str, None] = 'f3894f381e68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agregar columna preferencias como JSON
    op.add_column('clientes', sa.Column('preferencias', JSON, nullable=True))


def downgrade() -> None:
    # Eliminar columna preferencias
    op.drop_column('clientes', 'preferencias')
