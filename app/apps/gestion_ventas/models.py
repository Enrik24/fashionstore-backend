"""
Modelos SQLAlchemy para la Gestión de Ventas, Reservas y Pagos.
Contiene: Cupon, Carrito, ItemCarrito, Orden, DetalleOrden, VentaPresencial, Reserva, DetalleReserva, TransaccionPago, Comprobante, PasarelaPago.

Modificado en Examen 2 (SaaS multitenant): los modelos de esta app se
extienden con `tenant_id`, campos offline (UUID, estado de sincronización) y
los estados nuevos de CU13/CU16; no se crean tablas espejo (plan, 3.9).
"""
from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, JSON,
    Numeric, String, Text, Time, UniqueConstraint, Uuid, text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base
# [EXAMEN-2] Enums compartidos del paquete offline (Fase 1)
from app.apps.operaciones_offline.models import EstadoSincronizacion, OrigenOperacion


# Enumeraciones

class TipoCupon(str, enum.Enum):
    PORCENTAJE = "PORCENTAJE"
    MONTO_FIJO = "MONTO_FIJO"


class EstadoCupon(str, enum.Enum):
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"
    EXPIRADO = "EXPIRADO"
    AGOTADO = "AGOTADO"


class EstadoCarrito(str, enum.Enum):
    ACTIVO = "ACTIVO"
    ABANDONADO = "ABANDONADO"
    CONVERTIDO = "CONVERTIDO"
    SINCRONIZANDO = "SINCRONIZANDO"  # [EXAMEN-2] CU15 carrito offline


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
    # [EXAMEN-2] CU16 pago diferido, RN-016 y RN-017
    PENDIENTE_PAGO_DIFERIDO = "PENDIENTE_PAGO_DIFERIDO"
    PAGO_VENCIDO = "PAGO_VENCIDO"
    PENDIENTE_REGULARIZACION = "PENDIENTE_REGULARIZACION"


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
    # [EXAMEN-2] CU16 / RN-016
    EXPIRADO = "EXPIRADO"
    CANCELADO = "CANCELADO"


class TipoComprobante(str, enum.Enum):
    FACTURA = "FACTURA"
    RECIBO = "RECIBO"
    TICKET = "TICKET"
    NOTA_VENTA = "NOTA_VENTA"


class EstadoPasarela(str, enum.Enum):
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"
    MANTENIMIENTO = "MANTENIMIENTO"


# Modelo: Cupon
class Cupon(Base):
    """Tabla de cupones de descuento.

    Modificado en Examen 2 (SaaS multitenant): unicidad compuesta
    `(tenant_id, codigo)` en lugar de `codigo` único global.
    """
    __tablename__ = "cupones"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codigo", name="uq_cupones_tenant_codigo"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    codigo = Column(String(50), nullable=False, index=True)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = Column(Enum(TipoCupon), nullable=False)
    valor = Column(Numeric(10, 2), nullable=False)
    descripcion = Column(String(255), nullable=True)
    fecha_inicio = Column(DateTime(timezone=True), nullable=False)
    fecha_fin = Column(DateTime(timezone=True), nullable=False)
    usos_maximos = Column(Integer, nullable=True)
    usos_actuales = Column(Integer, default=0, nullable=False)
    monto_minimo = Column(Numeric(10, 2), nullable=True)
    estado = Column(Enum(EstadoCupon), default=EstadoCupon.ACTIVO, nullable=False)
    creado_por_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relaciones
    creado_por = relationship("Usuario")
    
    def __repr__(self):
        return f"<Cupon {self.codigo} - {self.tipo}: {self.valor}>"


# Modelo: Carrito
class Carrito(Base):
    """Tabla de carrito de compras.

    Modificado en Examen 2 (SaaS multitenant): el carrito del Examen 1 se
    extiende (no se recrea) con `tenant_id`, `uuid_carrito`, dispositivo,
    banderas offline y estado de sincronización; `cliente_id` pasa a nullable
    porque el carrito offline puede existir sin cliente autenticado
    (plan, 3.7.9).
    """
    __tablename__ = "carritos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "uuid_carrito", name="uq_carritos_tenant_uuid"),
        # [EXAMEN-2] Apoyo: un carrito ACTIVO por dispositivo/cliente se
        # refuerza con índice parcial en la migración ex2_006.
        Index("ix_carritos_tenant_dispositivo_cliente", "tenant_id", "dispositivo_id", "cliente_id"),
        # [EXAMEN-2] Índice obligatorio del plan, 16.1
        Index("ix_carritos_tenant_dispositivo_estado", "tenant_id", "dispositivo_id", "estado"),
        Index(
            "uq_carritos_activo_dispositivo_cliente",
            "tenant_id",
            "dispositivo_id",
            "cliente_id",
            unique=True,
            postgresql_where=text("estado = 'ACTIVO'"),
        ),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="CASCADE"), nullable=True)
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    estado = Column(Enum(EstadoCarrito), default=EstadoCarrito.ACTIVO)
    cupon_id = Column(Integer, ForeignKey("cupones.id", ondelete="SET NULL"), nullable=True)
    descuento_aplicado = Column(Numeric(10, 2), default=0)
    # [EXAMEN-2] Aislamiento multitenant y carrito offline (CU15)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    uuid_carrito = Column(Uuid(as_uuid=True), nullable=True, index=True)
    dispositivo_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("dispositivos_sincronizacion.id", ondelete="SET NULL"),
        nullable=True,
    )
    es_offline = Column(Boolean, nullable=False, default=False, server_default="false")
    estado_sincronizacion = Column(
        Enum(EstadoSincronizacion, name="estado_sincronizacion"),
        nullable=False,
        default=EstadoSincronizacion.NO_APLICA,
        server_default=EstadoSincronizacion.NO_APLICA.value,
    )
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # [EXAMEN-2] RNF-14
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] baja lógica
    
    # Relaciones
    cliente = relationship("Cliente", back_populates="carritos")
    items = relationship("ItemCarrito", back_populates="carrito", cascade="all, delete-orphan")
    cupon = relationship("Cupon")
    dispositivo = relationship("DispositivoSincronizacion")  # [EXAMEN-2] CU17
    tenant = relationship("Tenant")  # [EXAMEN-2]
    
    def __repr__(self):
        return f"<Carrito {self.id} - Cliente {self.cliente_id}>"


# Modelo: ItemCarrito
class ItemCarrito(Base):
    """Tabla de items del carrito.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id` y precios locales
    del dispositivo; `precio_unitario` sigue siendo la referencia del
    servidor (RN-014).
    """
    __tablename__ = "items_carrito"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    carrito_id = Column(Integer, ForeignKey("carritos.id", ondelete="CASCADE"), nullable=False)
    variante_producto_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="CASCADE"), nullable=False)
    cantidad = Column(Integer, default=1)
    precio_unitario = Column(Numeric(10, 2), nullable=False)
    subtotal = Column(Numeric(10, 2), nullable=False)
    # [EXAMEN-2] Aislamiento y contraste de precios offline (RN-014)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    precio_unitario_local = Column(Numeric(10, 2), nullable=True)
    stock_local_conocido = Column(Integer, nullable=True)
    
    # Relaciones
    carrito = relationship("Carrito", back_populates="items")
    variante_producto = relationship("VarianteProducto")
    
    def __repr__(self):
        return f"<ItemCarrito {self.id} - Producto {self.variante_producto_id}>"


# Modelo: Orden
class Orden(Base):
    """Tabla de órdenes.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, identificador
    offline `uuid_orden`, origen, banderas de sincronización, moneda y
    vigencias (`fecha_limite_cambio` RN-012, `expira_en` RN-016); unicidad
    compuesta `(tenant_id, numero_orden)`.
    """
    __tablename__ = "ordenes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "numero_orden", name="uq_ordenes_tenant_numero"),
        UniqueConstraint("tenant_id", "uuid_orden", name="uq_ordenes_tenant_uuid"),
        # [EXAMEN-2] Índices obligatorios del plan, 16.1
        Index("ix_ordenes_tenant_fecha", "tenant_id", "fecha"),
        Index("ix_ordenes_tenant_estado", "tenant_id", "estado"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    numero_orden = Column(String(50), nullable=False, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="SET NULL"), nullable=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="SET NULL"), nullable=True)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    tipo = Column(Enum(TipoOrden), nullable=False)
    estado = Column(Enum(EstadoOrden), default=EstadoOrden.PENDIENTE_PAGO)
    total = Column(Numeric(10, 2), nullable=False)
    impuestos = Column(Numeric(10, 2), default=0)
    descuentos = Column(Numeric(10, 2), default=0)
    cupon_id = Column(Integer, ForeignKey("cupones.id", ondelete="SET NULL"), nullable=True)
    direccion_envio = Column(String(500), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant y trazabilidad offline (CU06/CU07)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    uuid_orden = Column(Uuid(as_uuid=True), nullable=True, index=True)
    origen = Column(
        Enum(OrigenOperacion, name="origen_operacion"),
        nullable=False,
        default=OrigenOperacion.ONLINE,
        server_default=OrigenOperacion.ONLINE.value,
    )
    es_offline = Column(Boolean, nullable=False, default=False, server_default="false")
    estado_sincronizacion = Column(
        Enum(EstadoSincronizacion, name="estado_sincronizacion"),
        nullable=False,
        default=EstadoSincronizacion.NO_APLICA,
        server_default=EstadoSincronizacion.NO_APLICA.value,
    )
    # [EXAMEN-2] RN-012: plazo de devolución/cambio de 7 días
    fecha_limite_cambio = Column(DateTime(timezone=True), nullable=True)
    # [EXAMEN-2] CU16: vencimiento del pago y vigencia de 24 h (RN-016)
    fecha_limite_pago = Column(DateTime(timezone=True), nullable=True)
    expira_en = Column(DateTime(timezone=True), nullable=True)
    moneda = Column(String(3), nullable=False, default="BOB", server_default="BOB")
    # [EXAMEN-2] Fase 3: marca de datos sintéticos para no contaminar reportes (RF-023)
    es_sintetico = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    
    # Relaciones
    cliente = relationship("Cliente", back_populates="ordenes")
    sucursal = relationship("Sucursal")
    detalles = relationship("DetalleOrden", back_populates="orden", cascade="all, delete-orphan")
    transacciones = relationship("TransaccionPago", back_populates="orden", cascade="all, delete-orphan")
    comprobante = relationship("Comprobante", back_populates="orden", uselist=False)
    cupon = relationship("Cupon")
    
    def __repr__(self):
        return f"<Orden {self.numero_orden}>"


# Modelo: DetalleOrden
class DetalleOrden(Base):
    """Tabla de detalles de orden.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id` y precios local y
    de servidor para conciliar el recálculo (RN-014).
    """
    __tablename__ = "detalles_orden"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    orden_id = Column(Integer, ForeignKey("ordenes.id", ondelete="CASCADE"), nullable=False)
    variante_producto_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="SET NULL"), nullable=True)
    cantidad = Column(Integer, nullable=False)
    precio_unitario = Column(Numeric(10, 2), nullable=False)
    costo_unitario = Column(Numeric(10, 2), nullable=True)
    subtotal = Column(Numeric(10, 2), nullable=False)
    # [EXAMEN-2] Aislamiento multitenant (diagrama v2)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    precio_unitario_local = Column(Numeric(10, 2), nullable=True)  # [EXAMEN-2] RN-014
    precio_unitario_servidor = Column(Numeric(10, 2), nullable=True)  # [EXAMEN-2] RN-014
    
    # Relaciones
    orden = relationship("Orden", back_populates="detalles")
    variante_producto = relationship("VarianteProducto")
    
    def __repr__(self):
        return f"<DetalleOrden {self.id} - Orden {self.orden_id}>"


# Modelo: VentaPresencial
class VentaPresencial(Base):
    """Tabla de ventas presenciales.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, bandera offline y
    `uuid_offline` para la venta de contado del POS (CU07).
    """
    __tablename__ = "ventas_presenciales"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    orden_id = Column(Integer, ForeignKey("ordenes.id", ondelete="SET NULL"), nullable=True)
    cajero_id = Column(Integer, ForeignKey("cajeros.id", ondelete="SET NULL"), nullable=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="SET NULL"), nullable=False)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="SET NULL"), nullable=True)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    metodo_pago = Column(Enum(MetodoPagoPresencial), nullable=False)
    # [EXAMEN-2] Aislamiento y operación offline
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    es_offline = Column(Boolean, nullable=False, default=False, server_default="false")
    uuid_offline = Column(Uuid(as_uuid=True), unique=True, nullable=True, index=True)
    estado_sincronizacion = Column(
        Enum(EstadoSincronizacion, name="estado_sincronizacion"),
        nullable=False,
        default=EstadoSincronizacion.NO_APLICA,
        server_default=EstadoSincronizacion.NO_APLICA.value,
    )
    
    # Relaciones
    orden = relationship("Orden")
    cajero = relationship("Cajero", back_populates="ventas")
    sucursal = relationship("Sucursal")
    cliente = relationship("Cliente", back_populates="ventas_presenciales")
    
    def __repr__(self):
        return f"<VentaPresencial {self.id}>"


# Modelo: Reserva
class Reserva(Base):
    """Tabla de reservas de prendas.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, `uuid_reserva`,
    banderas offline y unicidad compuesta `(tenant_id, numero_reserva)`
    (CU06).
    """
    __tablename__ = "reservas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "numero_reserva", name="uq_reservas_tenant_numero"),
        UniqueConstraint("tenant_id", "uuid_reserva", name="uq_reservas_tenant_uuid"),
        # [EXAMEN-2] Índice obligatorio del plan, 16.1
        Index("ix_reservas_tenant_estado_fecha", "tenant_id", "estado", "fecha_reserva"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    numero_reserva = Column(String(50), nullable=False, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="CASCADE"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False)
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    fecha_reserva = Column(DateTime(timezone=True), nullable=False)
    horario_aproximado = Column(Time, nullable=True)
    estado = Column(Enum(EstadoReserva), default=EstadoReserva.PENDIENTE)
    notas = Column(Text, nullable=True)
    # [EXAMEN-2] Aislamiento y operación offline
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    uuid_reserva = Column(Uuid(as_uuid=True), nullable=True, index=True)
    es_offline = Column(Boolean, nullable=False, default=False, server_default="false")
    estado_sincronizacion = Column(
        Enum(EstadoSincronizacion, name="estado_sincronizacion"),
        nullable=False,
        default=EstadoSincronizacion.NO_APLICA,
        server_default=EstadoSincronizacion.NO_APLICA.value,
    )
    
    # Relaciones
    cliente = relationship("Cliente", back_populates="reservas")
    sucursal = relationship("Sucursal")
    detalles = relationship("DetalleReserva", back_populates="reserva", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Reserva {self.numero_reserva}>"


# Modelo: DetalleReserva
class DetalleReserva(Base):
    """Tabla de detalles de reserva.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id` NOT NULL.
    """
    __tablename__ = "detalles_reserva"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    reserva_id = Column(Integer, ForeignKey("reservas.id", ondelete="CASCADE"), nullable=False)
    variante_producto_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="CASCADE"), nullable=False)
    cantidad = Column(Integer, default=1)
    estado = Column(Enum(EstadoDetalleReserva), default=EstadoDetalleReserva.PENDIENTE)
    # [EXAMEN-2] Aislamiento multitenant (diagrama v2)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Relaciones
    reserva = relationship("Reserva", back_populates="detalles")
    variante_producto = relationship("VarianteProducto")
    
    def __repr__(self):
        return f"<DetalleReserva {self.id}>"


# Modelo: TransaccionPago
class TransaccionPago(Base):
    """Tabla de transacciones de pago.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, datos de Stripe,
    origen, idempotencia, reintentos y expiración (CU16 / RN-015 / RN-016).
    Sigue siendo el registro contable del pago (plan, 3.7.8).
    """
    __tablename__ = "transacciones_pago"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    orden_id = Column(Integer, ForeignKey("ordenes.id", ondelete="CASCADE"), nullable=False)
    pasarela_pago_id = Column(Integer, ForeignKey("pasarelas_pago.id", ondelete="SET NULL"), nullable=True)
    monto = Column(Numeric(10, 2), nullable=False)
    moneda = Column(String(3), default="USD")
    metodo_pago = Column(Enum(MetodoPagoDigital), nullable=False)
    estado = Column(Enum(EstadoTransaccion), default=EstadoTransaccion.PENDIENTE)
    referencia_externa = Column(String(255), nullable=True)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    datos_respuesta = Column(JSON, nullable=True)
    # [EXAMEN-2] Aislamiento multitenant (diagrama v2)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    # [EXAMEN-2] CU16 / RN-015
    stripe_payment_intent_id = Column(String(120), nullable=True, index=True)
    origen = Column(
        Enum(OrigenOperacion, name="origen_operacion"),
        nullable=False,
        default=OrigenOperacion.ONLINE,
        server_default=OrigenOperacion.ONLINE.value,
    )
    idempotency_key = Column(String(255), nullable=True)
    intentos = Column(Integer, nullable=False, default=0, server_default="0")
    fecha_expiracion = Column(DateTime(timezone=True), nullable=True)
    
    # Relaciones
    orden = relationship("Orden", back_populates="transacciones")
    pasarela_pago = relationship("PasarelaPago")
    
    def __repr__(self):
        return f"<TransaccionPago {self.id} - Orden {self.orden_id}>"


# Modelo: Comprobante
class Comprobante(Base):
    """Tabla de comprobantes.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, `uuid_offline`,
    `es_temporal` y unicidad compuesta `(tenant_id, numero)`.
    """
    __tablename__ = "comprobantes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "numero", name="uq_comprobantes_tenant_numero"),
        UniqueConstraint("tenant_id", "uuid_offline", name="uq_comprobantes_tenant_uuid"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    numero = Column(String(50), nullable=False)
    tipo = Column(Enum(TipoComprobante), nullable=False)
    fecha_emision = Column(DateTime(timezone=True), server_default=func.now())
    monto_total = Column(Numeric(10, 2), nullable=False)
    archivo_pdf = Column(String(500), nullable=True)
    orden_id = Column(Integer, ForeignKey("ordenes.id", ondelete="CASCADE"), nullable=False)
    # [EXAMEN-2] Aislamiento y comprobante temporal del POS offline
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    uuid_offline = Column(Uuid(as_uuid=True), nullable=True, index=True)
    es_temporal = Column(Boolean, nullable=False, default=False, server_default="false")
    
    # Relaciones
    orden = relationship("Orden", back_populates="comprobante")
    
    def __repr__(self):
        return f"<Comprobante {self.numero}>"


# Modelo: PasarelaPago
class PasarelaPago(Base):
    """Tabla de pasarelas de pago.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id` nullable (NULL =
    configuración global de Stripe), clave pública y configuración pública.
    La clave secreta sigue solo en variables de entorno (RNF-15).
    """
    __tablename__ = "pasarelas_pago"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    tipo = Column(String(50), nullable=False)  # stripe, paypal
    configuracion = Column(JSON, nullable=True)
    estado = Column(Enum(EstadoPasarela), default=EstadoPasarela.ACTIVO)
    # [EXAMEN-2] NULL = configuración global de la plataforma
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    clave_publica = Column(String(255), nullable=True)  # [EXAMEN-2] CU16
    configuracion_publica = Column(JSON, nullable=True)  # [EXAMEN-2] CU16
    
    # Relaciones
    transacciones = relationship("TransaccionPago", back_populates="pasarela_pago")
    
    def __repr__(self):
        return f"<PasarelaPago {self.nombre}>"


# Enumeraciones adicionales para CU28
class TipoSolicitudDevolucion(str, enum.Enum):
    DEVOLUCION = "DEVOLUCION"
    # [EXAMEN-2] CU18: reposición = devolución + prenda nueva del mismo producto.
    REPOSICION = "REPOSICION"
    CAMBIO = "CAMBIO"  # legacy: se conserva para clientes y datos antiguos


class MotivoDevolucion(str, enum.Enum):
    TALLA_INCORRECTA = "TALLA_INCORRECTA"
    COLOR_INCORRECTO = "COLOR_INCORRECTO"
    DEFECTO_FABRICA = "DEFECTO_FABRICA"
    OTRO = "OTRO"


class EstadoSolicitudDevolucion(str, enum.Enum):
    PENDIENTE = "PENDIENTE"
    EN_REVISION = "EN_REVISION"
    APROBADA = "APROBADA"
    RECHAZADA = "RECHAZADA"
    COMPLETADA = "COMPLETADA"
    PENDIENTE_REEMBOLSO = "PENDIENTE_REEMBOLSO"
    # [EXAMEN-2] CU13 offline y errores de sincronización (plan, 3.8.5)
    PENDIENTE_SINCRONIZACION = "PENDIENTE_SINCRONIZACION"
    CONFLICTO_MANUAL = "CONFLICTO_MANUAL"
    ERROR_VENTA_INVALIDA = "ERROR_VENTA_INVALIDA"
    # [EXAMEN-2] Fase D / RF-034: no hay talla alternativa con stock.
    SIN_ALTERNATIVA = "SIN_ALTERNATIVA"


class TipoDiferencia(str, enum.Enum):
    """[EXAMEN-2] Resultado económico del cambio (CU13 / plan, 3.8.3)."""
    COBRO = "COBRO"
    SALDO_FAVOR = "SALDO_FAVOR"
    SIN_DIFERENCIA = "SIN_DIFERENCIA"


# Modelo: SolicitudDevolucion (CU28)
class SolicitudDevolucion(Base):
    """Tabla de solicitudes de devolución y cambio.

    Modificado en Examen 2 (SaaS multitenant): se extiende en su app original
    con `tenant_id`, idempotencia offline, plazo RN-012, diferencia de precio,
    saldo a favor y nota de devolución (plan, 3.8.3). No se duplica la tabla.
    """
    __tablename__ = "solicitudes_devolucion"
    __table_args__ = (
        UniqueConstraint("tenant_id", "numero_solicitud", name="uq_solicitudes_tenant_numero"),
        UniqueConstraint("tenant_id", "uuid_offline", name="uq_solicitudes_tenant_uuid"),
        # [EXAMEN-2] Fase C / CU18: número de reposición único por tenant.
        Index(
            "uq_solicitudes_tenant_numero_reposicion",
            "tenant_id",
            "numero_reposicion",
            unique=True,
            postgresql_where=text("numero_reposicion IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    numero_solicitud = Column(String(50), nullable=False, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="SET NULL"), nullable=True)
    orden_id = Column(Integer, ForeignKey("ordenes.id", ondelete="CASCADE"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="SET NULL"), nullable=True)
    tipo = Column(Enum(TipoSolicitudDevolucion), nullable=False)
    motivo = Column(Enum(MotivoDevolucion), nullable=False)
    motivo_detalle = Column(Text, nullable=True)
    estado = Column(Enum(EstadoSolicitudDevolucion), default=EstadoSolicitudDevolucion.PENDIENTE, nullable=False)
    monto_reembolso = Column(Numeric(10, 2), nullable=True)
    observaciones_staff = Column(Text, nullable=True)
    revisado_por_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    fecha_solicitud = Column(DateTime(timezone=True), server_default=func.now())
    fecha_resolucion = Column(DateTime(timezone=True), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant y operación offline (CU13)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    uuid_offline = Column(Uuid(as_uuid=True), nullable=True, index=True)  # [EXAMEN-2] idempotencia RN-005
    es_offline = Column(Boolean, nullable=False, default=False, server_default="false")
    estado_sincronizacion = Column(
        Enum(EstadoSincronizacion, name="estado_sincronizacion"),
        nullable=False,
        default=EstadoSincronizacion.NO_APLICA,
        server_default=EstadoSincronizacion.NO_APLICA.value,
    )
    fecha_limite_cambio = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] RN-012
    diferencia_precio = Column(Numeric(10, 2), nullable=True)  # [EXAMEN-2] CU13
    tipo_diferencia = Column(Enum(TipoDiferencia), nullable=True)  # [EXAMEN-2] CU13
    monto_pagado_diferencia = Column(Numeric(10, 2), nullable=True)  # [EXAMEN-2] CU13
    saldo_favor = Column(Numeric(10, 2), nullable=True)  # [EXAMEN-2] nota de crédito
    nota_devolucion_url = Column(String(500), nullable=True)  # [EXAMEN-2] PDF
    usuario_procesa_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)  # [EXAMEN-2] CU13
    motivo_rechazo = Column(Text, nullable=True)  # [EXAMEN-2] trazabilidad
    # [EXAMEN-2] Fase C / CU18: numeración y nota propias de la reposición.
    numero_reposicion = Column(String(50), nullable=True)
    nota_reposicion_url = Column(String(500), nullable=True)

    # Relaciones
    cliente = relationship("Cliente", back_populates="solicitudes_devolucion")
    orden = relationship("Orden", back_populates="solicitudes_devolucion")
    sucursal = relationship("Sucursal")
    revisado_por = relationship("Usuario", foreign_keys=[revisado_por_id])
    usuario_procesa = relationship("Usuario", foreign_keys=[usuario_procesa_id])  # [EXAMEN-2] CU13
    detalles = relationship("DetalleSolicitudDevolucion", back_populates="solicitud", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SolicitudDevolucion {self.numero_solicitud} - {self.tipo}: {self.estado}>"


# Modelo: DetalleSolicitudDevolucion (CU28)
class DetalleSolicitudDevolucion(Base):
    """Tabla de detalles de solicitud de devolución/cambio.

    Modificado en Examen 2 (SaaS multitenant): `tenant_id`, precio histórico
    de la variante nueva, subtotal de diferencia y estado del ítem
    (plan, 3.8.4).
    """
    __tablename__ = "detalles_solicitud_devolucion"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    solicitud_id = Column(Integer, ForeignKey("solicitudes_devolucion.id", ondelete="CASCADE"), nullable=False)
    detalle_orden_id = Column(Integer, ForeignKey("detalles_orden.id", ondelete="CASCADE"), nullable=False)
    variante_producto_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="SET NULL"), nullable=True)
    cantidad = Column(Integer, nullable=False)
    variante_cambio_id = Column(Integer, ForeignKey("variantes_producto.id", ondelete="SET NULL"), nullable=True)
    precio_unitario = Column(Numeric(10, 2), nullable=False)
    # [EXAMEN-2] Aislamiento multitenant (diagrama v2)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    precio_variante_nueva = Column(Numeric(10, 2), nullable=True)  # [EXAMEN-2] snapshot de precio
    subtotal_diferencia = Column(Numeric(10, 2), nullable=True)  # [EXAMEN-2] CU13
    estado_item = Column(String(30), nullable=True)  # [EXAMEN-2] PENDIENTE/RESERVADO/ENTREGADO/RECHAZADO

    # Relaciones
    solicitud = relationship("SolicitudDevolucion", back_populates="detalles")
    detalle_orden = relationship("DetalleOrden")
    variante_producto = relationship("VarianteProducto", foreign_keys=[variante_producto_id])
    variante_cambio = relationship("VarianteProducto", foreign_keys=[variante_cambio_id])

    def __repr__(self):
        return f"<DetalleSolicitudDevolucion {self.id} - Solicitud {self.solicitud_id}>"


# ============================================================
# [EXAMEN-2] Fase D / RF-037 / RN-024 — Notificaciones al cliente
# ============================================================

class CanalNotificacion(str, enum.Enum):
    PUSH = "PUSH"
    CORREO = "CORREO"
    SMS = "SMS"


class EstadoNotificacion(str, enum.Enum):
    PENDIENTE = "PENDIENTE"
    ENVIADA = "ENVIADA"
    FALLIDA = "FALLIDA"
    LEIDA = "LEIDA"


class NotificacionCliente(Base):
    """Notificación persistida antes de intentar el envío (RN-024).

    [EXAMEN-2] Fase D: si FCM/correo/SMS falla, la fila queda `FALLIDA` y es
    reintentable; la sucursal siempre puede consultarla.
    """
    __tablename__ = "notificaciones_cliente"
    __table_args__ = (
        Index("ix_notificaciones_cliente_fecha", "tenant_id", "fecha_creacion"),
        Index("ix_notificaciones_cliente_estado", "tenant_id", "estado"),
        # [EXAMEN-2] Evita duplicados al reintentar (plan, capítulo 11).
        Index(
            "uq_notificaciones_cliente_referencia",
            "tenant_id", "tipo", "referencia_tipo", "referencia_id", "canal",
            unique=True,
            postgresql_where=text("referencia_id IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="SET NULL"), nullable=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    tipo = Column(String(50), nullable=False)  # COMPENSACION, DEVOLUCION, REPOSICION, SIN_ALTERNATIVA
    canal = Column(Enum(CanalNotificacion), nullable=False)
    asunto = Column(String(150), nullable=True)
    mensaje = Column(Text, nullable=False)
    estado = Column(Enum(EstadoNotificacion), nullable=False, default=EstadoNotificacion.PENDIENTE)
    referencia_tipo = Column(String(50), nullable=True)
    referencia_id = Column(Integer, nullable=True)
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    fecha_envio = Column(DateTime(timezone=True), nullable=True)
    leida = Column(Boolean, nullable=False, default=False, server_default="false")
    error_detalle = Column(Text, nullable=True)

    cliente = relationship("Cliente")
    usuario = relationship("Usuario")

    def __repr__(self):
        return f"<NotificacionCliente {self.tipo} {self.canal}: {self.estado}>"


# Importar modelos relacionados para registrar relaciones
from app.apps.gestion_usuarios.models import Usuario, Cliente, Cajero
from app.apps.gestion_catalogo.models import VarianteProducto, Sucursal

# Inyectar relaciones en Orden y Cliente
Orden.solicitudes_devolucion = relationship("SolicitudDevolucion", back_populates="orden", cascade="all, delete-orphan")
Cliente.solicitudes_devolucion = relationship("SolicitudDevolucion", back_populates="cliente")