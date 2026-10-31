"""ex2_013 - Pago diferido y eventos de Stripe

[EXAMEN-2] Fase 2 (plan, 3.3.10/3.3.11 y 9.2.3). Crea las tablas de la cola de
pago, la deduplicación de webhooks y el enum `estado_pago_diferido`.

Revision ID: ex2_013_pago_diferido
Revises: ex2_012
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "ex2_013"
down_revision: Union[str, None] = "ex2_012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_type(name: str, values: tuple[str, ...]) -> None:
    labels = ", ".join(f"'{value}'" for value in values)
    op.execute(sa.text(
        f"""
        DO $$ BEGIN
            CREATE TYPE {name} AS ENUM ({labels});
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    ))


def upgrade() -> None:
    _create_type("estado_pago_diferido", (
        "PENDIENTE_ENVIO",
        "EN_COLA",
        "EN_PROCESO",
        "REQUIERE_ACCION_CLIENTE",
        "CONFIRMADO",
        "RECHAZADO",
        "EXPIRADO",
        "CANCELADO",
        "ERROR_CRITICO",
    ))
    for valor in ("EXPIRADO", "CANCELADO"):
        op.execute(sa.text(
            f"ALTER TYPE estadotransaccion ADD VALUE IF NOT EXISTS '{valor}'"
        ))
    for valor in ("PENDIENTE_PAGO_DIFERIDO", "PAGO_VENCIDO", "PENDIENTE_REGULARIZACION"):
        op.execute(sa.text(
            f"ALTER TYPE estadoorden ADD VALUE IF NOT EXISTS '{valor}'"
        ))

    tablas = set(sa.inspect(op.get_bind()).get_table_names())

    if "pagos_diferidos" not in tablas:
        op.create_table(
            "pagos_diferidos",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("orden_id", sa.Integer(), nullable=False),
            sa.Column("uuid_pago", sa.Uuid(), nullable=False),
            sa.Column("monto", sa.Numeric(10, 2), nullable=False),
            sa.Column("moneda", sa.String(length=3), server_default="BOB", nullable=False),
            sa.Column(
                "estado",
                postgresql.ENUM(
                    "PENDIENTE_ENVIO", "EN_COLA", "EN_PROCESO",
                    "REQUIERE_ACCION_CLIENTE", "CONFIRMADO", "RECHAZADO",
                    "EXPIRADO", "CANCELADO", "ERROR_CRITICO",
                    name="estado_pago_diferido",
                    create_type=False,
                ),
                server_default="PENDIENTE_ENVIO",
                nullable=False,
            ),
            sa.Column("stripe_payment_intent_id", sa.String(length=120), nullable=True),
            sa.Column("stripe_checkout_session_id", sa.String(length=120), nullable=True),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("intentos", sa.Integer(), server_default="0", nullable=False),
            sa.Column("fecha_creacion", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("fecha_expiracion", sa.DateTime(timezone=True), nullable=False),
            sa.Column("fecha_ultimo_intento", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ultimo_error", sa.Text(), nullable=True),
            sa.Column("operacion_offline_id", sa.Integer(), nullable=True),
            sa.Column("client_secret", sa.String(length=255), nullable=True),
            sa.Column("motivo_rechazo", sa.Text(), nullable=True),
            sa.Column("reembolso_id", sa.String(length=120), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["orden_id"], ["ordenes.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["operacion_offline_id"], ["operaciones_offline.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "uuid_pago", name="uq_pagos_diferidos_tenant_uuid"),
        )

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pagos_diferidos_tenant_id "
        "ON pagos_diferidos (tenant_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pagos_diferidos_orden_id "
        "ON pagos_diferidos (orden_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pagos_diferidos_uuid_pago "
        "ON pagos_diferidos (uuid_pago)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pagos_diferidos_tenant_estado "
        "ON pagos_diferidos (tenant_id, estado)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pagos_diferidos_tenant_fecha "
        "ON pagos_diferidos (tenant_id, fecha_creacion)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pagos_diferidos_stripe_payment_intent_id "
        "ON pagos_diferidos (stripe_payment_intent_id)"
    ))

    if "eventos_stripe" not in tablas:
        op.create_table(
            "eventos_stripe",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=True),
            sa.Column("stripe_event_id", sa.String(length=120), nullable=False),
            sa.Column("tipo", sa.String(length=80), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("procesado", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("fecha_recepcion", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("fecha_procesamiento", sa.DateTime(timezone=True), nullable=True),
            sa.Column("pago_diferido_id", sa.Integer(), nullable=True),
            sa.Column("error_detalle", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["pago_diferido_id"], ["pagos_diferidos.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("stripe_event_id", name="uq_eventos_stripe_event_id"),
        )

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_eventos_stripe_tenant_id "
        "ON eventos_stripe (tenant_id)"
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_eventos_stripe_stripe_event_id "
        "ON eventos_stripe (stripe_event_id)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS eventos_stripe"))
    op.execute(sa.text("DROP TABLE IF EXISTS pagos_diferidos"))
    op.execute(sa.text("DROP TYPE IF EXISTS estado_pago_diferido"))
