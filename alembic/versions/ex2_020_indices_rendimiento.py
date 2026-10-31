"""ex2_020 - Índice único de Stripe e índices obligatorios (plan 3.6 y 16.1)

[EXAMEN-2] Cierra las brechas de rendimiento y de idempotencia detectadas en la
verificación del plan:

- `pagos_diferidos.stripe_payment_intent_id` pasa de índice simple a índice
  **único parcial** `WHERE stripe_payment_intent_id IS NOT NULL` (plan, 3.6).
- Se crean los índices que el capítulo 16.1 marca como obligatorios, y se
  reemplazan los que quedan cubiertos por un compuesto más largo
  (`modelos_ml` y `pagos_diferidos`).

Revision ID: ex2_020_indices
Revises: ex2_019
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ex2_020"
down_revision: Union[str, None] = "ex2_019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (nombre, tabla y columnas) — capítulo 16.1 del plan.
_INDICES = (
    ("ix_productos_tenant_estado", "productos (tenant_id, estado)"),
    ("ix_productos_tenant_categoria", "productos (tenant_id, categoria_id)"),
    ("ix_variantes_tenant_producto", "variantes_producto (tenant_id, producto_id)"),
    ("ix_ordenes_tenant_fecha", "ordenes (tenant_id, fecha)"),
    ("ix_ordenes_tenant_estado", "ordenes (tenant_id, estado)"),
    ("ix_reservas_tenant_estado_fecha", "reservas (tenant_id, estado, fecha_reserva)"),
    ("ix_traspasos_tenant_origen", "traspasos (tenant_id, sucursal_origen_id)"),
    ("ix_traspasos_tenant_destino", "traspasos (tenant_id, sucursal_destino_id)"),
    ("ix_detalles_traspaso_traspaso", "detalles_traspaso (traspaso_id)"),
    ("ix_carritos_tenant_dispositivo_estado", "carritos (tenant_id, dispositivo_id, estado)"),
    ("ix_pagos_diferidos_tenant_estado_expira", "pagos_diferidos (tenant_id, estado, fecha_expiracion)"),
    ("ix_eventos_stripe_procesado_fecha", "eventos_stripe (procesado, fecha_recepcion)"),
    ("ix_modelos_ml_tenant_objetivo_estado", "modelos_ml (tenant_id, objetivo, estado)"),
)


def _duplicados_payment_intent():
    """Devuelve los `PaymentIntent` repetidos antes de crear el índice único."""
    conexion = op.get_bind()
    resultado = conexion.execute(sa.text(
        """
        SELECT stripe_payment_intent_id, count(*) AS total
        FROM pagos_diferidos
        WHERE stripe_payment_intent_id IS NOT NULL
        GROUP BY stripe_payment_intent_id
        HAVING count(*) > 1
        """
    ))
    return list(resultado)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tablas = set(inspector.get_table_names())

    # 1. Índice único parcial de Stripe (plan, 3.6).
    if "pagos_diferidos" in tablas:
        duplicados = _duplicados_payment_intent()
        if duplicados:
            detalle = ", ".join(
                f"{fila[0]} x{fila[1]}" for fila in duplicados[:5]
            )
            raise RuntimeError(
                "Hay PaymentIntent duplicados en pagos_diferidos y no se puede "
                "crear el índice único: "
                f"{detalle}. Consolide los pagos (conserve el CONFIRMADO y "
                "cancele el resto) y vuelva a ejecutar la migración."
            )
        op.execute(sa.text(
            "DROP INDEX IF EXISTS ix_pagos_diferidos_stripe_payment_intent_id"
        ))
        op.execute(sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_pagos_diferidos_stripe_payment_intent "
            "ON pagos_diferidos (stripe_payment_intent_id) "
            "WHERE stripe_payment_intent_id IS NOT NULL"
        ))

    # 2. Índices del capítulo 16.1.
    for nombre, destino in _INDICES:
        tabla = destino.split(" ", 1)[0]
        if tabla in tablas:
            op.execute(sa.text(
                f"CREATE INDEX IF NOT EXISTS {nombre} ON {destino}"
            ))

    # 3. Los compuestos nuevos cubren el prefijo de los antiguos.
    op.execute(sa.text("DROP INDEX IF EXISTS ix_modelos_ml_tenant_objetivo"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_pagos_diferidos_tenant_estado"))


def downgrade() -> None:
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_modelos_ml_tenant_objetivo "
        "ON modelos_ml (tenant_id, objetivo)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pagos_diferidos_tenant_estado "
        "ON pagos_diferidos (tenant_id, estado)"
    ))

    for nombre, _ in reversed(_INDICES):
        op.execute(sa.text(f"DROP INDEX IF EXISTS {nombre}"))

    op.execute(sa.text(
        "DROP INDEX IF EXISTS uq_pagos_diferidos_stripe_payment_intent"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pagos_diferidos_stripe_payment_intent_id "
        "ON pagos_diferidos (stripe_payment_intent_id)"
    ))
