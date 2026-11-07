"""ex2_022 - Plan Empresarial, límites y respaldos (punto 6)

[EXAMEN-2] Fase B / RF-035 / RN-023 / RNF-19: columnas nuevas de `planes`,
tablas `backups` y `restauraciones`. El plan Empresarial **no** se siembra
aquí: lo hace `DatosInicialesService` / `PlanService.sembrar_planes` de forma
idempotente, para que las pruebas también lo tengan.

Revision ID: ex2_022
Revises: ex2_021
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_022"
down_revision: Union[str, None] = "ex2_021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNAS_PLAN = (
    ("max_usuarios", sa.Integer(), True, None),
    ("max_productos", sa.Integer(), True, None),
    ("backup_automatico", sa.Boolean(), False, sa.text("false")),
    ("backup_frecuencia_horas", sa.Integer(), True, None),
    ("backup_retencion_dias", sa.Integer(), False, sa.text("7")),
    ("backup_manual_mensual", sa.Integer(), True, None),
    ("permite_restauracion", sa.Boolean(), False, sa.text("false")),
)


def _tablas() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columnas(tabla: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if tabla not in inspector.get_table_names():
        return set()
    return {columna["name"] for columna in inspector.get_columns(tabla)}


def upgrade() -> None:
    existentes = _columnas("planes")
    for nombre, tipo, nullable, default in _COLUMNAS_PLAN:
        if nombre in existentes:
            continue
        columna = sa.Column(nombre, tipo, nullable=nullable, server_default=default)
        op.add_column("planes", columna)

    if "backups" not in _tablas():
        op.create_table(
            "backups",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "tipo", sa.Enum("MANUAL", "AUTOMATICO", name="tipobackup"), nullable=False
            ),
            sa.Column(
                "estado",
                sa.Enum(
                    "EN_PROCESO", "COMPLETADO", "FALLIDO", "EXPIRADO",
                    name="estadobackup",
                ),
                nullable=False,
                server_default="EN_PROCESO",
            ),
            sa.Column(
                "fecha_inicio", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column("fecha_fin", sa.DateTime(timezone=True), nullable=True),
            sa.Column("tamano_bytes", sa.BigInteger(), nullable=True),
            sa.Column("ruta", sa.String(500), nullable=True),
            sa.Column("hash_sha256", sa.String(64), nullable=True),
            sa.Column("retencion_hasta", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "creado_por",
                sa.Integer(),
                sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("error_detalle", sa.Text(), nullable=True),
        )

    if "restauraciones" not in _tablas():
        op.create_table(
            "restauraciones",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "backup_id",
                sa.Integer(),
                sa.ForeignKey("backups.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "solicitado_por",
                sa.Integer(),
                sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "estado",
                sa.Enum(
                    "SOLICITADA", "EN_PROCESO", "COMPLETADA", "FALLIDA",
                    name="estadorestauracion",
                ),
                nullable=False,
                server_default="SOLICITADA",
            ),
            sa.Column("motivo", sa.String(200), nullable=True),
            sa.Column(
                "fecha_solicitud",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column("fecha_inicio", sa.DateTime(timezone=True), nullable=True),
            sa.Column("fecha_fin", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_detalle", sa.Text(), nullable=True),
        )

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_backups_tenant_fecha "
        "ON backups (tenant_id, fecha_inicio)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_backups_tenant_estado "
        "ON backups (tenant_id, estado)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_restauraciones_tenant_fecha "
        "ON restauraciones (tenant_id, fecha_solicitud)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_restauraciones_tenant_fecha"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_backups_tenant_estado"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_backups_tenant_fecha"))
    inspector = sa.inspect(op.get_bind())
    tablas = inspector.get_table_names()
    if "restauraciones" in tablas:
        op.drop_table("restauraciones")
    if "backups" in tablas:
        op.drop_table("backups")
    op.execute(sa.text("DROP TYPE IF EXISTS estadorestauracion"))
    op.execute(sa.text("DROP TYPE IF EXISTS estadobackup"))
    op.execute(sa.text("DROP TYPE IF EXISTS tipobackup"))

    columnas = _columnas("planes")
    if "planes" in tablas:
        for nombre, *_ in reversed(_COLUMNAS_PLAN):
            if nombre in columnas:
                op.drop_column("planes", nombre)
