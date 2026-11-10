"""ex2_006 - Unicidad compuesta por tenant

[EXAMEN-2] Fase 1 (plan, 3.6 y 9.1.4). Elimina los únicos simples del Examen 1
y crea los compuestos `(tenant_id, ...)`, además de los UUID de idempotencia.

Se usan guardas porque los nombres de constraints/índices pueden variar entre
bases creadas por migraciones y por `create_all`.

Revision ID: ex2_006_unicidad_compuesta
Revises: ex2_005_tenant_id_not_null
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_006"
down_revision: Union[str, None] = "ex2_005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_constraint(table: str, name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))


def _drop_index(name: str) -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {name}"))


def _index(table: str, name: str, columns: str, unique: bool = False) -> None:
    prefix = "UNIQUE " if unique else ""
    op.execute(
        sa.text(
            f"CREATE {prefix}INDEX IF NOT EXISTS {name} ON {table} ({columns})"
        )
    )


def _unique(table: str, name: str, columns: str) -> None:
    op.execute(
        sa.text(
            f"""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = '{name}' AND conrelid = '{table}'::regclass
                ) THEN
                    ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({columns});
                END IF;
            END $$;
            """
        )
    )


def upgrade() -> None:
    # --- Eliminar únicos simples ---
    _drop_constraint("clientes", "clientes_nit_ci_key")
    _drop_constraint("clientes", "clientes_usuario_id_key")
    _drop_constraint("categorias", "categorias_nombre_key")
    _drop_constraint("proveedores", "proveedores_nit_key")
    _drop_constraint("variantes_producto", "variantes_producto_sku_variante_key")
    _drop_constraint("inventario", "uq_inventario_variante_sucursal")
    _drop_constraint("cupones", "cupones_codigo_key")
    _drop_constraint("comprobantes", "comprobantes_numero_key")
    _drop_constraint("solicitudes_devolucion", "solicitudes_devolucion_numero_solicitud_key")

    for index_name in (
        "ix_productos_sku",
        "ix_cupones_codigo",
        "ix_ordenes_numero_orden",
        "ix_reservas_numero_reserva",
        "ix_solicitudes_devolucion_numero_solicitud",
    ):
        _drop_index(index_name)

    # Recrear como índices no únicos (el modelo los declara index=True)
    _index("productos", "ix_productos_sku", "sku", unique=False)
    _index("cupones", "ix_cupones_codigo", "codigo", unique=False)
    _index("ordenes", "ix_ordenes_numero_orden", "numero_orden", unique=False)
    _index("reservas", "ix_reservas_numero_reserva", "numero_reserva", unique=False)
    _index(
        "solicitudes_devolucion",
        "ix_solicitudes_devolucion_numero_solicitud",
        "numero_solicitud",
        unique=False,
    )

    # --- Únicos compuestos del capítulo 3.6 ---
    _unique("clientes", "uq_clientes_tenant_nit_ci", "tenant_id, nit_ci")
    _unique("clientes", "uq_clientes_usuario_tenant", "usuario_id, tenant_id")
    _unique("categorias", "uq_categorias_tenant_nombre", "tenant_id, nombre")
    _unique("proveedores", "uq_proveedores_tenant_nit", "tenant_id, nit")
    _unique("productos", "uq_productos_tenant_sku", "tenant_id, sku")
    _unique("variantes_producto", "uq_variantes_tenant_sku", "tenant_id, sku_variante")
    _unique(
        "inventario",
        "uq_inventario_tenant_variante_sucursal",
        "tenant_id, variante_producto_id, sucursal_id",
    )
    _unique("cupones", "uq_cupones_tenant_codigo", "tenant_id, codigo")
    _unique("ordenes", "uq_ordenes_tenant_numero", "tenant_id, numero_orden")
    _unique("ordenes", "uq_ordenes_tenant_uuid", "tenant_id, uuid_orden")
    _unique("reservas", "uq_reservas_tenant_numero", "tenant_id, numero_reserva")
    _unique("reservas", "uq_reservas_tenant_uuid", "tenant_id, uuid_reserva")
    _unique(
        "solicitudes_devolucion",
        "uq_solicitudes_tenant_numero",
        "tenant_id, numero_solicitud",
    )
    _unique(
        "solicitudes_devolucion",
        "uq_solicitudes_tenant_uuid",
        "tenant_id, uuid_offline",
    )
    _unique("comprobantes", "uq_comprobantes_tenant_numero", "tenant_id, numero")
    _unique("comprobantes", "uq_comprobantes_tenant_uuid", "tenant_id, uuid_offline")
    _unique("carritos", "uq_carritos_tenant_uuid", "tenant_id, uuid_carrito")
    _unique(
        "usuarios_roles",
        "uq_usuarios_roles_tenant_usuario_rol",
        "tenant_id, usuario_id, rol_id",
    )
    _unique("tallas", "uq_tallas_tenant_tipo_valor", "tenant_id, tipo, valor")
    _unique("colores", "uq_colores_tenant_nombre", "tenant_id, nombre")
    _unique("sucursales", "uq_sucursales_tenant_nombre", "tenant_id, nombre")
    _unique(
        "indicadores_kpi",
        "uq_kpi_tenant_sucursal_nombre_periodo",
        "tenant_id, sucursal_id, nombre, periodo",
    )

    # Un carrito ACTIVO por dispositivo/cliente (plan, 3.6)
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_carritos_activo_dispositivo_cliente
            ON carritos (tenant_id, dispositivo_id, cliente_id)
            WHERE estado = 'ACTIVO'
            """
        )
    )


def downgrade() -> None:
    composites = [
        ("clientes", "uq_clientes_tenant_nit_ci"),
        ("clientes", "uq_clientes_usuario_tenant"),
        ("categorias", "uq_categorias_tenant_nombre"),
        ("proveedores", "uq_proveedores_tenant_nit"),
        ("productos", "uq_productos_tenant_sku"),
        ("variantes_producto", "uq_variantes_tenant_sku"),
        ("inventario", "uq_inventario_tenant_variante_sucursal"),
        ("cupones", "uq_cupones_tenant_codigo"),
        ("ordenes", "uq_ordenes_tenant_numero"),
        ("ordenes", "uq_ordenes_tenant_uuid"),
        ("reservas", "uq_reservas_tenant_numero"),
        ("reservas", "uq_reservas_tenant_uuid"),
        ("solicitudes_devolucion", "uq_solicitudes_tenant_numero"),
        ("solicitudes_devolucion", "uq_solicitudes_tenant_uuid"),
        ("comprobantes", "uq_comprobantes_tenant_numero"),
        ("comprobantes", "uq_comprobantes_tenant_uuid"),
        ("carritos", "uq_carritos_tenant_uuid"),
        ("usuarios_roles", "uq_usuarios_roles_tenant_usuario_rol"),
        ("tallas", "uq_tallas_tenant_tipo_valor"),
        ("colores", "uq_colores_tenant_nombre"),
        ("sucursales", "uq_sucursales_tenant_nombre"),
        ("indicadores_kpi", "uq_kpi_tenant_sucursal_nombre_periodo"),
    ]
    for table, name in composites:
        _drop_constraint(table, name)

    _drop_index("uq_carritos_activo_dispositivo_cliente")

    # Restaurar únicos simples del Examen 1
    _unique("clientes", "clientes_nit_ci_key", "nit_ci")
    _unique("clientes", "clientes_usuario_id_key", "usuario_id")
    _unique("categorias", "categorias_nombre_key", "nombre")
    _unique("proveedores", "proveedores_nit_key", "nit")
    _unique(
        "variantes_producto",
        "variantes_producto_sku_variante_key",
        "sku_variante",
    )
    _unique("inventario", "uq_inventario_variante_sucursal", "variante_producto_id, sucursal_id")
    _unique("cupones", "cupones_codigo_key", "codigo")
    _unique("comprobantes", "comprobantes_numero_key", "numero")
    _unique(
        "solicitudes_devolucion",
        "solicitudes_devolucion_numero_solicitud_key",
        "numero_solicitud",
    )

    _drop_index("ix_productos_sku")
    _drop_index("ix_cupones_codigo")
    _drop_index("ix_ordenes_numero_orden")
    _drop_index("ix_reservas_numero_reserva")
    _drop_index("ix_solicitudes_devolucion_numero_solicitud")

    _index("productos", "ix_productos_sku", "sku", unique=True)
    _index("cupones", "ix_cupones_codigo", "codigo", unique=True)
    _index("ordenes", "ix_ordenes_numero_orden", "numero_orden", unique=True)
    _index("reservas", "ix_reservas_numero_reserva", "numero_reserva", unique=True)
    _index(
        "solicitudes_devolucion",
        "ix_solicitudes_devolucion_numero_solicitud",
        "numero_solicitud",
        unique=True,
    )

