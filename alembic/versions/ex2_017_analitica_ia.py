"""ex2_017 - Analítica avanzada e IA (CU10/CU12)

[EXAMEN-2] Fase 3 (plan, 3.3.7, 3.3.8 y 9.3.3). Crea `solicitudes_reporte_ia`
y `modelos_ml` con sus enums e índices.

Revision ID: ex2_017_analitica_ia
Revises: ex2_016
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "ex2_017"
down_revision: Union[str, None] = "ex2_016"
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
    _create_type("estado_solicitud_reporte_ia", (
        "INTERPRETADA", "AMBIGUA", "NO_SOPORTADA", "EJECUTADA", "ERROR",
    ))
    _create_type("tipo_modelo_ml", ("SUPERVISADO", "NO_SUPERVISADO"))

    tablas = set(sa.inspect(op.get_bind()).get_table_names())

    if "solicitudes_reporte_ia" not in tablas:
        op.create_table(
            "solicitudes_reporte_ia",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("usuario_id", sa.Integer(), nullable=True),
            sa.Column("prompt_original", sa.Text(), nullable=False),
            sa.Column("query_sql_generada", sa.Text(), nullable=True),
            sa.Column(
                "estado",
                postgresql.ENUM(
                    "INTERPRETADA", "AMBIGUA", "NO_SOPORTADA", "EJECUTADA", "ERROR",
                    name="estado_solicitud_reporte_ia",
                    create_type=False,
                ),
                server_default="INTERPRETADA",
                nullable=False,
            ),
            sa.Column("resultado_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.Column("tiempo_ms", sa.Integer(), nullable=True),
            sa.Column("error_detalle", sa.Text(), nullable=True),
            sa.Column("fecha_ejecucion", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("plantilla_usada", sa.String(length=80), nullable=True),
            sa.Column("parametros_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.Column("resultado_resumen_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_solicitudes_reporte_ia_tenant_id", "solicitudes_reporte_ia", ["tenant_id"])
        op.create_index(
            "ix_solicitudes_reporte_ia_tenant_fecha",
            "solicitudes_reporte_ia",
            ["tenant_id", "fecha_ejecucion"],
        )

    if "modelos_ml" not in tablas:
        op.create_table(
            "modelos_ml",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("nombre", sa.String(length=100), nullable=False),
            sa.Column(
                "tipo",
                postgresql.ENUM(
                    "SUPERVISADO", "NO_SUPERVISADO",
                    name="tipo_modelo_ml",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("version", sa.String(length=20), server_default="1.0", nullable=False),
            sa.Column("metricas_json", postgresql.JSON(astext_type=sa.Text()), nullable=False),
            sa.Column("fecha_entrenamiento", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("estado", sa.String(length=20), server_default="ACTIVO", nullable=False),
            sa.Column("algoritmo", sa.String(length=80), nullable=False),
            sa.Column("objetivo", sa.String(length=40), nullable=False),
            sa.Column("ruta_artefacto", sa.String(length=500), nullable=False),
            sa.Column("datos_entrenamiento_json", postgresql.JSON(astext_type=sa.Text()), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_modelos_ml_tenant_id", "modelos_ml", ["tenant_id"])
        op.create_index("ix_modelos_ml_tenant_objetivo", "modelos_ml", ["tenant_id", "objetivo"])


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS modelos_ml CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS solicitudes_reporte_ia CASCADE"))
    op.execute(sa.text("DROP TYPE IF EXISTS tipo_modelo_ml"))
    op.execute(sa.text("DROP TYPE IF EXISTS estado_solicitud_reporte_ia"))
