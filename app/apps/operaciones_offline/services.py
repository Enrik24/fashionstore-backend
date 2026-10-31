"""
[EXAMEN-2] Servicios de la Fase 1 — dispositivos y sesión offline (CU17).

El motor de sincronización y la descarga de catálogo (CU05-CU09) se
implementan en la Fase 2. Aquí solo vive lo necesario para que un usuario
pueda operar sin red con PIN, token de propósito offline y dispositivo
autorizado (RN-019 / RNF-13 / RNF-17).
"""
from datetime import date, datetime, time, timedelta
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import (
    DispositivoRevocado,
    NotFoundException,
    PinBloqueado,
    PinIncorrecto,
    PinNoConfigurado,
    SesionOfflineVencida,
    SuscripcionVencida,
    TenantSuspendido,
    ValidationException,
)
from app.security import (
    create_offline_token,
    get_password_hash,
    verify_offline_token,
    verify_password,
)
from app.apps.operaciones_offline.models import (
    ConfiguracionOffline,
    DispositivoSincronizacion,
)
from app.apps.operaciones_offline.schemas import (
    DispositivoRegistroRequest,
    PinConfigurarRequest,
)
from app.apps.gestion_saas.models import (
    EstadoSuscripcion,
    EstadoTenant,
    Suscripcion,
    Tenant,
)
from app.apps.gestion_usuarios.models import (
    EstadoUsuario,
    Permiso,
    Rol,
    RolPermiso,
    Usuario,
    UsuarioRol,
)


# Intentos fallidos de PIN por (usuario, dispositivo). En Fase 1 es memoria
# del proceso; el plan solo exige limitar la fuerza bruta por dispositivo.
_INTENTOS_PIN: dict[tuple[int, str], int] = {}
_BLOQUEOS_PIN: dict[tuple[int, str], datetime] = {}
_BLOQUEO_MINUTOS = 15
_ROLES_OFFLINE = {"Administrador", "Encargado", "Cajero", "Super Administrador"}


# ============================================================
# ConfiguracionOfflineService
# ============================================================

class ConfiguracionOfflineService:

    @staticmethod
    def obtener_valores_por_defecto() -> dict:
        return {
            "descarga_automatica": True,
            "hora_descarga": None,
            "incluir_clientes": True,
            "max_clientes": settings.OFFLINE_MAX_CLIENTES_DEFECTO,
            "habilitar_carrito_offline": True,
            "max_horas_sesion_offline": settings.OFFLINE_SESION_HORAS_DEFECTO,
            "max_dias_orden_pendiente": 1,
        }

    @staticmethod
    async def obtener_configuracion(db: AsyncSession, tenant_id: int) -> ConfiguracionOffline:
        result = await db.execute(
            select(ConfiguracionOffline).where(ConfiguracionOffline.tenant_id == tenant_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            config = ConfiguracionOffline(
                tenant_id=tenant_id,
                **ConfiguracionOfflineService.obtener_valores_por_defecto(),
            )
            db.add(config)
            await db.commit()
            await db.refresh(config)
        return config

    @staticmethod
    async def sembrar_configuracion_por_defecto(db: AsyncSession, tenant_id: int) -> ConfiguracionOffline:
        return await ConfiguracionOfflineService.obtener_configuracion(db, tenant_id)

    @staticmethod
    async def actualizar_configuracion(db: AsyncSession, tenant_id: int, datos: dict) -> ConfiguracionOffline:
        config = await ConfiguracionOfflineService.obtener_configuracion(db, tenant_id)
        if datos.get("hora_descarga") and isinstance(datos["hora_descarga"], str):
            try:
                hora, minuto = datos["hora_descarga"].split(":")[:2]
                datos["hora_descarga"] = time(int(hora), int(minuto))
            except (ValueError, AttributeError):
                raise ValidationException("hora_descarga debe tener formato HH:MM")
        for campo, valor in datos.items():
            if valor is not None:
                setattr(config, campo, valor)
        await db.commit()
        await db.refresh(config)
        return config


# ============================================================
# DispositivoService
# ============================================================

class DispositivoService:

    @staticmethod
    async def registrar_dispositivo(
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int,
        datos: DispositivoRegistroRequest,
    ) -> DispositivoSincronizacion:
        device_uuid = uuid4()
        if datos.id:
            try:
                device_uuid = UUID(str(datos.id))
            except (TypeError, ValueError):
                raise ValidationException("El id del dispositivo debe ser un UUID")

        result = await db.execute(
            select(DispositivoSincronizacion).where(DispositivoSincronizacion.id == device_uuid)
        )
        dispositivo = result.scalar_one_or_none()

        if dispositivo is not None:
            if dispositivo.tenant_id != tenant_id or dispositivo.usuario_id != usuario_id:
                raise NotFoundException("Dispositivo no encontrado")
            if not dispositivo.activo or dispositivo.revocado_en is not None:
                raise DispositivoRevocado("El dispositivo está revocado; use uno nuevo")
            dispositivo.plataforma = datos.plataforma
            dispositivo.nombre = datos.nombre or dispositivo.nombre
            dispositivo.ultima_conexion = datetime.utcnow()
        else:
            dispositivo = DispositivoSincronizacion(
                id=device_uuid,
                tenant_id=tenant_id,
                usuario_id=usuario_id,
                plataforma=datos.plataforma,
                nombre=datos.nombre,
                ultima_conexion=datetime.utcnow(),
                activo=True,
            )
            db.add(dispositivo)

        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo

    @staticmethod
    async def obtener_dispositivo(db: AsyncSession, dispositivo_id, tenant_id: int) -> DispositivoSincronizacion:
        try:
            device_uuid = UUID(str(dispositivo_id))
        except (TypeError, ValueError):
            raise ValidationException("dispositivo_id inválido")
        result = await db.execute(
            select(DispositivoSincronizacion).where(
                DispositivoSincronizacion.id == device_uuid,
                DispositivoSincronizacion.tenant_id == tenant_id,
            )
        )
        dispositivo = result.scalar_one_or_none()
        if dispositivo is None:
            raise NotFoundException("Dispositivo no encontrado")
        return dispositivo

    @staticmethod
    async def listar_dispositivos(
        db: AsyncSession, usuario_id: int, tenant_id: int
    ) -> list[DispositivoSincronizacion]:
        result = await db.execute(
            select(DispositivoSincronizacion)
            .where(
                DispositivoSincronizacion.usuario_id == usuario_id,
                DispositivoSincronizacion.tenant_id == tenant_id,
            )
            .order_by(DispositivoSincronizacion.ultima_conexion.desc().nullslast())
        )
        return list(result.scalars().all())

    @staticmethod
    async def validar_dispositivo_activo(
        db: AsyncSession, dispositivo_id, tenant_id: int
    ) -> DispositivoSincronizacion:
        dispositivo = await DispositivoService.obtener_dispositivo(db, dispositivo_id, tenant_id)
        if not dispositivo.activo or dispositivo.revocado_en is not None:
            raise DispositivoRevocado()
        return dispositivo

    @staticmethod
    async def revocar_dispositivo(
        db: AsyncSession,
        dispositivo_id,
        usuario_id: int,
        tenant_id: int,
        motivo: Optional[str] = None,
    ) -> DispositivoSincronizacion:
        dispositivo = await DispositivoService.obtener_dispositivo(db, dispositivo_id, tenant_id)
        dispositivo.activo = False
        dispositivo.revocado_en = datetime.utcnow()
        dispositivo.revocado_por_id = usuario_id
        dispositivo.motivo_revocacion = motivo or "Revocación manual"
        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo

    @staticmethod
    async def reactivar_dispositivo(
        db: AsyncSession, dispositivo_id, tenant_id: int
    ) -> DispositivoSincronizacion:
        dispositivo = await DispositivoService.obtener_dispositivo(db, dispositivo_id, tenant_id)
        dispositivo.activo = True
        dispositivo.revocado_en = None
        dispositivo.revocado_por_id = None
        dispositivo.motivo_revocacion = None
        await db.commit()
        await db.refresh(dispositivo)
        return dispositivo

    @staticmethod
    async def revocar_por_inactividad(
        db: AsyncSession, dias: int | None = None
    ) -> int:
        """Revoca dispositivos sin conexión durante `dias` (CU17, RN-019).

        Devuelve cuántos dispositivos se revocaron. No toca los que nunca se
        conectaron (`ultima_conexion IS NULL`): no hay forma de saber si están
        en uso y revocarlos rompería un alta recién registrada.
        """
        dias = dias or settings.DISPOSITIVO_REVOCACION_AUTOMATICA_DIAS
        limite = datetime.utcnow() - timedelta(days=dias)
        result = await db.execute(
            select(DispositivoSincronizacion).where(
                DispositivoSincronizacion.activo.is_(True),
                DispositivoSincronizacion.revocado_en.is_(None),
                DispositivoSincronizacion.ultima_conexion.is_not(None),
                DispositivoSincronizacion.ultima_conexion < limite,
            )
        )
        dispositivos = list(result.scalars().all())
        ahora = datetime.utcnow()
        for dispositivo in dispositivos:
            dispositivo.activo = False
            dispositivo.revocado_en = ahora
            dispositivo.motivo_revocacion = (
                f"Revocación automática por inactividad ({dias} días)"
            )
        await db.commit()
        return len(dispositivos)

    @staticmethod
    async def marcar_dispositivo_perdido(
        db: AsyncSession, dispositivo_id, usuario_id: int, tenant_id: int
    ) -> DispositivoSincronizacion:
        """Atajo de `revocar_dispositivo` para equipos perdidos o robados."""
        return await DispositivoService.revocar_dispositivo(
            db,
            dispositivo_id,
            usuario_id,
            tenant_id,
            motivo="Dispositivo perdido o robado",
        )

    @staticmethod
    async def contar_dispositivos_activos(db: AsyncSession, tenant_id: int) -> int:
        from sqlalchemy import func

        return (await db.execute(
            select(func.count(DispositivoSincronizacion.id)).where(
                DispositivoSincronizacion.tenant_id == tenant_id,
                DispositivoSincronizacion.activo.is_(True),
            )
        )).scalar_one() or 0


# ============================================================
# SesionOfflineService
# ============================================================

class SesionOfflineService:

    @staticmethod
    def validar_formato_pin(pin: str) -> None:
        if not pin or not pin.isdigit():
            raise ValidationException("El PIN debe contener solo dígitos")
        if len(pin) < settings.OFFLINE_PIN_LONGITUD_MIN:
            raise ValidationException(
                f"El PIN debe tener al menos {settings.OFFLINE_PIN_LONGITUD_MIN} dígitos"
            )
        if len(set(pin)) == 1:
            raise ValidationException("El PIN no puede ser una secuencia trivial")

    @staticmethod
    async def configurar_pin(db: AsyncSession, tenant_id: int, usuario_id: int, datos: PinConfigurarRequest):
        result = await db.execute(select(Usuario).where(
            Usuario.id == usuario_id, Usuario.tenant_id == tenant_id
        ))
        usuario = result.scalar_one_or_none()
        if usuario is None:
            raise NotFoundException("Usuario no encontrado")
        if not verify_password(datos.contrasena_actual, usuario.contrasena_hash):
            raise ValidationException("La contraseña actual es incorrecta")
        SesionOfflineService.validar_formato_pin(datos.pin)
        usuario.offline_pin_hash = get_password_hash(datos.pin)
        await db.commit()
        return {"configurado": True, "mensaje": "PIN offline configurado"}

    @staticmethod
    async def cambiar_pin(db: AsyncSession, tenant_id: int, usuario_id: int, pin_actual: str, pin_nuevo: str):
        result = await db.execute(select(Usuario).where(
            Usuario.id == usuario_id, Usuario.tenant_id == tenant_id
        ))
        usuario = result.scalar_one_or_none()
        if usuario is None:
            raise NotFoundException("Usuario no encontrado")
        if not usuario.offline_pin_hash:
            raise PinNoConfigurado()
        if not verify_password(pin_actual, usuario.offline_pin_hash):
            raise PinIncorrecto()
        SesionOfflineService.validar_formato_pin(pin_nuevo)
        usuario.offline_pin_hash = get_password_hash(pin_nuevo)
        await db.commit()
        return {"configurado": True, "mensaje": "PIN actualizado"}

    @staticmethod
    async def eliminar_pin(db: AsyncSession, tenant_id: int, usuario_id: int):
        result = await db.execute(select(Usuario).where(
            Usuario.id == usuario_id, Usuario.tenant_id == tenant_id
        ))
        usuario = result.scalar_one_or_none()
        if usuario is None:
            raise NotFoundException("Usuario no encontrado")
        usuario.offline_pin_hash = None
        await db.commit()
        return {"eliminado": True, "modo_offline": False}

    @staticmethod
    def _clave_intentos(usuario_id: int, dispositivo_id) -> tuple[int, str]:
        return (usuario_id, str(dispositivo_id))

    @staticmethod
    async def iniciar_sesion_offline(
        db: AsyncSession,
        tenant_id: int,
        dispositivo_id,
        pin: str,
        usuario_id: Optional[int] = None,
    ) -> dict:
        dispositivo = await DispositivoService.obtener_dispositivo(db, dispositivo_id, tenant_id)
        if not dispositivo.activo or dispositivo.revocado_en is not None:
            raise DispositivoRevocado()
        if usuario_id is not None and dispositivo.usuario_id != usuario_id:
            raise NotFoundException("Dispositivo no encontrado")

        clave = SesionOfflineService._clave_intentos(dispositivo.usuario_id, dispositivo.id)
        bloqueo = _BLOQUEOS_PIN.get(clave)
        if bloqueo and bloqueo > datetime.utcnow():
            raise PinBloqueado()

        result = await db.execute(select(Usuario).where(Usuario.id == dispositivo.usuario_id))
        usuario = result.scalar_one_or_none()
        if usuario is None or usuario.estado != EstadoUsuario.ACTIVO:
            raise SesionOfflineVencida("Usuario inactivo")
        if usuario.tenant_id != tenant_id:
            raise SesionOfflineVencida("El dispositivo pertenece a otro tenant")
        if not usuario.offline_pin_hash:
            raise PinNoConfigurado()

        if not verify_password(pin, usuario.offline_pin_hash):
            SesionOfflineService.registrar_intento_fallido(usuario.id, dispositivo.id)
            # El quinto intento fallido ya deja la sesión bloqueada (RN-019).
            bloqueo = _BLOQUEOS_PIN.get(clave)
            if bloqueo and bloqueo > datetime.utcnow():
                raise PinBloqueado()
            raise PinIncorrecto()

        SesionOfflineService.reiniciar_intentos(usuario.id, dispositivo.id)

        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant is None or tenant.estado != EstadoTenant.OPERATIVO:
            raise TenantSuspendido("El tenant no está operativo")

        result = await db.execute(
            select(Suscripcion).where(
                Suscripcion.tenant_id == tenant_id,
                Suscripcion.estado == EstadoSuscripcion.ACTIVA,
                Suscripcion.fecha_vencimiento >= date.today(),
            )
        )
        if result.scalars().first() is None:
            raise SuscripcionVencida()

        contexto = await SesionOfflineService.cargar_contexto_offline(db, usuario.id, tenant_id)
        roles = contexto["roles"]
        if not set(roles).intersection(_ROLES_OFFLINE):
            raise ValidationException("El rol del usuario no permite operar offline")

        config = await ConfiguracionOfflineService.obtener_configuracion(db, tenant_id)
        ahora = datetime.utcnow()
        vigencia = timedelta(hours=config.max_horas_sesion_offline)
        token = create_offline_token(
            {
                "sub": str(usuario.id),
                "correo": usuario.correo,
                "tenant_id": tenant_id,
                "dispositivo_id": str(dispositivo.id),
                "roles": roles,
                "es_super_admin": False,
                "sesion_iniciada": ahora.isoformat(),
            },
            expires_delta=vigencia,
        )

        dispositivo.ultima_conexion = ahora
        await db.commit()

        return {
            "token_offline": token,
            "token_type": "bearer",
            "expira_en": ahora + vigencia,
            "usuario_id": usuario.id,
            "tenant_id": tenant_id,
            "dispositivo_id": str(dispositivo.id),
            "roles": roles,
            "permisos": contexto["permisos"],
            "configuracion": config,
            "ultima_descarga": None,
        }

    @staticmethod
    async def renovar_sesion_offline(db: AsyncSession, token: str) -> dict:
        payload = await verify_offline_token(token, db)
        tenant_id = int(payload["tenant_id"])
        config = await ConfiguracionOfflineService.obtener_configuracion(db, tenant_id)

        iniciada_raw = payload.get("sesion_iniciada")
        try:
            iniciada = datetime.fromisoformat(iniciada_raw)
        except (TypeError, ValueError):
            raise SesionOfflineVencida("Token offline sin marca de inicio")

        limite = iniciada + timedelta(hours=config.max_horas_sesion_offline)
        ahora = datetime.utcnow()
        if ahora >= limite:
            raise SesionOfflineVencida()

        nuevo = create_offline_token(
            {
                "sub": payload["sub"],
                "correo": payload.get("correo"),
                "tenant_id": tenant_id,
                "dispositivo_id": payload["dispositivo_id"],
                "roles": payload.get("roles", []),
                "es_super_admin": False,
                "sesion_iniciada": iniciada.isoformat(),
            },
            expires_delta=limite - ahora,
        )
        return {
            "token_offline": nuevo,
            "token_type": "bearer",
            "expira_en": limite,
            "usuario_id": int(payload["sub"]),
            "tenant_id": tenant_id,
            "dispositivo_id": payload["dispositivo_id"],
            "roles": payload.get("roles", []),
            "permisos": [],
            "configuracion": config,
            "ultima_descarga": None,
        }

    @staticmethod
    def instruccion_borrado_local(tenant_id: int) -> dict:
        return {
            "tenant_id": tenant_id,
            "eliminar": [
                "token_offline",
                "pin_local",
                "catalogo_descargado",
                "clientes_descargados",
                "carrito_offline",
                "cola_operaciones",
                "comprobantes_temporales",
            ],
            "motivo": "Cierre de sesión offline (RNF-17)",
        }

    @staticmethod
    async def cerrar_sesion_offline(db: AsyncSession, payload: dict) -> dict:
        tenant_id = int(payload["tenant_id"])
        dispositivo_id = payload["dispositivo_id"]
        dispositivo = await DispositivoService.obtener_dispositivo(db, dispositivo_id, tenant_id)
        dispositivo.ultima_conexion = datetime.utcnow()
        await db.commit()
        return {
            "cerrada": True,
            "instruccion_borrado_local": SesionOfflineService.instruccion_borrado_local(tenant_id),
        }

    @staticmethod
    async def cargar_contexto_offline(db: AsyncSession, usuario_id: int, tenant_id: int) -> dict:
        result = await db.execute(
            select(Rol.nombre)
            .join(UsuarioRol, Rol.id == UsuarioRol.rol_id)
            .where(
                UsuarioRol.usuario_id == usuario_id,
                UsuarioRol.tenant_id == tenant_id,
            )
        )
        roles = list(result.scalars().all())

        result = await db.execute(
            select(Permiso.nombre)
            .join(RolPermiso, Permiso.id == RolPermiso.permiso_id)
            .join(Rol, Rol.id == RolPermiso.rol_id)
            .join(UsuarioRol, Rol.id == UsuarioRol.rol_id)
            .where(
                UsuarioRol.usuario_id == usuario_id,
                UsuarioRol.tenant_id == tenant_id,
            )
        )
        permisos = sorted(set(result.scalars().all()))
        return {"roles": roles, "permisos": permisos}

    @staticmethod
    async def obtener_estado_sesion(
        db: AsyncSession,
        usuario_id: int,
        tenant_id: int,
        dispositivo_id=None,
    ) -> dict:
        result = await db.execute(select(Usuario).where(
            Usuario.id == usuario_id, Usuario.tenant_id == tenant_id
        ))
        usuario = result.scalar_one_or_none()
        if usuario is None:
            raise NotFoundException("Usuario no encontrado")

        config = await ConfiguracionOfflineService.obtener_configuracion(db, tenant_id)
        dispositivo = None
        if dispositivo_id:
            dispositivo = await DispositivoService.obtener_dispositivo(db, dispositivo_id, tenant_id)
        clave = (usuario_id, str(dispositivo.id)) if dispositivo else (usuario_id, "")
        intentos = _INTENTOS_PIN.get(clave, 0)

        return {
            "usuario_id": usuario.id,
            "tenant_id": tenant_id,
            "dispositivo_id": str(dispositivo.id) if dispositivo else None,
            "pin_configurado": bool(usuario.offline_pin_hash),
            "dispositivo_activo": bool(dispositivo.activo) if dispositivo else False,
            "intentos_restantes": max(0, settings.OFFLINE_PIN_MAX_INTENTOS - intentos),
            "max_horas_sesion_offline": config.max_horas_sesion_offline,
            "ultima_conexion": dispositivo.ultima_conexion if dispositivo else None,
            "ultima_descarga": None,
        }

    @staticmethod
    def registrar_intento_fallido(usuario_id: int, dispositivo_id) -> None:
        clave = SesionOfflineService._clave_intentos(usuario_id, dispositivo_id)
        intentos = _INTENTOS_PIN.get(clave, 0) + 1
        _INTENTOS_PIN[clave] = intentos
        if intentos >= settings.OFFLINE_PIN_MAX_INTENTOS:
            _BLOQUEOS_PIN[clave] = datetime.utcnow() + timedelta(minutes=_BLOQUEO_MINUTOS)

    @staticmethod
    def reiniciar_intentos(usuario_id: int, dispositivo_id) -> None:
        clave = SesionOfflineService._clave_intentos(usuario_id, dispositivo_id)
        _INTENTOS_PIN.pop(clave, None)
        _BLOQUEOS_PIN.pop(clave, None)
