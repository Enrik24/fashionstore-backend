"""
Modelos SQLAlchemy del Paquete 5 — Carrito offline y pago diferido.

[EXAMEN-2] Fase 2 (plan 3.3.10/3.3.11). Solo se declaran las tablas nuevas
`pagos_diferidos` y `eventos_stripe`; el carrito y la orden siguen viviendo en
`gestion_ventas` (regla anti-duplicados, capítulo 3).
"""
from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, JSON,
    Numeric, String, Text, UniqueConstraint, Uuid, text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.apps.operaciones_offline.models import EstadoPagoDiferido


# ============================================================
# 3.3.10 PagoDiferido — tabla pagos_diferidos
# ============================================================

class PagoDiferido(Base):
    """Intención de cobro de una orden creada offline (CU16).

    `uuid_pago` es la clave de idempotencia y la `Idempotency-Key` de Stripe
    (RN-015). Un pago solo pasa a `CONFIRMADO` desde un webhook firmado o una
    verificación directa del `PaymentIntent`.
    """
    __tablename__ = "pagos_diferidos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "uuid_pago", name="uq_pagos_diferidos_tenant_uuid"),
        # [EXAMEN-2] Plan 3.6: único cuando no es NULL
        Index(
            "uq_pagos_diferidos_stripe_payment_intent",
            "stripe_payment_intent_id",
            unique=True,
            postgresql_where=text("stripe_payment_intent_id IS NOT NULL"),
        ),
        # [EXAMEN-2] Índices obligatorios del plan, 16.1
        Index(
            "ix_pagos_diferidos_tenant_estado_expira",
            "tenant_id", "estado", "fecha_expiracion",
        ),
        Index("ix_pagos_diferidos_tenant_fecha", "tenant_id", "fecha_creacion"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    orden_id = Column(Integer, ForeignKey("ordenes.id", ondelete="CASCADE"), nullable=False, index=True)
    uuid_pago = Column(Uuid(as_uuid=True), nullable=False, index=True)
    monto = Column(Numeric(10, 2), nullable=False)
    moneda = Column(String(3), nullable=False, default="BOB", server_default="BOB")
    estado = Column(
        Enum(EstadoPagoDiferido, name="estado_pago_diferido"),
        nullable=False,
        default=EstadoPagoDiferido.PENDIENTE_ENVIO,
        server_default=EstadoPagoDiferido.PENDIENTE_ENVIO.value,
        index=True,
    )
    stripe_payment_intent_id = Column(String(120), nullable=True)
    stripe_checkout_session_id = Column(String(120), nullable=True)
    idempotency_key = Column(String(255), nullable=False)
    intentos = Column(Integer, nullable=False, default=0, server_default="0")
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    fecha_expiracion = Column(DateTime(timezone=True), nullable=False)
    fecha_ultimo_intento = Column(DateTime(timezone=True), nullable=True)
    ultimo_error = Column(Text, nullable=True)
    operacion_offline_id = Column(
        Integer, ForeignKey("operaciones_offline.id", ondelete="SET NULL"), nullable=True
    )
    client_secret = Column(String(255), nullable=True)
    motivo_rechazo = Column(Text, nullable=True)
    reembolso_id = Column(String(120), nullable=True)

    orden = relationship("Orden")
    operacion_offline = relationship("OperacionOffline", back_populates="pago_diferido")
    eventos = relationship("EventoStripe", back_populates="pago_diferido")

    def __repr__(self):
        return f"<PagoDiferido {self.uuid_pago} {self.estado}>"


# ============================================================
# 3.3.11 EventoStripe — tabla eventos_stripe
# ============================================================

class EventoStripe(Base):
    """Webhook de Stripe recibido, con deduplicación global por evento."""
    __tablename__ = "eventos_stripe"
    __table_args__ = (
        UniqueConstraint("stripe_event_id", name="uq_eventos_stripe_event_id"),
        # [EXAMEN-2] Índice obligatorio del plan, 16.1
        Index("ix_eventos_stripe_procesado_fecha", "procesado", "fecha_recepcion"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    stripe_event_id = Column(String(120), nullable=False, unique=True, index=True)
    tipo = Column(String(80), nullable=False)
    payload_json = Column(JSON, nullable=False)
    procesado = Column(Boolean, nullable=False, default=False, server_default="false")
    fecha_recepcion = Column(DateTime(timezone=True), server_default=func.now())
    fecha_procesamiento = Column(DateTime(timezone=True), nullable=True)
    pago_diferido_id = Column(
        Integer, ForeignKey("pagos_diferidos.id", ondelete="SET NULL"), nullable=True
    )
    error_detalle = Column(Text, nullable=True)

    pago_diferido = relationship("PagoDiferido", back_populates="eventos")

    def __repr__(self):
        return f"<EventoStripe {self.stripe_event_id} {self.tipo} procesado={self.procesado}>"
