"""ex2_025 - Compensación de ventas offline rechazadas (punto 1)

[EXAMEN-2] Fase E / RF-032 / RN-020: una sola compensación por operación
offline (unique), con trazabilidad del medio de pago y del usuario que la
resolvió.

Revision ID: ex2_025
Revises: ex2_024
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_025"
down_revision: Union[str, None] = "ex2_024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "compensaciones" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "compensaciones",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operacion_offline_id",
            sa.Integer(),
            sa.ForeignKey("operaciones_offline.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "orden_id",
            sa.Integer(),
            sa.ForeignKey("ordenes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "cliente_id",
            sa.Integer(),
            sa.ForeignKey("clientes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "tipo",
            sa.Enum(
                "REEMBOLSO_EFECTIVO", "REEMBOLSO_TARJETA", "NOTA_CREDITO",
                "SALDO_FAVOR", name="tipocompensacion",
            ),
            nullable=False,
        ),
        sa.Column("monto", sa.Numeric(10, 2), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False, server_default="BOB"),
        sa.Column(
            "estado",
            sa.Enum(
                "PENDIENTE", "EN_PROCESO", "COMPLETADA", "FALLIDA", "NO_APLICA",
                name="estadocompensacion",
            ),
            nullable=False,
            server_default="PENDIENTE",
        ),
        sa.Column("motivo", sa.String(200), nullable=True),
        sa.Column("referencia_externa", sa.String(120), nullable=True),
        sa.Column(
            "notificado_cliente",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("error_detalle", sa.Text(), nullable=True),
        sa.Column(
            "fecha_creacion", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("fecha_resolucion", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resuelto_por",
            sa.Integer(),
            sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("operacion_offline_id", name="uq_compensaciones_operacion"),
    )
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_compensaciones_tenant_estado "
        "ON compensaciones (tenant_id, estado)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_compensaciones_tenant_fecha "
        "ON compensaciones (tenant_id, fecha_creacion)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_compensaciones_tenant_fecha"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_compensaciones_tenant_estado"))
    inspector = sa.inspect(op.get_bind())
    if "compensaciones" in inspector.get_table_names():
        op.drop_table("compensaciones")
    op.execute(sa.text("DROP TYPE IF EXISTS estadocompensacion"))
    op.execute(sa.text("DROP TYPE IF EXISTS tipocompensacion"))
