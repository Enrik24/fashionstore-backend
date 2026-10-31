"""
Modelos SQLAlchemy para la Gestión de Catálogo, Productos e Inventario.
Contiene: Ciudad, Sucursal, Categoria, Talla, Color, Temporada, Coleccion, Proveedor, 
Producto, VarianteProducto, Inventario, MovimientoInventario.
"""
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey, Enum, Text, Float, JSON, Numeric, Date, Time, UniqueConstraint, CheckConstraint, Uuid, Index, text, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base


# Enumeraciones
class EstadoSucursal(str, enum.Enum):
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"


class EstadoProducto(str, enum.Enum):
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"
    AGOTADO = "AGOTADO"
    PROXIMO_INGRESO = "PROXIMO_INGRESO"


class GeneroProducto(str, enum.Enum):
    HOMBRE = "HOMBRE"
    MUJER = "MUJER"
    UNISEX = "UNISEX"


class EstadoStock(str, enum.Enum):
    DISPONIBLE = "DISPONIBLE"
    RESERVADO = "RESERVADO"
    VENDIDO = "VENDIDO"
    AGOTADO = "AGOTADO"
    PROXIMO_A_INGRESAR = "PROXIMO_A_INGRESAR"


class TipoMovimiento(str, enum.Enum):
    RECEPCION = "RECEPCION"
    VENTA = "VENTA"
    RESERVA = "RESERVA"
    DEVOLUCION = "DEVOLUCION"
    AJUSTE = "AJUSTE"
    CANCELACION_RESERVA = "CANCELACION_RESERVA"
    # [EXAMEN-2] CU14 traspasos entre sucursales y CU13 cambios/mermas
    TRASPASO_SALIDA = "TRASPASO_SALIDA"
    TRASPASO_ENTRADA = "TRASPASO_ENTRADA"
    MERMA = "MERMA"
    CAMBIO = "CAMBIO"


# Modelo: Ciudad
class Ciudad(Base):
    """Tabla de ciudades."""
    __tablename__ = "ciudades"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    codigo_postal = Column(String(20), nullable=True)
    pais = Column(String(100), default="Guatemala")
    
    # Relaciones
    sucursales = relationship("Sucursal", back_populates="ciudad")
    
    def __repr__(self):
        return f"<Ciudad {self.nombre}>"


# Modelo: Sucursal
class Sucursal(Base):
    """Tabla de sucursales.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, `updated_at` y
    `deleted_at`; unicidad lógica `(tenant_id, nombre)`.
    """
    __tablename__ = "sucursales"
    __table_args__ = (
        # [EXAMEN-2] Fase 2.5: el nombre solo es único entre sucursales activas;
        # una baja lógica lo libera sin romper el historial (ex2_015).
        Index(
            "uq_sucursales_tenant_nombre_activas",
            "tenant_id",
            "nombre",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    direccion = Column(String(500), nullable=False)
    telefono = Column(String(20), nullable=False)
    horario_atencion = Column(String(200), nullable=True)
    estado = Column(Enum(EstadoSucursal), default=EstadoSucursal.ACTIVO)
    latitud = Column(Float, nullable=True)
    longitud = Column(Float, nullable=True)
    ciudad_id = Column(Integer, ForeignKey("ciudades.id", ondelete="CASCADE"), nullable=False)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    # [EXAMEN-2] Descarga incremental y baja lógica (RNF-14)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relaciones
    tenant = relationship("Tenant", back_populates="sucursales")  # [EXAMEN-2]
    ciudad = relationship("Ciudad", back_populates="sucursales")
    encargado = relationship("EncargadoSucursal", back_populates="sucursal", uselist=False)
    cajeros = relationship("Cajero", back_populates="sucursal")
    inventarios = relationship("Inventario", back_populates="sucursal")
    
    def __repr__(self):
        return f"<Sucursal {self.nombre}>"


# Modelo: Categoría
class Categoria(Base):
    """Tabla de categorías de productos.

    Modificado en Examen 2 (SaaS multitenant): unicidad compuesta
    `(tenant_id, nombre)` en lugar de `nombre` único global.
    """
    __tablename__ = "categorias"
    __table_args__ = (
        UniqueConstraint("tenant_id", "nombre", name="uq_categorias_tenant_nombre"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    descripcion = Column(Text, nullable=True)
    imagen = Column(String(500), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # [EXAMEN-2] RNF-14
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] baja lógica
    
    # Relaciones
    productos = relationship("Producto", back_populates="categoria")
    
    def __repr__(self):
        return f"<Categoria {self.nombre}>"


# Modelo: Talla
class Talla(Base):
    """Tabla de tallas.

    Modificado en Examen 2 (SaaS multitenant): unicidad compuesta
    `(tenant_id, tipo, valor)`.
    """
    __tablename__ = "tallas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tipo", "valor", name="uq_tallas_tenant_tipo_valor"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    valor = Column(String(10), nullable=False)
    tipo = Column(String(50), nullable=True)  # ropa, calzado, accesorios
    descripcion = Column(String(100), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # [EXAMEN-2] RNF-14
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] baja lógica
    
    # Relaciones
    variantes = relationship("VarianteProducto", back_populates="talla")
    
    def __repr__(self):
        return f"<Talla {self.valor}>"


# Modelo: Color
class Color(Base):
    """Tabla de colores.

    Modificado en Examen 2 (SaaS multitenant): unicidad compuesta
    `(tenant_id, nombre)`.
    """
    __tablename__ = "colores"
    __table_args__ = (
        UniqueConstraint("tenant_id", "nombre", name="uq_colores_tenant_nombre"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(50), nullable=False)
    codigo_hex = Column(String(7), nullable=True)
    imagen_muestra = Column(String(500), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # [EXAMEN-2] RNF-14
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] baja lógica
    
    # Relaciones
    variantes = relationship("VarianteProducto", back_populates="color")
    
    def __repr__(self):
        return f"<Color {self.nombre}>"


# Modelo: Temporada
class Temporada(Base):
    """Tabla de temporadas comerciales.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id`,
    `updated_at` y `deleted_at`.
    """
    __tablename__ = "temporadas"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    fecha_inicio = Column(Date, nullable=False)
    fecha_fin = Column(Date, nullable=False)
    descripcion = Column(Text, nullable=True)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # [EXAMEN-2] RNF-14
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] baja lógica
    
    # Relaciones
    productos = relationship("Producto", back_populates="temporada")
    colecciones = relationship("Coleccion", back_populates="temporada")
    
    def __repr__(self):
        return f"<Temporada {self.nombre}>"


# Modelo: Colección
class Coleccion(Base):
    """Tabla de colecciones.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id` y
    `deleted_at` para evitar filtraciones entre tenants.
    """
    __tablename__ = "colecciones"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    descripcion = Column(Text, nullable=True)
    temporada_id = Column(Integer, ForeignKey("temporadas.id", ondelete="SET NULL"), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] baja lógica
    
    # Relaciones
    temporada = relationship("Temporada", back_populates="colecciones")
    productos = relationship("Producto", secondary="productos_colecciones", back_populates="colecciones")
    
    def __repr__(self):
        return f"<Coleccion {self.nombre}>"


# Modelo: Proveedor
class Proveedor(Base):
    """Tabla de proveedores.

    Modificado en Examen 2 (SaaS multitenant): unicidad compuesta
    `(tenant_id, nit)` en lugar de `nit` único global.
    """
    __tablename__ = "proveedores"
    __table_args__ = (
        UniqueConstraint("tenant_id", "nit", name="uq_proveedores_tenant_nit"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(200), nullable=False)
    nit = Column(String(20), nullable=False)
    contacto = Column(String(100), nullable=True)
    telefono = Column(String(20), nullable=True)
    correo = Column(String(255), nullable=True)
    direccion = Column(String(500), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # [EXAMEN-2] RNF-14
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] baja lógica
    
    # Relaciones
    productos = relationship("Producto", back_populates="proveedor")
    recepciones = relationship("Recepcion", back_populates="proveedor")
    
    def __repr__(self):
        return f"<Proveedor {self.nombre}>"


# Modelo: Producto
class Producto(Base):
    """Tabla de productos (prendas de vestir).

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, `version`,
    `updated_at` y `deleted_at`; la unicidad del SKU pasa a ser compuesta
    `(tenant_id, sku)` y `version`/`updated_at` habilitan la descarga
    incremental (CU05 / RNF-14).
    """
    __tablename__ = "productos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_productos_tenant_sku"),
        # [EXAMEN-2] Índices obligatorios del plan, 16.1
        Index("ix_productos_tenant_estado", "tenant_id", "estado"),
        Index("ix_productos_tenant_categoria", "tenant_id", "categoria_id"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    sku = Column(String(50), nullable=False, index=True)
    nombre = Column(String(200), nullable=False)
    descripcion = Column(Text, nullable=True)
    precio = Column(Numeric(10, 2), nullable=False)
    costo_compra = Column(Numeric(10, 2), nullable=False, server_default="0")
    imagenes = Column(JSON, default=list)  # Lista de URLs de imágenes
    estado = Column(Enum(EstadoProducto), default=EstadoProducto.ACTIVO)
    genero = Column(Enum(GeneroProducto), default=GeneroProducto.UNISEX, nullable=True)
    promedio_valoracion = Column(Numeric(3, 2), default=0.00, server_default="0.00")
    total_valoraciones = Column(Integer, default=0, server_default="0")
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    categoria_id = Column(Integer, ForeignKey("categorias.id", ondelete="SET NULL"), nullable=True)
    temporada_id = Column(Integer, ForeignKey("temporadas.id", ondelete="SET NULL"), nullable=True)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id", ondelete="SET NULL"), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    # [EXAMEN-2] Base de la descarga incremental CU05
    version = Column(BigInteger, nullable=False, default=1, server_default="1")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] baja lógica
    
    # Relaciones
    tenant = relationship("Tenant", back_populates="productos")  # [EXAMEN-2]
    categoria = relationship("Categoria", back_populates="productos")
    temporada = relationship("Temporada", back_populates="productos")
    proveedor = relationship("Proveedor", back_populates="productos")
    variantes = relationship("VarianteProducto", back_populates="producto")
    colecciones = relationship("Coleccion", secondary="productos_colecciones", back_populates="productos")
    favoritos = relationship("ProductoFavorito", back_populates="producto", cascade="all, delete-orphan")
    valoraciones = relationship("ValoracionProducto", back_populates="producto", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Producto {self.sku}: {self.nombre}>"


# Tabla intermedia: Producto-Colección
class ProductoColeccion(Base):
    """Tabla intermedia para la relación N:M entre Producto y Coleccion."""
    __tablename__ = "productos_colecciones"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    producto_id = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False)
    coleccion_id = Column(Integer, ForeignKey("colecciones.id", ondelete="CASCADE"), nullable=False)
    
    def __repr__(self):
        return f"<ProductoColeccion producto={self.producto_id} coleccion={self.coleccion_id}>"


# Modelo: VarianteProducto
class VarianteProducto(Base):
    """Tabla de variantes de producto (combinación de talla y color).

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, `version`,
    `updated_at`, `deleted_at`; unicidad compuesta `(tenant_id, sku_variante)`.
    """
    __tablename__ = "variantes_producto"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku_variante", name="uq_variantes_tenant_sku"),
        # [EXAMEN-2] Índice obligatorio del plan, 16.1
        Index("ix_variantes_tenant_producto", "tenant_id", "producto_id"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    producto_id = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False)
    talla_id = Column(Integer, ForeignKey("tallas.id", ondelete="SET NULL"), nullable=True)
    color_id = Column(Integer, ForeignKey("colores.id", ondelete="CASCADE"), nullable=False)
    sku_variante = Column(String(50), nullable=False)
    precio_variante = Column(Numeric(10, 2), nullable=True)
    costo_variante = Column(Numeric(10, 2), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(BigInteger, nullable=False, default=1, server_default="1")  # [EXAMEN-2] CU05
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # [EXAMEN-2]
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] baja lógica
    
    # Relaciones
    producto = relationship("Producto", back_populates="variantes")
    talla = relationship("Talla", back_populates="variantes")
    color = relationship("Color", back_populates="variantes")
    inventarios = relationship("Inventario", back_populates="variante_producto")
    
    def __repr__(self):
        return f"<VarianteProducto {self.sku_variante}>"


# Modelo: Inventario
class Inventario(Base):
    """Tabla de inventario por sucursal y variante de producto.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, `version` y
    `updated_at`; unicidad compuesta por tenant (plan, 3.6).
    """
    __tablename__ = "inventario"
    __table_args__ = (
        UniqueConstraint("tenant_id", "variante_producto_id", "sucursal_id", name="uq_inventario_tenant_variante_sucursal"),
        CheckConstraint("cantidad >= 0", name="ck_inventario_cantidad_no_negativa"),
        CheckConstraint("cantidad_reservada >= 0", name="ck_inventario_reservada_no_negativa"),
        CheckConstraint("cantidad_vendida >= 0", name="ck_inventario_vendida_no_negativa"),
        CheckConstraint("cantidad_reservada <= cantidad", name="ck_inventario_reservada_lte_cantidad"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    variante_producto_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="CASCADE"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False)
    cantidad = Column(Integer, default=0)
    cantidad_reservada = Column(Integer, default=0)
    cantidad_vendida = Column(Integer, default=0)
    stock_minimo = Column(Integer, default=5)
    estado = Column(Enum(EstadoStock), default=EstadoStock.DISPONIBLE)
    # [EXAMEN-2] Aislamiento multitenant y descarga incremental
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(BigInteger, nullable=False, default=1, server_default="1")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relaciones
    variante_producto = relationship("VarianteProducto", back_populates="inventarios")
    sucursal = relationship("Sucursal", back_populates="inventarios")
    movimientos = relationship("MovimientoInventario", back_populates="inventario")
    
    def __repr__(self):
        return f"<Inventario {self.variante_producto_id} - {self.sucursal_id}: {self.cantidad}>"
    
    @property
    def cantidad_disponible(self) -> int:
        """Cantidad disponible para venta."""
        return max(0, self.cantidad - self.cantidad_reservada)


# Modelo: MovimientoInventario
class MovimientoInventario(Base):
    """Tabla de movimientos de inventario.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, `referencia_uuid`
    y nuevos valores de `TipoMovimiento` (traspaso, merma y cambio).
    """
    __tablename__ = "movimientos_inventario"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    inventario_id = Column(Integer, ForeignKey("inventario.id", ondelete="CASCADE"), nullable=False)
    tipo = Column(Enum(TipoMovimiento), nullable=False)
    cantidad = Column(Integer, nullable=False)
    motivo = Column(String(500), nullable=True)
    fecha_hora = Column(DateTime(timezone=True), server_default=func.now())
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant y trazabilidad de la operación
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    referencia_uuid = Column(Uuid(as_uuid=True), nullable=True, index=True)  # [EXAMEN-2] offline/traspaso/cambio
    # [EXAMEN-2] Fase 3: movimientos creados por el generador de datos sintéticos (RF-023)
    es_sintetico = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    
    # Relaciones
    inventario = relationship("Inventario", back_populates="movimientos")
    usuario = relationship("Usuario")
    
    def __repr__(self):
        return f"<MovimientoInventario {self.tipo}: {self.cantidad}>"


# Modelo: Recepcion de mercadería por proveedor (Opción A - Recepción Directa)
class Recepcion(Base):
    """Cabecera de recepción de mercadería proveniente de un proveedor.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id` NOT NULL.
    """
    __tablename__ = "recepciones"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    numero = Column(String(30), unique=True, nullable=False, index=True)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id", ondelete="RESTRICT"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="RESTRICT"), nullable=False)
    fecha_hora = Column(DateTime(timezone=True), server_default=func.now())
    nro_factura = Column(String(50), nullable=True)
    observaciones = Column(String(500), nullable=True)
    total_unidades = Column(Integer, default=0, server_default="0")
    total_costo = Column(Numeric(12, 2), default=0, server_default="0")
    creado_por = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    # Relaciones
    proveedor = relationship("Proveedor", back_populates="recepciones")
    sucursal = relationship("Sucursal")
    detalles = relationship("DetalleRecepcion", back_populates="recepcion", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Recepcion {self.numero} prov={self.proveedor_id} suc={self.sucursal_id}>"


class DetalleRecepcion(Base):
    """Detalle de recepción: variante + cantidad + costo."""
    __tablename__ = "detalles_recepcion"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    recepcion_id = Column(Integer, ForeignKey("recepciones.id", ondelete="CASCADE"), nullable=False)
    variante_producto_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="RESTRICT"), nullable=False)
    cantidad = Column(Integer, nullable=False)
    costo_unitario = Column(Numeric(10, 2), nullable=True)

    # Relaciones
    recepcion = relationship("Recepcion", back_populates="detalles")
    variante_producto = relationship("VarianteProducto")

    def __repr__(self):
        return f"<DetalleRecepcion rec={self.recepcion_id} var={self.variante_producto_id} cant={self.cantidad}>"


# Enumeraciones adicionales para CU26
class EstadoValoracion(str, enum.Enum):
    PUBLICADA = "PUBLICADA"
    PENDIENTE_MODERACION = "PENDIENTE_MODERACION"
    RECHAZADA = "RECHAZADA"


# Modelo: ProductoFavorito (CU25)
class ProductoFavorito(Base):
    """Tabla de productos favoritos de los clientes."""
    __tablename__ = "productos_favoritos"
    __table_args__ = (
        UniqueConstraint("cliente_id", "producto_id", name="uq_cliente_producto_favorito"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False)
    producto_id = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False)
    fecha_agregado = Column(DateTime(timezone=True), server_default=func.now())

    # Relaciones
    cliente = relationship("Cliente", back_populates="favoritos")
    producto = relationship("Producto", back_populates="favoritos")

    def __repr__(self):
        return f"<ProductoFavorito cliente={self.cliente_id} producto={self.producto_id}>"


# Modelo: ValoracionProducto (CU26)
class ValoracionProducto(Base):
    """Tabla de valoraciones y reseñas de productos."""
    __tablename__ = "valoraciones_producto"
    __table_args__ = (
        UniqueConstraint("cliente_id", "producto_id", name="uq_cliente_producto_valoracion"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False)
    producto_id = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False)
    puntuacion = Column(Integer, nullable=False)
    comentario = Column(Text, nullable=True)
    estado = Column(Enum(EstadoValoracion), default=EstadoValoracion.PUBLICADA, nullable=False)
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    fecha_actualizacion = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relaciones
    cliente = relationship("Cliente", back_populates="valoraciones")
    producto = relationship("Producto", back_populates="valoraciones")

    def __repr__(self):
        return f"<ValoracionProducto cliente={self.cliente_id} producto={self.producto_id}: {self.puntuacion}★>"


# Importación de modelos relacionados para registrar relaciones
from app.apps.gestion_usuarios.models import Usuario, EncargadoSucursal, Cajero, Cliente

# Inyectar relaciones en Cliente
Cliente.favoritos = relationship("ProductoFavorito", back_populates="cliente", cascade="all, delete-orphan")
Cliente.valoraciones = relationship("ValoracionProducto", back_populates="cliente", cascade="all, delete-orphan")