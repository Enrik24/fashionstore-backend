"""
Modelos SQLAlchemy para la Gestión de Marketing y Promociones (CU24) y Aplicabilidad de Cupones (CU27).
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Text, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base


class TipoPromocion(str, enum.Enum):
    PORCENTAJE = "PORCENTAJE"
    MONTO_FIJO = "MONTO_FIJO"
    DOS_POR_UNO = "DOS_POR_UNO"
    ENVIO_GRATIS = "ENVIO_GRATIS"


class EstadoPromocion(str, enum.Enum):
    ACTIVA = "ACTIVA"
    INACTIVA = "INACTIVA"
    PROGRAMADA = "PROGRAMADA"
    FINALIZADA = "FINALIZADA"


class Promocion(Base):
    """Tabla de promociones comerciales."""
    __tablename__ = "promociones"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(150), nullable=False)
    descripcion = Column(Text, nullable=True)
    tipo = Column(Enum(TipoPromocion), nullable=False)
    valor = Column(Numeric(10, 2), nullable=True)
    fecha_inicio = Column(DateTime(timezone=True), nullable=False)
    fecha_fin = Column(DateTime(timezone=True), nullable=False)
    condiciones = Column(Text, nullable=True)
    estado = Column(Enum(EstadoPromocion), default=EstadoPromocion.ACTIVA, nullable=False)
    creado_por_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())

    # Relaciones
    creado_por = relationship("Usuario")
    promocion_productos = relationship("PromocionProducto", back_populates="promocion", cascade="all, delete-orphan")
    promocion_categorias = relationship("PromocionCategoria", back_populates="promocion", cascade="all, delete-orphan")
    promocion_sucursales = relationship("PromocionSucursal", back_populates="promocion", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Promocion {self.id}: {self.nombre} ({self.tipo})>"


class PromocionProducto(Base):
    """Tabla intermedia para asociar productos específicos a una promoción."""
    __tablename__ = "promocion_productos"
    __table_args__ = (
        UniqueConstraint("promocion_id", "producto_id", name="uq_promocion_producto"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    promocion_id = Column(Integer, ForeignKey("promociones.id", ondelete="CASCADE"), nullable=False)
    producto_id = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False)

    # Relaciones
    promocion = relationship("Promocion", back_populates="promocion_productos")
    producto = relationship("Producto")

    def __repr__(self):
        return f"<PromocionProducto promocion={self.promocion_id} producto={self.producto_id}>"


class PromocionCategoria(Base):
    """Tabla intermedia para asociar categorías completas a una promoción."""
    __tablename__ = "promocion_categorias"
    __table_args__ = (
        UniqueConstraint("promocion_id", "categoria_id", name="uq_promocion_categoria"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    promocion_id = Column(Integer, ForeignKey("promociones.id", ondelete="CASCADE"), nullable=False)
    categoria_id = Column(Integer, ForeignKey("categorias.id", ondelete="CASCADE"), nullable=False)

    # Relaciones
    promocion = relationship("Promocion", back_populates="promocion_categorias")
    categoria = relationship("Categoria")

    def __repr__(self):
        return f"<PromocionCategoria promocion={self.promocion_id} categoria={self.categoria_id}>"


class PromocionSucursal(Base):
    """Tabla intermedia para asociar sucursales a una promoción (vacía = aplica a todas)."""
    __tablename__ = "promocion_sucursales"
    __table_args__ = (
        UniqueConstraint("promocion_id", "sucursal_id", name="uq_promocion_sucursal"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    promocion_id = Column(Integer, ForeignKey("promociones.id", ondelete="CASCADE"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False)

    # Relaciones
    promocion = relationship("Promocion", back_populates="promocion_sucursales")
    sucursal = relationship("Sucursal")

    def __repr__(self):
        return f"<PromocionSucursal promocion={self.promocion_id} sucursal={self.sucursal_id}>"


class CuponProducto(Base):
    """Tabla intermedia para asociar productos específicos a un cupón (CU27)."""
    __tablename__ = "cupon_productos"
    __table_args__ = (
        UniqueConstraint("cupon_id", "producto_id", name="uq_cupon_producto"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    cupon_id = Column(Integer, ForeignKey("cupones.id", ondelete="CASCADE"), nullable=False)
    producto_id = Column(Integer, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False)

    # Relaciones
    cupon = relationship("Cupon", back_populates="cupon_productos")
    producto = relationship("Producto")

    def __repr__(self):
        return f"<CuponProducto cupon={self.cupon_id} producto={self.producto_id}>"


class CuponCategoria(Base):
    """Tabla intermedia para asociar categorías a un cupón (CU27)."""
    __tablename__ = "cupon_categorias"
    __table_args__ = (
        UniqueConstraint("cupon_id", "categoria_id", name="uq_cupon_categoria"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    cupon_id = Column(Integer, ForeignKey("cupones.id", ondelete="CASCADE"), nullable=False)
    categoria_id = Column(Integer, ForeignKey("categorias.id", ondelete="CASCADE"), nullable=False)

    # Relaciones
    cupon = relationship("Cupon", back_populates="cupon_categorias")
    categoria = relationship("Categoria")

    def __repr__(self):
        return f"<CuponCategoria cupon={self.cupon_id} categoria={self.categoria_id}>"


# Importación tardía para registrar relaciones con otros módulos
from app.apps.gestion_usuarios.models import Usuario
from app.apps.gestion_catalogo.models import Producto, Categoria, Sucursal
from app.apps.gestion_ventas.models import Cupon

# Registrar relaciones inversas en Cupon si no existen
Cupon.cupon_productos = relationship("CuponProducto", back_populates="cupon", cascade="all, delete-orphan")
Cupon.cupon_categorias = relationship("CuponCategoria", back_populates="cupon", cascade="all, delete-orphan")
