"""ex2_023 - Reposición separada de devolución (CU18)

[EXAMEN-2] Fase C / RF-031 / RN-025. Nota: PostgreSQL no permite quitar
valores de un enum, por eso `downgrade()` conserva los valores nuevos y solo
elimina columnas e índices.

Revision ID: ex2_023
Revises: ex2_022
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_023"
down_revision: Union[str, None] = "ex2_022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_VALORES = {
    "tiposolicituddevolucion": ("REPOSICION",),
    "tipomovimiento": ("REPOSICION_ENTRADA", "REPOSICION_SALIDA"),
    "tipo_operacion_offline": ("REPOSICION",),
    "estadosolicituddevolucion": ("SIN_ALTERNATIVA",),
}


def _columnas(tabla: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if tabla not in inspector.get_table_names():
        return set()
    return {columna["name"] for columna in inspector.get_columns(tabla)}


def upgrade() -> None:
    for enum_name, valores in _VALORES.items():
        for valor in valores:
            op.execute(sa.text(
                f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{valor}'"
            ))

    columnas = _columnas("solicitudes_devolucion")
    if "numero_reposicion" not in columnas:
        op.add_column(
            "solicitudes_devolucion",
            sa.Column("numero_reposicion", sa.String(50), nullable=True),
        )
    if "nota_reposicion_url" not in columnas:
        op.add_column(
            "solicitudes_devolucion",
            sa.Column("nota_reposicion_url", sa.String(500), nullable=True),
        )

    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_solicitudes_tenant_numero_reposicion "
        "ON solicitudes_devolucion (tenant_id, numero_reposicion) "
        "WHERE numero_reposicion IS NOT NULL"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS uq_solicitudes_tenant_numero_reposicion"
    ))
    columnas = _columnas("solicitudes_devolucion")
    if "nota_reposicion_url" in columnas:
        op.drop_column("solicitudes_devolucion", "nota_reposicion_url")
    if "numero_reposicion" in columnas:
        op.drop_column("solicitudes_devolucion", "numero_reposicion")
    # Irreversible por diseño: los valores de enum permanecen.
