"""
Servicios de lógica de negocio para Marketing y Promociones (CU24).
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, delete
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone
from typing import List, Optional

from app.apps.gestion_marketing.models import (
    Promocion,
    PromocionProducto,
    PromocionCategoria,
    PromocionSucursal,
    EstadoPromocion,
    TipoPromocion
)
from app.apps.gestion_marketing.schemas import (
    PromocionCreate,
    PromocionUpdate,
    PromocionResponse,
    PromocionPublicaResponse
)
from app.exceptions import NotFoundException, BadRequestException, ConflictException


class PromocionService:
    """Servicio de lógica de negocio para promociones comerciales (CU24)."""

    @classmethod
    def _ahora(cls) -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    async def _validar_solapamiento(
        cls,
        db: AsyncSession,
        promocion_id: Optional[int],
        fecha_inicio: datetime,
        fecha_fin: datetime,
        producto_ids: List[int],
        categoria_ids: List[int],
        sucursal_ids: Optional[List[int]] = None
    ) -> None:
        """
        Valida que no existan otras promociones ACTIVAS que se solapen en fechas
        y compartan productos o categorías aplicables.
        """
        stmt = (
            select(Promocion)
            .options(
                selectinload(Promocion.promocion_productos),
                selectinload(Promocion.promocion_categorias),
                selectinload(Promocion.promocion_sucursales)
            )
            .where(
                Promocion.estado == EstadoPromocion.ACTIVA,
                Promocion.fecha_inicio < fecha_fin,
                Promocion.fecha_fin > fecha_inicio
            )
        )
        if promocion_id:
            stmt = stmt.where(Promocion.id != promocion_id)

        res = await db.execute(stmt)
        promociones_activas = res.scalars().all()

        prod_set = set(producto_ids or [])
        cat_set = set(categoria_ids or [])

        for p in promociones_activas:
            p_prods = {pp.producto_id for pp in p.promocion_productos}
            p_cats = {pc.categoria_id for pc in p.promocion_categorias}

            # Si ambas no tienen filtros específicos (ambas aplican a todo) -> solapamiento
            if not p_prods and not p_cats and not prod_set and not cat_set:
                raise ConflictException(
                    f"Existe solapamiento con la promoción activa '{p.nombre}' (ID: {p.id}), ambas aplican a todo el catálogo en las mismas fechas."
                )

            # Si comparten al menos un producto
            prods_compartidos = prod_set.intersection(p_prods)
            if prods_compartidos:
                raise ConflictException(
                    f"Existe solapamiento con la promoción activa '{p.nombre}' (ID: {p.id}) en los productos con ID: {list(prods_compartidos)}."
                )

            # Si comparten al menos una categoría
            cats_compartidas = cat_set.intersection(p_cats)
            if cats_compartidas:
                raise ConflictException(
                    f"Existe solapamiento con la promoción activa '{p.nombre}' (ID: {p.id}) en las categorías con ID: {list(cats_compartidas)}."
                )

    @classmethod
    def _armar_response(cls, promo: Promocion, advertencia: Optional[str] = None) -> PromocionResponse:
        p_ids = [pp.producto_id for pp in (promo.promocion_productos or [])]
        c_ids = [pc.categoria_id for pc in (promo.promocion_categorias or [])]
        s_ids = [ps.sucursal_id for ps in (promo.promocion_sucursales or [])]

        adv = advertencia
        if not adv and not p_ids and not c_ids:
            adv = "La promoción no tendrá efecto: no se seleccionaron productos ni categorías asociadas."

        return PromocionResponse(
            id=promo.id,
            nombre=promo.nombre,
            descripcion=promo.descripcion,
            tipo=promo.tipo,
            valor=promo.valor,
            fecha_inicio=promo.fecha_inicio,
            fecha_fin=promo.fecha_fin,
            condiciones=promo.condiciones,
            estado=promo.estado,
            creado_por_id=promo.creado_por_id,
            fecha_creacion=promo.fecha_creacion,
            producto_ids=p_ids,
            categoria_ids=c_ids,
            sucursal_ids=s_ids,
            advertencia=adv
        )

    @classmethod
    async def crear(cls, db: AsyncSession, datos: PromocionCreate, creado_por_id: Optional[int]) -> PromocionResponse:
        """Crea una nueva promoción comercial."""
        # Si se crea como ACTIVA, validar solapamiento
        if datos.estado == EstadoPromocion.ACTIVA:
            await cls._validar_solapamiento(
                db=db,
                promocion_id=None,
                fecha_inicio=datos.fecha_inicio,
                fecha_fin=datos.fecha_fin,
                producto_ids=datos.producto_ids,
                categoria_ids=datos.categoria_ids,
                sucursal_ids=datos.sucursal_ids
            )

        promocion = Promocion(
            nombre=datos.nombre,
            descripcion=datos.descripcion,
            tipo=datos.tipo,
            valor=datos.valor,
            fecha_inicio=datos.fecha_inicio,
            fecha_fin=datos.fecha_fin,
            condiciones=datos.condiciones,
            estado=datos.estado,
            creado_por_id=creado_por_id
        )
        db.add(promocion)
        await db.flush()

        for pid in set(datos.producto_ids or []):
            db.add(PromocionProducto(promocion_id=promocion.id, producto_id=pid))

        for cid in set(datos.categoria_ids or []):
            db.add(PromocionCategoria(promocion_id=promocion.id, categoria_id=cid))

        for sid in set(datos.sucursal_ids or []):
            db.add(PromocionSucursal(promocion_id=promocion.id, sucursal_id=sid))

        await db.commit()

        # Recargar con relaciones
        return await cls.obtener_response(db, promocion.id)

    @classmethod
    async def obtener(cls, db: AsyncSession, promocion_id: int) -> Promocion:
        """Obtiene el modelo SQLAlchemy de la promoción con sus relaciones cargadas."""
        stmt = (
            select(Promocion)
            .options(
                selectinload(Promocion.promocion_productos),
                selectinload(Promocion.promocion_categorias),
                selectinload(Promocion.promocion_sucursales)
            )
            .where(Promocion.id == promocion_id)
        )
        res = await db.execute(stmt)
        promocion = res.scalar_one_or_none()
        if not promocion:
            raise NotFoundException(f"Promoción con ID {promocion_id} no encontrada")
        return promocion

    @classmethod
    async def obtener_response(cls, db: AsyncSession, promocion_id: int) -> PromocionResponse:
        promocion = await cls.obtener(db, promocion_id)
        return cls._armar_response(promocion)

    @classmethod
    async def listar(
        cls,
        db: AsyncSession,
        estado: Optional[EstadoPromocion] = None,
        tipo: Optional[TipoPromocion] = None,
        solo_vigentes: bool = False,
        skip: int = 0,
        limit: int = 100
    ) -> List[PromocionResponse]:
        """Lista promociones con filtros y relaciones cargadas."""
        stmt = (
            select(Promocion)
            .options(
                selectinload(Promocion.promocion_productos),
                selectinload(Promocion.promocion_categorias),
                selectinload(Promocion.promocion_sucursales)
            )
            .order_by(Promocion.fecha_creacion.desc())
        )

        if estado:
            stmt = stmt.where(Promocion.estado == estado)
        if tipo:
            stmt = stmt.where(Promocion.tipo == tipo)
        if solo_vigentes:
            ahora = cls._ahora()
            stmt = stmt.where(
                Promocion.fecha_inicio <= ahora,
                Promocion.fecha_fin >= ahora
            )

        stmt = stmt.offset(skip).limit(limit)
        res = await db.execute(stmt)
        promociones = res.scalars().all()
        return [cls._armar_response(p) for p in promociones]

    @classmethod
    async def actualizar(cls, db: AsyncSession, promocion_id: int, datos: PromocionUpdate) -> PromocionResponse:
        """Actualiza una promoción y sus relaciones."""
        promocion = await cls.obtener(db, promocion_id)

        nueva_fecha_inicio = datos.fecha_inicio if datos.fecha_inicio is not None else promocion.fecha_inicio
        nueva_fecha_fin = datos.fecha_fin if datos.fecha_fin is not None else promocion.fecha_fin
        nuevo_estado = datos.estado if datos.estado is not None else promocion.estado

        prods_finales = datos.producto_ids if datos.producto_ids is not None else [pp.producto_id for pp in promocion.promocion_productos]
        cats_finales = datos.categoria_ids if datos.categoria_ids is not None else [pc.categoria_id for pc in promocion.promocion_categorias]
        sucs_finales = datos.sucursal_ids if datos.sucursal_ids is not None else [ps.sucursal_id for ps in promocion.promocion_sucursales]

        # Si el estado resultante es ACTIVA, validar solapamiento
        if nuevo_estado == EstadoPromocion.ACTIVA:
            await cls._validar_solapamiento(
                db=db,
                promocion_id=promocion.id,
                fecha_inicio=nueva_fecha_inicio,
                fecha_fin=nueva_fecha_fin,
                producto_ids=prods_finales,
                categoria_ids=cats_finales,
                sucursal_ids=sucs_finales
            )

        # Actualizar campos directos
        for field in ["nombre", "descripcion", "tipo", "valor", "fecha_inicio", "fecha_fin", "condiciones", "estado"]:
            val = getattr(datos, field)
            if val is not None:
                setattr(promocion, field, val)

        # Actualizar relaciones si vinieron en el payload
        if datos.producto_ids is not None:
            await db.execute(delete(PromocionProducto).where(PromocionProducto.promocion_id == promocion.id))
            for pid in set(datos.producto_ids):
                db.add(PromocionProducto(promocion_id=promocion.id, producto_id=pid))

        if datos.categoria_ids is not None:
            await db.execute(delete(PromocionCategoria).where(PromocionCategoria.promocion_id == promocion.id))
            for cid in set(datos.categoria_ids):
                db.add(PromocionCategoria(promocion_id=promocion.id, categoria_id=cid))

        if datos.sucursal_ids is not None:
            await db.execute(delete(PromocionSucursal).where(PromocionSucursal.promocion_id == promocion.id))
            for sid in set(datos.sucursal_ids):
                db.add(PromocionSucursal(promocion_id=promocion.id, sucursal_id=sid))

        await db.commit()
        return await cls.obtener_response(db, promocion.id)

    @classmethod
    async def cambiar_estado(cls, db: AsyncSession, promocion_id: int, nuevo_estado: EstadoPromocion) -> PromocionResponse:
        """Cambia el estado de una promoción con validación de solapamiento al activar."""
        promocion = await cls.obtener(db, promocion_id)

        if nuevo_estado == EstadoPromocion.ACTIVA:
            p_ids = [pp.producto_id for pp in promocion.promocion_productos]
            c_ids = [pc.categoria_id for pc in promocion.promocion_categorias]
            s_ids = [ps.sucursal_id for ps in promocion.promocion_sucursales]

            await cls._validar_solapamiento(
                db=db,
                promocion_id=promocion.id,
                fecha_inicio=promocion.fecha_inicio,
                fecha_fin=promocion.fecha_fin,
                producto_ids=p_ids,
                categoria_ids=c_ids,
                sucursal_ids=s_ids
            )

        promocion.estado = nuevo_estado
        await db.commit()
        return await cls.obtener_response(db, promocion.id)

    @classmethod
    async def eliminar(cls, db: AsyncSession, promocion_id: int) -> dict:
        """Elimina una promoción o realiza soft-delete si ya estuvo activa."""
        promocion = await cls.obtener(db, promocion_id)
        if promocion.estado == EstadoPromocion.ACTIVA:
            promocion.estado = EstadoPromocion.INACTIVA
            await db.commit()
            return {"mensaje": f"Promoción '{promocion.nombre}' desactivada exitosamente (soft delete)", "id": promocion.id}
        else:
            await db.delete(promocion)
            await db.commit()
            return {"mensaje": f"Promoción con ID {promocion_id} eliminada exitosamente", "id": promocion_id}

    @classmethod
    async def obtener_promociones_activas(cls, db: AsyncSession, sucursal_id: Optional[int] = None) -> List[PromocionPublicaResponse]:
        """Obtiene las promociones activas y vigentes para el catálogo público."""
        ahora = cls._ahora()
        stmt = (
            select(Promocion)
            .options(
                selectinload(Promocion.promocion_productos),
                selectinload(Promocion.promocion_categorias),
                selectinload(Promocion.promocion_sucursales)
            )
            .where(
                Promocion.estado == EstadoPromocion.ACTIVA,
                Promocion.fecha_inicio <= ahora,
                Promocion.fecha_fin >= ahora
            )
            .order_by(Promocion.fecha_creacion.desc())
        )
        res = await db.execute(stmt)
        promociones = res.scalars().all()

        resultado: List[PromocionPublicaResponse] = []
        for p in promociones:
            s_ids = [ps.sucursal_id for ps in p.promocion_sucursales]
            # Si tiene sucursales especificadas y viene sucursal_id, verificar si aplica
            if sucursal_id is not None and s_ids and sucursal_id not in s_ids:
                continue

            resultado.append(
                PromocionPublicaResponse(
                    id=p.id,
                    nombre=p.nombre,
                    descripcion=p.descripcion,
                    tipo=p.tipo,
                    valor=p.valor,
                    fecha_inicio=p.fecha_inicio,
                    fecha_fin=p.fecha_fin,
                    condiciones=p.condiciones,
                    producto_ids=[pp.producto_id for pp in p.promocion_productos],
                    categoria_ids=[pc.categoria_id for pc in p.promocion_categorias],
                    sucursal_ids=s_ids
                )
            )
        return resultado
