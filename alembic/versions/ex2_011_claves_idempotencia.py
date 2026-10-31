"""ex2_011 - Claves de idempotencia y puntos de sincronización

[EXAMEN-2] Fase 2 (plan, 3.3.12/3.3.15 y 9.2.3).

Revision ID: ex2_011_claves_idempotencia
Revises: ex2_010
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_011"
down_revision: Union[str, None] = "ex2_010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tablas = set(sa.inspect(op.get_bind()).get_table_names())

    if "claves_idempotencia" not in tablas:
        op.create_table(
            "claves_idempotencia",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("clave", sa.Uuid(), nullable=False),
            sa.Column("endpoint", sa.String(length=120), nullable=False),
            sa.Column("request_hash", sa.String(length=64), nullable=False),
            sa.Column("estado", sa.String(length=20), server_default="EN_PROCESO", nullable=False),
            sa.Column("respuesta_json", sa.JSON(), nullable=True),
            sa.Column("fecha_creacion", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("fecha_expiracion", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "clave", "endpoint",
                name="uq_claves_idempotencia_tenant_clave_endpoint",
            ),
        )

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_claves_idempotencia_tenant_id "
        "ON claves_idempotencia (tenant_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_claves_idempotencia_tenant_clave "
        "ON claves_idempotencia (tenant_id, clave)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_claves_idempotencia_fecha_expiracion "
        "ON claves_idempotencia (fecha_expiracion)"
    ))

    if "puntos_sincronizacion" not in tablas:
        op.create_table(
            "puntos_sincronizacion",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("dispositivo_id", sa.Uuid(), nullable=False),
            sa.Column("ultima_sincronizacion", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cursor_catalogo", sa.String(length=100), nullable=True),
            sa.Column("ultima_descarga_completa", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dispositivo_id"], ["dispositivos_sincronizacion.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "dispositivo_id",
                name="uq_puntos_sincronizacion_tenant_dispositivo",
            ),
        )

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_puntos_sincronizacion_tenant_id "
        "ON puntos_sincronizacion (tenant_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_puntos_sincronizacion_tenant "
        "ON puntos_sincronizacion (tenant_id)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS puntos_sincronizacion"))
    op.execute(sa.text("DROP TABLE IF EXISTS claves_idempotencia"))
