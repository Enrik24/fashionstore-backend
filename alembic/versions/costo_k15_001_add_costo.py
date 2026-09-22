"""Migración manual: agregar columnas de costo para beneficio real K15."""
from alembic import op
import sqlalchemy as sa

revision = "costo_k15_001"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    cols_prod = [c["name"] for c in sa.inspect(conn).get_columns("productos")]
    if "costo_compra" not in cols_prod:
        op.add_column("productos", sa.Column("costo_compra", sa.Numeric(10, 2), nullable=False, server_default="0"))
    cols_var = [c["name"] for c in sa.inspect(conn).get_columns("variantes_producto")]
    if "costo_variante" not in cols_var:
        op.add_column("variantes_producto", sa.Column("costo_variante", sa.Numeric(10, 2), nullable=True))
    cols_det = [c["name"] for c in sa.inspect(conn).get_columns("detalles_orden")]
    if "costo_unitario" not in cols_det:
        op.add_column("detalles_orden", sa.Column("costo_unitario", sa.Numeric(10, 2), nullable=True))
    # Backfill: costo = 60% del precio para mantener K15 estimado hasta cargar costos reales
    op.execute("UPDATE productos SET costo_compra = ROUND(precio * 0.6, 2) WHERE costo_compra = 0")


def downgrade():
    op.drop_column("detalles_orden", "costo_unitario")
    op.drop_column("variantes_producto", "costo_variante")
    op.drop_column("productos", "costo_compra")
