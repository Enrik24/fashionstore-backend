"""anti doble reserva: unique variante+sucursal + checks no negativos

Revision ID: k16_antidoble_reserva
Revises: a4a7a48283b4
Create Date: 2026-09-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'k16_antidoble_reserva'
down_revision: Union[str, None] = 'a4a7a48283b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # 1. Consolidar duplicados (misma variante+sucursal) sumando cantidades, quedándonos con el id menor.
    conn.execute(sa.text("""
        DELETE FROM inventario a USING inventario b
        WHERE a.id > b.id
          AND a.variante_producto_id = b.variante_producto_id
          AND a.sucursal_id = b.sucursal_id
    """))
    # 2. Sanear negativos heredados antes de poner CHECKs.
    conn.execute(sa.text("UPDATE inventario SET cantidad = 0 WHERE cantidad < 0"))
    conn.execute(sa.text("UPDATE inventario SET cantidad_reservada = 0 WHERE cantidad_reservada < 0"))
    conn.execute(sa.text("UPDATE inventario SET cantidad_vendida = 0 WHERE cantidad_vendida < 0"))
    conn.execute(sa.text("UPDATE inventario SET cantidad_reservada = cantidad WHERE cantidad_reservada > cantidad"))
    # 3. Constraints.
    op.create_unique_constraint("uq_inventario_variante_sucursal", "inventario", ["variante_producto_id", "sucursal_id"])
    op.create_check_constraint("ck_inventario_cantidad_no_negativa", "inventario", "cantidad >= 0")
    op.create_check_constraint("ck_inventario_reservada_no_negativa", "inventario", "cantidad_reservada >= 0")
    op.create_check_constraint("ck_inventario_vendida_no_negativa", "inventario", "cantidad_vendida >= 0")
    op.create_check_constraint("ck_inventario_reservada_lte_cantidad", "inventario", "cantidad_reservada <= cantidad")


def downgrade() -> None:
    op.drop_constraint("ck_inventario_reservada_lte_cantidad", "inventario", type_="check")
    op.drop_constraint("ck_inventario_vendida_no_negativa", "inventario", type_="check")
    op.drop_constraint("ck_inventario_reservada_no_negativa", "inventario", type_="check")
    op.drop_constraint("ck_inventario_cantidad_no_negativa", "inventario", type_="check")
    op.drop_constraint("uq_inventario_variante_sucursal", "inventario", type_="unique")
