"""ex2_002 - Crear pagos_suscripcion

[EXAMEN-2] Fase 1 (plan, 9.1.4). Historial de cobros del plan (RN-011).
Reutiliza el enum `estado_transaccion` del Examen 1; los valores EXPIRADO y
CANCELADO se agregan en `ex2_008_nuevos_valores_enum`.

Revision ID: ex2_002_pagos_suscripcion
Revises: ex2_001_crear_tenant_plan_suscripcion
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "ex2_002"
down_revision: Union[str, None] = "ex2_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if "pagos_suscripcion" in sa.inspect(conn).get_table_names():
        return

    op.create_table(
        "pagos_suscripcion",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("suscripcion_id", sa.Integer(), nullable=False),
        sa.Column("monto", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("moneda", sa.String(length=3), server_default="BOB", nullable=False),
        sa.Column(
            "estado",
            postgresql.ENUM(
                "PENDIENTE", "CONFIRMADO", "RECHAZADO", "REEMBOLSADO", "EN_PROCESO",
                name="estadotransaccion",
                create_type=False,
            ),
            server_default="PENDIENTE",
            nullable=False,
        ),
        sa.Column("stripe_payment_intent_id", sa.String(length=120), nullable=True),
        sa.Column("referencia", sa.String(length=120), nullable=True),
        sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("datos_respuesta", sa.JSON(), nullable=True),
        sa.CheckConstraint("monto >= 0", name="ck_pagos_suscripcion_monto_no_negativo"),
        sa.ForeignKeyConstraint(["suscripcion_id"], ["suscripciones.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pagos_suscripcion_id"), "pagos_suscripcion", ["id"], unique=False)
    op.create_index(op.f("ix_pagos_suscripcion_tenant_id"), "pagos_suscripcion", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_pagos_suscripcion_suscripcion_id"), "pagos_suscripcion", ["suscripcion_id"], unique=False)
    op.create_index(
        op.f("ix_pagos_suscripcion_stripe_payment_intent_id"),
        "pagos_suscripcion",
        ["stripe_payment_intent_id"],
        unique=False,
    )
    op.create_index("ix_pagos_suscripcion_tenant_fecha", "pagos_suscripcion", ["tenant_id", "fecha"], unique=False)
    op.create_index(
        "ix_pagos_suscripcion_suscripcion_estado",
        "pagos_suscripcion",
        ["suscripcion_id", "estado"],
        unique=False,
    )
    op.create_index(
        "uq_pagos_suscripcion_tenant_pi",
        "pagos_suscripcion",
        ["tenant_id", "stripe_payment_intent_id"],
        unique=True,
    )


def downgrade() -> None:
    conn = op.get_bind()
    if "pagos_suscripcion" not in sa.inspect(conn).get_table_names():
        return
    op.drop_table("pagos_suscripcion")

