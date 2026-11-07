"""ex2_026 - Stripe session de PagoSuscripcion (pago de plan)

[EXAMEN-2] Checkout de Stripe para planes: guardar el `session_id`
(`cs_...`) generado en `crear_sesion_stripe` para confirmar por retorno
o webhook (análogo a `stripe_checkout_session_id` de pagos diferidos).

Revision ID: ex2_026
Revises: ex2_025
Create Date: 2026-11-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_026"
down_revision: Union[str, None] = "ex2_025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "pagos_suscripcion" not in inspector.get_table_names():
        return
    columnas = [c["name"] for c in inspector.get_columns("pagos_suscripcion")]
    if "stripe_checkout_session_id" not in columnas:
        op.add_column(
            "pagos_suscripcion",
            sa.Column("stripe_checkout_session_id", sa.String(length=120), nullable=True),
        )
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pagos_suscripcion_stripe_session "
        "ON pagos_suscripcion (stripe_checkout_session_id)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_pagos_suscripcion_stripe_session"))
    inspector = sa.inspect(op.get_bind())
    if "pagos_suscripcion" in inspector.get_table_names():
        columnas = [c["name"] for c in inspector.get_columns("pagos_suscripcion")]
        if "stripe_checkout_session_id" in columnas:
            op.drop_column("pagos_suscripcion", "stripe_checkout_session_id")
