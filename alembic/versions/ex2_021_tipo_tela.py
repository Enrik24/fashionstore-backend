"""ex2_021 - Tipo de tela y composición (punto 3)

[EXAMEN-2] Fase A / RF-033 / RN-021: catálogo de telas por tenant, referencia
desde `productos` (ON DELETE SET NULL para conservar el histórico) y texto
libre de composición.

Revision ID: ex2_021
Revises: ex2_020
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_021"
down_revision: Union[str, None] = "ex2_020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tablas() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columnas(tabla: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if tabla not in inspector.get_table_names():
        return set()
    return {columna["name"] for columna in inspector.get_columns(tabla)}


def upgrade() -> None:
    tablas = _tablas()

    if "tipos_tela" not in tablas:
        op.create_table(
            "tipos_tela",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("nombre", sa.String(80), nullable=False),
            sa.Column("descripcion", sa.Text(), nullable=True),
            sa.Column("cuidados_lavado", sa.String(200), nullable=True),
            sa.Column("estado", sa.String(20), nullable=False, server_default="ACTIVO"),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("tenant_id", "nombre", name="uq_tipos_tela_tenant_nombre"),
        )

    columnas = _columnas("productos")
    if "tipo_tela_id" not in columnas:
        op.add_column("productos", sa.Column("tipo_tela_id", sa.Integer(), nullable=True))
    if "composicion" not in columnas:
        op.add_column("productos", sa.Column("composicion", sa.String(200), nullable=True))

    # La FK se crea aparte para poder declararla con SET NULL e idempotencia.
    inspector = sa.inspect(op.get_bind())
    fks = {fk["name"] for fk in inspector.get_foreign_keys("productos")}
    if "fk_productos_tipo_tela" not in fks:
        op.create_foreign_key(
            "fk_productos_tipo_tela",
            "productos",
            "tipos_tela",
            ["tipo_tela_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_tipos_tela_tenant_estado "
        "ON tipos_tela (tenant_id, estado)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_productos_tenant_tipo_tela "
        "ON productos (tenant_id, tipo_tela_id)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_productos_tenant_tipo_tela"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_tipos_tela_tenant_estado"))
    inspector = sa.inspect(op.get_bind())
    if "productos" in inspector.get_table_names():
        fks = {fk["name"] for fk in inspector.get_foreign_keys("productos")}
        if "fk_productos_tipo_tela" in fks:
            op.drop_constraint("fk_productos_tipo_tela", "productos", type_="foreignkey")
        columnas = {columna["name"] for columna in inspector.get_columns("productos")}
        if "composicion" in columnas:
            op.drop_column("productos", "composicion")
        if "tipo_tela_id" in columnas:
            op.drop_column("productos", "tipo_tela_id")
    if "tipos_tela" in inspector.get_table_names():
        op.drop_table("tipos_tela")
