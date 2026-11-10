"""ex2_005 - tenant_id NOT NULL en tablas de negocio

[EXAMEN-2] Fase 1 (plan, 9.1.4). Después del backfill, convierte `tenant_id`
en obligatorio en las tablas de negocio. Las tablas de plataforma
(`usuarios`, `roles`, `bitacora`, `reportes`, `indicadores_kpi`,
`pasarelas_pago`) conservan la columna nullable.

Revision ID: ex2_005_tenant_id_not_null
Revises: ex2_004_backfill_tenant_demo
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_005"
down_revision: Union[str, None] = "ex2_004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = [
    "administradores", "cajeros", "encargados_sucursal", "carritos",
    "categorias", "clientes", "colecciones", "colores", "comprobantes",
    "cupones", "detalles_orden", "detalles_reserva",
    "detalles_solicitud_devolucion", "inventario", "items_carrito",
    "movimientos_inventario", "ordenes", "productos", "promociones",
    "proveedores", "recepciones", "reservas", "solicitudes_devolucion",
    "sucursales", "tallas", "temporadas", "transacciones_pago",
    "usuarios_roles", "variantes_producto", "ventas_presenciales",
]


def _tables_with_tenant(conn) -> set[str]:
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())
    result = set()
    for table in TABLES:
        if table not in existing:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "tenant_id" in columns:
            result.add(table)
    return result


def upgrade() -> None:
    conn = op.get_bind()
    tables = _tables_with_tenant(conn)

    # Red de seguridad: nada de negocio debe quedar sin tenant.
    for table in tables:
        op.execute(
            sa.text(
                f"""
                UPDATE {table} SET tenant_id = (
                    SELECT id FROM tenants WHERE slug = 'fashionstore-demo'
                )
                WHERE tenant_id IS NULL
                  AND EXISTS (
                      SELECT 1 FROM tenants WHERE slug = 'fashionstore-demo'
                  )
                """
            )
        )

    for table in tables:
        op.execute(
            sa.text(f"ALTER TABLE {table} ALTER COLUMN tenant_id SET NOT NULL")
        )


def downgrade() -> None:
    conn = op.get_bind()
    for table in _tables_with_tenant(conn):
        op.execute(
            sa.text(f"ALTER TABLE {table} ALTER COLUMN tenant_id DROP NOT NULL")
        )

