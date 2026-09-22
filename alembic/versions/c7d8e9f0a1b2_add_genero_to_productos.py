"""add genero to productos

Revision ID: c7d8e9f0a1b2
Revises: b5c6d7e8f9g0
Create Date: 2026-09-19 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'b5c6d7e8f9g0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = [c["name"] for c in sa.inspect(conn).get_columns("productos")]
    if "genero" in cols:
        return
    genero_enum = sa.Enum('HOMBRE', 'MUJER', 'UNISEX', name='generoproducto')
    genero_enum.create(conn, checkfirst=True)

    op.add_column(
        'productos',
        sa.Column('genero', genero_enum, nullable=True, server_default='UNISEX')
    )


def downgrade() -> None:
    op.drop_column('productos', 'genero')
    genero_enum = sa.Enum('HOMBRE', 'MUJER', 'UNISEX', name='generoproducto')
    genero_enum.drop(op.get_bind(), checkfirst=True)
