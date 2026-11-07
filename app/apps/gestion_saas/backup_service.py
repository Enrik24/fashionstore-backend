"""[EXAMEN-2] Fase B — respaldos y restauraciones por tenant (RF-035 / RNF-19).

Modos de respaldo (`settings.BACKUP_MODO`):

* `logico` (demo, por defecto): exporta las tablas del tenant a JSON y lo
  comprime en `.json.gz` usando la misma sesión async. No depende de binarios
  externos ni de drivers adicionales.
* `sql`: exporta las tablas del tenant como `INSERT` en un `.sql` legible
  (solo filas con `tenant_id`, sin tabla `usuarios`). No depende de binarios
  externos ni de drivers adicionales.
* `pg_dump`: ejecuta `pg_dump --format=custom` en un hilo aparte. Es el modo de
  producción y requiere que el binario exista en el PATH.

La restauración está protegida doblemente: por plan (`permite_restauracion`) y
por la variable `BACKUP_PERMITE_RESTAURACION`, que por defecto es `false` para
no destruir la base durante una demostración.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.exceptions import ConflictException, NotFoundException, ValidationException
from app.apps.gestion_saas.models import (
    Backup,
    EstadoBackup,
    EstadoRestauracion,
    EstadoSuscripcion,
    Restauracion,
    Suscripcion,
    TipoBackup,
)

logger = logging.getLogger(__name__)


def _directorio() -> Path:
    ruta = Path(settings.BACKUP_DIR)
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _tablas_exportables() -> list[str]:
    """Tablas del modelo con `tenant_id`, sin datos sensibles de usuarios."""
    from app.database import Base

    tablas: list[str] = []
    for tabla in Base.metadata.sorted_tables:
        if tabla.name == "usuarios":
            continue
        if "tenant_id" in tabla.columns:
            tablas.append(tabla.name)
    return tablas


def _serializar(valor):
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    if isinstance(valor, (bytes, bytearray)):
        return valor.hex()
    if isinstance(valor, (int, float, bool)) or valor is None or isinstance(valor, str):
        return valor
    return str(valor)


def _ident_sql(nombre: str) -> str:
    return f'"{nombre.replace(chr(34), chr(34) + chr(34))}"'


def _literal_sql(valor) -> str:
    """Literal en SQL para un valor crudo de la DB (PostgreSQL)."""
    from decimal import Decimal

    if valor is None:
        return "NULL"
    if isinstance(valor, bool):  # antes que int: bool es subclase de int
        return "TRUE" if valor else "FALSE"
    if isinstance(valor, (int, float, Decimal)):
        return str(valor)
    if isinstance(valor, (bytes, bytearray)):
        return f"'\\x{valor.hex()}'::bytea"
    if hasattr(valor, "isoformat"):  # datetime/date/time
        return f"'{valor.isoformat().replace(chr(39), chr(39) + chr(39))}'"
    return f"'{str(valor).replace(chr(39), chr(39) + chr(39))}'"


class BackupService:
    """Creación, listado, retención y restauración de respaldos."""

    # ------------------------------------------------------------------
    # Consultas y validaciones
    # ------------------------------------------------------------------

    @staticmethod
    async def _plan(db: AsyncSession, tenant_id: int):
        result = await db.execute(
            select(Suscripcion)
            .options(selectinload(Suscripcion.plan))
            .where(
                Suscripcion.tenant_id == tenant_id,
                Suscripcion.estado == EstadoSuscripcion.ACTIVA,
            )
            .order_by(Suscripcion.fecha_vencimiento.desc())
        )
        suscripcion = result.scalars().first()
        return suscripcion.plan if suscripcion else None

    @staticmethod
    async def listar(
        db: AsyncSession,
        tenant_id: int,
        estado: str | None = None,
        tipo: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Backup]:
        query = select(Backup).where(Backup.tenant_id == tenant_id)
        if estado:
            query = query.where(Backup.estado == EstadoBackup(estado.upper()))
        if tipo:
            query = query.where(Backup.tipo == TipoBackup(tipo.upper()))
        if desde:
            query = query.where(Backup.fecha_inicio >= desde)
        if hasta:
            query = query.where(Backup.fecha_inicio <= hasta)
        query = (
            query.order_by(Backup.fecha_inicio.desc(), Backup.id.desc())
            .offset(max(0, skip))
            .limit(max(1, min(limit, 200)))
        )
        return list((await db.execute(query)).scalars().all())

    @staticmethod
    async def obtener(db: AsyncSession, tenant_id: int, backup_id: int) -> Backup:
        backup = (await db.execute(
            select(Backup).where(
                Backup.id == backup_id,
                Backup.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if backup is None:
            raise NotFoundException("Respaldo no encontrado")
        return backup

    @staticmethod
    async def listar_restauraciones(
        db: AsyncSession, tenant_id: int, skip: int = 0, limit: int = 50
    ) -> list[Restauracion]:
        return list((await db.execute(
            select(Restauracion)
            .where(Restauracion.tenant_id == tenant_id)
            .order_by(Restauracion.fecha_solicitud.desc())
            .offset(max(0, skip))
            .limit(max(1, min(limit, 200)))
        )).scalars().all())

    @staticmethod
    async def uso(db: AsyncSession, tenant_id: int) -> dict:
        plan = await BackupService._plan(db, tenant_id)
        inicio_mes = _ahora().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        consumidos = (await db.execute(
            select(func.count(Backup.id)).where(
                Backup.tenant_id == tenant_id,
                Backup.tipo == TipoBackup.MANUAL,
                Backup.fecha_inicio >= inicio_mes,
            )
        )).scalar_one() or 0
        maximo = plan.backup_manual_mensual if plan else 0
        restantes = None if maximo is None else max(0, maximo - int(consumidos))
        return {
            "tenant_id": tenant_id,
            "backups_mes_actual": int(consumidos),
            "max_backups_manuales": maximo,
            "restantes_mes_actual": restantes,
        }

    @staticmethod
    async def _validar_manual(db: AsyncSession, tenant_id: int):
        plan = await BackupService._plan(db, tenant_id)
        if plan is None:
            raise ConflictException("El tenant no tiene una suscripción activa")
        maximo = plan.backup_manual_mensual
        if maximo is not None:
            uso = await BackupService.uso(db, tenant_id)
            if uso["backups_mes_actual"] >= maximo:
                raise ConflictException(
                    f"El plan {plan.nombre} permite {maximo} respaldos manuales por mes"
                )
        # Una automatización por tenant a la vez (concurrencia).
        en_proceso = (await db.execute(
            select(func.count(Backup.id)).where(
                Backup.tenant_id == tenant_id,
                Backup.estado == EstadoBackup.EN_PROCESO,
            )
        )).scalar_one() or 0
        if en_proceso:
            raise ConflictException("Ya hay un respaldo en proceso para este tenant")
        return plan

    # ------------------------------------------------------------------
    # Creación
    # ------------------------------------------------------------------

    @staticmethod
    async def crear_manual(db: AsyncSession, tenant_id: int, usuario_id: int | None) -> Backup:
        plan = await BackupService._validar_manual(db, tenant_id)
        backup = Backup(
            tenant_id=tenant_id,
            tipo=TipoBackup.MANUAL,
            estado=EstadoBackup.EN_PROCESO,
            creado_por=usuario_id,
            retencion_hasta=_ahora() + timedelta(days=int(plan.backup_retencion_dias or 7)),
        )
        db.add(backup)
        await db.commit()
        await db.refresh(backup)
        return await BackupService.ejecutar(db, backup)

    @staticmethod
    async def crear_automatico(db: AsyncSession, tenant_id: int) -> Backup | None:
        plan = await BackupService._plan(db, tenant_id)
        if plan is None or not plan.backup_automatico:
            return None
        backup = Backup(
            tenant_id=tenant_id,
            tipo=TipoBackup.AUTOMATICO,
            estado=EstadoBackup.EN_PROCESO,
            retencion_hasta=_ahora() + timedelta(days=int(plan.backup_retencion_dias or 7)),
        )
        db.add(backup)
        await db.commit()
        await db.refresh(backup)
        return await BackupService.ejecutar(db, backup)

    @staticmethod
    async def ejecutar(db: AsyncSession, backup: Backup) -> Backup:
        ruta: Path | None = None
        try:
            if (settings.BACKUP_MODO or "logico").lower() == "pg_dump":
                ruta = await asyncio.to_thread(
                    BackupService._generar_pg_dump,
                    _directorio(),
                    f"tenant_{backup.tenant_id}_{_ahora():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}",
                )
            elif (settings.BACKUP_MODO or "logico").lower() == "sql":
                contenido_sql = await BackupService._exportar_sql(db, backup.tenant_id)
                destino = _directorio() / (
                    f"tenant_{backup.tenant_id}_{_ahora():%Y%m%d%H%M%S}_"
                    f"{uuid.uuid4().hex[:8]}.sql"
                )
                await asyncio.to_thread(BackupService._escribir_sql, destino, contenido_sql)
                ruta = destino
            else:
                datos = await BackupService._exportar_json(db, backup.tenant_id)
                destino = _directorio() / (
                    f"tenant_{backup.tenant_id}_{_ahora():%Y%m%d%H%M%S}_"
                    f"{uuid.uuid4().hex[:8]}.json.gz"
                )
                await asyncio.to_thread(BackupService._escribir_json, destino, datos)
                ruta = destino
            contenido = await asyncio.to_thread(ruta.read_bytes)
            backup.ruta = str(ruta)
            backup.tamano_bytes = len(contenido)
            backup.hash_sha256 = hashlib.sha256(contenido).hexdigest()
            backup.estado = EstadoBackup.COMPLETADO
            backup.fecha_fin = _ahora()
            backup.error_detalle = None
        except Exception as exc:  # pragma: no cover - depende del entorno
            logger.exception("Fallo el respaldo %s del tenant %s", backup.id, backup.tenant_id)
            backup.estado = EstadoBackup.FALLIDO
            backup.fecha_fin = _ahora()
            backup.error_detalle = str(exc)[:2000]
            if ruta is not None and ruta.exists():
                try:
                    ruta.unlink()
                except OSError:
                    pass
        await db.commit()
        await db.refresh(backup)
        return backup

    @staticmethod
    async def _exportar_json(db: AsyncSession, tenant_id: int) -> dict:
        datos: dict[str, list[dict]] = {}
        for tabla in _tablas_exportables():
            filas = (await db.execute(
                text(f"SELECT * FROM {tabla} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )).mappings().all()
            datos[tabla] = [
                {clave: _serializar(valor) for clave, valor in fila.items()}
                for fila in filas
            ]
        return {
            "tenant_id": tenant_id,
            "generado_en": _ahora().isoformat(),
            "tablas": datos,
        }

    @staticmethod
    def _escribir_json(destino: Path, datos: dict) -> None:
        with gzip.open(destino, "wt", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False)

    @staticmethod
    async def _exportar_sql(db: AsyncSession, tenant_id: int) -> str:
        """Genera INSERTs en SQL por tabla (filas del tenant, sin `usuarios`)."""
        secciones = [
            f"-- Backup tenant {tenant_id} generado en {_ahora().isoformat()}",
            "BEGIN;",
        ]
        for tabla in _tablas_exportables():
            filas = (await db.execute(
                text(f"SELECT * FROM {tabla} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )).mappings().all()
            if not filas:
                continue
            secciones.append(f"\n-- Tabla: {tabla} ({len(filas)} filas)")
            for fila in filas:
                columnas = list(fila.keys())
                columnas_sql = ", ".join(_ident_sql(c) for c in columnas)
                valores_sql = ", ".join(_literal_sql(fila[c]) for c in columnas)
                secciones.append(
                    f"INSERT INTO {_ident_sql(tabla)} ({columnas_sql}) VALUES ({valores_sql});"
                )
        secciones.append("COMMIT;")
        return "\n".join(secciones) + "\n"

    @staticmethod
    def _escribir_sql(destino: Path, contenido: str) -> None:
        destino.write_text(contenido, encoding="utf-8")

    @staticmethod
    def _generar_pg_dump(directorio: Path, nombre: str) -> Path:
        destino = directorio / f"{nombre}.dump"
        comando = [
            "pg_dump",
            "--no-owner",
            "--format=custom",
            f"--file={destino}",
            settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"),
        ]
        subprocess.run(
            comando,
            check=True,
            timeout=settings.BACKUP_TIMEOUT_SEG,
            capture_output=True,
        )
        return destino

    # ------------------------------------------------------------------
    # Restauración
    # ------------------------------------------------------------------

    @staticmethod
    async def restaurar(
        db: AsyncSession,
        tenant_id: int,
        backup_id: int,
        usuario_id: int | None,
        motivo: str | None = None,
        es_super_admin: bool = False,
    ) -> Restauracion:
        backup = await BackupService.obtener(db, tenant_id, backup_id)
        plan = await BackupService._plan(db, tenant_id)
        if not es_super_admin and not (plan and plan.permite_restauracion):
            raise ConflictException("El plan actual no permite restaurar respaldos")
        if backup.estado != EstadoBackup.COMPLETADO:
            raise ConflictException("Solo se puede restaurar un respaldo completado")

        restauracion = Restauracion(
            tenant_id=tenant_id,
            backup_id=backup.id,
            solicitado_por=usuario_id,
            estado=EstadoRestauracion.SOLICITADA,
            motivo=motivo,
        )
        db.add(restauracion)
        await db.commit()
        await db.refresh(restauracion)

        if not settings.BACKUP_PERMITE_RESTAURACION:
            # La solicitud queda registrada, pero no se destruye la base.
            raise ConflictException(
                "La restauración está deshabilitada en este entorno "
                "(BACKUP_PERMITE_RESTAURACION=false)"
            )

        restauracion.estado = EstadoRestauracion.EN_PROCESO
        restauracion.fecha_inicio = _ahora()
        await db.flush()
        try:
            await BackupService._ejecutar_restauracion(db, backup, restauracion)
            restauracion.estado = EstadoRestauracion.COMPLETADA
        except Exception as exc:
            logger.exception("Fallo la restauración %s", restauracion.id)
            restauracion.estado = EstadoRestauracion.FALLIDA
            restauracion.error_detalle = str(exc)[:2000]
        restauracion.fecha_fin = _ahora()
        await db.commit()
        await db.refresh(restauracion)
        return restauracion

    @staticmethod
    async def _ejecutar_restauracion(
        db: AsyncSession, backup: Backup, restauracion: Restauracion
    ) -> None:
        """Punto de extensión; en producción se usa `pg_restore` sobre el dump.

        En el entorno de examen la base no debe destruirse, por eso el flujo
        real solo se ejercita con un mock en las pruebas.
        """
        if not backup.ruta or not os.path.exists(backup.ruta):
            raise ValidationException("El archivo del respaldo no existe")
        raise ValidationException(
            "La restauración automática no está implementada para el modo actual; "
            "use pg_restore sobre el archivo del respaldo"
        )

    # ------------------------------------------------------------------
    # Retención y programación
    # ------------------------------------------------------------------

    @staticmethod
    async def aplicar_retencion(db: AsyncSession) -> int:
        vencidos = (await db.execute(
            select(Backup).where(
                Backup.estado == EstadoBackup.COMPLETADO,
                Backup.retencion_hasta.is_not(None),
                Backup.retencion_hasta < _ahora(),
            )
        )).scalars().all()
        for backup in vencidos:
            if backup.ruta:
                try:
                    Path(backup.ruta).unlink(missing_ok=True)
                except OSError:  # pragma: no cover - permisos
                    logger.warning("No se pudo borrar el archivo %s", backup.ruta)
            backup.estado = EstadoBackup.EXPIRADO
        if vencidos:
            await db.commit()
        return len(vencidos)

    @staticmethod
    async def respaldos_pendientes(db: AsyncSession) -> list[int]:
        """Tenants cuyo último respaldo automático superó la frecuencia del plan."""
        ahora = _ahora()
        suscripciones = (await db.execute(
            select(Suscripcion)
            .options(selectinload(Suscripcion.plan))
            .where(Suscripcion.estado == EstadoSuscripcion.ACTIVA)
        )).scalars().all()
        pendientes: list[int] = []
        for suscripcion in suscripciones:
            plan = suscripcion.plan
            if plan is None or not plan.backup_automatico:
                continue
            frecuencia = plan.backup_frecuencia_horas or 24
            ultimo = (await db.execute(
                select(Backup.fecha_inicio)
                .where(
                    Backup.tenant_id == suscripcion.tenant_id,
                    Backup.tipo == TipoBackup.AUTOMATICO,
                    Backup.estado == EstadoBackup.COMPLETADO,
                )
                .order_by(Backup.fecha_inicio.desc())
                .limit(1)
            )).scalar_one_or_none()
            if ultimo is None:
                pendientes.append(suscripcion.tenant_id)
                continue
            fecha = ultimo if ultimo.tzinfo else ultimo.replace(tzinfo=timezone.utc)
            if fecha + timedelta(hours=frecuencia) <= ahora:
                pendientes.append(suscripcion.tenant_id)
        return pendientes
