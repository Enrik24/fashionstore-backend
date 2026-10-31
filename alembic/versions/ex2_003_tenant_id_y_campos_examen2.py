"""ex2_003 - tenant_id nullable y campos nuevos del Examen 2

[EXAMEN-2] Fase 1 (plan, 9.1.3/9.1.4). Agrega en su app original:
`tenant_id` nullable en raÃ­ces y tablas hijas, campos offline, versiones para
descarga incremental y los campos de devoluciÃ³n/cambio del capÃ­tulo 3.8.

Se usa `ADD COLUMN IF NOT EXISTS` para que la migraciÃ³n pueda aplicarse tambiÃ©n
sobre bases creadas con `Base.metadata.create_all`.

Revision ID: ex2_003_tenant_id_y_campos_examen2
Revises: ex2_002_pagos_suscripcion
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_003"
down_revision: Union[str, None] = "ex2_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (tabla, columna, definiciÃ³n SQL)
COLUMNS: list[tuple[str, str, str]] = [
    # Usuarios y seguridad
    ("usuarios", "tenant_id", "integer"),
    ("usuarios", "offline_pin_hash", "varchar(255)"),
    ("usuarios", "debe_cambiar_contrasena", "boolean NOT NULL DEFAULT false"),
    ("roles", "tenant_id", "integer"),
    ("usuarios_roles", "tenant_id", "integer"),
    ("clientes", "tenant_id", "integer"),
    ("clientes", "telefono", "varchar(20)"),
    ("clientes", "correo", "varchar(255)"),
    ("clientes", "updated_at", "timestamptz DEFAULT now()"),
    ("clientes", "deleted_at", "timestamptz"),
    ("administradores", "tenant_id", "integer"),
    ("encargados_sucursal", "tenant_id", "integer"),
    ("cajeros", "tenant_id", "integer"),
    ("bitacora", "tenant_id", "integer"),
    # CatÃ¡logo
    ("sucursales", "tenant_id", "integer"),
    ("sucursales", "updated_at", "timestamptz DEFAULT now()"),
    ("sucursales", "deleted_at", "timestamptz"),
    ("categorias", "tenant_id", "integer"),
    ("categorias", "updated_at", "timestamptz DEFAULT now()"),
    ("categorias", "deleted_at", "timestamptz"),
    ("tallas", "tenant_id", "integer"),
    ("tallas", "updated_at", "timestamptz DEFAULT now()"),
    ("tallas", "deleted_at", "timestamptz"),
    ("colores", "tenant_id", "integer"),
    ("colores", "updated_at", "timestamptz DEFAULT now()"),
    ("colores", "deleted_at", "timestamptz"),
    ("temporadas", "tenant_id", "integer"),
    ("temporadas", "updated_at", "timestamptz DEFAULT now()"),
    ("temporadas", "deleted_at", "timestamptz"),
    ("colecciones", "tenant_id", "integer"),
    ("colecciones", "deleted_at", "timestamptz"),
    ("proveedores", "tenant_id", "integer"),
    ("proveedores", "updated_at", "timestamptz DEFAULT now()"),
    ("proveedores", "deleted_at", "timestamptz"),
    ("productos", "tenant_id", "integer"),
    ("productos", "version", "bigint NOT NULL DEFAULT 1"),
    ("productos", "updated_at", "timestamptz DEFAULT now()"),
    ("productos", "deleted_at", "timestamptz"),
    ("variantes_producto", "tenant_id", "integer"),
    ("variantes_producto", "version", "bigint NOT NULL DEFAULT 1"),
    ("variantes_producto", "updated_at", "timestamptz DEFAULT now()"),
    ("variantes_producto", "deleted_at", "timestamptz"),
    ("inventario", "tenant_id", "integer"),
    ("inventario", "version", "bigint NOT NULL DEFAULT 1"),
    ("inventario", "updated_at", "timestamptz DEFAULT now()"),
    ("movimientos_inventario", "tenant_id", "integer"),
    ("movimientos_inventario", "referencia_uuid", "uuid"),
    ("recepciones", "tenant_id", "integer"),
    # Marketing / ventas
    ("cupones", "tenant_id", "integer"),
    ("promociones", "tenant_id", "integer"),
    ("carritos", "tenant_id", "integer"),
    ("carritos", "uuid_carrito", "uuid"),
    ("carritos", "dispositivo_id", "uuid"),
    ("carritos", "es_offline", "boolean NOT NULL DEFAULT false"),
    ("carritos", "estado_sincronizacion", "estado_sincronizacion NOT NULL DEFAULT 'NO_APLICA'"),
    ("carritos", "updated_at", "timestamptz DEFAULT now()"),
    ("carritos", "deleted_at", "timestamptz"),
    ("items_carrito", "tenant_id", "integer"),
    ("items_carrito", "precio_unitario_local", "numeric(10,2)"),
    ("items_carrito", "stock_local_conocido", "integer"),
    ("ordenes", "tenant_id", "integer"),
    ("ordenes", "uuid_orden", "uuid"),
    ("ordenes", "origen", "origen_operacion NOT NULL DEFAULT 'ONLINE'"),
    ("ordenes", "es_offline", "boolean NOT NULL DEFAULT false"),
    ("ordenes", "estado_sincronizacion", "estado_sincronizacion NOT NULL DEFAULT 'NO_APLICA'"),
    ("ordenes", "fecha_limite_cambio", "timestamptz"),
    ("ordenes", "fecha_limite_pago", "timestamptz"),
    ("ordenes", "expira_en", "timestamptz"),
    ("ordenes", "moneda", "varchar(3) NOT NULL DEFAULT 'BOB'"),
    ("detalles_orden", "tenant_id", "integer"),
    ("detalles_orden", "precio_unitario_local", "numeric(10,2)"),
    ("detalles_orden", "precio_unitario_servidor", "numeric(10,2)"),
    ("ventas_presenciales", "tenant_id", "integer"),
    ("ventas_presenciales", "es_offline", "boolean NOT NULL DEFAULT false"),
    ("ventas_presenciales", "uuid_offline", "uuid"),
    ("ventas_presenciales", "estado_sincronizacion", "estado_sincronizacion NOT NULL DEFAULT 'NO_APLICA'"),
    ("reservas", "tenant_id", "integer"),
    ("reservas", "uuid_reserva", "uuid"),
    ("reservas", "es_offline", "boolean NOT NULL DEFAULT false"),
    ("reservas", "estado_sincronizacion", "estado_sincronizacion NOT NULL DEFAULT 'NO_APLICA'"),
    ("detalles_reserva", "tenant_id", "integer"),
    ("transacciones_pago", "tenant_id", "integer"),
    ("transacciones_pago", "stripe_payment_intent_id", "varchar(120)"),
    ("transacciones_pago", "origen", "origen_operacion NOT NULL DEFAULT 'ONLINE'"),
    ("transacciones_pago", "idempotency_key", "varchar(255)"),
    ("transacciones_pago", "intentos", "integer NOT NULL DEFAULT 0"),
    ("transacciones_pago", "fecha_expiracion", "timestamptz"),
    ("pasarelas_pago", "tenant_id", "integer"),
    ("pasarelas_pago", "clave_publica", "varchar(255)"),
    ("pasarelas_pago", "configuracion_publica", "json"),
    ("comprobantes", "tenant_id", "integer"),
    ("comprobantes", "uuid_offline", "uuid"),
    ("comprobantes", "es_temporal", "boolean NOT NULL DEFAULT false"),
    # Devoluciones / cambios (CU13)
    ("solicitudes_devolucion", "tenant_id", "integer"),
    ("solicitudes_devolucion", "uuid_offline", "uuid"),
    ("solicitudes_devolucion", "es_offline", "boolean NOT NULL DEFAULT false"),
    ("solicitudes_devolucion", "estado_sincronizacion", "estado_sincronizacion NOT NULL DEFAULT 'NO_APLICA'"),
    ("solicitudes_devolucion", "fecha_limite_cambio", "timestamptz"),
    ("solicitudes_devolucion", "diferencia_precio", "numeric(10,2)"),
    ("solicitudes_devolucion", "tipo_diferencia", "tipodiferencia"),
    ("solicitudes_devolucion", "monto_pagado_diferencia", "numeric(10,2)"),
    ("solicitudes_devolucion", "saldo_favor", "numeric(10,2)"),
    ("solicitudes_devolucion", "nota_devolucion_url", "varchar(500)"),
    ("solicitudes_devolucion", "usuario_procesa_id", "integer"),
    ("solicitudes_devolucion", "motivo_rechazo", "text"),
    ("detalles_solicitud_devolucion", "tenant_id", "integer"),
    ("detalles_solicitud_devolucion", "precio_variante_nueva", "numeric(10,2)"),
    ("detalles_solicitud_devolucion", "subtotal_diferencia", "numeric(10,2)"),
    ("detalles_solicitud_devolucion", "estado_item", "varchar(30)"),
    # Reportes
    ("reportes", "tenant_id", "integer"),
    ("reportes", "sucursal_id", "integer"),
    ("reportes", "usuario_id", "integer"),
    ("indicadores_kpi", "tenant_id", "integer"),
    ("indicadores_kpi", "sucursal_id", "integer"),
]

# Tablas cuyo tenant_id es NOT NULL (se valida en ex2_005)
TENANT_NOT_NULL = [
    "administradores", "cajeros", "encargados_sucursal", "carritos", "categorias",
    "clientes", "colecciones", "colores", "comprobantes", "cupones",
    "detalles_orden", "detalles_reserva", "detalles_solicitud_devolucion",
    "inventario", "items_carrito", "movimientos_inventario", "ordenes",
    "productos", "promociones", "proveedores", "recepciones", "reservas",
    "solicitudes_devolucion", "sucursales", "tallas", "temporadas",
    "transacciones_pago", "usuarios_roles", "variantes_producto",
    "ventas_presenciales",
]

# (tabla, columna de tenant, ondelete) para las FKs del plan
TENANT_FKS = {
    "usuarios": "SET NULL",
    "roles": "SET NULL",
    "bitacora": "SET NULL",
    "clientes": "CASCADE",
    "administradores": "CASCADE",
    "encargados_sucursal": "CASCADE",
    "cajeros": "CASCADE",
    "usuarios_roles": "CASCADE",
    "sucursales": "CASCADE",
    "categorias": "CASCADE",
    "tallas": "CASCADE",
    "colores": "CASCADE",
    "temporadas": "CASCADE",
    "colecciones": "CASCADE",
    "proveedores": "CASCADE",
    "productos": "CASCADE",
    "variantes_producto": "CASCADE",
    "inventario": "CASCADE",
    "movimientos_inventario": "CASCADE",
    "recepciones": "CASCADE",
    "cupones": "CASCADE",
    "promociones": "CASCADE",
    "carritos": "CASCADE",
    "items_carrito": "CASCADE",
    "ordenes": "CASCADE",
    "detalles_orden": "CASCADE",
    "ventas_presenciales": "CASCADE",
    "reservas": "CASCADE",
    "detalles_reserva": "CASCADE",
    "transacciones_pago": "CASCADE",
    "pasarelas_pago": "CASCADE",
    "comprobantes": "CASCADE",
    "solicitudes_devolucion": "CASCADE",
    "detalles_solicitud_devolucion": "CASCADE",
    "reportes": "CASCADE",
    "indicadores_kpi": "CASCADE",
}

EXTRA_FKS = [
    ("indicadores_kpi", "sucursal_id", "sucursales", "id", "SET NULL"),
    ("reportes", "sucursal_id", "sucursales", "id", "SET NULL"),
    ("reportes", "usuario_id", "usuarios", "id", "SET NULL"),
    ("solicitudes_devolucion", "usuario_procesa_id", "usuarios", "id", "SET NULL"),
]


def _fk_name(table: str, column: str, ref_table: str) -> str:
    return f"fk_{table}_{column}_{ref_table}"


def _create_type(name: str, values: tuple[str, ...]) -> None:
    labels = ", ".join(f"'{value}'" for value in values)
    op.execute(
        sa.text(
            f"""
            DO $$ BEGIN
                CREATE TYPE {name} AS ENUM ({labels});
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
            """
        )
    )


def _add_fk(table: str, column: str, ref_table: str, ref_column: str, ondelete: str) -> None:
    name = _fk_name(table, column, ref_table)
    op.execute(
        sa.text(
            f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
                    ALTER TABLE {table}
                    ADD CONSTRAINT {name}
                    FOREIGN KEY ({column}) REFERENCES {ref_table} ({ref_column})
                    ON DELETE {ondelete};
                END IF;
            END $$;
            """
        )
    )


def upgrade() -> None:
    _create_type("estado_sincronizacion", (
        "PENDIENTE", "ENVIADA", "CONFIRMADA", "RECHAZADA",
        "CONFLICTO", "DUPLICADA", "ERROR_CRITICO", "NO_APLICA",
    ))
    _create_type("origen_operacion", ("WEB", "MOVIL", "POS", "ONLINE", "OFFLINE"))
    _create_type("tipodiferencia", ("COBRO", "SALDO_FAVOR", "SIN_DIFERENCIA"))

    for table, column, ddl in COLUMNS:
        op.execute(
            sa.text(
                f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}'
            )
        )

    # Campos existentes que pasan a nullable (plan, 3.4)
    op.execute(sa.text("ALTER TABLE clientes ALTER COLUMN usuario_id DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE clientes ALTER COLUMN nit_ci DROP NOT NULL"))
    op.execute(sa.text("ALTER TABLE carritos ALTER COLUMN cliente_id DROP NOT NULL"))

    # Ãndices simples por tenant
    for table in TENANT_FKS:
        op.execute(
            sa.text(
                f'CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id ON {table} (tenant_id)'
            )
        )

    # Ãndices de apoyo del plan
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_carritos_tenant_dispositivo_cliente "
        "ON carritos (tenant_id, dispositivo_id, cliente_id)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_carritos_uuid_carrito ON carritos (uuid_carrito)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_comprobantes_uuid_offline "
        "ON comprobantes (uuid_offline)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_movimientos_inventario_referencia_uuid "
        "ON movimientos_inventario (referencia_uuid)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_ordenes_uuid_orden ON ordenes (uuid_orden)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_reservas_uuid_reserva ON reservas (uuid_reserva)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_solicitudes_devolucion_uuid_offline "
        "ON solicitudes_devolucion (uuid_offline)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_transacciones_pago_stripe_payment_intent_id "
        "ON transacciones_pago (stripe_payment_intent_id)"
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_ventas_presenciales_uuid_offline "
        "ON ventas_presenciales (uuid_offline)"
    ))

    # FKs a tenants
    for table, ondelete in TENANT_FKS.items():
        _add_fk(table, "tenant_id", "tenants", "id", ondelete)

    # FKs adicionales del plan
    for table, column, ref_table, ref_column, ondelete in EXTRA_FKS:
        _add_fk(table, column, ref_table, ref_column, ondelete)


def downgrade() -> None:
    # Las FKs e Ã­ndices de tenant se eliminan junto con la columna.
    for table in reversed(TENANT_FKS):
        op.execute(
            sa.text(
                f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "
                f"{_fk_name(table, 'tenant_id', 'tenants')}"
            )
        )

    for table, column, ref_table, ref_column, _ in reversed(EXTRA_FKS):
        op.execute(
            sa.text(
                f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "
                f"{_fk_name(table, column, ref_table)}"
            )
        )

    for table, column, _ in reversed(COLUMNS):
        op.execute(
            sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
        )

    # Restaurar NOT NULL original de columnas alteradas
    op.execute(sa.text("ALTER TABLE carritos ALTER COLUMN cliente_id SET NOT NULL"))
    op.execute(sa.text("ALTER TABLE clientes ALTER COLUMN nit_ci SET NOT NULL"))
    op.execute(sa.text("ALTER TABLE clientes ALTER COLUMN usuario_id SET NOT NULL"))

    op.execute(sa.text("DROP TYPE IF EXISTS tipodiferencia"))
    op.execute(sa.text("DROP TYPE IF EXISTS origen_operacion"))
    op.execute(sa.text("DROP TYPE IF EXISTS estado_sincronizacion"))

