"""ex2_004 - Backfill del tenant demo y datos SaaS iniciales

[EXAMEN-2] Fase 1 (plan, 4.6 y 9.1.4). Crea de forma idempotente:
planes BÃ¡sico/Profesional, tenant demo, super administrador, asignaciÃ³n masiva
de `tenant_id` (incluidas tablas hijas), suscripciÃ³n activa, pago demo y
configuraciÃ³n offline.

El `downgrade` elimina solo los datos sembrados por esta migraciÃ³n.

Revision ID: ex2_004_backfill_tenant_demo
Revises: ex2_003_tenant_id_y_campos_examen2
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_004"
down_revision: Union[str, None] = "ex2_003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEMO_SLUG = "fashionstore-demo"
DEMO_NIT = "900000001"
DEMO_RAZON = "FashionStore Demo"
DEMO_CORREO = "contacto@fashionstore.com"
DEMO_DIRECCION = "Av. Demo #123, La Paz, Bolivia"
# hashes bcrypt de Admin123! y SuperAdmin123!
ADMIN_HASH = "$2b$12$lLkIVNeXJgvLOkE/IcizzuTFncQYafkx5vzJwVZ6JBuoKwc1vcFHK"
SUPER_HASH = "$2b$12$eNYo7fhomF/BCNGsHu1Txu4Fjnm6BUVeR8JTWXYS2Fbnp3CZvbK3e"


# Tablas raÃ­z: todo lo existente pertenece al tenant demo.
ROOT_TABLES = [
    "sucursales", "categorias", "tallas", "colores", "temporadas",
    "colecciones", "proveedores", "productos", "variantes_producto",
    "recepciones", "cupones", "promociones", "carritos", "ordenes",
    "ventas_presenciales", "reservas", "comprobantes",
    "solicitudes_devolucion", "reportes", "indicadores_kpi",
]


def _create_config_table() -> None:
    """La tabla se necesita para el backfill; ex2_009 la reutiliza."""
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS configuracion_offline (
                id serial PRIMARY KEY,
                tenant_id integer NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                descarga_automatica boolean NOT NULL DEFAULT true,
                hora_descarga time,
                incluir_clientes boolean NOT NULL DEFAULT true,
                max_clientes integer NOT NULL DEFAULT 100,
                habilitar_carrito_offline boolean NOT NULL DEFAULT true,
                max_horas_sesion_offline integer NOT NULL DEFAULT 12,
                max_dias_orden_pendiente integer NOT NULL DEFAULT 1,
                updated_at timestamptz DEFAULT now(),
                CONSTRAINT uq_configuracion_offline_tenant UNIQUE (tenant_id)
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_configuracion_offline_id "
            "ON configuracion_offline (id)"
        )
    )


def upgrade() -> None:
    _create_config_table()

    demo = "(SELECT id FROM tenants WHERE slug = :slug)"
    params = {"slug": DEMO_SLUG}

    # 1. Planes comerciales (plan, 3.7.11)
    op.execute(
        sa.text(
            """
            INSERT INTO planes
                (nombre, descripcion, precio_mensual, max_sucursales,
                 permite_reportes_ia, permite_pago_diferido, estado, orden,
                 soporte_prioritario)
            SELECT 'BÃ¡sico', 'Plan inicial para una o dos sucursales',
                   299.00, 2, false, true, 'ACTIVO'::estado_plan, 0, false
            WHERE NOT EXISTS (SELECT 1 FROM planes WHERE nombre = 'BÃ¡sico')
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO planes
                (nombre, descripcion, precio_mensual, max_sucursales,
                 permite_reportes_ia, permite_pago_diferido, estado, orden,
                 soporte_prioritario)
            SELECT 'Profesional', 'Plan completo con reportes IA',
                   799.00, 10, true, true, 'ACTIVO'::estado_plan, 1, true
            WHERE NOT EXISTS (SELECT 1 FROM planes WHERE nombre = 'Profesional')
            """
        )
    )

    # 2. Tenant demo
    op.execute(
        sa.text(
            """
            INSERT INTO tenants
                (razon_social, nit, direccion_fiscal, correo_contacto,
                 moneda, zona_horaria, estado, slug)
            SELECT :razon, :nit, :direccion, :correo, 'BOB', 'America/La_Paz',
                   'OPERATIVO'::estado_tenant, :slug
            WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE slug = :slug)
            """
        ).bindparams(
            razon=DEMO_RAZON,
            nit=DEMO_NIT,
            direccion=DEMO_DIRECCION,
            correo=DEMO_CORREO,
            slug=DEMO_SLUG,
        )
    )

    # 3. Roles del tenant y rol de plataforma
    for nombre in ("Administrador", "Cliente", "Encargado", "Cajero"):
        op.execute(
            sa.text(
                f"""
                INSERT INTO roles (nombre, descripcion, tenant_id)
                SELECT '{nombre}', 'Rol del tenant', {demo}
                WHERE NOT EXISTS (SELECT 1 FROM roles WHERE nombre = '{nombre}')
                """
            ).bindparams(**params)
        )
        op.execute(
            sa.text(
                f"""
                UPDATE roles SET tenant_id = {demo}
                WHERE nombre = '{nombre}' AND tenant_id IS NULL
                """
            ).bindparams(**params)
        )

    op.execute(
        sa.text(
            f"""
            INSERT INTO roles (nombre, descripcion, tenant_id)
            SELECT 'Super Administrador',
                   'Super Administrador de la plataforma SaaS', NULL
            WHERE NOT EXISTS (SELECT 1 FROM roles WHERE nombre = 'Super Administrador')
            """
        )
    )
    op.execute(
        sa.text(
            "UPDATE roles SET tenant_id = NULL WHERE nombre = 'Super Administrador'"
        )
    )

    # 4. Usuarios existentes al tenant demo; el superadmin queda fuera
    op.execute(
        sa.text(
            f"""
            UPDATE usuarios SET tenant_id = {demo}
            WHERE tenant_id IS NULL AND correo <> 'superadmin@fashionstore.com'
            """
        ).bindparams(**params)
    )
    op.execute(
        sa.text(
            "UPDATE usuarios SET offline_pin_hash = NULL, "
            "debe_cambiar_contrasena = false"
        )
    )

    # 5. Super Administrador de plataforma
    op.execute(
        sa.text(
            """
            INSERT INTO usuarios
                (nombre, apellido, correo, contrasena_hash, estado,
                 tenant_id, debe_cambiar_contrasena)
            SELECT 'Super', 'Administrador', 'superadmin@fashionstore.com',
                   :hash, 'ACTIVO'::estadousuario, NULL, false
            WHERE NOT EXISTS (
                SELECT 1 FROM usuarios WHERE correo = 'superadmin@fashionstore.com'
            )
            """
        ).bindparams(hash=SUPER_HASH)
    )
    op.execute(
        sa.text(
            "UPDATE usuarios SET tenant_id = NULL, estado = 'ACTIVO'::estadousuario "
            "WHERE correo = 'superadmin@fashionstore.com'"
        )
    )

    # 6. Administrador del tenant demo
    op.execute(
        sa.text(
            f"""
            INSERT INTO usuarios
                (nombre, apellido, correo, contrasena_hash, estado,
                 tenant_id, debe_cambiar_contrasena)
            SELECT 'Administrador', 'FashionStore', 'admin@fashionstore.com',
                   :hash, 'ACTIVO'::estadousuario, {demo}, false
            WHERE NOT EXISTS (
                SELECT 1 FROM usuarios WHERE correo = 'admin@fashionstore.com'
            )
            """
        ).bindparams(hash=ADMIN_HASH, **params)
    )
    op.execute(
        sa.text(
            f"""
            UPDATE usuarios SET tenant_id = {demo}
            WHERE correo = 'admin@fashionstore.com'
            """
        ).bindparams(**params)
    )

    # 7. Tablas puente y perfiles: heredan del usuario
    op.execute(
        sa.text(
            f"""
            UPDATE usuarios_roles ur SET tenant_id = COALESCE(u.tenant_id, {demo})
            FROM usuarios u
            WHERE ur.usuario_id = u.id AND ur.tenant_id IS NULL
            """
        ).bindparams(**params)
    )
    for table in ("administradores", "encargados_sucursal", "cajeros"):
        op.execute(
            sa.text(
                f"""
                UPDATE {table} p SET tenant_id = COALESCE(u.tenant_id, {demo})
                FROM usuarios u
                WHERE p.usuario_id = u.id AND p.tenant_id IS NULL
                """
            ).bindparams(**params)
        )

    # 8. Asignar roles base al admin y al superadmin (idempotente)
    op.execute(
        sa.text(
            f"""
            INSERT INTO usuarios_roles (usuario_id, rol_id, tenant_id)
            SELECT u.id, r.id, {demo}
            FROM usuarios u, roles r
            WHERE u.correo = 'admin@fashionstore.com'
              AND r.nombre = 'Administrador'
              AND NOT EXISTS (
                  SELECT 1 FROM usuarios_roles ur
                  WHERE ur.usuario_id = u.id AND ur.rol_id = r.id
              )
            """
        ).bindparams(**params)
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO usuarios_roles (usuario_id, rol_id, tenant_id)
            SELECT u.id, r.id, {demo}
            FROM usuarios u, roles r
            WHERE u.correo = 'superadmin@fashionstore.com'
              AND r.nombre = 'Super Administrador'
              AND NOT EXISTS (
                  SELECT 1 FROM usuarios_roles ur
                  WHERE ur.usuario_id = u.id AND ur.rol_id = r.id
              )
            """
        ).bindparams(**params)
    )

    # Perfil de administrador para el usuario admin
    op.execute(
        sa.text(
            f"""
            INSERT INTO administradores (usuario_id, tenant_id)
            SELECT u.id, {demo} FROM usuarios u
            WHERE u.correo = 'admin@fashionstore.com'
              AND NOT EXISTS (
                  SELECT 1 FROM administradores a WHERE a.usuario_id = u.id
              )
            """
        ).bindparams(**params)
    )

    # 9. Backfill de raÃ­ces y tablas hijas
    for table in ROOT_TABLES:
        op.execute(
            sa.text(
                f"UPDATE {table} SET tenant_id = {demo} WHERE tenant_id IS NULL"
            ).bindparams(**params)
        )

    op.execute(
        sa.text(
            f"""
            UPDATE clientes c SET tenant_id = COALESCE(u.tenant_id, {demo})
            FROM usuarios u
            WHERE c.usuario_id = u.id AND c.tenant_id IS NULL
            """
        ).bindparams(**params)
    )
    op.execute(
        sa.text(
            f"UPDATE clientes SET tenant_id = {demo} WHERE tenant_id IS NULL"
        ).bindparams(**params)
    )

    # Hijas que heredan del padre
    parent_updates = [
        ("inventario", "sucursales", "sucursal_id"),
        ("movimientos_inventario", "inventario", "inventario_id"),
        ("items_carrito", "carritos", "carrito_id"),
        ("detalles_orden", "ordenes", "orden_id"),
        ("detalles_reserva", "reservas", "reserva_id"),
        ("transacciones_pago", "ordenes", "orden_id"),
        ("comprobantes", "ordenes", "orden_id"),
        ("detalles_solicitud_devolucion", "solicitudes_devolucion", "solicitud_id"),
    ]
    for child, parent, fk in parent_updates:
        op.execute(
            sa.text(
                f"""
                UPDATE {child} c SET tenant_id = p.tenant_id
                FROM {parent} p
                WHERE c.{fk} = p.id AND c.tenant_id IS NULL
                """
            )
        )

    # Cualquier residuo de negocio queda en el tenant demo
    for table in ROOT_TABLES + [
        "inventario", "movimientos_inventario", "items_carrito",
        "detalles_orden", "detalles_reserva", "transacciones_pago",
        "detalles_solicitud_devolucion",
    ]:
        op.execute(
            sa.text(
                f"UPDATE {table} SET tenant_id = {demo} WHERE tenant_id IS NULL"
            ).bindparams(**params)
        )

    # BitÃ¡cora: las acciones de plataforma (usuario NULL) conservan NULL
    op.execute(
        sa.text(
            f"""
            UPDATE bitacora b SET tenant_id = u.tenant_id
            FROM usuarios u
            WHERE b.usuario_id = u.id AND b.tenant_id IS NULL
              AND u.tenant_id IS NOT NULL
            """
        )
    )

    # 10. SuscripciÃ³n activa al plan Profesional
    op.execute(
        sa.text(
            f"""
            INSERT INTO suscripciones
                (tenant_id, plan_id, fecha_inicio, fecha_vencimiento, estado,
                 estado_pago, renovacion_automatica, motivo_estado)
            SELECT {demo}, p.id, CURRENT_DATE,
                   (CURRENT_DATE + INTERVAL '365 days')::date,
                   'ACTIVA'::estado_suscripcion, 'PAGADO', true,
                   'ex2_004_backfill'
            FROM planes p
            WHERE p.nombre = 'Profesional'
              AND NOT EXISTS (
                  SELECT 1 FROM suscripciones s
                  WHERE s.tenant_id = {demo} AND s.estado = 'ACTIVA'
              )
            """
        ).bindparams(**params)
    )

    # 11. Pago de demostraciÃ³n (idempotente por idempotency_key)
    op.execute(
        sa.text(
            f"""
            INSERT INTO pagos_suscripcion
                (tenant_id, suscripcion_id, monto, moneda, estado,
                 referencia, idempotency_key)
            SELECT {demo}, s.id, p.precio_mensual, 'BOB',
                   'CONFIRMADO'::estadotransaccion, 'DEMO-MIGRACION',
                   'ex2-demo-profesional'
            FROM suscripciones s
            JOIN planes p ON p.id = s.plan_id
            WHERE s.tenant_id = {demo} AND s.estado = 'ACTIVA'
              AND NOT EXISTS (
                  SELECT 1 FROM pagos_suscripcion ps
                  WHERE ps.idempotency_key = 'ex2-demo-profesional'
              )
            """
        ).bindparams(**params)
    )

    # 12. ConfiguraciÃ³n offline del tenant demo
    op.execute(
        sa.text(
            f"""
            INSERT INTO configuracion_offline
                (tenant_id, descarga_automatica, incluir_clientes, max_clientes,
                 habilitar_carrito_offline, max_horas_sesion_offline,
                 max_dias_orden_pendiente)
            SELECT {demo}, true, true, 100, true, 12, 1
            WHERE NOT EXISTS (
                SELECT 1 FROM configuracion_offline c
                WHERE c.tenant_id = {demo}
            )
            """
        ).bindparams(**params)
    )


def downgrade() -> None:
    demo = "(SELECT id FROM tenants WHERE slug = :slug)"
    params = {"slug": DEMO_SLUG}

    op.execute(
        sa.text(
            "DELETE FROM pagos_suscripcion WHERE idempotency_key = 'ex2-demo-profesional'"
        )
    )
    op.execute(
        sa.text(
            f"DELETE FROM configuracion_offline WHERE tenant_id = {demo}"
        ).bindparams(**params)
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM suscripciones
            WHERE tenant_id = {demo} AND motivo_estado = 'ex2_004_backfill'
            """
        ).bindparams(**params)
    )

    # Usuario de plataforma y su rol; solo si sigue siendo de plataforma
    op.execute(
        sa.text(
            """
            DELETE FROM usuarios_roles ur
            USING usuarios u
            WHERE ur.usuario_id = u.id
              AND u.correo = 'superadmin@fashionstore.com'
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM usuarios
            WHERE correo = 'superadmin@fashionstore.com' AND tenant_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM roles r
            WHERE r.nombre = 'Super Administrador'
              AND NOT EXISTS (
                  SELECT 1 FROM usuarios_roles ur WHERE ur.rol_id = r.id
              )
            """
        )
    )

    # Tenant demo (el borrado en cascada elimina sus datos de negocio)
    op.execute(
        sa.text("DELETE FROM tenants WHERE slug = :slug").bindparams(**params)
    )
    op.execute(
        sa.text(
            """
            DELETE FROM planes p
            WHERE p.nombre IN ('BÃ¡sico', 'Profesional')
              AND NOT EXISTS (
                  SELECT 1 FROM suscripciones s WHERE s.plan_id = p.id
              )
            """
        )
    )
    op.execute(sa.text("DROP TABLE IF EXISTS configuracion_offline"))

