"""ex2_009 - Dispositivos de sincronización y configuración offline

[EXAMEN-2] Fase 1 (plan, 3.3.13/3.3.14 y 9.1.4). Crea las tablas de CU17/CU04,
el PIN offline y la FK del carrito al dispositivo. La configuración offline se
crea en `ex2_004` para poder sembrarla; aquí se garantiza su existencia.

Revision ID: ex2_009_dispositivos_y_config_offline
Revises: ex2_008_nuevos_valores_enum
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_009"
down_revision: Union[str, None] = "ex2_008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_config_table() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS configuracion_offline (
                id serial PRIMARY KEY,
                tenant_id integer NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                descarga_automatica boolean NOT NULL DEFAULT true,
                hora_descarga time,
                incluir_clientes boolean NOT NULL DEFAULT true,
                max_clientes integer NOT NULL DEFAULT 100,
                habilitar_carrito_offline boolean NOT NULL DEFAULT true,
                max_horas_sesion_offline integer NOT NULL DEFAULT 12,
                max_dias_orden_pendiente integer NOT NULL DEFAULT 1,
                updated_at timestamptz DEFAULT now(),
                CONSTRAINT uq_configuracion_offline_tenant UNIQUE (tenant_id)
            )
            """
        )
    )


def upgrade() -> None:
    _create_config_table()

    op.execute(
        sa.text(
            "ALTER TABLE usuarios "
            "ADD COLUMN IF NOT EXISTS offline_pin_hash varchar(255)"
        )
    )

    conn = op.get_bind()
    if "dispositivos_sincronizacion" not in sa.inspect(conn).get_table_names():
        op.create_table(
            "dispositivos_sincronizacion",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("usuario_id", sa.Integer(), nullable=False),
            sa.Column("plataforma", sa.String(length=20), nullable=False),
            sa.Column("nombre", sa.String(length=100), nullable=True),
            sa.Column("ultima_conexion", sa.DateTime(timezone=True), nullable=True),
            sa.Column("activo", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("revocado_en", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revocado_por_id", sa.Integer(), nullable=True),
            sa.Column("motivo_revocacion", sa.Text(), nullable=True),
            sa.Column("fecha_registro", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["revocado_por_id"], ["usuarios.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_dispositivos_sincronizacion_tenant_id"),
            "dispositivos_sincronizacion",
            ["tenant_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_dispositivos_sincronizacion_usuario_id"),
            "dispositivos_sincronizacion",
            ["usuario_id"],
            unique=False,
        )

    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_dispositivos_sincronizacion_tenant_usuario_activo "
            "ON dispositivos_sincronizacion (tenant_id, usuario_id, activo)"
        )
    )

    # FK diferida del carrito offline al dispositivo (CU15/CU17)
    op.execute(
        sa.text(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'fk_carritos_dispositivo_id_dispositivos_sincronizacion'
                ) THEN
                    ALTER TABLE carritos
                    ADD CONSTRAINT fk_carritos_dispositivo_id_dispositivos_sincronizacion
                    FOREIGN KEY (dispositivo_id)
                    REFERENCES dispositivos_sincronizacion (id)
                    ON DELETE SET NULL;
                END IF;
            END $$;
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE carritos DROP CONSTRAINT IF EXISTS "
            "fk_carritos_dispositivo_id_dispositivos_sincronizacion"
        )
    )
    conn = op.get_bind()
    if "dispositivos_sincronizacion" in sa.inspect(conn).get_table_names():
        op.drop_table("dispositivos_sincronizacion")

