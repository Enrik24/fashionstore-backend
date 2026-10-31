"""
[EXAMEN-2] ConflictoService — detección y resolución de conflictos (CU09).

Compara el mundo local con el central y aplica RN-006/RN-007. Solo escala a
revisión humana lo que no puede resolverse de forma segura. No importa los
Paquetes 4 ni 5: los conflictos de pago se reciben como datos.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.exceptions import ConflictException, NotFoundException
from app.apps.gestion_catalogo.models import (
    Inventario,
    Producto,
    Sucursal,
    VarianteProducto,
)
from app.apps.gestion_saas.models import EstadoTenant, Tenant
from app.apps.gestion_usuarios.models import Cliente
from app.apps.gestion_ventas.models import Orden, EstadoOrden
from app.apps.operaciones_offline.models import (
    EstadoSincronizacion,
    EstrategiaResolucion,
    LogConflicto,
    OperacionOffline,
    TipoConflicto,
)

PORCENTAJE_CONFLICTO = Decimal(str(settings.SYNC_CONFLICTO_UMBRAL_PRECIO))


def _valor(enum_o_texto) -> str:
    return getattr(enum_o_texto, "value", enum_o_texto)


class ConflictoService:

    # ------------------------------------------------------------------
    # Detección
    # ------------------------------------------------------------------
    @staticmethod
    def evaluar_umbral_precio(diferencia, precio_central: Decimal) -> bool:
        """True si la diferencia porcentual es resoluble automáticamente."""
        precio = Decimal(str(precio_central or 0))
        if precio <= 0:
            return True
        return (abs(Decimal(str(diferencia))) / precio) <= PORCENTAJE_CONFLICTO

    @staticmethod
    async def detectar_stock(
        db: AsyncSession, tenant_id: int, sucursal_id: int, items: Iterable[dict]
    ) -> list[dict]:
        conflictos = []
        for item in items:
            variante_id = item.get("variante_producto_id") or item.get("variante_id")
            cantidad = int(item.get("cantidad") or 0)
            if variante_id is None or cantidad <= 0:
                continue
            result = await db.execute(
                select(Inventario).where(
                    Inventario.tenant_id == tenant_id,
                    Inventario.sucursal_id == sucursal_id,
                    Inventario.variante_producto_id == variante_id,
                )
            )
            inventario = result.scalar_one_or_none()
            disponible = inventario.cantidad_disponible if inventario else 0
            if disponible < cantidad:
                conflictos.append({
                    "tipo_conflicto": TipoConflicto.STOCK_INSUFICIENTE.value,
                    "variante_producto_id": variante_id,
                    "solicitado": cantidad,
                    "disponible": disponible,
                    "faltante": cantidad - disponible,
                })
        return conflictos

    @staticmethod
    def detectar_precio(items: Iterable[dict], precios_centrales: dict[int, Decimal]) -> dict:
        diferencias = []
        total_local = Decimal("0.00")
        total_central = Decimal("0.00")
        hay_conflicto = False
        for item in items:
            variante_id = item.get("variante_producto_id") or item.get("variante_id")
            cantidad = int(item.get("cantidad") or 0)
            central = Decimal(str(precios_centrales.get(variante_id, 0)))
            local = item.get("precio_unitario_local")
            local = Decimal(str(local)) if local is not None else central
            total_local += local * cantidad
            total_central += central * cantidad
            if local != central:
                diferencia = local - central
                resoluble = ConflictoService.evaluar_umbral_precio(diferencia, central)
                if not resoluble:
                    hay_conflicto = True
                diferencias.append({
                    "variante_producto_id": variante_id,
                    "precio_local": float(local),
                    "precio_central": float(central),
                    "diferencia": float(diferencia),
                    "diferencia_porcentaje": float(abs(diferencia) / central) if central else 0.0,
                    "resoluble_automaticamente": resoluble,
                })
        return {
            "diferencias": diferencias,
            "total_local": total_local,
            "total_central": total_central,
            "hay_cambio": bool(diferencias),
            "requiere_revision": hay_conflicto,
        }

    @staticmethod
    async def detectar_datos_maestros(
        db: AsyncSession,
        tenant_id: int,
        variante_ids: Iterable[int],
        cliente_id: Optional[int] = None,
        sucursal_id: Optional[int] = None,
    ) -> list[dict]:
        conflictos = []
        for variante_id in variante_ids:
            result = await db.execute(
                select(VarianteProducto)
                .options(selectinload(VarianteProducto.producto))
                .join(Producto, Producto.id == VarianteProducto.producto_id)
                .where(
                    VarianteProducto.id == variante_id,
                    VarianteProducto.tenant_id == tenant_id,
                    VarianteProducto.deleted_at.is_(None),
                )
            )
            variante = result.scalar_one_or_none()
            if variante is None:
                conflictos.append({
                    "tipo_conflicto": TipoConflicto.VARIANTE_INEXISTENTE.value,
                    "variante_producto_id": variante_id,
                })
            elif _valor(variante.producto.estado) != "ACTIVO":
                conflictos.append({
                    "tipo_conflicto": TipoConflicto.PRODUCTO_INACTIVO.value,
                    "variante_producto_id": variante_id,
                    "producto_id": variante.producto_id,
                })

        if cliente_id is not None:
            result = await db.execute(
                select(Cliente).where(
                    Cliente.id == cliente_id, Cliente.tenant_id == tenant_id
                )
            )
            if result.scalar_one_or_none() is None:
                conflictos.append({
                    "tipo_conflicto": TipoConflicto.PRODUCTO_INACTIVO.value,
                    "detalle": "cliente inexistente en el tenant",
                    "cliente_id": cliente_id,
                })

        if sucursal_id is not None:
            result = await db.execute(
                select(Sucursal).where(
                    Sucursal.id == sucursal_id, Sucursal.tenant_id == tenant_id
                )
            )
            if result.scalar_one_or_none() is None:
                conflictos.append({
                    "tipo_conflicto": TipoConflicto.SUCURSAL_INVALIDA.value,
                    "sucursal_id": sucursal_id,
                })
        return conflictos

    @staticmethod
    def detectar_tenant_suspendido(tenant: Tenant) -> list[dict]:
        if tenant is None or tenant.estado != EstadoTenant.OPERATIVO:
            return [{
                "tipo_conflicto": TipoConflicto.TENANT_SUSPENDIDO.value,
                "estado_tenant": _valor(getattr(tenant, "estado", None)),
                "orden_sugerida": EstadoOrden.PENDIENTE_REGULARIZACION.value,
            }]
        return []

    @staticmethod
    def detectar_orden_expirada(orden: Orden, ahora: Optional[datetime] = None) -> list[dict]:
        ahora = ahora or datetime.utcnow()
        expira = orden.expira_en or orden.fecha_limite_pago
        if expira is None:
            return []
        if getattr(expira, "tzinfo", None) is not None:
            expira = expira.replace(tzinfo=None)
        if expira < ahora:
            return [{
                "tipo_conflicto": TipoConflicto.ORDEN_EXPIRADA.value,
                "orden_id": orden.id,
                "expira_en": expira.isoformat(),
            }]
        return []

    @staticmethod
    def detectar_pago_ya_capturado(pago_estado: Optional[str], monto_capturado=None) -> list[dict]:
        if pago_estado and str(pago_estado).upper() in {"CONFIRMADO", "SUCCEEDED", "CAPTURED"}:
            return [{
                "tipo_conflicto": TipoConflicto.PAGO_YA_CAPTURADO.value,
                "monto": float(monto_capturado) if monto_capturado is not None else None,
            }]
        return []

    @staticmethod
    def detectar_pago_duplicado(pago_existente) -> list[dict]:
        if pago_existente is None:
            return []
        return [{
            "tipo_conflicto": TipoConflicto.PAGO_DUPLICADO.value,
            "pago_id": getattr(pago_existente, "id", None),
            "uuid_pago": str(getattr(pago_existente, "uuid_pago", "")) or None,
            "estado": _valor(getattr(pago_existente, "estado", None)),
        }]

    @classmethod
    async def detectar_conflictos(
        cls,
        db: AsyncSession,
        operacion: OperacionOffline,
        datos_centrales: Optional[dict] = None,
    ) -> list[dict]:
        datos = datos_centrales or {}
        conflictos: list[dict] = []
        if datos.get("tenant") is not None:
            conflictos.extend(cls.detectar_tenant_suspendido(datos["tenant"]))
        if datos.get("orden") is not None:
            conflictos.extend(cls.detectar_orden_expirada(datos["orden"]))
        if datos.get("pago_estado") is not None:
            conflictos.extend(cls.detectar_pago_ya_capturado(datos["pago_estado"], datos.get("monto_capturado")))
        if datos.get("pago_existente") is not None:
            conflictos.extend(cls.detectar_pago_duplicado(datos["pago_existente"]))
        if datos.get("items"):
            conflictos.extend(await cls.detectar_datos_maestros(
                db,
                operacion.tenant_id,
                [i.get("variante_producto_id") or i.get("variante_id") for i in datos["items"]],
                cliente_id=datos.get("cliente_id"),
                sucursal_id=operacion.sucursal_id,
            ))
            if operacion.sucursal_id is not None:
                conflictos.extend(await cls.detectar_stock(
                    db, operacion.tenant_id, operacion.sucursal_id, datos["items"]
                ))
        return conflictos

    # ------------------------------------------------------------------
    # Clasificación y persistencia
    # ------------------------------------------------------------------
    @staticmethod
    def clasificar_conflicto(conflicto: dict) -> dict:
        tipo = conflicto.get("tipo_conflicto")
        automaticos = {
            TipoConflicto.PRECIO_DESACTUALIZADO.value,
            TipoConflicto.PAGO_DUPLICADO.value,
        }
        return {
            **conflicto,
            "severidad": "ALTA" if tipo in {
                TipoConflicto.TENANT_SUSPENDIDO.value,
                TipoConflicto.PAGO_YA_CAPTURADO.value,
            } else "MEDIA",
            "estrategia_resolucion": (
                EstrategiaResolucion.AUTOMATICA.value if tipo in automaticos
                else EstrategiaResolucion.MANUAL.value
            ),
        }

    @staticmethod
    async def registrar_conflicto(
        db: AsyncSession, operacion: OperacionOffline, conflicto: dict
    ) -> LogConflicto:
        clasificado = ConflictoService.clasificar_conflicto(conflicto)
        registro = LogConflicto(
            tenant_id=operacion.tenant_id,
            operacion_offline_id=operacion.id,
            tipo_conflicto=clasificado["tipo_conflicto"],
            detalle_json=clasificado,
            resuelto=False,
            estrategia_resolucion=clasificado["estrategia_resolucion"],
        )
        db.add(registro)
        await db.flush()
        return registro

    @staticmethod
    def aplicar_rn006(faltante: dict) -> dict:
        return {
            "estado": EstadoSincronizacion.RECHAZADA.value,
            "motivo": "Stock insuficiente en el servidor central",
            "datos_resultado": {"faltante": faltante},
            "reintentar": False,
            "codigo_http_sugerido": 409,
        }

    @staticmethod
    def aplicar_rn007(diferencias: list[dict]) -> list[dict]:
        # El precio central prevalece; la diferencia queda para nota de crédito.
        return [dict(d, precio_aplicado=d["precio_central"]) for d in diferencias]

    @staticmethod
    async def resolver_automaticamente(
        db: AsyncSession, conflicto: LogConflicto, observacion: Optional[str] = None
    ) -> LogConflicto:
        conflicto.resuelto = True
        conflicto.resolucion = "AUTOMATICA"
        conflicto.estrategia_resolucion = EstrategiaResolucion.AUTOMATICA.value
        conflicto.fecha_resolucion = datetime.utcnow()
        conflicto.observaciones = observacion
        await db.flush()
        return conflicto

    @staticmethod
    async def escalar_a_manual(
        db: AsyncSession, operacion: OperacionOffline, conflicto: LogConflicto
    ) -> OperacionOffline:
        operacion.estado_sincronizacion = EstadoSincronizacion.CONFLICTO
        operacion.requiere_revision = True
        operacion.motivo_rechazo = conflicto.tipo_conflicto
        operacion.resultado_json = {
            "estado": EstadoSincronizacion.CONFLICTO.value,
            "motivo": conflicto.tipo_conflicto,
            "conflicto_id": conflicto.id,
            "requiere_revision": True,
        }
        conflicto.estrategia_resolucion = EstrategiaResolucion.MANUAL.value
        await db.flush()
        return operacion

    # ------------------------------------------------------------------
    # Consulta y resolución manual
    # ------------------------------------------------------------------
    @staticmethod
    async def listar_conflictos(
        db: AsyncSession,
        tenant_id: int,
        resuelto: Optional[bool] = None,
        tipo: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[LogConflicto]:
        query = select(LogConflicto).options(selectinload(LogConflicto.operacion)).where(LogConflicto.tenant_id == tenant_id)
        if resuelto is not None:
            query = query.where(LogConflicto.resuelto.is_(resuelto))
        if tipo:
            query = query.where(LogConflicto.tipo_conflicto == tipo)
        query = query.order_by(LogConflicto.fecha.desc()).offset(skip).limit(limit)
        return list((await db.execute(query)).scalars().all())

    @staticmethod
    async def obtener_conflicto(
        db: AsyncSession, conflicto_id: int, tenant_id: int
    ) -> LogConflicto:
        result = await db.execute(
            select(LogConflicto)
            .options(selectinload(LogConflicto.operacion))
            .where(
                LogConflicto.id == conflicto_id,
                LogConflicto.tenant_id == tenant_id,
            )
        )
        conflicto = result.scalar_one_or_none()
        if conflicto is None:
            raise NotFoundException("Conflicto no encontrado")
        return conflicto

    @staticmethod
    async def contar_pendientes(db: AsyncSession, tenant_id: int) -> int:
        return (await db.execute(
            select(func.count(LogConflicto.id)).where(
                LogConflicto.tenant_id == tenant_id,
                LogConflicto.resuelto.is_(False),
            )
        )).scalar_one() or 0

    @classmethod
    async def resolver_manualmente(
        cls,
        db: AsyncSession,
        conflicto_id: int,
        tenant_id: int,
        decision: str,
        observacion: Optional[str],
        usuario_id: int,
    ) -> LogConflicto:
        conflicto = await cls.obtener_conflicto(db, conflicto_id, tenant_id)
        if conflicto.resuelto:
            raise ConflictException("El conflicto ya fue resuelto")
        decision = (decision or "").upper()
        if decision not in {"CONFIRMAR", "RECHAZAR", "AJUSTAR"}:
            raise ConflictException("Decisión inválida: use CONFIRMAR, RECHAZAR o AJUSTAR")

        conflicto.resuelto = True
        conflicto.resolucion = decision
        conflicto.estrategia_resolucion = EstrategiaResolucion.MANUAL.value
        conflicto.resuelto_por = usuario_id
        conflicto.fecha_resolucion = datetime.utcnow()
        conflicto.observaciones = observacion
        await db.flush()

        operacion = conflicto.operacion
        if decision == "RECHAZAR":
            operacion.estado_sincronizacion = EstadoSincronizacion.RECHAZADA
            operacion.requiere_revision = False
            operacion.motivo_rechazo = observacion or "Rechazado por revisión manual"
            operacion.resultado_json = {
                "estado": EstadoSincronizacion.RECHAZADA.value,
                "motivo": operacion.motivo_rechazo,
            }
            await db.flush()
        else:
            # CONFIRMAR/AJUSTAR: se reintenta la operación con los datos
            # centrales; si vuelve a fallar, el conflicto se reabre.
            operacion.estado_sincronizacion = EstadoSincronizacion.PENDIENTE
            operacion.requiere_revision = False
            operacion.motivo_rechazo = None
            await db.commit()
            from app.apps.operaciones_offline.sincronizacion_service import (
                SincronizacionService,
            )
            await SincronizacionService.reintentar_operacion(
                db, operacion.uuid_operacion, usuario_id
            )
            await db.refresh(conflicto)
        await db.commit()
        return conflicto

    @classmethod
    async def descartar_conflicto(
        cls, db: AsyncSession, conflicto_id: int, tenant_id: int, usuario_id: int, motivo: Optional[str] = None
    ) -> LogConflicto:
        conflicto = await cls.obtener_conflicto(db, conflicto_id, tenant_id)
        if conflicto.resuelto:
            return conflicto
        conflicto.resuelto = True
        conflicto.resolucion = "DESCARTADO"
        conflicto.estrategia_resolucion = EstrategiaResolucion.MANUAL.value
        conflicto.resuelto_por = usuario_id
        conflicto.fecha_resolucion = datetime.utcnow()
        conflicto.observaciones = motivo
        conflicto.operacion.requiere_revision = False
        await db.commit()
        return conflicto

    @staticmethod
    async def notificar_conflicto(db: AsyncSession, conflicto: LogConflicto) -> None:
        from app.apps.operaciones_offline.websocket import publicar_sync

        operacion = conflicto.operacion
        await publicar_sync(operacion.tenant_id, operacion.sucursal_id, {
            "evento": "conflicto_nuevo",
            "conflicto_id": conflicto.id,
            "tipo_conflicto": conflicto.tipo_conflicto,
            "uuid_operacion": str(operacion.uuid_operacion),
        })

    @staticmethod
    async def notificar_conflicto_pago(db: AsyncSession, conflicto: dict) -> None:
        from app.apps.operaciones_offline.websocket import publicar_sync

        tenant_id = conflicto.get("tenant_id")
        if tenant_id is None:
            return
        await publicar_sync(tenant_id, None, {
            "evento": "conflicto_pago",
            **{k: v for k, v in conflicto.items() if k != "tenant_id"},
        })
