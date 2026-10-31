"""
[EXAMEN-2] SincronizacionService — motor de lotes offline (CU06/CU07/CU08).

Diseño obligatorio del plan 9.2.5:

1. El lote llega desordenado; se ordena por `secuencia_local` y, si falta,
   por `fecha_registro_local` (RN-010 / RNF-16).
2. Cada operación se registra antes de procesarse y se procesa dentro de un
   *savepoint*, de modo que un fallo de negocio no deja datos parciales.
3. El despacho se hace por el registro de manejadores (plan 4.7): este
   paquete **no** importa los Paquetes 4 ni 5.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import (
    AceptacionRequerida,
    BusinessException,
    ConflictException,
    ManejadorNoRegistrado,
    NotFoundException,
    OperacionRechazada,
    ValidationException,
)
from app.apps.gestion_catalogo.models import Sucursal
from app.apps.operaciones_offline.idempotencia_service import IdempotenciaService
from app.apps.operaciones_offline.models import (
    EstadoSincronizacion,
    EstrategiaResolucion,
    LogConflicto,
    OperacionOffline,
    TipoConflicto,
    TipoOperacionOffline,
)
from app.apps.operaciones_offline.registro_manejadores import obtener_manejador
from app.apps.operaciones_offline.schemas import LoteSyncRequest, OperacionLoteItem

TIPOS_VALIDOS = {t.value for t in TipoOperacionOffline}
ESTADOS_REINTENTABLES = {
    EstadoSincronizacion.PENDIENTE,
    EstadoSincronizacion.RECHAZADA,
    EstadoSincronizacion.CONFLICTO,
    EstadoSincronizacion.ERROR_CRITICO,
}


def _iso(valor: datetime | None) -> str | None:
    return valor.isoformat() if valor else None


class SincronizacionService:

    # ------------------------------------------------------------------
    # Validación y orden del lote
    # ------------------------------------------------------------------
    @staticmethod
    async def validar_lote(
        db: AsyncSession,
        tenant_id: int,
        lote: LoteSyncRequest,
        dispositivo_id: Optional[str] = None,
    ) -> list[OperacionLoteItem]:
        operaciones = list(lote.operaciones or [])
        if len(operaciones) > settings.OFFLINE_LOTE_MAX_OPERACIONES:
            raise ValidationException(
                f"El lote supera el máximo de {settings.OFFLINE_LOTE_MAX_OPERACIONES} operaciones"
            )

        dispositivo_lote = dispositivo_id or lote.dispositivo_id
        for indice, item in enumerate(operaciones):
            # UUID bien formado
            try:
                UUID(str(item.uuid_operacion))
            except (TypeError, ValueError):
                raise ValidationException(
                    f"Operación #{indice + 1}: uuid_operacion inválido"
                )
            tipo = str(item.tipo_operacion or "").upper()
            if tipo not in TIPOS_VALIDOS:
                raise ValidationException(
                    f"Operación #{indice + 1}: tipo_operacion no permitido ({tipo})"
                )
            # RN-008: no se ejecuta un cobro sin conexión
            if tipo == TipoOperacionOffline.PAGO_DIFERIDO.value and item.payload.get("sin_conexion"):
                raise ValidationException("El cobro no puede ejecutarse sin conexión")
            # Antigüedad y fechas futuras
            IdempotenciaService.validar_antiguedad(item.fecha_registro_local)
            # El dispositivo debe ser el autenticado o el del lote
            if item.dispositivo_id and dispositivo_lote and str(item.dispositivo_id) != str(dispositivo_lote):
                raise ValidationException(
                    f"Operación #{indice + 1}: el dispositivo no coincide con la sesión"
                )
            # La sucursal debe pertenecer al tenant
            sucursal_id = item.sucursal_id or lote.sucursal_id
            if sucursal_id is not None:
                result = await db.execute(
                    select(Sucursal.id).where(
                        Sucursal.id == sucursal_id,
                        Sucursal.tenant_id == tenant_id,
                    )
                )
                if result.scalar_one_or_none() is None:
                    raise ValidationException(
                        f"Operación #{indice + 1}: sucursal ajena al tenant"
                    )
        return operaciones

    @staticmethod
    def ordenar_fifo(operaciones: Iterable[OperacionLoteItem]) -> list[OperacionLoteItem]:
        """RN-010 / RNF-16: secuencia local ascendente, luego fecha local."""
        posiciones = list(enumerate(operaciones))

        def clave(par):
            indice, item = par
            secuencia = item.secuencia_local
            if secuencia is None:
                secuencia = 2**62 + indice
            fecha = item.fecha_registro_local or datetime.max
            return (secuencia, fecha, indice)

        return [item for _, item in sorted(posiciones, key=clave)]

    @staticmethod
    def calcular_backoff(intentos: int) -> int:
        """Backoff exponencial con tope de 5 minutos (plan 9.2.9)."""
        base = max(1, settings.OFFLINE_BACKOFF_BASE_SEG)
        return min(base * (2 ** max(0, int(intentos))), 300)

    @staticmethod
    def construir_resultado(
        uuid_operacion,
        estado: str,
        motivo: Optional[str] = None,
        datos_resultado: Optional[dict] = None,
        reintentar: bool = False,
        codigo_http_sugerido: int = 200,
    ) -> dict:
        return {
            "uuid_operacion": str(uuid_operacion),
            "estado": estado,
            "motivo": motivo,
            "datos_resultado": datos_resultado,
            "reintentar": reintentar,
            "codigo_http_sugerido": codigo_http_sugerido,
        }

    @staticmethod
    def _resultado_desde_operacion(operacion: OperacionOffline, estado: str) -> dict:
        datos = operacion.resultado_json or {}
        motivo = operacion.motivo_rechazo or datos.get("motivo")
        return SincronizacionService.construir_resultado(
            operacion.uuid_operacion,
            estado,
            motivo=motivo,
            datos_resultado=datos.get("datos_resultado") or datos,
            reintentar=False,
            codigo_http_sugerido=200 if estado == EstadoSincronizacion.DUPLICADA.value else 409,
        )

    # ------------------------------------------------------------------
    # Procesamiento del lote
    # ------------------------------------------------------------------
    @classmethod
    async def procesar_lote(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int,
        dispositivo_id,
        lote: LoteSyncRequest,
    ) -> dict:
        operaciones = await cls.validar_lote(db, tenant_id, lote, dispositivo_id)
        ordenadas = cls.ordenar_fifo(operaciones)
        resultados: list[dict] = []
        vistos: dict[str, dict] = {}
        contadores: dict[str, int] = {}

        for item in ordenadas:
            uuid_texto = str(item.uuid_operacion)
            if uuid_texto in vistos:
                # UUID repetido dentro del mismo lote: no se vuelve a procesar.
                resultados.append(
                    cls.construir_resultado(
                        uuid_texto,
                        EstadoSincronizacion.DUPLICADA.value,
                        motivo="UUID repetido dentro del mismo lote",
                        datos_resultado=vistos[uuid_texto].get("datos_resultado"),
                    )
                )
                contadores["DUPLICADA"] = contadores.get("DUPLICADA", 0) + 1
                continue

            resultado = await cls._procesar_item(
                db, tenant_id, usuario_id, dispositivo_id, lote, item
            )
            resultados.append(resultado)
            vistos[uuid_texto] = resultado
            contadores[resultado["estado"]] = contadores.get(resultado["estado"], 0) + 1

            if resultado["estado"] in {
                EstadoSincronizacion.CONFIRMADA.value,
                EstadoSincronizacion.CONFLICTO.value,
            }:
                from app.apps.operaciones_offline.websocket import publicar_sync

                await publicar_sync(
                    tenant_id,
                    item.sucursal_id or lote.sucursal_id,
                    {
                        "evento": "operacion_procesada",
                        "uuid_operacion": resultado["uuid_operacion"],
                        "estado": resultado["estado"],
                        "tipo_operacion": item.tipo_operacion,
                    },
                )

        return {
            "lote_id": uuid4().hex,
            "tenant_id": tenant_id,
            "recibidas": len(operaciones),
            "procesadas": len(resultados),
            "resultados": resultados,
            "resumen": contadores,
        }

    @classmethod
    async def _procesar_item(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int,
        dispositivo_id,
        lote: LoteSyncRequest,
        item: OperacionLoteItem,
    ) -> dict:
        existente = await IdempotenciaService.buscar_operacion(db, item.uuid_operacion)

        if existente is not None:
            # RN-005: el UUID es único global.
            if existente.tenant_id != tenant_id:
                return cls.construir_resultado(
                    item.uuid_operacion,
                    EstadoSincronizacion.RECHAZADA.value,
                    motivo="El uuid_operacion pertenece a otro tenant",
                    reintentar=False,
                    codigo_http_sugerido=409,
                )
            if existente.estado_sincronizacion in {
                EstadoSincronizacion.PENDIENTE,
                EstadoSincronizacion.ENVIADA,
            }:
                if existente.estado_sincronizacion == EstadoSincronizacion.PENDIENTE:
                    # Quedó pendiente de un intento anterior: se reintenta.
                    return await cls._ejecutar_operacion(db, existente, item, lote)
                return cls.construir_resultado(
                    item.uuid_operacion,
                    "EN_PROCESO",
                    motivo="La operación ya está siendo procesada",
                    reintentar=True,
                    codigo_http_sugerido=409,
                )
            # CONFIRMADA / RECHAZADA / CONFLICTO / ERROR_CRITICO: respuesta estable.
            estado = (
                EstadoSincronizacion.DUPLICADA.value
                if existente.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA
                else str(existente.estado_sincronizacion.value)
            )
            return cls._resultado_desde_operacion(existente, estado)

        operacion = await IdempotenciaService.registrar_operacion(
            db,
            uuid_operacion=item.uuid_operacion,
            tenant_id=tenant_id,
            sucursal_id=item.sucursal_id or lote.sucursal_id,
            tipo_operacion=str(item.tipo_operacion).upper(),
            payload=item.payload,
            fecha_local=item.fecha_registro_local,
            usuario_id=usuario_id,
            dispositivo_id=dispositivo_id,
            secuencia_local=item.secuencia_local or 0,
        )
        # La operación queda registrada aunque el handler falle (plan 9.2.5).
        await db.commit()
        await db.refresh(operacion)
        return await cls._ejecutar_operacion(db, operacion, item, lote)

    @classmethod
    async def _ejecutar_operacion(
        cls,
        db: AsyncSession,
        operacion: OperacionOffline,
        item: OperacionLoteItem,
        lote: LoteSyncRequest,
    ) -> dict:
        tipo = str(operacion.tipo_operacion.value if hasattr(operacion.tipo_operacion, "value") else operacion.tipo_operacion)
        operacion_id = operacion.id
        uuid_operacion = str(operacion.uuid_operacion)
        intentos_previos = operacion.intentos or 0
        contexto = {
            "tenant_id": operacion.tenant_id,
            "usuario_id": operacion.usuario_id,
            "dispositivo_id": str(operacion.dispositivo_id),
            "lote_id": lote.dispositivo_id,
        }

        try:
            async with db.begin_nested():
                operacion.estado_sincronizacion = EstadoSincronizacion.ENVIADA
                operacion.intentos = (operacion.intentos or 0) + 1
                operacion.fecha_ultimo_intento = datetime.utcnow()
                await db.flush()

                manejador = obtener_manejador(tipo)
                datos = await manejador(db, operacion, contexto)
                if datos is None:
                    datos = {}

                operacion.estado_sincronizacion = EstadoSincronizacion.CONFIRMADA
                operacion.resultado_json = datos
                operacion.requiere_revision = False
                operacion.motivo_rechazo = None
                operacion.error_detalle = None
                await db.flush()
            await db.commit()
            return cls.construir_resultado(
                uuid_operacion,
                EstadoSincronizacion.CONFIRMADA.value,
                datos_resultado=datos,
                reintentar=False,
                codigo_http_sugerido=200,
            )
        except ManejadorNoRegistrado as exc:
            await db.rollback()
            operacion = await db.get(OperacionOffline, operacion_id)
            cls._marcar_intento(operacion, intentos_previos)
            operacion.estado_sincronizacion = EstadoSincronizacion.ERROR_CRITICO
            operacion.error_detalle = str(exc)
            operacion.resultado_json = {
                "estado": EstadoSincronizacion.ERROR_CRITICO.value,
                "motivo": "MANEJADOR_NO_REGISTRADO",
                "detalle": str(exc),
                "reintentar": False,
            }
            await db.commit()
            return cls.construir_resultado(
                uuid_operacion,
                EstadoSincronizacion.ERROR_CRITICO.value,
                motivo="MANEJADOR_NO_REGISTRADO",
                datos_resultado={"detalle": str(exc)},
                reintentar=False,
                codigo_http_sugerido=409,
            )
        except AceptacionRequerida as exc:
            await db.rollback()
            operacion = await db.get(OperacionOffline, operacion_id)
            cls._marcar_intento(operacion, intentos_previos)
            await cls._registrar_conflicto_post_rollback(
                db,
                operacion,
                tipo_conflicto=TipoConflicto.PRECIO_DESACTUALIZADO.value,
                detalle={
                    "total_anterior": float(exc.total_anterior),
                    "total_nuevo": float(exc.total_nuevo),
                    "diferencias": exc.diferencias,
                    "requiere_aceptacion": True,
                },
                manual=True,
            )
            operacion.estado_sincronizacion = EstadoSincronizacion.RECHAZADA
            operacion.requiere_revision = False
            operacion.motivo_rechazo = str(exc)
            operacion.resultado_json = {
                "estado": EstadoSincronizacion.RECHAZADA.value,
                "motivo": str(exc),
                "requiere_aceptacion": True,
                "total_anterior": float(exc.total_anterior),
                "total_nuevo": float(exc.total_nuevo),
                "diferencias": exc.diferencias,
                "reintentar": True,
            }
            await db.commit()
            return cls.construir_resultado(
                uuid_operacion,
                EstadoSincronizacion.RECHAZADA.value,
                motivo=str(exc),
                datos_resultado=operacion.resultado_json,
                reintentar=True,
                codigo_http_sugerido=409,
            )
        except OperacionRechazada as exc:
            await db.rollback()
            operacion = await db.get(OperacionOffline, operacion_id)
            cls._marcar_intento(operacion, intentos_previos)
            manual = str(exc.estrategia or "").upper() != "RECHAZO"
            if exc.tipo_conflicto:
                await cls._registrar_conflicto_post_rollback(
                    db,
                    operacion,
                    tipo_conflicto=exc.tipo_conflicto,
                    detalle=exc.datos or {"motivo": str(exc)},
                    manual=manual,
                )
            operacion.estado_sincronizacion = (
                EstadoSincronizacion.CONFLICTO if manual else EstadoSincronizacion.RECHAZADA
            )
            operacion.requiere_revision = manual
            operacion.motivo_rechazo = str(exc)
            operacion.resultado_json = {
                "estado": operacion.estado_sincronizacion.value,
                "motivo": str(exc),
                "datos_resultado": exc.datos or {},
                "reintentar": manual,
            }
            await db.commit()
            return cls.construir_resultado(
                uuid_operacion,
                operacion.estado_sincronizacion.value,
                motivo=str(exc),
                datos_resultado=exc.datos or {},
                reintentar=manual,
                codigo_http_sugerido=409,
            )
        except BusinessException as exc:
            await db.rollback()
            operacion = await db.get(OperacionOffline, operacion_id)
            cls._marcar_intento(operacion, intentos_previos)
            operacion.estado_sincronizacion = EstadoSincronizacion.RECHAZADA
            operacion.motivo_rechazo = str(exc)
            operacion.resultado_json = {
                "estado": EstadoSincronizacion.RECHAZADA.value,
                "motivo": str(exc),
                "reintentar": False,
            }
            await db.commit()
            return cls.construir_resultado(
                uuid_operacion,
                EstadoSincronizacion.RECHAZADA.value,
                motivo=str(exc),
                reintentar=False,
                codigo_http_sugerido=400,
            )
        except SQLAlchemyError as exc:  # error transitorio de base de datos
            await db.rollback()
            return cls.construir_resultado(
                uuid_operacion,
                EstadoSincronizacion.PENDIENTE.value,
                motivo="Error transitorio; reintente con backoff",
                datos_resultado={"detalle": str(exc)},
                reintentar=True,
                codigo_http_sugerido=503,
            )

    @staticmethod
    def _marcar_intento(operacion: OperacionOffline, intentos_previos: int) -> None:
        """El savepoint se revierte; el conteo de intentos se repone a mano."""
        operacion.intentos = intentos_previos + 1
        operacion.fecha_ultimo_intento = datetime.utcnow()

    @staticmethod
    async def _registrar_conflicto_post_rollback(
        db: AsyncSession,
        operacion: OperacionOffline,
        tipo_conflicto: str,
        detalle: Optional[dict] = None,
        manual: bool = True,
    ) -> LogConflicto:
        from app.apps.operaciones_offline.conflicto_service import ConflictoService

        conflicto = await ConflictoService.registrar_conflicto(
            db,
            operacion,
            {
                "tipo_conflicto": tipo_conflicto,
                "detalle": detalle or {},
            },
        )
        if not manual:
            conflicto.resuelto = True
            conflicto.resolucion = "RECHAZO"
            conflicto.estrategia_resolucion = EstrategiaResolucion.RECHAZO.value
            conflicto.fecha_resolucion = datetime.utcnow()
        return conflicto

    # ------------------------------------------------------------------
    # Reintento manual
    # ------------------------------------------------------------------
    @classmethod
    async def reintentar_operacion(
        cls, db: AsyncSession, uuid_operacion, usuario_id: int
    ) -> dict:
        operacion = await cls.obtener_operacion_global(db, uuid_operacion)
        if operacion.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA:
            raise ConflictException("La operación ya está confirmada y no se reintenta")
        if operacion.estado_sincronizacion not in ESTADOS_REINTENTABLES:
            raise ConflictException(
                f"La operación está en estado {operacion.estado_sincronizacion.value} y no es reintentable"
            )

        item = OperacionLoteItem(
            uuid_operacion=str(operacion.uuid_operacion),
            dispositivo_id=str(operacion.dispositivo_id),
            secuencia_local=operacion.secuencia_local,
            tipo_operacion=str(operacion.tipo_operacion.value),
            sucursal_id=operacion.sucursal_id,
            fecha_registro_local=operacion.fecha_registro_local,
            payload=operacion.payload_json or {},
        )
        operacion.estado_sincronizacion = EstadoSincronizacion.PENDIENTE
        operacion.requiere_revision = False
        await db.commit()
        await db.refresh(operacion)
        return await cls._ejecutar_operacion(
            db,
            operacion,
            item,
            LoteSyncRequest(dispositivo_id=str(operacion.dispositivo_id)),
        )

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    @staticmethod
    async def obtener_operacion_global(
        db: AsyncSession, uuid_operacion
    ) -> OperacionOffline:
        operacion = await IdempotenciaService.buscar_operacion(db, uuid_operacion)
        if operacion is None:
            raise NotFoundException("Operación offline no encontrada")
        return operacion

    @staticmethod
    async def obtener_operacion(
        db: AsyncSession, uuid_operacion, tenant_id: int
    ) -> OperacionOffline:
        operacion = await SincronizacionService.obtener_operacion_global(db, uuid_operacion)
        if operacion.tenant_id != tenant_id:
            raise NotFoundException("Operación offline no encontrada")
        return operacion

    @staticmethod
    async def listar_operaciones(
        db: AsyncSession,
        tenant_id: int,
        estado: Optional[str] = None,
        tipo: Optional[str] = None,
        desde: Optional[datetime] = None,
        hasta: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[OperacionOffline]:
        query = select(OperacionOffline).where(OperacionOffline.tenant_id == tenant_id)
        if estado:
            query = query.where(OperacionOffline.estado_sincronizacion == estado.upper())
        if tipo:
            query = query.where(OperacionOffline.tipo_operacion == tipo.upper())
        if desde:
            query = query.where(OperacionOffline.fecha_registro_local >= desde)
        if hasta:
            query = query.where(OperacionOffline.fecha_registro_local <= hasta)
        query = (
            query.order_by(
                OperacionOffline.fecha_registro_local.desc(),
                OperacionOffline.id.desc(),
            )
            .offset(max(0, skip))
            .limit(max(1, min(limit, 200)))
        )
        return list((await db.execute(query)).scalars().all())

    @staticmethod
    async def resumen(
        db: AsyncSession,
        tenant_id: int,
        desde: Optional[datetime] = None,
        hasta: Optional[datetime] = None,
    ) -> dict:
        query = (
            select(
                OperacionOffline.estado_sincronizacion,
                func.count(OperacionOffline.id),
            )
            .where(OperacionOffline.tenant_id == tenant_id)
        )
        if desde:
            query = query.where(OperacionOffline.fecha_registro_local >= desde)
        if hasta:
            query = query.where(OperacionOffline.fecha_registro_local <= hasta)
        query = query.group_by(OperacionOffline.estado_sincronizacion)
        filas = await db.execute(query)

        conteo = {
            (estado.value if hasattr(estado, "value") else str(estado)): total
            for estado, total in filas.all()
        }
        return {
            "tenant_id": tenant_id,
            "desde": desde,
            "hasta": hasta,
            "confirmadas": conteo.get("CONFIRMADA", 0),
            "duplicadas": conteo.get("DUPLICADA", 0),
            "rechazadas": conteo.get("RECHAZADA", 0),
            "conflictos": conteo.get("CONFLICTO", 0),
            "errores_criticos": conteo.get("ERROR_CRITICO", 0),
            "pendientes": conteo.get("PENDIENTE", 0) + conteo.get("ENVIADA", 0),
            "total": sum(conteo.values()),
        }

    # ------------------------------------------------------------------
    # Job de expiración (CU16 / RN-016)
    # ------------------------------------------------------------------
    @staticmethod
    async def expirar_operaciones_pendientes(
        db: AsyncSession, horas: Optional[int] = None
    ) -> int:
        """Marca PENDIENTE/ENVIADA antiguas como CONFLICTO para revisión."""
        limite = datetime.utcnow() - timedelta(
            hours=horas or (settings.PAGO_DIFERIDO_VIGENCIA_HORAS * 2)
        )
        result = await db.execute(
            select(OperacionOffline)
            .where(
                OperacionOffline.estado_sincronizacion.in_(
                    [EstadoSincronizacion.PENDIENTE, EstadoSincronizacion.ENVIADA]
                ),
                OperacionOffline.fecha_recepcion_servidor < limite,
                OperacionOffline.requiere_revision.is_(False),
            )
            .execution_options(ignorar_tenant=True)
        )
        operaciones = list(result.scalars().all())
        for operacion in operaciones:
            operacion.estado_sincronizacion = EstadoSincronizacion.CONFLICTO
            operacion.requiere_revision = True
            operacion.motivo_rechazo = "Operación pendiente demasiado tiempo"
        if operaciones:
            await db.commit()
        return len(operaciones)
