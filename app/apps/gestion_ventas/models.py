"""
Modelos SQLAlchemy para la Gestión de Ventas, Reservas y Pagos.
Contiene: Carrito, Orden, VentaPresencial, Reserva, TransaccionPago, Comprobante, PasarelaPago.
(Implementación completa en Iteración 2)
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Text, Numeric, Time, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base


# Enumeraciones - Se importarán de otros módulos cuando estén definidos
# Aquí definimos las enumeraciones locales para esta iteración

class EstadoCarrito(str, enum.Enum):
    ACTIVO = "ACTIVO"
    ABANDONADO = "ABANDONADO"
    CONVERTIDO = "CONVERTIDO"


class TipoOrden(str, enum.Enum):
    DIGITAL = "DIGITAL"
    PRESENCIAL = "PRESENCIAL"
    RESERVA = "RESERVA"


class EstadoOrden(str, enum.Enum):
    PENDIENTE_PAGO = "PENDIENTE_PAGO"
    PAGADO = "PAGADO"
    EN_PROCESO = "EN_PROCESO"
    ENVIADO = "ENVIADO"
    ENTREGADO = "ENTREGADO"
    CANCELADO = "CANCELADO"


class MetodoPagoPresencial(str, enum.Enum):
    EFECTIVO = "EFECTIVO"
    TARJETA_DEBITO = "TARJETA_DEBITO"
    TARJETA_CREDITO = "TARJETA_CREDITO"
    QR = "QR"


class EstadoReserva(str, enum.Enum):
    PENDIENTE = "PENDIENTE"
    PREPARADA = "PREPARADA"
    EN_PRUEBA = "EN_PRUEBA"
    COMPLETADA = "COMPLETADA"
    CANCELADA = "CANCELADA"
    CADUCADA = "CADUCADA"


class EstadoDetalleReserva(str, enum.Enum):
    PENDIENTE = "PENDIENTE"
    PREPARADO = "PREPARADO"
    EN_PRUEBA = "EN_PRUEBA"
    COMPRADO = "COMPRADO"
    DEVUELTO = "DEVUELTO"


class MetodoPagoDigital(str, enum.Enum):
    TARJETA_DEBITO = "TARJETA_DEBITO"
    TARJETA_CREDITO = "TARJETA_CREDITO"
    PAYPAL = "PAYPAL"
    STRIPE = "STRIPE"
    QR = "QR"
    TRANSFERENCIA = "TRANSFERENCIA"


class EstadoTransaccion(str, enum.Enum):
    PENDIENTE = "PENDIENTE"
    CONFIRMADO = "CONFIRMADO"
    RECHAZADO = "RECHAZADO"
    REEMBOLSADO = "REEMBOLSADO"
    EN_PROCESO = "EN_PROCESO"


class TipoComprobante(str, enum.Enum):
    FACTURA = "FACTURA"
    RECIBO = "RECIBO"
    TICKET = "TICKET"
    NOTA_VENTA = "NOTA_VENTA"


class EstadoPasarela(str, enum.Enum):
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"
    MANTENIMIENTO = "MANTENIMIENTO"


# Modelo: Carrito
class Carrito(Base):
    """Tabla de carrito de compras."""
    __tablename__ = "carritos"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False)
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    estado = Column(Enum(EstadoCarrito), default=EstadoCarrito.ACTIVO)
    
    # Relaciones
    cliente = relationship("Cliente", back_populates="carritos")
    items = relationship("ItemCarrito", back_populates="carrito")
    
    def __repr__(self):
        return f"<Carrito {self.id} - Cliente {self.cliente_id}>"


# Modelo: ItemCarrito
class ItemCarrito(Base):
    """Tabla de items del carrito."""
    __tablename__ = "items_carrito"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    carrito_id = Column(Integer, ForeignKey("carritos.id", ondelete="CASCADE"), nullable=False)
    variante_producto_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="CASCADE"), nullable=False)
    cantidad = Column(Integer, default=1)
    precio_unitario = Column(Numeric(10, 2), nullable=False)
    subtotal = Column(Numeric(10, 2), nullable=False)
    
    # Relaciones
    carrito = relationship("Carrito", back_populates="items")
    variante_producto = relationship("VarianteProducto")
    
    def __repr__(self):
        return f"<ItemCarrito {self.id} - Producto {self.variante_producto_id}>"


# Modelo: Orden
class Orden(Base):
    """Tabla de órdenes."""
    __tablename__ = "ordenes"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    numero_orden = Column(String(50), unique=True, nullable=False, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="SET NULL"), nullable=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="SET NULL"), nullable=True)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    tipo = Column(Enum(TipoOrden), nullable=False)
    estado = Column(Enum(EstadoOrden), default=EstadoOrden.PENDIENTE_PAGO)
    total = Column(Numeric(10, 2), nullable=False)
    impuestos = Column(Numeric(10, 2), default=0)
    descuentos = Column(Numeric(10, 2), default=0)
    direccion_envio = Column(String(500), nullable=True)
    
    # Relaciones
    cliente = relationship("Cliente", back_populates="ordenes")
    sucursal = relationship("Sucursal")
    detalles = relationship("DetalleOrden", back_populates="orden")
    transacciones = relationship("TransaccionPago", back_populates="orden")
    comprobante = relationship("Comprobante", back_populates="orden", uselist=False)
    
    def __repr__(self):
        return f"<Orden {self.numero_orden}>"


# Modelo: DetalleOrden
class DetalleOrden(Base):
    """Tabla de detalles de orden."""
    __tablename__ = "detalles_orden"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    orden_id = Column(Integer, ForeignKey("ordenes.id", ondelete="CASCADE"), nullable=False)
    variante_producto_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="SET NULL"), nullable=True)
    cantidad = Column(Integer, nullable=False)
    precio_unitario = Column(Numeric(10, 2), nullable=False)
    subtotal = Column(Numeric(10, 2), nullable=False)
    
    # Relaciones
    orden = relationship("Orden", back_populates="detalles")
    variante_producto = relationship("VarianteProducto")
    
    def __repr__(self):
        return f"<DetalleOrden {self.id} - Orden {self.orden_id}>"


# Modelo: VentaPresencial
class VentaPresencial(Base):
    """Tabla de ventas presenciales."""
    __tablename__ = "ventas_presenciales"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    orden_id = Column(Integer, ForeignKey("ordenes.id", ondelete="SET NULL"), nullable=True)
    cajero_id = Column(Integer, ForeignKey("cajeros.id", ondelete="SET NULL"), nullable=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="SET NULL"), nullable=False)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="SET NULL"), nullable=True)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    metodo_pago = Column(Enum(MetodoPagoPresencial), nullable=False)
    
    # Relaciones
    orden = relationship("Orden")
    cajero = relationship("Cajero", back_populates="ventas")
    sucursal = relationship("Sucursal")
    cliente = relationship("Cliente", back_populates="ventas_presenciales")
    
    def __repr__(self):
        return f"<VentaPresencial {self.id}>"


# Modelo: Reserva
class Reserva(Base):
    """Tabla de reservas de prendas."""
    __tablename__ = "reservas"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    numero_reserva = Column(String(50), unique=True, nullable=False, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False)
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    fecha_reserva = Column(DateTime(timezone=True), nullable=False)
    horario_aproximado = Column(Time, nullable=True)
    estado = Column(Enum(EstadoReserva), default=EstadoReserva.PENDIENTE)
    notas = Column(Text, nullable=True)
    
    # Relaciones
    cliente = relationship("Cliente", back_populates="reservas")
    sucursal = relationship("Sucursal")
    detalles = relationship("DetalleReserva", back_populates="reserva")
    
    def __repr__(self):
        return f"<Reserva {self.numero_reserva}>"


# Modelo: DetalleReserva
class DetalleReserva(Base):
    """Tabla de detalles de reserva."""
    __tablename__ = "detalles_reserva"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    reserva_id = Column(Integer, ForeignKey("reservas.id", ondelete="CASCADE"), nullable=False)
    variante_producto_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="CASCADE"), nullable=False)
    cantidad = Column(Integer, default=1)
    estado = Column(Enum(EstadoDetalleReserva), default=EstadoDetalleReserva.PENDIENTE)
    
    # Relaciones
    reserva = relationship("Reserva", back_populates="detalles")
    variante_producto = relationship("VarianteProducto")
    
    def __repr__(self):
        return f"<DetalleReserva {self.id}>"


# Modelo: TransaccionPago
class TransaccionPago(Base):
    """Tabla de transacciones de pago."""
    __tablename__ = "transacciones_pago"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    orden_id = Column(Integer, ForeignKey("ordenes.id", ondelete="CASCADE"), nullable=False)
    pasarela_pago_id = Column(Integer, ForeignKey("pasarelas_pago.id", ondelete="SET NULL"), nullable=True)
    monto = Column(Numeric(10, 2), nullable=False)
    moneda = Column(String(3), default="GTQ")
    metodo_pago = Column(Enum(MetodoPagoDigital), nullable=False)
    estado = Column(Enum(EstadoTransaccion), default=EstadoTransaccion.PENDIENTE)
    referencia_externa = Column(String(255), nullable=True)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    datos_respuesta = Column(JSON, nullable=True)
    
    # Relaciones
    orden = relationship("Orden", back_populates="transacciones")
    pasarela_pago = relationship("PasarelaPago")
    
    def __repr__(self):
        return f"<TransaccionPago {self.id} - Orden {self.orden_id}>"


# Modelo: Comprobante
class Comprobante(Base):
    """Tabla de comprobantes."""
    __tablename__ = "comprobantes"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    numero = Column(String(50), unique=True, nullable=False)
    tipo = Column(Enum(TipoComprobante), nullable=False)
    fecha_emision = Column(DateTime(timezone=True), server_default=func.now())
    monto_total = Column(Numeric(10, 2), nullable=False)
    archivo_pdf = Column(String(500), nullable=True)
    orden_id = Column(Integer, ForeignKey("ordenes.id", ondelete="CASCADE"), nullable=False)
    
    # Relaciones
    orden = relationship("Orden", back_populates="comprobante")
    
    def __repr__(self):
        return f"<Comprobante {self.numero}>"


# Modelo: PasarelaPago
class PasarelaPago(Base):
    """Tabla de pasarelas de pago."""
    __tablename__ = "pasarelas_pago"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    tipo = Column(String(50), nullable=False)  # stripe, paypal
    configuracion = Column(JSON, nullable=True)
    estado = Column(Enum(EstadoPasarela), default=EstadoPasarela.ACTIVO)
    
    # Relaciones
    transacciones = relationship("TransaccionPago", back_populates="pasarela_pago")
    
    def __repr__(self):
        return f"<PasarelaPago {self.nombre}>"


# Importar modelos que faltan
from app.apps.gestion_usuarios.models import Cliente, Cajero
from app.apps.gestion_catalogo.models import VarianteProducto, Sucursal