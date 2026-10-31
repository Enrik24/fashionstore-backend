"""ex2_012 - Carrito, orden y detalle offline

[EXAMEN-2] Fase 2 (plan, 3.7.9 y 9.2.3). Garantiza las columnas offline del
carrito, la orden y el detalle; las migraciones de Fase 1 ya agregaron la
mayoría, por lo que esta revisión es idempotente.

Revision ID: ex2_012_carrito_offline
Revises: ex2_011
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_012"
down_revision: Union[str, None] = "ex2_011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNAS = (
    ("carritos", "uuid_carrito", "uuid"),
    ("carritos", "dispositivo_id", "uuid"),
    ("carritos", "es_offline", "boolean NOT NULL DEFAULT false"),
    ("carritos", "estado_sincronizacion", "estado_sincronizacion NOT NULL DEFAULT 'NO_APLICA'"),
    ("carritos", "updated_at", "timestamptz DEFAULT now()"),
    ("carritos", "deleted_at", "timestamptz"),
    ("items_carrito", "precio_unitario_local", "numeric(10,2)"),
    ("items_carrito", "stock_local_conocido", "integer"),
    ("ordenes", "uuid_orden", "uuid"),
    ("ordenes", "origen", "origen_operacion NOT NULL DEFAULT 'ONLINE'"),
    ("ordenes", "es_offline", "boolean NOT NULL DEFAULT false"),
    ("ordenes", "estado_sincronizacion", "estado_sincronizacion NOT NULL DEFAULT 'NO_APLICA'"),
    ("ordenes", "fecha_limite_pago", "timestamptz"),
    ("ordenes", "expira_en", "timestamptz"),
    ("ordenes", "moneda", "varchar(3) NOT NULL DEFAULT 'BOB'"),
    ("detalles_orden", "precio_unitario_local", "numeric(10,2)"),
    ("detalles_orden", "precio_unitario_servidor", "numeric(10,2)"),
    ("ventas_presenciales", "uuid_offline", "uuid"),
    ("ventas_presenciales", "es_offline", "boolean NOT NULL DEFAULT false"),
    ("reservas", "uuid_reserva", "uuid"),
)


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TYPE estadocarrito ADD VALUE IF NOT EXISTS 'SINCRONIZANDO'"
    ))
    op.execute(sa.text(
        "ALTER TYPE estado_sincronizacion ADD VALUE IF NOT EXISTS 'NO_APLICA'"
    ))

    inspector = sa.inspect(op.get_bind())
    for tabla, columna, tipo in COLUMNAS:
        existentes = {c["name"] for c in inspector.get_columns(tabla)}
        if columna not in existentes:
            op.execute(sa.text(
                f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {columna} {tipo}"
            ))

    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_carritos_tenant_uuid "
        "ON carritos (tenant_id, uuid_carrito)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_carritos_uuid_carrito "
        "ON carritos (uuid_carrito)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_carritos_tenant_dispositivo_cliente "
        "ON carritos (tenant_id, dispositivo_id, cliente_id)"
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_carritos_activo_dispositivo_cliente "
        "ON carritos (tenant_id, dispositivo_id, cliente_id) "
        "WHERE estado = 'ACTIVO'"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_ordenes_tenant_uuid_orden "
        "ON ordenes (tenant_id, uuid_orden)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_ordenes_estado_expira_en "
        "ON ordenes (estado, expira_en)"
    ))


def downgrade() -> None:
    # Las columnas pueden existir desde ex2_003 y `uq_carritos_tenant_uuid` es
    # una restricción creada en ex2_006; no se eliminan para no perder datos
    # históricos del Examen 1.
    op.execute(sa.text("DROP INDEX IF EXISTS uq_carritos_activo_dispositivo_cliente"))
