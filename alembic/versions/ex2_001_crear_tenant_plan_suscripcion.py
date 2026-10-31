"""ex2_001 - Crear tenants, planes, suscripciones y sus enums

[EXAMEN-2] Fase 1 (plan, 9.1.4). Crea las tablas raÃ­z del SaaS multitenant.
La migraciÃ³n es idempotente: si la tabla o el tipo ya existen, no falla.

Revision ID: ex2_001_crear_tenant_plan_suscripcion
Revises: k17_fcm_token_unique
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "ex2_001"
down_revision: Union[str, None] = "k17_fcm_token_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ENUMS = {
    "estado_tenant": (
        "CONFIGURACION_INICIAL",
        "OPERATIVO",
        "SUSPENDIDO",
        "CANCELADO",
    ),
    "estado_plan": ("ACTIVO", "INACTIVO"),
    "estado_suscripcion": ("ACTIVA", "SUSPENDIDA", "CANCELADA", "VENCIDA"),
}


def _create_enum(name: str, values: tuple[str, ...]) -> None:
    labels = ", ".join(f"'{value}'" for value in values)
    op.execute(
        sa.text(
            f"""
            DO $$ BEGIN
                CREATE TYPE {name} AS ENUM ({labels});
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
            """
        )
    )


def _enum(name: str) -> postgresql.ENUM:
    """Enum ya creado: evita que Alembic intente crearlo otra vez."""
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())

    for name, values in ENUMS.items():
        _create_enum(name, values)

    if "planes" not in tables:
        op.create_table(
            "planes",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("nombre", sa.String(length=50), nullable=False),
            sa.Column("descripcion", sa.Text(), nullable=True),
            sa.Column("precio_mensual", sa.Numeric(precision=10, scale=2), nullable=False),
            sa.Column("max_sucursales", sa.Integer(), nullable=False),
            sa.Column("permite_reportes_ia", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("permite_pago_diferido", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("estado", _enum("estado_plan"), server_default="ACTIVO", nullable=False),
            sa.Column("orden", sa.Integer(), server_default="0", nullable=False),
            sa.Column("soporte_prioritario", sa.Boolean(), server_default="false", nullable=False),
            sa.CheckConstraint("max_sucursales > 0", name="ck_planes_max_sucursales_positivo"),
            sa.CheckConstraint("precio_mensual >= 0", name="ck_planes_precio_no_negativo"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("nombre", name="planes_nombre_key"),
        )
        op.create_index(op.f("ix_planes_id"), "planes", ["id"], unique=False)

    if "tenants" not in tables:
        op.create_table(
            "tenants",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("razon_social", sa.String(length=200), nullable=False),
            sa.Column("nit", sa.String(length=50), nullable=False),
            sa.Column("direccion_fiscal", sa.String(length=500), nullable=False),
            sa.Column("correo_contacto", sa.String(length=255), nullable=False),
            sa.Column("telefono", sa.String(length=30), nullable=True),
            sa.Column("logo_url", sa.String(length=500), nullable=True),
            sa.Column("moneda", sa.String(length=3), server_default="BOB", nullable=False),
            sa.Column("zona_horaria", sa.String(length=50), server_default="America/La_Paz", nullable=False),
            sa.Column("estado", _enum("estado_tenant"), server_default="CONFIGURACION_INICIAL", nullable=False),
            sa.Column("fecha_registro", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("slug", sa.String(length=60), nullable=False),
            sa.Column("fecha_baja", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_tenants_id"), "tenants", ["id"], unique=False)
        op.create_index(op.f("ix_tenants_nit"), "tenants", ["nit"], unique=True)
        op.create_index(op.f("ix_tenants_slug"), "tenants", ["slug"], unique=True)
        op.create_index(op.f("ix_tenants_correo_contacto"), "tenants", ["correo_contacto"], unique=False)

    if "suscripciones" not in tables:
        op.create_table(
            "suscripciones",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("fecha_inicio", sa.Date(), nullable=False),
            sa.Column("fecha_vencimiento", sa.Date(), nullable=False),
            sa.Column("estado", _enum("estado_suscripcion"), nullable=False),
            sa.Column("estado_pago", sa.String(length=20), server_default="PENDIENTE", nullable=False),
            sa.Column("stripe_customer_id", sa.String(length=120), nullable=True),
            sa.Column("referencia_ultimo_pago", sa.String(length=120), nullable=True),
            sa.Column("renovacion_automatica", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("motivo_estado", sa.Text(), nullable=True),
            sa.CheckConstraint("fecha_vencimiento > fecha_inicio", name="ck_suscripciones_vigencia"),
            sa.ForeignKeyConstraint(["plan_id"], ["planes.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_suscripciones_id"), "suscripciones", ["id"], unique=False)
        op.create_index(op.f("ix_suscripciones_tenant_id"), "suscripciones", ["tenant_id"], unique=False)
        op.create_index(op.f("ix_suscripciones_plan_id"), "suscripciones", ["plan_id"], unique=False)
        op.create_index("ix_suscripciones_tenant_estado", "suscripciones", ["tenant_id", "estado"], unique=False)

    # Una sola suscripciÃ³n ACTIVA por tenant (plan, 3.6)
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_suscripciones_tenant_activa
            ON suscripciones (tenant_id)
            WHERE estado = 'ACTIVA'
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())

    if "suscripciones" in tables:
        op.execute(sa.text("DROP INDEX IF EXISTS uq_suscripciones_tenant_activa"))
        op.drop_table("suscripciones")
    if "tenants" in tables:
        op.drop_table("tenants")
    if "planes" in tables:
        op.drop_table("planes")

    for name in ("estado_suscripcion", "estado_tenant", "estado_plan"):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {name}"))

