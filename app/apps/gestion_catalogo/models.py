"""
Modelos SQLAlchemy para la Gestión de Catálogo, Productos e Inventario.
Contiene: Ciudad, Sucursal, Categoria, Talla, Color, Temporada, Coleccion, Proveedor, 
Producto, VarianteProducto, Inventario, MovimientoInventario.
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Text, Float, JSON, Numeric, Date, Time, UniqueConstraint
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
    """Tabla de sucursales."""
    __tablename__ = "sucursales"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    direccion = Column(String(500), nullable=False)
    telefono = Column(String(20), nullable=False)
    horario_atencion = Column(String(200), nullable=True)
    estado = Column(Enum(EstadoSucursal), default=EstadoSucursal.ACTIVO)
    latitud = Column(Float, nullable=True)
    longitud = Column(Float, nullable=True)
    ciudad_id = Column(Integer, ForeignKey("ciudades.id", ondelete="CASCADE"), nullable=False)
    
    # Relaciones
    ciudad = relationship("Ciudad", back_populates="sucursales")
    encargado = relationship("EncargadoSucursal", back_populates="sucursal", uselist=False)
    cajeros = relationship("Cajero", back_populates="sucursal")
    inventarios = relationship("Inventario", back_populates="sucursal")
    
    def __repr__(self):
        return f"<Sucursal {self.nombre}>"


# Modelo: Categoría
class Categoria(Base):
    """Tabla de categorías de productos."""
    __tablename__ = "categorias"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), unique=True, nullable=False)
    descripcion = Column(Text, nullable=True)
    imagen = Column(String(500), nullable=True)
    
    # Relaciones
    productos = relationship("Producto", back_populates="categoria")
    
    def __repr__(self):
        return f"<Categoria {self.nombre}>"


# Modelo: Talla
class Talla(Base):
    """Tabla de tallas."""
    __tablename__ = "tallas"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    valor = Column(String(10), nullable=False)
    tipo = Column(String(50), nullable=True)  # ropa, calzado, accesorios
    descripcion = Column(String(100), nullable=True)
    
    # Relaciones
    variantes = relationship("VarianteProducto", back_populates="talla")
    
    def __repr__(self):
        return f"<Talla {self.valor}>"


# Modelo: Color
class Color(Base):
    """Tabla de colores."""
    __tablename__ = "colores"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(50), nullable=False)
    codigo_hex = Column(String(7), nullable=True)
    imagen_muestra = Column(String(500), nullable=True)
    
    # Relaciones
    variantes = relationship("VarianteProducto", back_populates="color")
    
    def __repr__(self):
        return f"<Color {self.nombre}>"


# Modelo: Temporada
class Temporada(Base):
    """Tabla de temporadas comerciales."""
    __tablename__ = "temporadas"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    fecha_inicio = Column(Date, nullable=False)
    fecha_fin = Column(Date, nullable=False)
    descripcion = Column(Text, nullable=True)
    
    # Relaciones
    productos = relationship("Producto", back_populates="temporada")
    colecciones = relationship("Coleccion", back_populates="temporada")
    
    def __repr__(self):
        return f"<Temporada {self.nombre}>"


# Modelo: Colección
class Coleccion(Base):
    """Tabla de colecciones."""
    __tablename__ = "colecciones"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    descripcion = Column(Text, nullable=True)
    temporada_id = Column(Integer, ForeignKey("temporadas.id", ondelete="SET NULL"), nullable=True)
    
    # Relaciones
    temporada = relationship("Temporada", back_populates="colecciones")
    productos = relationship("Producto", secondary="productos_colecciones", back_populates="colecciones")
    
    def __repr__(self):
        return f"<Coleccion {self.nombre}>"


# Modelo: Proveedor
class Proveedor(Base):
    """Tabla de proveedores."""
    __tablename__ = "proveedores"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(200), nullable=False)
    nit = Column(String(20), unique=True, nullable=False)
    contacto = Column(String(100), nullable=True)
    telefono = Column(String(20), nullable=True)
    correo = Column(String(255), nullable=True)
    direccion = Column(String(500), nullable=True)
    
    # Relaciones
    productos = relationship("Producto", back_populates="proveedor")
    recepciones = relationship("Recepcion", back_populates="proveedor")
    
    def __repr__(self):
        return f"<Proveedor {self.nombre}>"


# Modelo: Producto
class Producto(Base):
    """Tabla de productos (prendas de vestir)."""
    __tablename__ = "productos"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    sku = Column(String(50), unique=True, nullable=False, index=True)
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
    
    # Relaciones
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
    """Tabla de variantes de producto (combinación de talla y color)."""
    __tablename__ = "variantes_producto"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    producto_id = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False)
    talla_id = Column(Integer, ForeignKey("tallas.id", ondelete="SET NULL"), nullable=True)
    color_id = Column(Integer, ForeignKey("colores.id", ondelete="CASCADE"), nullable=False)
    sku_variante = Column(String(50), unique=True, nullable=False)
    precio_variante = Column(Numeric(10, 2), nullable=True)
    costo_variante = Column(Numeric(10, 2), nullable=True)
    
    # Relaciones
    producto = relationship("Producto", back_populates="variantes")
    talla = relationship("Talla", back_populates="variantes")
    color = relationship("Color", back_populates="variantes")
    inventarios = relationship("Inventario", back_populates="variante_producto")
    
    def __repr__(self):
        return f"<VarianteProducto {self.sku_variante}>"


# Modelo: Inventario
class Inventario(Base):
    """Tabla de inventario por sucursal y variante de producto."""
    __tablename__ = "inventario"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    variante_producto_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="CASCADE"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False)
    cantidad = Column(Integer, default=0)
    cantidad_reservada = Column(Integer, default=0)
    cantidad_vendida = Column(Integer, default=0)
    stock_minimo = Column(Integer, default=5)
    estado = Column(Enum(EstadoStock), default=EstadoStock.DISPONIBLE)
    
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
    """Tabla de movimientos de inventario."""
    __tablename__ = "movimientos_inventario"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    inventario_id = Column(Integer, ForeignKey("inventario.id", ondelete="CASCADE"), nullable=False)
    tipo = Column(Enum(TipoMovimiento), nullable=False)
    cantidad = Column(Integer, nullable=False)
    motivo = Column(String(500), nullable=True)
    fecha_hora = Column(DateTime(timezone=True), server_default=func.now())
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    
    # Relaciones
    inventario = relationship("Inventario", back_populates="movimientos")
    usuario = relationship("Usuario")
    
    def __repr__(self):
        return f"<MovimientoInventario {self.tipo}: {self.cantidad}>"


# Modelo: Recepcion de mercadería por proveedor (Opción A - Recepción Directa)
class Recepcion(Base):
    """Cabecera de recepción de mercadería proveniente de un proveedor."""
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