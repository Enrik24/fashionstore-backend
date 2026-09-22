"""recepciones proveedor opcion A

Revision ID: a4a7a48283b4
Revises: f6a7b8c9d0e1
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4a7a48283b4'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tablas de recepción directa por proveedor (Opción A). Idempotente.
    # Nota: una sentencia por op.execute porque asyncpg no permite
    # múltiples órdenes en una sentencia preparada.
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS recepciones (
        id SERIAL PRIMARY KEY,
        numero VARCHAR(30) UNIQUE NOT NULL,
        proveedor_id INTEGER NOT NULL REFERENCES proveedores(id),
        sucursal_id INTEGER NOT NULL REFERENCES sucursales(id),
        fecha_hora TIMESTAMPTZ DEFAULT now(),
        nro_factura VARCHAR(50),
        observaciones VARCHAR(500),
        total_unidades INTEGER DEFAULT 0,
        total_costo NUMERIC(12, 2) DEFAULT 0,
        creado_por INTEGER REFERENCES usuarios(id) ON DELETE SET NULL
    )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_recepciones_id ON recepciones (id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_recepciones_numero ON recepciones (numero)"))
    op.execute(sa.text("""
    CREATE TABLE IF NOT EXISTS detalles_recepcion (
        id SERIAL PRIMARY KEY,
        recepcion_id INTEGER NOT NULL REFERENCES recepciones(id) ON DELETE CASCADE,
        variante_producto_id INTEGER NOT NULL REFERENCES variantes_producto(id),
        cantidad INTEGER NOT NULL CHECK (cantidad >= 1),
        costo_unitario NUMERIC(10, 2)
    )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_detalles_recepcion_id ON detalles_recepcion (id)"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS detalles_recepcion"))
    op.execute(sa.text("DROP TABLE IF EXISTS recepciones"))
