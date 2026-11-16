"""ex2_028 - URL del Checkout Session de PagoDiferido

[EXAMEN-2] CU16: el backend crea un Checkout Session de Stripe para el pago
diferido y devuelve su URL al dispositivo. El `session_id` ya existía
(`stripe_checkout_session_id`); aquí se persiste la URL hospedada.

Revision ID: ex2_028
Revises: ex2_027
Create Date: 2026-11-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_028"
down_revision: Union[str, None] = "ex2_027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "pagos_diferidos" not in inspector.get_table_names():
        return
    columnas = [c["name"] for c in inspector.get_columns("pagos_diferidos")]
    if "stripe_checkout_url" not in columnas:
        op.add_column(
            "pagos_diferidos",
            sa.Column("stripe_checkout_url", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "pagos_diferidos" in inspector.get_table_names():
        columnas = [c["name"] for c in inspector.get_columns("pagos_diferidos")]
        if "stripe_checkout_url" in columnas:
            op.drop_column("pagos_diferidos", "stripe_checkout_url")