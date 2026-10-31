"""ex2_016 - Traspasos entre sucursales (CU14)

[EXAMEN-2] Fase 3 (plan, 3.3.6, 3.3.16 y 9.3.3). Crea `traspasos` y
`detalles_traspaso`, el enum `estado_traspaso`, la restricción de sucursales
distintas y los índices de consulta.

Revision ID: ex2_016_traspasos
Revises: ex2_015
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "ex2_016"
down_revision: Union[str, None] = "ex2_015"
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
    _create_type("estado_traspaso", (
        "EN_TRANSITO",
        "RECIBIDO",
        "RECHAZADO_PARCIAL",
        "CANCELADO",
    ))

    tablas = set(sa.inspect(op.get_bind()).get_table_names())

    if "traspasos" not in tablas:
        op.create_table(
            "traspasos",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("numero", sa.String(length=50), nullable=False),
            sa.Column("sucursal_origen_id", sa.Integer(), nullable=False),
            sa.Column("sucursal_destino_id", sa.Integer(), nullable=False),
            sa.Column(
                "estado",
                postgresql.ENUM(
                    "EN_TRANSITO", "RECIBIDO", "RECHAZADO_PARCIAL", "CANCELADO",
                    name="estado_traspaso",
                    create_type=False,
                ),
                server_default="EN_TRANSITO",
                nullable=False,
            ),
            sa.Column("fecha_envio", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("fecha_recepcion", sa.DateTime(timezone=True), nullable=True),
            sa.Column("usuario_id", sa.Integer(), nullable=True),
            sa.Column("observaciones", sa.Text(), nullable=True),
            sa.Column("motivo", sa.Text(), nullable=True),
            sa.Column("usuario_recibe_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["sucursal_origen_id"], ["sucursales.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["sucursal_destino_id"], ["sucursales.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["usuario_recibe_id"], ["usuarios.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "numero", name="uq_traspasos_tenant_numero"),
            sa.CheckConstraint(
                "sucursal_origen_id <> sucursal_destino_id",
                name="ck_traspasos_sucursales_distintas",
            ),
        )
        op.create_index("ix_traspasos_tenant_id", "traspasos", ["tenant_id"])
        op.create_index("ix_traspasos_tenant_estado", "traspasos", ["tenant_id", "estado"])
        op.create_index("ix_traspasos_tenant_fecha", "traspasos", ["tenant_id", "fecha_envio"])

    if "detalles_traspaso" not in tablas:
        op.create_table(
            "detalles_traspaso",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("traspaso_id", sa.Integer(), nullable=False),
            sa.Column("variante_producto_id", sa.Integer(), nullable=False),
            sa.Column("cantidad", sa.Integer(), nullable=False),
            sa.Column("cantidad_recibida", sa.Integer(), server_default="0", nullable=False),
            sa.Column("cantidad_merma", sa.Integer(), server_default="0", nullable=False),
            sa.Column("observaciones", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["traspaso_id"], ["traspasos.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["variante_producto_id"], ["variantes_producto.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint("cantidad > 0", name="ck_detalles_traspaso_cantidad_positiva"),
            sa.CheckConstraint(
                "cantidad_recibida >= 0 AND cantidad_merma >= 0",
                name="ck_detalles_traspaso_recibida_merma_no_negativas",
            ),
        )
        op.create_index("ix_detalles_traspaso_tenant_id", "detalles_traspaso", ["tenant_id"])
        op.create_index(
            "ix_detalles_traspaso_tenant_variante",
            "detalles_traspaso",
            ["tenant_id", "variante_producto_id"],
        )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS detalles_traspaso CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS traspasos CASCADE"))
    op.execute(sa.text("DROP TYPE IF EXISTS estado_traspaso"))
