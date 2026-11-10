"""
[EXAMEN-2] PaqueteOfflineService — descarga de datos para operar sin red (CU05).

Construye un snapshot coherente, versionado y limitado al tenant. El cliente
nunca recibe datos de otra empresa ni la clave secreta de Stripe (RNF-15).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterable, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.exceptions import NotFoundException, ValidationException
from app.apps.gestion_catalogo.models import (
    Categoria,
    Coleccion,
    Color,
    Inventario,
    Producto,
    Sucursal,
    Talla,
    Temporada,
    VarianteProducto,
)
from app.apps.gestion_marketing.models import Promocion
from app.apps.gestion_usuarios.models import Bitacora, Cliente
from app.apps.gestion_ventas.models import Cupon
from app.apps.operaciones_offline.models import PuntoSincronizacion
from app.apps.operaciones_offline.services import (
    ConfiguracionOfflineService,
    SesionOfflineService,
)
from app.apps.operaciones_offline.schemas import DescargaRegistrarRequest


def _jsonable(valor: Any) -> Any:
    """Normaliza Decimal, datetime y UUID para la respuesta JSON."""
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (datetime, date, time)):
        return valor.isoformat()
    if isinstance(valor, UUID):
        return str(valor)
    if isinstance(valor, dict):
        return {clave: _jsonable(v) for clave, v in valor.items()}
    if isinstance(valor, (list, tuple, set)):
        return [_jsonable(v) for v in valor]
    return valor


def _normalizar_imagenes(valor: Any) -> list[str]:
    """La columna JSON a veces guarda un string serializado '["url"]'."""
    if not valor:
        return []
    if isinstance(valor, list):
        return [str(x) for x in valor if x]
    if isinstance(valor, str):
        s = valor.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
            return [s]
        except (ValueError, TypeError):
            return [s]
    return []


class PaqueteOfflineService:

    # ------------------------------------------------------------------
    # Validaciones y metadatos
    # ------------------------------------------------------------------
    @staticmethod
    async def validar_sucursal_del_tenant(
        db: AsyncSession, tenant_id: int, sucursal_id: int
    ) -> Sucursal:
        result = await db.execute(
            select(Sucursal).where(
                Sucursal.id == sucursal_id,
                Sucursal.tenant_id == tenant_id,
                # [EXAMEN-2] Fase 2.5: no distribuir paquetes de sucursales cerradas
                Sucursal.deleted_at.is_(None),
            )
        )
        sucursal = result.scalar_one_or_none()
        if sucursal is None:
            raise NotFoundException("Sucursal no encontrada en el tenant")
        return sucursal

    @staticmethod
    def obtener_fecha_corte(tenant_id: int, sucursal_id: Optional[int] = None) -> datetime:
        return datetime.utcnow()

    @staticmethod
    async def obtener_configuracion_offline(db: AsyncSession, tenant_id: int) -> dict:
        config = await ConfiguracionOfflineService.obtener_configuracion(db, tenant_id)
        return _jsonable({
            "descarga_automatica": config.descarga_automatica,
            "hora_descarga": config.hora_descarga,
            "incluir_clientes": config.incluir_clientes,
            "max_clientes": config.max_clientes,
            "habilitar_carrito_offline": config.habilitar_carrito_offline,
            "max_horas_sesion_offline": config.max_horas_sesion_offline,
            "max_dias_orden_pendiente": config.max_dias_orden_pendiente,
        })

    @staticmethod
    def obtener_impuestos_vigentes(tenant_id: int) -> dict:
        # El Examen 1 no modela una tabla de impuestos; se expone el contrato
        # estable que el cliente usa para calcular el total estimado.
        return {"moneda": settings.STRIPE_MONEDA.upper(), "impuestos": [], "tasa_total": 0.0}

    @staticmethod
    def obtener_clave_publica_stripe(tenant_id: int) -> dict:
        # RNF-15: solo la clave pública, nunca la secreta.
        return {
            "stripe_publishable_key": settings.STRIPE_PUBLISHABLE_KEY,
            "moneda": settings.STRIPE_MONEDA.upper(),
            "modo_simulado": not bool(settings.STRIPE_API_KEY),
        }

    # ------------------------------------------------------------------
    # Contenido del paquete
    # ------------------------------------------------------------------
    @staticmethod
    async def obtener_catalogo(
        db: AsyncSession, tenant_id: int, filtros: Optional[dict] = None, desde: Optional[datetime] = None
    ) -> list[dict]:
        query = (
            select(Producto)
            .options(
                selectinload(Producto.categoria),
                selectinload(Producto.temporada),
                selectinload(Producto.colecciones),
            )
            .where(
                Producto.tenant_id == tenant_id,
                Producto.deleted_at.is_(None),
            )
            .order_by(Producto.id.asc())
        )
        estado = (filtros or {}).get("estado", "ACTIVO")
        if estado:
            query = query.where(Producto.estado == estado)
        if desde is not None:
            query = query.where(Producto.updated_at > desde)
        result = await db.execute(query)
        productos = result.scalars().unique().all()
        return [
            _jsonable({
                "id": p.id,
                "sku": p.sku,
                "nombre": p.nombre,
                "descripcion": p.descripcion,
                "precio": p.precio,
                "imagenes": _normalizar_imagenes(p.imagenes),
                "estado": getattr(p.estado, "value", p.estado),
                "genero": getattr(p.genero, "value", p.genero),
                "categoria_id": p.categoria_id,
                "categoria": p.categoria.nombre if p.categoria else None,
                "temporada_id": p.temporada_id,
                "temporada": p.temporada.nombre if p.temporada else None,
                "colecciones": [c.nombre for c in (p.colecciones or [])],
                "version": p.version,
                "updated_at": p.updated_at,
            })
            for p in productos
        ]

    @staticmethod
    async def obtener_variantes_y_precios(
        db: AsyncSession, tenant_id: int, desde: Optional[datetime] = None
    ) -> list[dict]:
        query = (
            select(VarianteProducto)
            .options(
                selectinload(VarianteProducto.producto),
                selectinload(VarianteProducto.talla),
                selectinload(VarianteProducto.color),
            )
            .join(Producto, Producto.id == VarianteProducto.producto_id)
            .where(
                VarianteProducto.tenant_id == tenant_id,
                VarianteProducto.deleted_at.is_(None),
                Producto.deleted_at.is_(None),
            )
            .order_by(VarianteProducto.id.asc())
        )
        if desde is not None:
            query = query.where(VarianteProducto.updated_at > desde)
        result = await db.execute(query)
        variantes = result.scalars().unique().all()
        salida = []
        for v in variantes:
            precio = v.precio_variante if v.precio_variante is not None else v.producto.precio
            salida.append(_jsonable({
                "id": v.id,
                "producto_id": v.producto_id,
                "sku_producto": v.producto.sku if v.producto else None,
                "sku_variante": v.sku_variante,
                "talla_id": v.talla_id,
                "talla": v.talla.valor if v.talla else None,
                "color_id": v.color_id,
                "color": v.color.nombre if v.color else None,
                "precio": precio,
                "precio_variante": v.precio_variante,
                "version": v.version,
                "updated_at": v.updated_at,
            }))
        return salida

    @staticmethod
    async def obtener_stock_sucursal(
        db: AsyncSession, tenant_id: int, sucursal_id: int, desde: Optional[datetime] = None
    ) -> list[dict]:
        query = (
            select(Inventario)
            .options(selectinload(Inventario.variante_producto))
            .where(
                Inventario.tenant_id == tenant_id,
                Inventario.sucursal_id == sucursal_id,
            )
            .order_by(Inventario.variante_producto_id.asc())
        )
        if desde is not None:
            query = query.where(Inventario.updated_at > desde)
        result = await db.execute(query)
        salida = []
        for inv in result.scalars().all():
            salida.append(_jsonable({
                "variante_producto_id": inv.variante_producto_id,
                "sucursal_id": inv.sucursal_id,
                "cantidad": inv.cantidad,
                "cantidad_reservada": inv.cantidad_reservada,
                "cantidad_disponible": inv.cantidad_disponible,
                "stock_minimo": inv.stock_minimo,
                "estado": getattr(inv.estado, "value", inv.estado),
                "version": inv.version,
                "updated_at": inv.updated_at,
            }))
        return salida

    @staticmethod
    async def obtener_datos_maestros(db: AsyncSession, tenant_id: int) -> dict:
        async def _lista(modelo, campos: tuple[str, ...]):
            result = await db.execute(
                select(modelo).where(modelo.tenant_id == tenant_id).order_by(modelo.id.asc())
            )
            return [
                {campo: _jsonable(getattr(obj, campo)) for campo in ("id",) + campos}
                for obj in result.scalars().all()
            ]

        return {
            "categorias": await _lista(Categoria, ("nombre", "descripcion")),
            "tallas": await _lista(Talla, ("valor", "tipo", "descripcion")),
            "colores": await _lista(Color, ("nombre", "codigo_hex")),
            "temporadas": await _lista(Temporada, ("nombre", "fecha_inicio", "fecha_fin")),
            "colecciones": await _lista(Coleccion, ("nombre", "temporada_id")),
        }

    @staticmethod
    async def obtener_promociones_vigentes(db: AsyncSession, tenant_id: int) -> dict:
        ahora = datetime.utcnow()
        promociones = (await db.execute(
            select(Promocion).where(
                Promocion.tenant_id == tenant_id,
                Promocion.estado == "ACTIVA",
                Promocion.fecha_inicio <= ahora,
                Promocion.fecha_fin >= ahora,
            ).order_by(Promocion.id.asc())
        )).scalars().all()
        cupones = (await db.execute(
            select(Cupon).where(
                Cupon.tenant_id == tenant_id,
                Cupon.estado == "ACTIVO",
                Cupon.fecha_inicio <= ahora,
                Cupon.fecha_fin >= ahora,
            ).order_by(Cupon.id.asc())
        )).scalars().all()
        return {
            "promociones": [
                _jsonable({
                    "id": p.id,
                    "nombre": p.nombre,
                    "descripcion": p.descripcion,
                    "tipo": getattr(p.tipo, "value", p.tipo),
                    "valor": p.valor,
                    "fecha_inicio": p.fecha_inicio,
                    "fecha_fin": p.fecha_fin,
                })
                for p in promociones
            ],
            "cupones": [
                _jsonable({
                    "id": c.id,
                    "codigo": c.codigo,
                    "tipo": getattr(c.tipo, "value", c.tipo),
                    "valor": c.valor,
                    "monto_minimo": c.monto_minimo,
                    "fecha_fin": c.fecha_fin,
                })
                for c in cupones
            ],
        }

    @staticmethod
    async def obtener_clientes_frecuentes(
        db: AsyncSession, tenant_id: int, limite: int = 100
    ) -> list[dict]:
        from app.apps.gestion_ventas.models import Orden

        subconsulta = (
            select(Orden.cliente_id, func.count(Orden.id).label("compras"))
            .where(Orden.tenant_id == tenant_id, Orden.cliente_id.is_not(None))
            .group_by(Orden.cliente_id)
            .subquery()
        )
        result = await db.execute(
            select(Cliente, subconsulta.c.compras)
            .join(subconsulta, subconsulta.c.cliente_id == Cliente.id)
            .where(Cliente.tenant_id == tenant_id, Cliente.deleted_at.is_(None))
            .order_by(subconsulta.c.compras.desc(), Cliente.id.asc())
            .limit(max(0, limite))
        )
        return [
            _jsonable({
                "id": cliente.id,
                "nit_ci": cliente.nit_ci,
                "telefono": cliente.telefono,
                "correo": cliente.correo,
                "direccion_envio": cliente.direccion_envio,
                "compras": compras,
            })
            for cliente, compras in result.all()
        ]

    @staticmethod
    async def obtener_permisos_offline(
        db: AsyncSession, usuario_id: int, tenant_id: int
    ) -> dict:
        return await SesionOfflineService.cargar_contexto_offline(db, usuario_id, tenant_id)

    @staticmethod
    async def priorizar_por_rotacion(
        db: AsyncSession, tenant_id: int, sucursal_id: int, limite: int = 500
    ) -> list[int]:
        from app.apps.gestion_ventas.models import DetalleOrden, Orden

        result = await db.execute(
            select(DetalleOrden.variante_producto_id, func.sum(DetalleOrden.cantidad).label("vendidos"))
            .join(Orden, Orden.id == DetalleOrden.orden_id)
            .where(Orden.tenant_id == tenant_id, Orden.sucursal_id == sucursal_id)
            .group_by(DetalleOrden.variante_producto_id)
            .order_by(func.sum(DetalleOrden.cantidad).desc())
            .limit(limite)
        )
        return [fila[0] for fila in result.all() if fila[0] is not None]

    # ------------------------------------------------------------------
    # Versión, hash y delta
    # ------------------------------------------------------------------
    @staticmethod
    async def _max_actualizacion(
        db: AsyncSession, tenant_id: int, sucursal_id: Optional[int]
    ) -> Optional[datetime]:
        fechas: list[datetime] = []
        for modelo in (Producto, VarianteProducto, Categoria, Talla, Color, Temporada):
            valor = (await db.execute(
                select(func.max(modelo.updated_at)).where(modelo.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if valor is not None:
                fechas.append(valor)
        consulta_inv = select(func.max(Inventario.updated_at)).where(Inventario.tenant_id == tenant_id)
        if sucursal_id is not None:
            consulta_inv = consulta_inv.where(Inventario.sucursal_id == sucursal_id)
        valor = (await db.execute(consulta_inv)).scalar_one_or_none()
        if valor is not None:
            fechas.append(valor)
        return max(fechas) if fechas else None

    @classmethod
    async def calcular_version_paquete(
        cls, db: AsyncSession, tenant_id: int, sucursal_id: Optional[int] = None
    ) -> str:
        actualizado = await cls._max_actualizacion(db, tenant_id, sucursal_id)
        base = f"{tenant_id}:{sucursal_id or 'all'}:{actualizado.isoformat() if actualizado else 'vacio'}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def calcular_hash_paquete(contenido: Any) -> str:
        normalizado = json.dumps(_jsonable(contenido), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()

    @staticmethod
    def serializar_paquete(contenido: dict) -> dict:
        return _jsonable(contenido)

    @staticmethod
    def comprimir_paquete(contenido: dict) -> bytes:
        import gzip

        return gzip.compress(
            json.dumps(_jsonable(contenido), sort_keys=True, ensure_ascii=False).encode("utf-8")
        )

    @classmethod
    async def obtener_delta(
        cls,
        db: AsyncSession,
        tenant_id: int,
        sucursal_id: Optional[int],
        desde_version: Optional[str],
    ) -> dict:
        desde = None
        if desde_version:
            try:
                desde = datetime.fromisoformat(desde_version.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                # Una versión desconocida fuerza la descarga completa.
                desde = None
        return {
            "desde_version": desde_version,
            "catalogo": await cls.obtener_catalogo(db, tenant_id, desde=desde),
            "variantes": await cls.obtener_variantes_y_precios(db, tenant_id, desde=desde),
            "stock": (
                await cls.obtener_stock_sucursal(db, tenant_id, sucursal_id, desde=desde)
                if sucursal_id is not None else []
            ),
        }

    @classmethod
    async def generar_paquete(
        cls,
        db: AsyncSession,
        tenant_id: int,
        sucursal_id: Optional[int],
        usuario_id: int,
        filtros: Optional[dict] = None,
        dispositivo_id: Optional[str] = None,
    ) -> dict:
        if sucursal_id is not None:
            await cls.validar_sucursal_del_tenant(db, tenant_id, sucursal_id)

        config = await cls.obtener_configuracion_offline(db, tenant_id)
        version = await cls.calcular_version_paquete(db, tenant_id, sucursal_id)
        fecha_corte = cls.obtener_fecha_corte(tenant_id, sucursal_id)
        contenido = {
            "tenant_id": tenant_id,
            "sucursal_id": sucursal_id,
            "usuario_id": usuario_id,
            "version": version,
            "fecha_corte": fecha_corte,
            "configuracion_offline": config,
            "catalogo": await cls.obtener_catalogo(db, tenant_id, filtros),
            "variantes": await cls.obtener_variantes_y_precios(db, tenant_id),
            "stock": (
                await cls.obtener_stock_sucursal(db, tenant_id, sucursal_id)
                if sucursal_id is not None else []
            ),
            "datos_maestros": await cls.obtener_datos_maestros(db, tenant_id),
            "promociones": await cls.obtener_promociones_vigentes(db, tenant_id),
            "clientes_frecuentes": (
                await cls.obtener_clientes_frecuentes(db, tenant_id, config["max_clientes"])
                if config["incluir_clientes"] else []
            ),
            "impuestos": cls.obtener_impuestos_vigentes(tenant_id),
            "stripe": cls.obtener_clave_publica_stripe(tenant_id),
            "permisos": await cls.obtener_permisos_offline(db, usuario_id, tenant_id),
        }
        contenido["hash_paquete"] = cls.calcular_hash_paquete(
            {k: v for k, v in contenido.items() if k != "hash_paquete"}
        )
        contenido = cls.serializar_paquete(contenido)

        if dispositivo_id:
            await cls.actualizar_punto_sincronizacion(
                db, tenant_id, dispositivo_id, cursor=version, fecha=fecha_corte
            )
        return contenido

    # ------------------------------------------------------------------
    # Punto de sincronización y auditoría
    # ------------------------------------------------------------------
    @staticmethod
    async def actualizar_punto_sincronizacion(
        db: AsyncSession,
        tenant_id: int,
        dispositivo_id,
        cursor: Optional[str],
        fecha: Optional[datetime] = None,
        completo: bool = False,
    ) -> PuntoSincronizacion:
        try:
            device_uuid = UUID(str(dispositivo_id))
        except (TypeError, ValueError):
            raise ValidationException("dispositivo_id inválido")

        result = await db.execute(
            select(PuntoSincronizacion).where(
                PuntoSincronizacion.tenant_id == tenant_id,
                PuntoSincronizacion.dispositivo_id == device_uuid,
            )
        )
        punto = result.scalar_one_or_none()
        ahora = fecha or datetime.utcnow()
        if punto is None:
            punto = PuntoSincronizacion(
                tenant_id=tenant_id,
                dispositivo_id=device_uuid,
                ultima_sincronizacion=ahora,
                cursor_catalogo=cursor,
                ultima_descarga_completa=ahora if completo else None,
            )
            db.add(punto)
        else:
            punto.ultima_sincronizacion = ahora
            if cursor is not None:
                punto.cursor_catalogo = cursor
            if completo:
                punto.ultima_descarga_completa = ahora
        await db.flush()
        return punto

    @classmethod
    async def registrar_descarga(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int,
        datos: DescargaRegistrarRequest,
    ) -> dict:
        punto = await cls.actualizar_punto_sincronizacion(
            db,
            tenant_id,
            datos.dispositivo_id,
            cursor=datos.version,
            completo=datos.completo,
        )
        db.add(Bitacora(
            tenant_id=tenant_id,
            usuario_id=usuario_id,
            accion="DESCARGA_OFFLINE",
            modulo="operaciones_offline",
            detalles=(
                f"Descarga {'completa' if datos.completo else 'incremental'} "
                f"de {datos.tamano_bytes or 0} bytes"
            ),
            valores_nuevos=_jsonable({
                "sucursal_id": datos.sucursal_id,
                "version": datos.version,
                "hash_paquete": datos.hash_paquete,
                "tamano_bytes": datos.tamano_bytes,
                "metadata": datos.metadata,
            }),
        ))
        await db.commit()
        await db.refresh(punto)
        return {
            "registrada": True,
            "dispositivo_id": str(punto.dispositivo_id),
            "cursor_catalogo": punto.cursor_catalogo,
            "ultima_sincronizacion": punto.ultima_sincronizacion,
            "ultima_descarga_completa": punto.ultima_descarga_completa,
        }

    @staticmethod
    def validar_capacidad_local(
        tenant_id: int, sucursal_id: Optional[int], tamano_estimado: int, capacidad_bytes: int = 10 * 1024 * 1024
    ) -> dict:
        excede = tamano_estimado > capacidad_bytes
        return {
            "tamano_estimado": tamano_estimado,
            "capacidad_bytes": capacidad_bytes,
            "excede_capacidad": excede,
            "sugerencia": "descarga selectiva por rotación" if excede else "descarga completa",
        }
