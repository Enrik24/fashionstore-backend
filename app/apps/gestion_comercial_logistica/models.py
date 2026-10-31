"""[EXAMEN-2] Modelos del Paquete 4 — Gestión comercial y logística (CU14).

`Traspaso` es la cabecera y `DetalleTraspaso` cada variante movida, según el
diagrama v2 (plan, 3.3.6 y 3.3.16). La devolución/cambio (CU13) reutiliza los
modelos de `gestion_ventas` y no crea tablas aquí.
"""
import enum

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class EstadoTraspaso(str, enum.Enum):
    """Máquina de estados del traspaso (plan, 9.3.8)."""
    EN_TRANSITO = "EN_TRANSITO"
    RECIBIDO = "RECIBIDO"
    RECHAZADO_PARCIAL = "RECHAZADO_PARCIAL"
    CANCELADO = "CANCELADO"


class Traspaso(Base):
    """Cabecera de un traspaso de stock entre sucursales (CU14)."""
    __tablename__ = "traspasos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "numero", name="uq_traspasos_tenant_numero"),
        CheckConstraint(
            "sucursal_origen_id <> sucursal_destino_id",
            name="ck_traspasos_sucursales_distintas",
        ),
        Index("ix_traspasos_tenant_estado", "tenant_id", "estado"),
        Index("ix_traspasos_tenant_fecha", "tenant_id", "fecha_envio"),
        # [EXAMEN-2] Índices obligatorios del plan, 16.1
        Index("ix_traspasos_tenant_origen", "tenant_id", "sucursal_origen_id"),
        Index("ix_traspasos_tenant_destino", "tenant_id", "sucursal_destino_id"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    numero = Column(String(50), nullable=False)
    sucursal_origen_id = Column(
        Integer, ForeignKey("sucursales.id", ondelete="RESTRICT"), nullable=False
    )
    sucursal_destino_id = Column(
        Integer, ForeignKey("sucursales.id", ondelete="RESTRICT"), nullable=False
    )
    estado = Column(
        Enum(EstadoTraspaso, name="estado_traspaso"),
        nullable=False,
        default=EstadoTraspaso.EN_TRANSITO,
        server_default=EstadoTraspaso.EN_TRANSITO.value,
    )
    fecha_envio = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    fecha_recepcion = Column(DateTime(timezone=True), nullable=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    observaciones = Column(Text, nullable=True)
    motivo = Column(Text, nullable=True)
    usuario_recibe_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)

    sucursal_origen = relationship("Sucursal", foreign_keys=[sucursal_origen_id])
    sucursal_destino = relationship("Sucursal", foreign_keys=[sucursal_destino_id])
    usuario = relationship("Usuario", foreign_keys=[usuario_id])
    usuario_recibe = relationship("Usuario", foreign_keys=[usuario_recibe_id])
    detalles = relationship(
        "DetalleTraspaso",
        back_populates="traspaso",
        cascade="all, delete-orphan",
        order_by="DetalleTraspaso.id",
    )

    def __repr__(self):
        return f"<Traspaso {self.numero} - {self.estado}>"


class DetalleTraspaso(Base):
    """Línea de un traspaso: variante, cantidad enviada y recibida/merma."""
    __tablename__ = "detalles_traspaso"
    __table_args__ = (
        CheckConstraint("cantidad > 0", name="ck_detalles_traspaso_cantidad_positiva"),
        CheckConstraint(
            "cantidad_recibida >= 0 AND cantidad_merma >= 0",
            name="ck_detalles_traspaso_recibida_merma_no_negativas",
        ),
        Index("ix_detalles_traspaso_tenant_variante", "tenant_id", "variante_producto_id"),
        # [EXAMEN-2] PostgreSQL no indexa las FK; el plan 16.1 lo exige
        Index("ix_detalles_traspaso_traspaso", "traspaso_id"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    traspaso_id = Column(
        Integer, ForeignKey("traspasos.id", ondelete="CASCADE"), nullable=False
    )
    variante_producto_id = Column(
        Integer, ForeignKey("variantes_producto.id", ondelete="RESTRICT"), nullable=False
    )
    cantidad = Column(Integer, nullable=False)
    cantidad_recibida = Column(Integer, nullable=False, default=0, server_default="0")
    cantidad_merma = Column(Integer, nullable=False, default=0, server_default="0")
    observaciones = Column(Text, nullable=True)

    traspaso = relationship("Traspaso", back_populates="detalles")
    variante_producto = relationship("VarianteProducto")

    def __repr__(self):
        return f"<DetalleTraspaso {self.id} - {self.cantidad}>"
