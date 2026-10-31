"""ex2_010 - Operaciones offline y conflictos

[EXAMEN-2] Fase 2 (plan, 3.3.4/3.3.5 y 9.2.3). Crea la cola de operaciones
offline, el log de conflictos y el enum `tipo_operacion_offline`.

Revision ID: ex2_010_operaciones_offline
Revises: ex2_009
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "ex2_010"
down_revision: Union[str, None] = "ex2_009"
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
    _create_type("tipo_operacion_offline", (
        "RESERVA",
        "VENTA_CONTADO",
        "CARRITO",
        "ORDEN_PAGO_DIFERIDO",
        "DEVOLUCION_CAMBIO",
        "PAGO_DIFERIDO",
    ))

    conn = op.get_bind()
    tablas = set(sa.inspect(conn).get_table_names())

    if "operaciones_offline" not in tablas:
        op.create_table(
            "operaciones_offline",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("dispositivo_id", sa.Uuid(), nullable=False),
            sa.Column("usuario_id", sa.Integer(), nullable=False),
            sa.Column("uuid_operacion", sa.Uuid(), nullable=False),
            sa.Column("secuencia_local", sa.BigInteger(), nullable=False),
            sa.Column(
                "tipo_operacion",
                postgresql.ENUM(
                    "RESERVA", "VENTA_CONTADO", "CARRITO",
                    "ORDEN_PAGO_DIFERIDO", "DEVOLUCION_CAMBIO", "PAGO_DIFERIDO",
                    name="tipo_operacion_offline",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column(
                "estado_sincronizacion",
                postgresql.ENUM(
                    "PENDIENTE", "ENVIADA", "CONFIRMADA", "RECHAZADA",
                    "CONFLICTO", "DUPLICADA", "ERROR_CRITICO", "NO_APLICA",
                    name="estado_sincronizacion",
                    create_type=False,
                ),
                server_default="PENDIENTE",
                nullable=False,
            ),
            sa.Column("intentos", sa.Integer(), server_default="0", nullable=False),
            sa.Column("fecha_registro_local", sa.DateTime(timezone=True), nullable=False),
            sa.Column("fecha_recepcion_servidor", sa.DateTime(timezone=True), nullable=True),
            sa.Column("fecha_ultimo_intento", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_detalle", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("sucursal_id", sa.Integer(), nullable=True),
            sa.Column("resultado_json", sa.JSON(), nullable=True),
            sa.Column("motivo_rechazo", sa.Text(), nullable=True),
            sa.Column("requiere_revision", sa.Boolean(), server_default="false", nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dispositivo_id"], ["dispositivos_sincronizacion.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["sucursal_id"], ["sucursales.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("uuid_operacion", name="uq_operaciones_offline_uuid"),
        )

    for nombre, columnas in (
        ("ix_operaciones_offline_tenant_id", ("tenant_id",)),
        ("ix_operaciones_offline_dispositivo_id", ("dispositivo_id",)),
        ("ix_operaciones_offline_uuid_operacion", ("uuid_operacion",)),
        ("ix_operaciones_offline_estado_sincronizacion", ("estado_sincronizacion",)),
    ):
        columnas_sql = ", ".join(columnas)
        op.execute(sa.text(
            f"CREATE INDEX IF NOT EXISTS {nombre} "
            f"ON operaciones_offline ({columnas_sql})"
        ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_operaciones_offline_tenant_estado_fecha "
        "ON operaciones_offline (tenant_id, estado_sincronizacion, fecha_registro_local)"
    ))

    if "log_conflictos" not in tablas:
        op.create_table(
            "log_conflictos",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("operacion_offline_id", sa.Integer(), nullable=False),
            sa.Column("tipo_conflicto", sa.String(length=50), nullable=False),
            sa.Column("detalle_json", sa.JSON(), nullable=False),
            sa.Column("resuelto", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("resolucion", sa.String(length=50), nullable=True),
            sa.Column("resuelto_por", sa.Integer(), nullable=True),
            sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("estrategia_resolucion", sa.String(length=50), nullable=True),
            sa.Column("fecha_resolucion", sa.DateTime(timezone=True), nullable=True),
            sa.Column("observaciones", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["operacion_offline_id"], ["operaciones_offline.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["resuelto_por"], ["usuarios.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    for nombre, columnas in (
        ("ix_log_conflictos_tenant_id", ("tenant_id",)),
        ("ix_log_conflictos_operacion_offline_id", ("operacion_offline_id",)),
    ):
        columnas_sql = ", ".join(columnas)
        op.execute(sa.text(
            f"CREATE INDEX IF NOT EXISTS {nombre} "
            f"ON log_conflictos ({columnas_sql})"
        ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_log_conflictos_operacion_resuelto "
        "ON log_conflictos (operacion_offline_id, resuelto)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS log_conflictos"))
    op.execute(sa.text("DROP TABLE IF EXISTS operaciones_offline"))
    op.execute(sa.text("DROP TYPE IF EXISTS tipo_operacion_offline"))
