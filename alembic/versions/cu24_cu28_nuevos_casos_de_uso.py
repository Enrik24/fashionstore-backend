"""nuevos casos de uso CU24-CU28

Revision ID: cu24_cu28_001
Revises: costo_k15_001
Create Date: 2026-09-20 18:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'cu24_cu28_001'
down_revision: Union[str, None] = 'costo_k15_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    existing_tables = sa.inspect(conn).get_table_names()
    
    # 1. Enums con create_type=False para evitar que op.create_table intente crearlos por segunda vez
    tipo_promocion_enum = postgresql.ENUM('PORCENTAJE', 'MONTO_FIJO', 'DOS_POR_UNO', 'ENVIO_GRATIS', name='tipopromocion', create_type=False)
    tipo_promocion_enum.create(conn, checkfirst=True)
    
    estado_promocion_enum = postgresql.ENUM('ACTIVA', 'INACTIVA', 'PROGRAMADA', 'FINALIZADA', name='estadopromocion', create_type=False)
    estado_promocion_enum.create(conn, checkfirst=True)
    
    estado_valoracion_enum = postgresql.ENUM('PUBLICADA', 'PENDIENTE_MODERACION', 'RECHAZADA', name='estadovaloracion', create_type=False)
    estado_valoracion_enum.create(conn, checkfirst=True)
    
    tipo_solicitud_enum = postgresql.ENUM('DEVOLUCION', 'CAMBIO', name='tiposolicituddevolucion', create_type=False)
    tipo_solicitud_enum.create(conn, checkfirst=True)
    
    motivo_devolucion_enum = postgresql.ENUM('TALLA_INCORRECTA', 'COLOR_INCORRECTO', 'DEFECTO_FABRICA', 'OTRO', name='motivodevolucion', create_type=False)
    motivo_devolucion_enum.create(conn, checkfirst=True)
    
    estado_solicitud_enum = postgresql.ENUM('PENDIENTE', 'EN_REVISION', 'APROBADA', 'RECHAZADA', 'COMPLETADA', 'PENDIENTE_REEMBOLSO', name='estadosolicituddevolucion', create_type=False)
    estado_solicitud_enum.create(conn, checkfirst=True)

    # 2. Tablas de Promociones (CU24)
    if 'promociones' not in existing_tables:
        op.create_table(
            'promociones',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('nombre', sa.String(length=150), nullable=False),
            sa.Column('descripcion', sa.Text(), nullable=True),
            sa.Column('tipo', tipo_promocion_enum, nullable=False),
            sa.Column('valor', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('fecha_inicio', sa.DateTime(timezone=True), nullable=False),
            sa.Column('fecha_fin', sa.DateTime(timezone=True), nullable=False),
            sa.Column('condiciones', sa.Text(), nullable=True),
            sa.Column('estado', estado_promocion_enum, server_default='ACTIVA', nullable=False),
            sa.Column('creado_por_id', sa.Integer(), nullable=True),
            sa.Column('fecha_creacion', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(['creado_por_id'], ['usuarios.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_promociones_id'), 'promociones', ['id'], unique=False)

    if 'promocion_productos' not in existing_tables:
        op.create_table(
            'promocion_productos',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('promocion_id', sa.Integer(), nullable=False),
            sa.Column('producto_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['producto_id'], ['productos.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['promocion_id'], ['promociones.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('promocion_id', 'producto_id', name='uq_promocion_producto')
        )
        op.create_index(op.f('ix_promocion_productos_id'), 'promocion_productos', ['id'], unique=False)

    if 'promocion_categorias' not in existing_tables:
        op.create_table(
            'promocion_categorias',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('promocion_id', sa.Integer(), nullable=False),
            sa.Column('categoria_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['categoria_id'], ['categorias.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['promocion_id'], ['promociones.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('promocion_id', 'categoria_id', name='uq_promocion_categoria')
        )
        op.create_index(op.f('ix_promocion_categorias_id'), 'promocion_categorias', ['id'], unique=False)

    if 'promocion_sucursales' not in existing_tables:
        op.create_table(
            'promocion_sucursales',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('promocion_id', sa.Integer(), nullable=False),
            sa.Column('sucursal_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['promocion_id'], ['promociones.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['sucursal_id'], ['sucursales.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('promocion_id', 'sucursal_id', name='uq_promocion_sucursal')
        )
        op.create_index(op.f('ix_promocion_sucursales_id'), 'promocion_sucursales', ['id'], unique=False)

    # 3. Tablas de aplicabilidad de Cupones (CU27)
    if 'cupon_productos' not in existing_tables:
        op.create_table(
            'cupon_productos',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('cupon_id', sa.Integer(), nullable=False),
            sa.Column('producto_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['cupon_id'], ['cupones.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['producto_id'], ['productos.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('cupon_id', 'producto_id', name='uq_cupon_producto')
        )
        op.create_index(op.f('ix_cupon_productos_id'), 'cupon_productos', ['id'], unique=False)

    if 'cupon_categorias' not in existing_tables:
        op.create_table(
            'cupon_categorias',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('cupon_id', sa.Integer(), nullable=False),
            sa.Column('categoria_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['categoria_id'], ['categorias.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['cupon_id'], ['cupones.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('cupon_id', 'categoria_id', name='uq_cupon_categoria')
        )
        op.create_index(op.f('ix_cupon_categorias_id'), 'cupon_categorias', ['id'], unique=False)

    # 4. Tabla de Favoritos (CU25)
    if 'productos_favoritos' not in existing_tables:
        op.create_table(
            'productos_favoritos',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('cliente_id', sa.Integer(), nullable=False),
            sa.Column('producto_id', sa.Integer(), nullable=False),
            sa.Column('fecha_agregado', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(['cliente_id'], ['clientes.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['producto_id'], ['productos.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('cliente_id', 'producto_id', name='uq_cliente_producto_favorito')
        )
        op.create_index(op.f('ix_productos_favoritos_id'), 'productos_favoritos', ['id'], unique=False)

    # 5. Tabla de Valoraciones (CU26) y columnas en Producto
    if 'valoraciones_producto' not in existing_tables:
        op.create_table(
            'valoraciones_producto',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('cliente_id', sa.Integer(), nullable=False),
            sa.Column('producto_id', sa.Integer(), nullable=False),
            sa.Column('puntuacion', sa.Integer(), nullable=False),
            sa.Column('comentario', sa.Text(), nullable=True),
            sa.Column('estado', estado_valoracion_enum, server_default='PUBLICADA', nullable=False),
            sa.Column('fecha_creacion', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column('fecha_actualizacion', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(['cliente_id'], ['clientes.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['producto_id'], ['productos.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('cliente_id', 'producto_id', name='uq_cliente_producto_valoracion')
        )
        op.create_index(op.f('ix_valoraciones_producto_id'), 'valoraciones_producto', ['id'], unique=False)

    cols_prod = [c["name"] for c in sa.inspect(conn).get_columns("productos")]
    if "promedio_valoracion" not in cols_prod:
        op.add_column('productos', sa.Column('promedio_valoracion', sa.Numeric(precision=3, scale=2), server_default='0.00', nullable=True))
    if "total_valoraciones" not in cols_prod:
        op.add_column('productos', sa.Column('total_valoraciones', sa.Integer(), server_default='0', nullable=True))

    # 6. Tablas de Solicitudes de Devolución y Cambios (CU28)
    if 'solicitudes_devolucion' not in existing_tables:
        op.create_table(
            'solicitudes_devolucion',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('numero_solicitud', sa.String(length=50), nullable=False),
            sa.Column('cliente_id', sa.Integer(), nullable=True),
            sa.Column('orden_id', sa.Integer(), nullable=False),
            sa.Column('sucursal_id', sa.Integer(), nullable=True),
            sa.Column('tipo', tipo_solicitud_enum, nullable=False),
            sa.Column('motivo', motivo_devolucion_enum, nullable=False),
            sa.Column('motivo_detalle', sa.Text(), nullable=True),
            sa.Column('estado', estado_solicitud_enum, server_default='PENDIENTE', nullable=False),
            sa.Column('monto_reembolso', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('observaciones_staff', sa.Text(), nullable=True),
            sa.Column('revisado_por_id', sa.Integer(), nullable=True),
            sa.Column('fecha_solicitud', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column('fecha_resolucion', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['cliente_id'], ['clientes.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['orden_id'], ['ordenes.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['revisado_por_id'], ['usuarios.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['sucursal_id'], ['sucursales.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('numero_solicitud')
        )
        op.create_index(op.f('ix_solicitudes_devolucion_id'), 'solicitudes_devolucion', ['id'], unique=False)
        op.create_index(op.f('ix_solicitudes_devolucion_numero_solicitud'), 'solicitudes_devolucion', ['numero_solicitud'], unique=True)

    if 'detalles_solicitud_devolucion' not in existing_tables:
        op.create_table(
            'detalles_solicitud_devolucion',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('solicitud_id', sa.Integer(), nullable=False),
            sa.Column('detalle_orden_id', sa.Integer(), nullable=False),
            sa.Column('variante_producto_id', sa.Integer(), nullable=True),
            sa.Column('cantidad', sa.Integer(), nullable=False),
            sa.Column('variante_cambio_id', sa.Integer(), nullable=True),
            sa.Column('precio_unitario', sa.Numeric(precision=10, scale=2), nullable=False),
            sa.ForeignKeyConstraint(['detalle_orden_id'], ['detalles_orden.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['solicitud_id'], ['solicitudes_devolucion.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['variante_cambio_id'], ['variantes_producto.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['variante_producto_id'], ['variantes_producto.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_detalles_solicitud_devolucion_id'), 'detalles_solicitud_devolucion', ['id'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    
    op.drop_table('detalles_solicitud_devolucion')
    op.drop_table('solicitudes_devolucion')
    
    op.drop_column('productos', 'total_valoraciones')
    op.drop_column('productos', 'promedio_valoracion')
    op.drop_table('valoraciones_producto')
    op.drop_table('productos_favoritos')
    
    op.drop_table('cupon_categorias')
    op.drop_table('cupon_productos')
    
    op.drop_table('promocion_sucursales')
    op.drop_table('promocion_categorias')
    op.drop_table('promocion_productos')
    op.drop_table('promociones')
    
    sa.Enum(name='estadosolicituddevolucion').drop(conn, checkfirst=True)
    sa.Enum(name='motivodevolucion').drop(conn, checkfirst=True)
    sa.Enum(name='tiposolicituddevolucion').drop(conn, checkfirst=True)
    sa.Enum(name='estadovaloracion').drop(conn, checkfirst=True)
    sa.Enum(name='estadopromocion').drop(conn, checkfirst=True)
    sa.Enum(name='tipopromocion').drop(conn, checkfirst=True)
