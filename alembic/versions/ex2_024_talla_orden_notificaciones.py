"""ex2_024 - Orden de talla y notificaciones al cliente (punto 5)

[EXAMEN-2] Fase D / RF-034 / RF-037 / RN-024.

Revision ID: ex2_024
Revises: ex2_023
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_024"
down_revision: Union[str, None] = "ex2_023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mapa del plan (6.2). Los valores numéricos se multiplican por 10.
_ORDENES = {
    "XXS": 5, "2XS": 5,
    "XS": 10,
    "S": 20,
    "M": 30,
    "L": 40,
    "XL": 50,
    "XXL": 60, "2XL": 60,
    "XXXL": 70, "3XL": 70,
}
for _numero in range(34, 47, 2):
    _ORDENES[str(_numero)] = _numero * 10


def _columnas(tabla: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if tabla not in inspector.get_table_names():
        return set()
    return {columna["name"] for columna in inspector.get_columns(tabla)}


def upgrade() -> None:
    if "orden" not in _columnas("tallas"):
        op.add_column("tallas", sa.Column("orden", sa.Integer(), nullable=True))

    for valor, orden in _ORDENES.items():
        op.execute(
            sa.text(
                "UPDATE tallas SET orden = :orden "
                "WHERE upper(valor) = :valor AND orden IS NULL"
            ).bindparams(orden=orden, valor=valor)
        )

    if "notificaciones_cliente" not in set(
        sa.inspect(op.get_bind()).get_table_names()
    ):
        op.create_table(
            "notificaciones_cliente",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "cliente_id",
                sa.Integer(),
                sa.ForeignKey("clientes.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "usuario_id",
                sa.Integer(),
                sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("tipo", sa.String(50), nullable=False),
            sa.Column(
                "canal",
                sa.Enum("PUSH", "CORREO", "SMS", name="canalnotificacion"),
                nullable=False,
            ),
            sa.Column("asunto", sa.String(150), nullable=True),
            sa.Column("mensaje", sa.Text(), nullable=False),
            sa.Column(
                "estado",
                sa.Enum(
                    "PENDIENTE", "ENVIADA", "FALLIDA", "LEIDA",
                    name="estadonotificacion",
                ),
                nullable=False,
                server_default="PENDIENTE",
            ),
            sa.Column("referencia_tipo", sa.String(50), nullable=True),
            sa.Column("referencia_id", sa.Integer(), nullable=True),
            sa.Column(
                "fecha_creacion",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column("fecha_envio", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "leida", sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
            sa.Column("error_detalle", sa.Text(), nullable=True),
        )

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_notificaciones_cliente_fecha "
        "ON notificaciones_cliente (tenant_id, fecha_creacion)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_notificaciones_cliente_estado "
        "ON notificaciones_cliente (tenant_id, estado)"
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_notificaciones_cliente_referencia "
        "ON notificaciones_cliente "
        "(tenant_id, tipo, referencia_tipo, referencia_id, canal) "
        "WHERE referencia_id IS NOT NULL"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_notificaciones_cliente_referencia"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_notificaciones_cliente_estado"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_notificaciones_cliente_fecha"))
    inspector = sa.inspect(op.get_bind())
    if "notificaciones_cliente" in inspector.get_table_names():
        op.drop_table("notificaciones_cliente")
    op.execute(sa.text("DROP TYPE IF EXISTS estadonotificacion"))
    op.execute(sa.text("DROP TYPE IF EXISTS canalnotificacion"))
    if "orden" in _columnas("tallas"):
        op.drop_column("tallas", "orden")
