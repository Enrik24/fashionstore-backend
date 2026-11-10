"""ex2_008 - Nuevos valores de enums del Examen 2

[EXAMEN-2] Fase 1 (plan, 3.8.5 y 9.1.4). Agrega los valores usados por la
operación offline, el pago diferido y las devoluciones.

Nota: PostgreSQL no permite eliminar valores de un enum sin recrear el tipo.
El `downgrade` es un no-op deliberado; las migraciones anteriores eliminan las
columnas que usan estos valores y nunca se pierden datos.

Revision ID: ex2_008_nuevos_valores_enum
Revises: ex2_007_permisos_plataforma
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_008"
down_revision: Union[str, None] = "ex2_007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VALORES = {
    "estadoorden": (
        "PENDIENTE_PAGO_DIFERIDO",
        "PAGO_VENCIDO",
        "PENDIENTE_REGULARIZACION",
    ),
    "estadocarrito": ("SINCRONIZANDO",),
    "estadotransaccion": ("EXPIRADO", "CANCELADO"),
    "tipomovimiento": ("TRASPASO_SALIDA", "TRASPASO_ENTRADA", "MERMA", "CAMBIO"),
    "estadosolicituddevolucion": (
        "PENDIENTE_SINCRONIZACION",
        "CONFLICTO_MANUAL",
        "ERROR_VENTA_INVALIDA",
    ),
}


def upgrade() -> None:
    for enum_name, values in VALORES.items():
        for value in values:
            op.execute(
                sa.text(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'")
            )


def downgrade() -> None:
    # Irreversible por diseño: PostgreSQL no soporta DROP VALUE.
    pass

