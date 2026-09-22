"""
Servicios de negocio para Gestión de Catálogo, Productos e Inventario.
"""
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, exists
from sqlalchemy.orm import selectinload, joinedload

from app.apps.gestion_catalogo.models import (
    Ciudad, Sucursal, Categoria, Talla, Color, Temporada, Coleccion,
    Proveedor, Producto, VarianteProducto, Inventario, MovimientoInventario,
    EstadoStock, TipoMovimiento, EstadoSucursal, EstadoProducto,
    ProductoFavorito, ValoracionProducto, EstadoValoracion,
    Recepcion, DetalleRecepcion
)
from app.apps.gestion_catalogo.schemas import (
    CiudadCreate, CiudadUpdate, SucursalCreate, SucursalUpdate,
    CategoriaCreate, CategoriaUpdate, TallaCreate, TallaUpdate, TallaResponse, ColorCreate, ColorUpdate, ColorResponse,
    TemporadaCreate, TemporadaUpdate,
    ColeccionCreate, ColeccionUpdate, ProveedorCreate, ProveedorUpdate,
    ProductoCreate, ProductoUpdate, VarianteProductoCreate, InventarioCreate,
    MovimientoInventarioCreate, AgregarVarianteRequest, StockPorSucursalRequest,
    ProductoFilter, FavoritoResponse, FavoritoIdsResponse, MoverFavoritoCarritoRequest,
    ValoracionCreate, ValoracionUpdate, ValoracionResponse, PuedeValorarResponse,
    EstadoValoracionEnum, ProductoResumenResponse,
    RecepcionCreate, RecepcionResponse, RecepcionDetalleItemResponse
)
from app.config import settings
from app.exceptions import (
    NotFoundException, ConflictException, ValidationException, InventoryException,
    ForbiddenException, BadRequestException
)


# ============================================
# Servicios de Ciudad
# ============================================

class CiudadService:
    """Servicio de gestión de ciudades."""
    
    @staticmethod
    async def create(db: AsyncSession, datos: CiudadCreate) -> Ciudad:
        ciudad = Ciudad(**datos.model_dump())
        db.add(ciudad)
        await db.commit()
        await db.refresh(ciudad)
        return ciudad
    
    @staticmethod
    async def get(db: AsyncSession, ciudad_id: int) -> Ciudad:
        result = await db.execute(select(Ciudad).where(Ciudad.id == ciudad_id))
        ciudad = result.scalar_one_or_none()
        if not ciudad:
            raise NotFoundException(f"Ciudad con ID {ciudad_id} no encontrada")
        return ciudad
    
    @staticmethod
    async def get_all(db: AsyncSession) -> List[Ciudad]:
        result = await db.execute(select(Ciudad))
        return result.scalars().all()
    
    @staticmethod
    async def update(db: AsyncSession, ciudad_id: int, datos: CiudadUpdate) -> Ciudad:
        ciudad = await CiudadService.get(db, ciudad_id)
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(ciudad, field, value)
        await db.commit()
        await db.refresh(ciudad)
        return ciudad
    
    @staticmethod
    async def delete(db: AsyncSession, ciudad_id: int):
        ciudad = await CiudadService.get(db, ciudad_id)
        # Verificar sucursales con query explícita (evita lazy load en sesión async)
        count_result = await db.execute(
            select(func.count()).select_from(Sucursal).where(Sucursal.ciudad_id == ciudad_id)
        )
        if count_result.scalar() > 0:
            raise ConflictException("No se puede eliminar una ciudad con sucursales asociadas")
        await db.delete(ciudad)
        await db.commit()


# ============================================
# Servicios de Sucursal
# ============================================

class SucursalService:
    """Servicio de gestión de sucursales."""
    
    @staticmethod
    async def create(db: AsyncSession, datos: SucursalCreate) -> Sucursal:
        # Verificar nombre duplicado en la misma ciudad
        result = await db.execute(
            select(Sucursal).where(
                and_(
                    Sucursal.nombre == datos.nombre,
                    Sucursal.ciudad_id == datos.ciudad_id
                )
            )
        )
        if result.scalar_one_or_none():
            raise ConflictException("Ya existe una sucursal con este nombre en la misma ciudad")
        
        sucursal = Sucursal(**datos.model_dump())
        db.add(sucursal)
        await db.commit()
        await db.refresh(sucursal)
        return sucursal
    
    @staticmethod
    async def get(db: AsyncSession, sucursal_id: int) -> Sucursal:
        result = await db.execute(
            select(Sucursal)
            .options(selectinload(Sucursal.ciudad))
            .where(Sucursal.id == sucursal_id)
        )
        sucursal = result.scalar_one_or_none()
        if not sucursal:
            raise NotFoundException(f"Sucursal con ID {sucursal_id} no encontrada")
        return sucursal
    
    @staticmethod
    async def get_all(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        ciudad_id: Optional[int] = None,
        estado: Optional[str] = None
    ) -> Tuple[List[Sucursal], int]:
        query = select(Sucursal).options(selectinload(Sucursal.ciudad))
        
        if ciudad_id:
            query = query.where(Sucursal.ciudad_id == ciudad_id)
        if estado:
            query = query.where(Sucursal.estado == estado)
        
        # Contar total
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar()
        
        # Aplicar paginación
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        
        return result.scalars().all(), total
    
    @staticmethod
    async def update(db: AsyncSession, sucursal_id: int, datos: SucursalUpdate) -> Sucursal:
        sucursal = await SucursalService.get(db, sucursal_id)
        
        # Verificar duplicado de nombre si se cambia
        if datos.nombre and datos.nombre != sucursal.nombre:
            ciudad_id = datos.ciudad_id or sucursal.ciudad_id
            result = await db.execute(
                select(Sucursal).where(
                    and_(
                        Sucursal.nombre == datos.nombre,
                        Sucursal.ciudad_id == ciudad_id
                    )
                )
            )
            if result.scalar_one_or_none():
                raise ConflictException("Ya existe una sucursal con este nombre en la misma ciudad")
        
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(sucursal, field, value)
        
        await db.commit()
        await db.refresh(sucursal)
        return sucursal
    
    @staticmethod
    async def delete(db: AsyncSession, sucursal_id: int):
        sucursal = await SucursalService.get(db, sucursal_id)
        
        # Verificar que no tenga inventario activo
        result = await db.execute(
            select(Inventario).where(
                and_(
                    Inventario.sucursal_id == sucursal_id,
                    Inventario.cantidad > 0
                )
            )
        )
        if result.scalars().first():
            raise ConflictException("No se puede eliminar una sucursal con inventario activo")
        
        await db.delete(sucursal)
        await db.commit()


# ============================================
# Servicios de Categoría
# ============================================

class CategoriaService:
    """Servicio de gestión de categorías."""
    
    @staticmethod
    async def create(db: AsyncSession, datos: CategoriaCreate) -> Categoria:
        result = await db.execute(
            select(Categoria).where(Categoria.nombre == datos.nombre)
        )
        if result.scalar_one_or_none():
            raise ConflictException("Ya existe una categoría con este nombre")
        
        categoria = Categoria(**datos.model_dump())
        db.add(categoria)
        await db.commit()
        await db.refresh(categoria)
        return categoria
    
    @staticmethod
    async def get(db: AsyncSession, categoria_id: int) -> Categoria:
        result = await db.execute(select(Categoria).where(Categoria.id == categoria_id))
        categoria = result.scalar_one_or_none()
        if not categoria:
            raise NotFoundException(f"Categoría con ID {categoria_id} no encontrada")
        return categoria
    
    @staticmethod
    async def get_all(db: AsyncSession) -> List[Categoria]:
        result = await db.execute(select(Categoria))
        return result.scalars().all()
    
    @staticmethod
    async def update(db: AsyncSession, categoria_id: int, datos: CategoriaUpdate) -> Categoria:
        categoria = await CategoriaService.get(db, categoria_id)
        
        if datos.nombre and datos.nombre != categoria.nombre:
            result = await db.execute(
                select(Categoria).where(Categoria.nombre == datos.nombre)
            )
            if result.scalar_one_or_none():
                raise ConflictException("Ya existe una categoría con este nombre")
        
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(categoria, field, value)
        
        await db.commit()
        await db.refresh(categoria)
        return categoria
    
    @staticmethod
    async def delete(db: AsyncSession, categoria_id: int):
        categoria = await CategoriaService.get(db, categoria_id)
        
        # Verificar productos con query explícita (evita lazy load en sesión async)
        count_result = await db.execute(
            select(func.count()).select_from(Producto).where(Producto.categoria_id == categoria_id)
        )
        if count_result.scalar() > 0:
            raise ConflictException("No se puede eliminar una categoría con productos asociados")
        
        await db.delete(categoria)
        await db.commit()


# ============================================
# Servicios de Talla
# ============================================

class TallaService:
    """Servicio de gestión de tallas."""
    
    @staticmethod
    async def create(db: AsyncSession, datos: TallaCreate) -> Talla:
        talla = Talla(**datos.model_dump())
        db.add(talla)
        await db.commit()
        await db.refresh(talla)
        return talla
    
    @staticmethod
    async def get(db: AsyncSession, talla_id: int) -> Talla:
        result = await db.execute(select(Talla).where(Talla.id == talla_id))
        talla = result.scalar_one_or_none()
        if not talla:
            raise NotFoundException(f"Talla con ID {talla_id} no encontrada")
        return talla
    
    @staticmethod
    async def get_all(db: AsyncSession, tipo: Optional[str] = None) -> List[Talla]:
        query = select(Talla)
        if tipo:
            query = query.where(Talla.tipo == tipo)
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def update(db: AsyncSession, talla_id: int, datos: TallaUpdate) -> Talla:
        talla = await TallaService.get(db, talla_id)
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(talla, field, value)
        await db.commit()
        await db.refresh(talla)
        return talla

    @staticmethod
    async def delete(db: AsyncSession, talla_id: int):
        talla = await TallaService.get(db, talla_id)
        # La FK de variantes es SET NULL: al eliminar, las variantes quedan "Sin talla".
        await db.delete(talla)
        await db.commit()


# ============================================
# Servicios de Color
# ============================================

class ColorService:
    """Servicio de gestión de colores."""
    
    @staticmethod
    async def create(db: AsyncSession, datos: ColorCreate) -> Color:
        color = Color(**datos.model_dump())
        db.add(color)
        await db.commit()
        await db.refresh(color)
        return color
    
    @staticmethod
    async def get(db: AsyncSession, color_id: int) -> Color:
        result = await db.execute(select(Color).where(Color.id == color_id))
        color = result.scalar_one_or_none()
        if not color:
            raise NotFoundException(f"Color con ID {color_id} no encontrado")
        return color
    
    @staticmethod
    async def get_all(db: AsyncSession) -> List[Color]:
        result = await db.execute(select(Color))
        return result.scalars().all()

    @staticmethod
    async def update(db: AsyncSession, color_id: int, datos: ColorUpdate) -> Color:
        color = await ColorService.get(db, color_id)
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(color, field, value)
        await db.commit()
        await db.refresh(color)
        return color

    @staticmethod
    async def delete(db: AsyncSession, color_id: int):
        color = await ColorService.get(db, color_id)
        # La FK de variantes es CASCADE y color es NOT NULL: bloquear si hay variantes en uso.
        count_result = await db.execute(
            select(func.count()).select_from(VarianteProducto).where(VarianteProducto.color_id == color_id)
        )
        if count_result.scalar() > 0:
            raise ConflictException("No se puede eliminar un color con variantes de producto asociadas")
        await db.delete(color)
        await db.commit()


# ============================================
# Servicios de Temporada
# ============================================

class TemporadaService:
    """Servicio de gestión de temporadas."""
    
    @staticmethod
    async def create(db: AsyncSession, datos: TemporadaCreate) -> Temporada:
        temporada = Temporada(**datos.model_dump())
        db.add(temporada)
        await db.commit()
        await db.refresh(temporada)
        return temporada
    
    @staticmethod
    async def get(db: AsyncSession, temporada_id: int) -> Temporada:
        result = await db.execute(select(Temporada).where(Temporada.id == temporada_id))
        temporada = result.scalar_one_or_none()
        if not temporada:
            raise NotFoundException(f"Temporada con ID {temporada_id} no encontrada")
        return temporada
    
    @staticmethod
    async def get_all(db: AsyncSession) -> List[Temporada]:
        result = await db.execute(select(Temporada))
        return result.scalars().all()
    
    @staticmethod
    async def update(db: AsyncSession, temporada_id: int, datos: TemporadaUpdate) -> Temporada:
        temporada = await TemporadaService.get(db, temporada_id)
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(temporada, field, value)
        await db.commit()
        await db.refresh(temporada)
        return temporada
    
    @staticmethod
    async def delete(db: AsyncSession, temporada_id: int):
        temporada = await TemporadaService.get(db, temporada_id)
        # Verificar productos con query explícita (evita lazy load en sesión async)
        count_result = await db.execute(
            select(func.count()).select_from(Producto).where(Producto.temporada_id == temporada_id)
        )
        if count_result.scalar() > 0:
            raise ConflictException("No se puede eliminar una temporada con productos asociados")
        await db.delete(temporada)
        await db.commit()


# ============================================
# Servicios de Colección
# ============================================

class ColeccionService:
    """Servicio de gestión de colecciones."""
    
    @staticmethod
    async def create(db: AsyncSession, datos: ColeccionCreate) -> Coleccion:
        coleccion = Coleccion(**datos.model_dump())
        db.add(coleccion)
        await db.commit()
        await db.refresh(coleccion)
        return coleccion
    
    @staticmethod
    async def get(db: AsyncSession, coleccion_id: int) -> Coleccion:
        result = await db.execute(select(Coleccion).where(Coleccion.id == coleccion_id))
        coleccion = result.scalar_one_or_none()
        if not coleccion:
            raise NotFoundException(f"Colección con ID {coleccion_id} no encontrada")
        return coleccion
    
    @staticmethod
    async def get_all(db: AsyncSession, temporada_id: Optional[int] = None) -> List[Coleccion]:
        query = select(Coleccion)
        if temporada_id:
            query = query.where(Coleccion.temporada_id == temporada_id)
        result = await db.execute(query)
        return result.scalars().all()
    
    @staticmethod
    async def update(db: AsyncSession, coleccion_id: int, datos: ColeccionUpdate) -> Coleccion:
        coleccion = await ColeccionService.get(db, coleccion_id)
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(coleccion, field, value)
        await db.commit()
        await db.refresh(coleccion)
        return coleccion
    
    @staticmethod
    async def delete(db: AsyncSession, coleccion_id: int):
        coleccion = await ColeccionService.get(db, coleccion_id)
        await db.delete(coleccion)
        await db.commit()
    
    @staticmethod
    async def associate_producto(db: AsyncSession, producto_id: int, coleccion_id: int):
        from app.apps.gestion_catalogo.models import ProductoColeccion
        
        # Verificar que existen
        await ProductoService.get(db, producto_id)
        await ColeccionService.get(db, coleccion_id)
        
        # Crear asociación
        result = await db.execute(
            select(ProductoColeccion).where(
                and_(
                    ProductoColeccion.producto_id == producto_id,
                    ProductoColeccion.coleccion_id == coleccion_id
                )
            )
        )
        if result.scalar_one_or_none():
            raise ConflictException("El producto ya está asociado a esta colección")
        
        assoc = ProductoColeccion(producto_id=producto_id, coleccion_id=coleccion_id)
        db.add(assoc)
        await db.commit()

    @staticmethod
    async def disassociate_producto(db: AsyncSession, producto_id: int, coleccion_id: int):
        from app.apps.gestion_catalogo.models import ProductoColeccion

        result = await db.execute(
            select(ProductoColeccion).where(
                and_(
                    ProductoColeccion.producto_id == producto_id,
                    ProductoColeccion.coleccion_id == coleccion_id
                )
            )
        )
        assoc = result.scalar_one_or_none()
        if not assoc:
            raise NotFoundException("El producto no está asociado a esta colección")

        await db.delete(assoc)
        await db.commit()

    @staticmethod
    async def get_colecciones_de_producto(db: AsyncSession, producto_id: int) -> List[Coleccion]:
        from app.apps.gestion_catalogo.models import ProductoColeccion

        await ProductoService.get(db, producto_id)
        result = await db.execute(
            select(Coleccion)
            .join(ProductoColeccion, ProductoColeccion.coleccion_id == Coleccion.id)
            .where(ProductoColeccion.producto_id == producto_id)
        )
        return list(result.scalars().all())


# ============================================
# Servicios de Proveedor
# ============================================

class ProveedorService:
    """Servicio de gestión de proveedores."""
    
    @staticmethod
    async def create(db: AsyncSession, datos: ProveedorCreate) -> Proveedor:
        result = await db.execute(
            select(Proveedor).where(Proveedor.nit == datos.nit)
        )
        if result.scalar_one_or_none():
            raise ConflictException("Ya existe un proveedor con este NIT")
        
        proveedor = Proveedor(**datos.model_dump())
        db.add(proveedor)
        await db.commit()
        await db.refresh(proveedor)
        return proveedor
    
    @staticmethod
    async def get(db: AsyncSession, proveedor_id: int) -> Proveedor:
        result = await db.execute(select(Proveedor).where(Proveedor.id == proveedor_id))
        proveedor = result.scalar_one_or_none()
        if not proveedor:
            raise NotFoundException(f"Proveedor con ID {proveedor_id} no encontrado")
        return proveedor
    
    @staticmethod
    async def get_all(db: AsyncSession, skip: int = 0, limit: int = 100) -> Tuple[List[Proveedor], int]:
        query = select(Proveedor).offset(skip).limit(limit)
        result = await db.execute(query)
        
        count_result = await db.execute(select(func.count()).select_from(Proveedor))
        total = count_result.scalar()
        
        return result.scalars().all(), total
    
    @staticmethod
    async def update(db: AsyncSession, proveedor_id: int, datos: ProveedorUpdate) -> Proveedor:
        proveedor = await ProveedorService.get(db, proveedor_id)
        
        if datos.nit and datos.nit != proveedor.nit:
            result = await db.execute(
                select(Proveedor).where(Proveedor.nit == datos.nit)
            )
            if result.scalar_one_or_none():
                raise ConflictException("Ya existe un proveedor con este NIT")
        
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(proveedor, field, value)
        
        await db.commit()
        await db.refresh(proveedor)
        return proveedor
    
    @staticmethod
    async def delete(db: AsyncSession, proveedor_id: int):
        proveedor = await ProveedorService.get(db, proveedor_id)
        
        # Verificar productos con query explícita (evita lazy load en sesión async)
        count_result = await db.execute(
            select(func.count()).select_from(Producto).where(Producto.proveedor_id == proveedor_id)
        )
        if count_result.scalar() > 0:
            raise ConflictException("No se puede eliminar un proveedor con productos asociados")
        
        await db.delete(proveedor)
        await db.commit()


# ============================================
# Servicios de Producto
# ============================================

class ProductoService:
    """Servicio de gestión de productos."""
    
    @staticmethod
    async def create(db: AsyncSession, datos: ProductoCreate, stock_por_sucursal: List[StockPorSucursalRequest] = None) -> Producto:
        # Verificar SKU único
        result = await db.execute(
            select(Producto).where(Producto.sku == datos.sku)
        )
        if result.scalar_one_or_none():
            raise ConflictException("Ya existe un producto con este SKU")
        
        datos_dict = datos.model_dump(exclude={"stock_por_sucursal"})
        producto = Producto(**datos_dict)
        db.add(producto)
        await db.flush()
        
        # Crear inventario si se especifica
        stock_list = stock_por_sucursal or getattr(datos, "stock_por_sucursal", None)
        if stock_list:
            for stock in stock_list:
                talla_id = getattr(stock, "talla_id", None)
                color_id = getattr(stock, "color_id", None)
                
                # Si no se especificó color, intentar obtener el primero disponible en el sistema
                if not color_id:
                    color_res = await db.execute(select(Color).limit(1))
                    color_obj = color_res.scalar_one_or_none()
                    if color_obj:
                        color_id = color_obj.id
                
                # Crear variante e inventario si hay un color definido (la talla puede ser None para gorras/accesorios)
                if color_id:
                    conditions = [
                        VarianteProducto.producto_id == producto.id,
                        VarianteProducto.color_id == color_id
                    ]
                    if talla_id is not None:
                        conditions.append(VarianteProducto.talla_id == talla_id)
                    else:
                        conditions.append(VarianteProducto.talla_id.is_(None))
                    
                    var_result = await db.execute(
                        select(VarianteProducto).where(and_(*conditions))
                    )
                    variante = var_result.scalar_one_or_none()
                    costo_var = getattr(stock, "costo_variante", None)
                    if not variante:
                        talla_part = f"T{talla_id}" if talla_id is not None else "ST"
                        sku_var = f"{producto.sku}-{talla_part}-C{color_id}"
                        variante = VarianteProducto(
                            producto_id=producto.id,
                            talla_id=talla_id,
                            color_id=color_id,
                            sku_variante=sku_var,
                            costo_variante=costo_var
                        )
                        db.add(variante)
                        await db.flush()
                    elif costo_var is not None and variante.costo_variante is None:
                        variante.costo_variante = costo_var
                        db.add(variante)
                    
                    # Verificar si ya existe inventario para esta variante en la misma sucursal
                    inv_res = await db.execute(
                        select(Inventario).where(
                            and_(
                                Inventario.variante_producto_id == variante.id,
                                Inventario.sucursal_id == stock.sucursal_id
                            )
                        )
                    )
                    exist_inv = inv_res.scalar_one_or_none()
                    if exist_inv:
                        exist_inv.cantidad += stock.cantidad
                        if exist_inv.cantidad > 0:
                            exist_inv.estado = EstadoStock.DISPONIBLE
                    else:
                        inventario = Inventario(
                            variante_producto_id=variante.id,
                            sucursal_id=stock.sucursal_id,
                            cantidad=stock.cantidad,
                            cantidad_reservada=0,
                            cantidad_vendida=0,
                            estado=EstadoStock.DISPONIBLE if stock.cantidad > 0 else EstadoStock.AGOTADO
                        )
                        db.add(inventario)
        
        await db.commit()
        return await ProductoService.get(db, producto.id)
    
    @staticmethod
    async def get(db: AsyncSession, producto_id: int) -> Producto:
        result = await db.execute(
            select(Producto)
            .options(
                selectinload(Producto.categoria),
                selectinload(Producto.temporada),
                selectinload(Producto.proveedor),
                selectinload(Producto.colecciones),
                selectinload(Producto.variantes).selectinload(VarianteProducto.talla),
                selectinload(Producto.variantes).selectinload(VarianteProducto.color),
                selectinload(Producto.variantes).selectinload(VarianteProducto.producto)
            )
            .where(Producto.id == producto_id)
        )
        producto = result.scalar_one_or_none()
        if not producto:
            raise NotFoundException(f"Producto con ID {producto_id} no encontrado")
        return producto
    
    @staticmethod
    async def get_all(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 50,
        categoria_id: Optional[int] = None,
        estado: Optional[str] = None,
        genero: Optional[str] = None,
        temporada_id: Optional[int] = None,
        proveedor_id: Optional[int] = None,
        buscar: Optional[str] = None
    ) -> Tuple[List[Producto], int]:
        query = select(Producto).options(
            selectinload(Producto.categoria),
            selectinload(Producto.temporada),
            selectinload(Producto.proveedor),
            selectinload(Producto.variantes)
        )
        
        if categoria_id:
            query = query.where(Producto.categoria_id == categoria_id)
        if estado:
            query = query.where(Producto.estado == estado)
        if genero:
            query = query.where(Producto.genero == genero)
        if temporada_id:
            query = query.where(Producto.temporada_id == temporada_id)
        if proveedor_id:
            query = query.where(Producto.proveedor_id == proveedor_id)
        if buscar:
            query = query.where(
                or_(
                    Producto.nombre.ilike(f"%{buscar}%"),
                    Producto.descripcion.ilike(f"%{buscar}%"),
                    Producto.sku.ilike(f"%{buscar}%")
                )
            )
        
        # Contar total
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar()
        
        # Paginación y orden (más recientes primero)
        query = query.order_by(Producto.id.desc()).offset(skip).limit(limit)
        result = await db.execute(query)
        
        return result.scalars().all(), total
    
    @staticmethod
    async def search_productos_publicos(
        db: AsyncSession, filtros: ProductoFilter
    ) -> Tuple[List[Producto], int]:
        """Búsqueda y filtrado avanzado de productos para el catálogo público."""
        query = (
            select(Producto)
            .options(
                selectinload(Producto.categoria),
                selectinload(Producto.temporada),
                selectinload(Producto.proveedor),
                selectinload(Producto.colecciones),
                selectinload(Producto.variantes).selectinload(VarianteProducto.talla),
                selectinload(Producto.variantes).selectinload(VarianteProducto.color)
            )
            .where(Producto.estado == EstadoProducto.ACTIVO)
        )
        
        # Filtro de texto libre
        if filtros.q:
            q_term = f"%{filtros.q.strip()}%"
            query = query.where(
                or_(
                    Producto.nombre.ilike(q_term),
                    Producto.descripcion.ilike(q_term),
                    Producto.sku.ilike(q_term)
                )
            )
            
        # Filtro por género
        if filtros.genero:
            query = query.where(Producto.genero == filtros.genero)

        # Filtro por categoría
        if filtros.categoria_id:
            query = query.where(Producto.categoria_id == filtros.categoria_id)
            
        # Filtro por temporada
        if filtros.temporada_id:
            query = query.where(Producto.temporada_id == filtros.temporada_id)
            
        # Filtro por rango de precios
        if filtros.precio_min is not None:
            query = query.where(Producto.precio >= filtros.precio_min)
        if filtros.precio_max is not None:
            query = query.where(Producto.precio <= filtros.precio_max)
            
        # Filtros por variante (talla / color) usando subconsulta EXISTS para evitar DISTINCT sobre columnas JSON
        if filtros.talla_id or filtros.color_id:
            variant_conditions = [VarianteProducto.producto_id == Producto.id]
            if filtros.talla_id:
                variant_conditions.append(VarianteProducto.talla_id == filtros.talla_id)
            if filtros.color_id:
                variant_conditions.append(VarianteProducto.color_id == filtros.color_id)
            query = query.where(
                exists(select(VarianteProducto.id).where(and_(*variant_conditions)))
            )

        # Filtro por colección (N:M) con EXISTS
        if filtros.coleccion_id:
            from app.apps.gestion_catalogo.models import ProductoColeccion
            query = query.where(
                exists(
                    select(ProductoColeccion.id).where(
                        and_(
                            ProductoColeccion.producto_id == Producto.id,
                            ProductoColeccion.coleccion_id == filtros.coleccion_id
                        )
                    )
                )
            )
            
        # Ordenamiento
        if filtros.ordenar_por == "precio_asc":
            query = query.order_by(Producto.precio.asc())
        elif filtros.ordenar_por == "precio_desc":
            query = query.order_by(Producto.precio.desc())
        elif filtros.ordenar_por == "nombre":
            query = query.order_by(Producto.nombre.asc())
        else:
            query = query.order_by(Producto.fecha_creacion.desc())
            
        # Total sin paginación (eliminando order_by en la subconsulta para mejor rendimiento)
        count_query = select(func.count()).select_from(query.order_by(None).subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0
        
        # Paginación
        skip = (filtros.pagina - 1) * filtros.limite
        query = query.offset(skip).limit(filtros.limite)
        result = await db.execute(query)
        
        return list(result.scalars().all()), total

    @staticmethod
    async def get_productos_populares(db: AsyncSession, limit: int = 10) -> List[Producto]:
        """Obtiene los productos más recientes o populares del catálogo."""
        query = (
            select(Producto)
            .options(
                selectinload(Producto.categoria),
                selectinload(Producto.temporada),
                selectinload(Producto.colecciones),
                selectinload(Producto.variantes).selectinload(VarianteProducto.talla),
                selectinload(Producto.variantes).selectinload(VarianteProducto.color)
            )
            .where(Producto.estado == EstadoProducto.ACTIVO)
            .order_by(Producto.fecha_creacion.desc())
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_productos_relacionados(db: AsyncSession, producto_id: int, limit: int = 6) -> List[Producto]:
        """Obtiene productos relacionados de la misma categoría o temporada."""
        prod = await ProductoService.get(db, producto_id)
        query = (
            select(Producto)
            .options(
                selectinload(Producto.categoria),
                selectinload(Producto.temporada),
                selectinload(Producto.proveedor),
                selectinload(Producto.colecciones),
                selectinload(Producto.variantes).selectinload(VarianteProducto.talla),
                selectinload(Producto.variantes).selectinload(VarianteProducto.color),
                selectinload(Producto.variantes).selectinload(VarianteProducto.producto).selectinload(Producto.proveedor),
            )
            .where(
                Producto.id != producto_id,
                Producto.estado == EstadoProducto.ACTIVO
            )
        )
        if prod.categoria_id:
            query = query.where(Producto.categoria_id == prod.categoria_id)
        elif prod.temporada_id:
            query = query.where(Producto.temporada_id == prod.temporada_id)
            
        query = query.order_by(Producto.fecha_creacion.desc()).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())
    
    @staticmethod
    async def update(db: AsyncSession, producto_id: int, datos: ProductoUpdate) -> Producto:
        producto = await ProductoService.get(db, producto_id)
        
        if datos.sku and datos.sku != producto.sku:
            result = await db.execute(
                select(Producto).where(Producto.sku == datos.sku)
            )
            if result.scalar_one_or_none():
                raise ConflictException("Ya existe un producto con este SKU")
        
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(producto, field, value)
        
        await db.commit()
        return await ProductoService.get(db, producto.id)
    
    @staticmethod
    async def delete(db: AsyncSession, producto_id: int):
        producto = await ProductoService.get(db, producto_id)
        
        # Verificar inventario activo
        for variante in producto.variantes:
            result = await db.execute(
                select(Inventario).where(
                    and_(
                        Inventario.variante_producto_id == variante.id,
                        Inventario.cantidad > 0
                    )
                )
            )
            if result.scalars().first():
                raise ConflictException("No se puede eliminar un producto con inventario activo")
        
        await db.delete(producto)
        await db.commit()
    
    @staticmethod
    async def agregar_variante(db: AsyncSession, producto_id: int, datos: AgregarVarianteRequest, cantidad_inicial: int = 0) -> VarianteProducto:
        """Agrega una variante (talla-color) a un producto."""
        await ProductoService.get(db, producto_id)
        
        # Verificar SKU de variante único
        result = await db.execute(
            select(VarianteProducto).where(VarianteProducto.sku_variante == datos.sku_variante)
        )
        if result.scalar_one_or_none():
            raise ConflictException("Ya existe una variante con este SKU")
        
        # Verificar que la combinación no exista
        conds = [
            VarianteProducto.producto_id == producto_id,
            VarianteProducto.color_id == datos.color_id
        ]
        if datos.talla_id is not None:
            conds.append(VarianteProducto.talla_id == datos.talla_id)
        else:
            conds.append(VarianteProducto.talla_id.is_(None))
        
        result = await db.execute(
            select(VarianteProducto).where(and_(*conds))
        )
        if result.scalar_one_or_none():
            raise ConflictException("Ya existe una variante con esta combinación de talla y color")
        
        variante = VarianteProducto(
            producto_id=producto_id,
            talla_id=datos.talla_id,
            color_id=datos.color_id,
            sku_variante=datos.sku_variante,
            precio_variante=datos.precio_variante,
            costo_variante=datos.costo_variante
        )
        db.add(variante)
        await db.flush()
        
        # Crear inventario para todas las sucursales
        result = await db.execute(select(Sucursal).where(Sucursal.estado == EstadoSucursal.ACTIVO))
        sucursales = result.scalars().all()
        
        for sucursal in sucursales:
            inventario = Inventario(
                variante_producto_id=variante.id,
                sucursal_id=sucursal.id,
                cantidad=cantidad_inicial,
                cantidad_reservada=0,
                cantidad_vendida=0,
                estado=EstadoStock.DISPONIBLE if cantidad_inicial > 0 else EstadoStock.AGOTADO
            )
            db.add(inventario)
        
        await db.commit()
        await db.refresh(variante)
        return variante


# ============================================
# Servicios de Inventario
# ============================================

class InventarioService:
    """Servicio de gestión de inventario."""
    
    @staticmethod
    async def get_inventario_global(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        sucursal_id: Optional[int] = None,
        producto_id: Optional[int] = None,
        estado: Optional[str] = None
    ) -> Tuple[List[Inventario], int]:
        query = select(Inventario).options(
            selectinload(Inventario.variante_producto).selectinload(VarianteProducto.producto).selectinload(Producto.categoria),
            selectinload(Inventario.variante_producto).selectinload(VarianteProducto.producto).selectinload(Producto.temporada),
            selectinload(Inventario.variante_producto).selectinload(VarianteProducto.producto).selectinload(Producto.proveedor),
            selectinload(Inventario.variante_producto).selectinload(VarianteProducto.talla),
            selectinload(Inventario.variante_producto).selectinload(VarianteProducto.color),
            selectinload(Inventario.sucursal)
        )
        
        if sucursal_id:
            query = query.where(Inventario.sucursal_id == sucursal_id)
        if producto_id:
            query = query.join(VarianteProducto).where(VarianteProducto.producto_id == producto_id)
        if estado:
            query = query.where(Inventario.estado == estado)
        
        # Contar total
        count_result = await db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar()
        
        # Paginación y orden (más recientes primero)
        query = query.order_by(Inventario.id.desc()).offset(skip).limit(limit)
        result = await db.execute(query)
        
        return result.scalars().all(), total
    
    @staticmethod
    async def get_inventario_por_id(db: AsyncSession, inventario_id: int) -> Optional[Inventario]:
        result = await db.execute(select(Inventario).where(Inventario.id == inventario_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_inventario_sucursal(
        db: AsyncSession,
        sucursal_id: int,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[List[Inventario], int]:
        return await InventarioService.get_inventario_global(
            db, skip, limit, sucursal_id=sucursal_id
        )
    
    @staticmethod
    async def update_cantidad(db: AsyncSession, inventario_id: int, cantidad: int, usuario_id: int, motivo: str):
        """Actualiza la cantidad de inventario y registra el movimiento."""
        result = await db.execute(select(Inventario).where(Inventario.id == inventario_id))
        inventario = result.scalar_one_or_none()
        
        if not inventario:
            raise NotFoundException(f"Inventario con ID {inventario_id} no encontrado")
        
        cantidad_anterior = inventario.cantidad
        inventario.cantidad = cantidad
        
        # Determinar estado
        if cantidad <= 0:
            inventario.estado = EstadoStock.AGOTADO
        elif cantidad <= inventario.stock_minimo:
            inventario.estado = EstadoStock.PROXIMO_A_INGRESAR
        else:
            inventario.estado = EstadoStock.DISPONIBLE
        
        # Registrar movimiento
        movimiento = MovimientoInventario(
            inventario_id=inventario_id,
            tipo=TipoMovimiento.AJUSTE,
            cantidad=cantidad - cantidad_anterior,
            motivo=motivo,
            usuario_id=usuario_id
        )
        db.add(movimiento)
        await db.commit()
        await db.refresh(inventario)
        
        return inventario
    
    @staticmethod
    async def registrar_movimiento(db: AsyncSession, datos: MovimientoInventarioCreate, usuario_id: int) -> MovimientoInventario:
        """Registra un movimiento de inventario."""
        result = await db.execute(select(Inventario).where(Inventario.id == datos.inventario_id))
        inventario = result.scalar_one_or_none()
        
        if not inventario:
            raise NotFoundException(f"Inventario con ID {datos.inventario_id} no encontrado")
        
        # Actualizar inventario según tipo de movimiento
        if datos.tipo == TipoMovimiento.RECEPCION:
            inventario.cantidad += datos.cantidad
            if inventario.cantidad > 0:
                inventario.estado = EstadoStock.DISPONIBLE
        elif datos.tipo == TipoMovimiento.VENTA:
            if inventario.cantidad_disponible < datos.cantidad:
                raise InventoryException("Stock insuficiente para la venta")
            inventario.cantidad -= datos.cantidad
            inventario.cantidad_vendida += datos.cantidad
        elif datos.tipo == TipoMovimiento.RESERVA:
            if inventario.cantidad_disponible < datos.cantidad:
                raise InventoryException("Stock insuficiente para la reserva")
            inventario.cantidad_reservada += datos.cantidad
        elif datos.tipo == TipoMovimiento.CANCELACION_RESERVA:
            inventario.cantidad_reservada -= datos.cantidad
        elif datos.tipo == TipoMovimiento.DEVOLUCION:
            inventario.cantidad += datos.cantidad
        elif datos.tipo == TipoMovimiento.AJUSTE:
            inventario.cantidad += datos.cantidad
        
        # Actualizar estado
        if inventario.cantidad <= 0:
            inventario.estado = EstadoStock.AGOTADO
        elif inventario.cantidad <= inventario.stock_minimo:
            inventario.estado = EstadoStock.PROXIMO_A_INGRESAR
        else:
            inventario.estado = EstadoStock.DISPONIBLE
        
        # Crear movimiento
        movimiento = MovimientoInventario(
            inventario_id=datos.inventario_id,
            tipo=datos.tipo,
            cantidad=datos.cantidad,
            motivo=datos.motivo,
            usuario_id=usuario_id
        )
        db.add(movimiento)
        await db.commit()
        await db.refresh(movimiento)
        
        return movimiento
    
    @staticmethod
    async def get_movimientos(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        inventario_id: Optional[int] = None,
        sucursal_id: Optional[int] = None,
        tipo: Optional[TipoMovimiento] = None,
        fecha_inicio: Optional[datetime] = None,
        fecha_fin: Optional[datetime] = None
    ) -> Tuple[List[MovimientoInventario], int]:
        query = select(MovimientoInventario).options(
            selectinload(MovimientoInventario.inventario)
        ).order_by(MovimientoInventario.fecha_hora.desc())
        
        if sucursal_id:
            query = query.join(MovimientoInventario.inventario).where(Inventario.sucursal_id == sucursal_id)
        if inventario_id:
            query = query.where(MovimientoInventario.inventario_id == inventario_id)
        if tipo:
            query = query.where(MovimientoInventario.tipo == tipo)
        if fecha_inicio:
            query = query.where(MovimientoInventario.fecha_hora >= fecha_inicio)
        if fecha_fin:
            query = query.where(MovimientoInventario.fecha_hora <= fecha_fin)
        
        # Contar
        count_result = await db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar()
        
        # Paginación
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        
        return result.scalars().all(), total
    
    @staticmethod
    async def get_alertas_stock(db: AsyncSession) -> List[Inventario]:
        """Obtiene inventario con stock bajo el mínimo."""
        result = await db.execute(
            select(Inventario)
            .options(
                selectinload(Inventario.variante_producto).selectinload(VarianteProducto.producto).selectinload(Producto.categoria),
                selectinload(Inventario.variante_producto).selectinload(VarianteProducto.producto).selectinload(Producto.temporada),
                selectinload(Inventario.variante_producto).selectinload(VarianteProducto.producto).selectinload(Producto.proveedor),
                selectinload(Inventario.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(Inventario.variante_producto).selectinload(VarianteProducto.color),
                selectinload(Inventario.sucursal)
            )
            .where(
                and_(
                    Inventario.cantidad <= Inventario.stock_minimo,
                    Inventario.cantidad > 0
                )
            )
        )
        return result.scalars().all()


# ============================================
# Servicios de Disponibilidad
# ============================================

class DisponibilidadService:
    """Servicio de consulta de disponibilidad por sucursal."""
    
    @staticmethod
    async def get_disponibilidad_producto(
        db: AsyncSession,
        producto_id: int,
        talla_id: Optional[int] = None,
        color_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Obtiene la disponibilidad de un producto por sucursal.

        Devuelve la estructura ``DisponibilidadProductoResponse`` que el frontend
        espera (producto + lista ``disponibilidad``), con el stock agregado por
        sucursal (sumando las cantidades de todas las variantes del producto).
        """
        producto = await ProductoService.get(db, producto_id)
        
        # Construir query para variantes
        query = select(VarianteProducto).options(
            selectinload(VarianteProducto.inventarios).selectinload(Inventario.sucursal),
            selectinload(VarianteProducto.talla),
            selectinload(VarianteProducto.color)
        ).where(VarianteProducto.producto_id == producto_id)
        
        if talla_id:
            query = query.where(VarianteProducto.talla_id == talla_id)
        if color_id:
            query = query.where(VarianteProducto.color_id == color_id)
        
        result = await db.execute(query)
        variantes = result.scalars().all()
        
        # Agregar stock por sucursal (una fila por sucursal, sumando variantes)
        sucursal_map: Dict[int, Dict[str, Any]] = {}
        for variante in variantes:
            for inventario in variante.inventarios:
                if inventario.sucursal is None:
                    continue
                if inventario.sucursal_id not in sucursal_map:
                    sucursal_map[inventario.sucursal_id] = {
                        "sucursal_id": inventario.sucursal_id,
                        "sucursal_nombre": inventario.sucursal.nombre,
                        "cantidad_disponible": int(inventario.cantidad_disponible),
                        "cantidad_reservada": int(inventario.cantidad_reservada),
                        "estado": EstadoStock.DISPONIBLE,
                        "latitud": getattr(inventario.sucursal, "latitud", None),
                        "longitud": getattr(inventario.sucursal, "longitud", None),
                        "direccion": getattr(inventario.sucursal, "direccion", None),
                        "horario_atencion": getattr(inventario.sucursal, "horario_atencion", None)
                    }
                else:
                    entry = sucursal_map[inventario.sucursal_id]
                    entry["cantidad_disponible"] += int(inventario.cantidad_disponible)
                    entry["cantidad_reservada"] += int(inventario.cantidad_reservada)
        
        # Derivar estado agregado por sucursal
        for entry in sucursal_map.values():
            if entry["cantidad_disponible"] > 0:
                entry["estado"] = EstadoStock.DISPONIBLE
            elif entry["cantidad_reservada"] > 0:
                entry["estado"] = EstadoStock.RESERVADO
            else:
                entry["estado"] = EstadoStock.AGOTADO
        
        disponibilidad = sorted(
            sucursal_map.values(),
            key=lambda x: x["sucursal_nombre"].lower()
        )
        
        # Valores de talla/color cuando se filtra por una combinación específica
        talla_valor = None
        color_nombre = None
        if talla_id and variantes and variantes[0].talla:
            talla_valor = variantes[0].talla.valor
        if color_id and variantes and variantes[0].color:
            color_nombre = variantes[0].color.nombre
        
        return {
            "producto_id": producto.id,
            "producto_nombre": producto.nombre,
            "sku": producto.sku,
            "talla": talla_valor,
            "color": color_nombre,
            "disponibilidad": disponibilidad
        }
    
    @staticmethod
    async def verificar_stock_reserva(
        db: AsyncSession,
        variante_id: int,
        sucursal_id: int,
        cantidad: int
    ) -> bool:
        """Verifica si hay stock suficiente para una reserva."""
        result = await db.execute(
            select(Inventario).where(
                and_(
                    Inventario.variante_producto_id == variante_id,
                    Inventario.sucursal_id == sucursal_id
                )
            )
        )
        inventario = result.scalar_one_or_none()
        
        if not inventario:
            return False
        
        return inventario.cantidad_disponible >= cantidad

    @staticmethod
    async def get_disponibilidad_variante(
        db: AsyncSession,
        variante_id: int
    ) -> Dict[str, Any]:
        """Obtiene la disponibilidad de una variante en todas las sucursales."""
        q_var = (
            select(VarianteProducto)
            .options(
                selectinload(VarianteProducto.inventarios).selectinload(Inventario.sucursal)
            )
            .where(VarianteProducto.id == variante_id)
        )
        res = await db.execute(q_var)
        variante = res.scalar_one_or_none()
        if not variante:
            raise NotFoundException(f"Variante #{variante_id} no encontrada")
            
        sucursales_info = []
        for inv in variante.inventarios:
            sucursales_info.append({
                "sucursal_id": inv.sucursal_id,
                "sucursal_nombre": inv.sucursal.nombre if inv.sucursal else "Sucursal",
                "cantidad_disponible": inv.cantidad_disponible,
                "cantidad_reservada": inv.cantidad_reservada,
                "estado": inv.estado,
                "latitud": getattr(inv.sucursal, "latitud", None) if inv.sucursal else None,
                "longitud": getattr(inv.sucursal, "longitud", None) if inv.sucursal else None,
                "direccion": getattr(inv.sucursal, "direccion", None) if inv.sucursal else None,
                "horario_atencion": getattr(inv.sucursal, "horario_atencion", None) if inv.sucursal else None
            })
            
        return {
            "variante_id": variante.id,
            "sku_variante": variante.sku_variante,
            "sucursales": sucursales_info
        }

    @staticmethod
    async def get_stock_por_sucursal(
        db: AsyncSession,
        producto_id: int,
        sucursal_id: int
    ) -> List[Dict[str, Any]]:
        """Obtiene el stock de todas las variantes de un producto en una sucursal específica."""
        await ProductoService.get(db, producto_id)
        
        query = (
            select(Inventario)
            .options(
                selectinload(Inventario.variante_producto).selectinload(VarianteProducto.talla),
                selectinload(Inventario.variante_producto).selectinload(VarianteProducto.color),
                selectinload(Inventario.sucursal)
            )
            .join(VarianteProducto, Inventario.variante_producto_id == VarianteProducto.id)
            .where(
                VarianteProducto.producto_id == producto_id,
                Inventario.sucursal_id == sucursal_id
            )
        )
        result = await db.execute(query)
        inventarios = result.scalars().all()
        
        items = []
        for inv in inventarios:
            items.append({
                "variante_id": inv.variante_producto_id,
                "sku_variante": inv.variante_producto.sku_variante if inv.variante_producto else "",
                "talla": inv.variante_producto.talla.valor if inv.variante_producto and inv.variante_producto.talla else None,
                "color": inv.variante_producto.color.nombre if inv.variante_producto and inv.variante_producto.color else None,
                "cantidad_disponible": inv.cantidad_disponible,
                "cantidad_reservada": inv.cantidad_reservada,
                "estado": inv.estado
            })
        return items


# ============================================
# Datos Iniciales
# ============================================

class DatosInicialesCatalogoService:
    """Servicio para crear datos iniciales del catálogo."""
    
    @staticmethod
    async def crear_datos_iniciales(db: AsyncSession):
        """Crea datos iniciales: categorías, tallas, colores, etc."""
        
        # Crear categorías
        categorias_data = [
            ("Poleras", "Poleras y camisetas para hombre y mujer"),
            ("Camisas", "Camisas de manga corta y larga"),
            ("Pantalones", "Pantalones de diferentes estilos"),
            ("Vestidos", "Vestidos formales y casuales"),
            ("Abrigos", "Chaquetas, abrigos y sweaters"),
            ("Calzado", "Zapatos, botas y sandalias"),
            ("Accesorios", "Cinturones, bufandas, gorros"),
        ]
        
        for nombre, desc in categorias_data:
            result = await db.execute(select(Categoria).where(Categoria.nombre == nombre))
            if not result.scalar_one_or_none():
                categoria = Categoria(nombre=nombre, descripcion=desc)
                db.add(categoria)
        
        # Crear tallas
        tallas_data = [
            ("XS", "ropa", "Extra Small"),
            ("S", "ropa", "Small"),
            ("M", "ropa", "Medium"),
            ("L", "ropa", "Large"),
            ("XL", "ropa", "Extra Large"),
            ("XXL", "ropa", "Extra Extra Large"),
            ("28", "calzado", "Talla 28"),
            ("29", "calzado", "Talla 29"),
            ("30", "calzado", "Talla 30"),
            ("31", "calzado", "Talla 31"),
            ("32", "calzado", "Talla 32"),
            ("33", "calzado", "Talla 33"),
            ("34", "calzado", "Talla 34"),
            ("35", "calzado", "Talla 35"),
            ("36", "calzado", "Talla 36"),
        ]
        
        for valor, tipo, desc in tallas_data:
            result = await db.execute(select(Talla).where(Talla.valor == valor))
            if not result.scalar_one_or_none():
                talla = Talla(valor=valor, tipo=tipo, descripcion=desc)
                db.add(talla)
        
        # Crear colores
        colores_data = [
            ("Negro", "#000000"),
            ("Blanco", "#FFFFFF"),
            ("Gris", "#808080"),
            ("Azul", "#0000FF"),
            ("Rojo", "#FF0000"),
            ("Verde", "#008000"),
            ("Amarillo", "#FFFF00"),
            ("Naranja", "#FFA500"),
            ("Café", "#8B4513"),
            ("Beige", "#F5F5DC"),
            ("Rosa", "#FFC0CB"),
            ("Morado", "#800080"),
            ("Celeste", "#87CEEB"),
            ("Verde Claro", "#4fff4d"),
        ]
        
        for nombre, hex_code in colores_data:
            result = await db.execute(select(Color).where(Color.nombre == nombre))
            if not result.scalar_one_or_none():
                color = Color(nombre=nombre, codigo_hex=hex_code)
                db.add(color)
        
        await db.commit()


# ============================================
# CU25: Favoritos
# ============================================

class FavoritoService:
    """Servicio de lógica de negocio para productos favoritos (CU25)."""

    @classmethod
    async def agregar(cls, db: AsyncSession, cliente_id: int, producto_id: int) -> dict:
        q_prod = select(Producto).where(Producto.id == producto_id)
        res_prod = await db.execute(q_prod)
        if not res_prod.scalar_one_or_none():
            raise NotFoundException(f"Producto con ID {producto_id} no encontrado")

        q_fav = select(ProductoFavorito).where(
            ProductoFavorito.cliente_id == cliente_id,
            ProductoFavorito.producto_id == producto_id
        )
        res_fav = await db.execute(q_fav)
        fav = res_fav.scalar_one_or_none()
        if not fav:
            fav = ProductoFavorito(cliente_id=cliente_id, producto_id=producto_id)
            db.add(fav)
            await db.commit()
            await db.refresh(fav)
        return {"mensaje": "Producto agregado a favoritos", "producto_id": producto_id, "favorito_id": fav.id}

    @classmethod
    async def quitar(cls, db: AsyncSession, cliente_id: int, producto_id: int) -> dict:
        q_prod = select(Producto).where(Producto.id == producto_id)
        res_prod = await db.execute(q_prod)
        if not res_prod.scalar_one_or_none():
            raise NotFoundException(f"Producto con ID {producto_id} no encontrado")

        q_fav = select(ProductoFavorito).where(
            ProductoFavorito.cliente_id == cliente_id,
            ProductoFavorito.producto_id == producto_id
        )
        res_fav = await db.execute(q_fav)
        fav = res_fav.scalar_one_or_none()
        if fav:
            await db.delete(fav)
            await db.commit()
        return {"mensaje": "Producto eliminado de favoritos", "producto_id": producto_id}

    @classmethod
    async def listar(cls, db: AsyncSession, cliente_id: int, skip: int = 0, limit: int = 100) -> List[FavoritoResponse]:
        stmt = (
            select(ProductoFavorito)
            .options(
                selectinload(ProductoFavorito.producto).selectinload(Producto.variantes).selectinload(VarianteProducto.inventarios)
            )
            .where(ProductoFavorito.cliente_id == cliente_id)
            .order_by(ProductoFavorito.fecha_agregado.desc())
            .offset(skip)
            .limit(limit)
        )
        res = await db.execute(stmt)
        favoritos = res.scalars().all()

        resultado: List[FavoritoResponse] = []
        for f in favoritos:
            p = f.producto
            if not p:
                continue
            stock_total = 0
            if p.variantes:
                for v in p.variantes:
                    if v.inventarios:
                        stock_total += sum(inv.cantidad_disponible for inv in v.inventarios)
            disponible = (p.estado == EstadoProducto.ACTIVO and stock_total > 0)

            p_resumen = ProductoResumenResponse(
                id=p.id,
                sku=p.sku,
                nombre=p.nombre,
                descripcion=p.descripcion,
                precio=p.precio,
                costo_compra=p.costo_compra,
                imagenes=p.imagenes or [],
                estado=p.estado,
                genero=p.genero,
                promedio_valoracion=p.promedio_valoracion or Decimal("0.00"),
                total_valoraciones=p.total_valoraciones or 0,
                fecha_creacion=p.fecha_creacion,
                categoria_id=p.categoria_id,
                temporada_id=p.temporada_id,
                proveedor_id=p.proveedor_id
            )

            resultado.append(
                FavoritoResponse(
                    id=f.id,
                    cliente_id=f.cliente_id,
                    producto_id=f.producto_id,
                    producto=p_resumen,
                    disponible=disponible,
                    fecha_agregado=f.fecha_agregado
                )
            )
        return resultado

    @classmethod
    async def listar_ids(cls, db: AsyncSession, cliente_id: int) -> List[int]:
        stmt = select(ProductoFavorito.producto_id).where(ProductoFavorito.cliente_id == cliente_id)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def mover_al_carrito(cls, db: AsyncSession, cliente_id: int, producto_id: int, datos: MoverFavoritoCarritoRequest) -> dict:
        from app.apps.gestion_ventas.services import CarritoService
        from app.apps.gestion_ventas.schemas import ItemCarritoCreate

        q_var = select(VarianteProducto).where(
            VarianteProducto.id == datos.variante_producto_id,
            VarianteProducto.producto_id == producto_id
        )
        res_var = await db.execute(q_var)
        if not res_var.scalar_one_or_none():
            raise BadRequestException(f"La variante ID {datos.variante_producto_id} no pertenece al producto ID {producto_id}")

        await CarritoService.agregar_item(
            db=db,
            cliente_id=cliente_id,
            item_in=ItemCarritoCreate(
                variante_producto_id=datos.variante_producto_id,
                cantidad=datos.cantidad
            )
        )

        if datos.quitar_de_favoritos:
            q_fav = select(ProductoFavorito).where(
                ProductoFavorito.cliente_id == cliente_id,
                ProductoFavorito.producto_id == producto_id
            )
            res_fav = await db.execute(q_fav)
            fav = res_fav.scalar_one_or_none()
            if fav:
                await db.delete(fav)
                await db.commit()

        return {
            "mensaje": "Producto movido al carrito exitosamente",
            "producto_id": producto_id,
            "variante_producto_id": datos.variante_producto_id,
            "cantidad": datos.cantidad
        }


# ============================================
# CU26: Valoraciones
# ============================================

class ValoracionService:
    """Servicio de lógica de negocio para valoraciones de productos (CU26)."""

    @classmethod
    def _formatear_nombre_cliente(cls, cliente: Any) -> str:
        if not cliente or not getattr(cliente, "usuario", None):
            return "Cliente Anónimo"
        nombre = cliente.usuario.nombre or ""
        apellido = cliente.usuario.apellido or ""
        if apellido:
            return f"{nombre} {apellido[0]}."
        return nombre

    @classmethod
    def _contiene_palabras_prohibidas(cls, texto: Optional[str]) -> bool:
        if not texto:
            return False
        texto_lower = texto.lower()
        prohibidas = [p.strip().lower() for p in settings.PALABRAS_PROHIBIDAS.split(",") if p.strip()]
        return any(p in texto_lower for p in prohibidas)

    @classmethod
    async def puede_valorar(cls, db: AsyncSession, cliente_id: int, producto_id: int) -> PuedeValorarResponse:
        from app.apps.gestion_ventas.models import Orden, DetalleOrden, EstadoOrden, Reserva, DetalleReserva, EstadoDetalleReserva
        from app.apps.gestion_usuarios.models import Cliente

        q_prod = select(Producto).where(Producto.id == producto_id)
        res_prod = await db.execute(q_prod)
        if not res_prod.scalar_one_or_none():
            raise NotFoundException(f"Producto con ID {producto_id} no encontrado")

        q_val = (
            select(ValoracionProducto)
            .options(joinedload(ValoracionProducto.cliente).joinedload(Cliente.usuario))
            .where(
                ValoracionProducto.cliente_id == cliente_id,
                ValoracionProducto.producto_id == producto_id
            )
        )
        res_val = await db.execute(q_val)
        val_existente = res_val.scalar_one_or_none()

        val_response = None
        if val_existente:
            val_response = ValoracionResponse(
                id=val_existente.id,
                producto_id=val_existente.producto_id,
                cliente_id=val_existente.cliente_id,
                cliente_nombre=cls._formatear_nombre_cliente(val_existente.cliente),
                puntuacion=val_existente.puntuacion,
                comentario=val_existente.comentario,
                estado=val_existente.estado,
                fecha_creacion=val_existente.fecha_creacion,
                fecha_actualizacion=val_existente.fecha_actualizacion
            )

        # 1. Buscar en órdenes pagadas o entregadas
        q_orden = (
            select(func.count(DetalleOrden.id))
            .join(Orden, Orden.id == DetalleOrden.orden_id)
            .join(VarianteProducto, VarianteProducto.id == DetalleOrden.variante_producto_id)
            .where(
                Orden.cliente_id == cliente_id,
                Orden.estado.in_([EstadoOrden.PAGADO, EstadoOrden.EN_PROCESO, EstadoOrden.ENVIADO, EstadoOrden.ENTREGADO]),
                VarianteProducto.producto_id == producto_id
            )
        )
        res_orden = await db.execute(q_orden)
        tiene_compra_orden = (res_orden.scalar() or 0) > 0

        # 2. Buscar en reservas compradas
        tiene_compra_reserva = False
        if not tiene_compra_orden:
            q_reserva = (
                select(func.count(DetalleReserva.id))
                .join(Reserva, Reserva.id == DetalleReserva.reserva_id)
                .join(VarianteProducto, VarianteProducto.id == DetalleReserva.variante_producto_id)
                .where(
                    Reserva.cliente_id == cliente_id,
                    DetalleReserva.estado == EstadoDetalleReserva.COMPRADO,
                    VarianteProducto.producto_id == producto_id
                )
            )
            res_reserva = await db.execute(q_reserva)
            tiene_compra_reserva = (res_reserva.scalar() or 0) > 0

        ha_comprado = tiene_compra_orden or tiene_compra_reserva
        if ha_comprado:
            return PuedeValorarResponse(
                puede_valorar=True,
                motivo=None,
                valoracion_existente=val_response
            )
        else:
            return PuedeValorarResponse(
                puede_valorar=False,
                motivo="Solo los clientes que hayan adquirido este producto pueden valorarlo.",
                valoracion_existente=val_response
            )

    @classmethod
    async def recalcular_promedio(cls, db: AsyncSession, producto_id: int):
        q_stats = (
            select(
                func.count(ValoracionProducto.id).label("total"),
                func.avg(ValoracionProducto.puntuacion).label("promedio")
            )
            .where(
                ValoracionProducto.producto_id == producto_id,
                ValoracionProducto.estado == EstadoValoracion.PUBLICADA
            )
        )
        res_stats = await db.execute(q_stats)
        row = res_stats.first()
        total = row.total if row else 0
        promedio = round(Decimal(str(row.promedio)), 2) if row and row.promedio is not None else Decimal("0.00")

        q_prod = select(Producto).where(Producto.id == producto_id)
        res_prod = await db.execute(q_prod)
        prod = res_prod.scalar_one_or_none()
        if prod:
            prod.total_valoraciones = total
            prod.promedio_valoracion = promedio
            await db.commit()

    @classmethod
    async def crear(cls, db: AsyncSession, cliente_id: int, producto_id: int, datos: ValoracionCreate) -> ValoracionResponse:
        from app.apps.gestion_usuarios.models import Cliente

        puede = await cls.puede_valorar(db, cliente_id, producto_id)
        if not puede.puede_valorar:
            raise ForbiddenException(puede.motivo or "No cumple los requisitos para valorar este producto")

        if puede.valoracion_existente:
            raise ConflictException("Ya has valorado este producto. Puedes editar tu valoración existente mediante el método PUT.")

        estado = EstadoValoracion.PENDIENTE_MODERACION if cls._contiene_palabras_prohibidas(datos.comentario) else EstadoValoracion.PUBLICADA

        valoracion = ValoracionProducto(
            cliente_id=cliente_id,
            producto_id=producto_id,
            puntuacion=datos.puntuacion,
            comentario=datos.comentario,
            estado=estado
        )
        db.add(valoracion)
        await db.commit()
        await db.refresh(valoracion)

        if estado == EstadoValoracion.PUBLICADA:
            await cls.recalcular_promedio(db, producto_id)

        stmt = (
            select(ValoracionProducto)
            .options(joinedload(ValoracionProducto.cliente).joinedload(Cliente.usuario))
            .where(ValoracionProducto.id == valoracion.id)
        )
        res = await db.execute(stmt)
        val_cargada = res.scalar_one()

        return ValoracionResponse(
            id=val_cargada.id,
            producto_id=val_cargada.producto_id,
            cliente_id=val_cargada.cliente_id,
            cliente_nombre=cls._formatear_nombre_cliente(val_cargada.cliente),
            puntuacion=val_cargada.puntuacion,
            comentario=val_cargada.comentario,
            estado=val_cargada.estado,
            fecha_creacion=val_cargada.fecha_creacion,
            fecha_actualizacion=val_cargada.fecha_actualizacion
        )

    @classmethod
    async def actualizar(cls, db: AsyncSession, valoracion_id: int, cliente_id: int, datos: ValoracionUpdate) -> ValoracionResponse:
        from app.apps.gestion_usuarios.models import Cliente

        stmt = (
            select(ValoracionProducto)
            .options(joinedload(ValoracionProducto.cliente).joinedload(Cliente.usuario))
            .where(ValoracionProducto.id == valoracion_id)
        )
        res = await db.execute(stmt)
        val = res.scalar_one_or_none()
        if not val:
            raise NotFoundException(f"Valoración con ID {valoracion_id} no encontrada")

        if val.cliente_id != cliente_id:
            raise ForbiddenException("No tienes permiso para modificar una valoración que no te pertenece")

        if datos.puntuacion is not None:
            val.puntuacion = datos.puntuacion

        if datos.comentario is not None:
            val.comentario = datos.comentario
            if cls._contiene_palabras_prohibidas(datos.comentario):
                val.estado = EstadoValoracion.PENDIENTE_MODERACION
            else:
                val.estado = EstadoValoracion.PUBLICADA

        await db.commit()
        await db.refresh(val)

        await cls.recalcular_promedio(db, val.producto_id)

        return ValoracionResponse(
            id=val.id,
            producto_id=val.producto_id,
            cliente_id=val.cliente_id,
            cliente_nombre=cls._formatear_nombre_cliente(val.cliente),
            puntuacion=val.puntuacion,
            comentario=val.comentario,
            estado=val.estado,
            fecha_creacion=val.fecha_creacion,
            fecha_actualizacion=val.fecha_actualizacion
        )

    @classmethod
    async def listar_por_producto(cls, db: AsyncSession, producto_id: int, skip: int = 0, limit: int = 50) -> List[ValoracionResponse]:
        from app.apps.gestion_usuarios.models import Cliente

        stmt = (
            select(ValoracionProducto)
            .options(joinedload(ValoracionProducto.cliente).joinedload(Cliente.usuario))
            .where(
                ValoracionProducto.producto_id == producto_id,
                ValoracionProducto.estado == EstadoValoracion.PUBLICADA
            )
            .order_by(ValoracionProducto.fecha_creacion.desc())
            .offset(skip)
            .limit(limit)
        )
        res = await db.execute(stmt)
        valoraciones = res.scalars().all()
        return [
            ValoracionResponse(
                id=v.id,
                producto_id=v.producto_id,
                cliente_id=v.cliente_id,
                cliente_nombre=cls._formatear_nombre_cliente(v.cliente),
                puntuacion=v.puntuacion,
                comentario=v.comentario,
                estado=v.estado,
                fecha_creacion=v.fecha_creacion,
                fecha_actualizacion=v.fecha_actualizacion
            )
            for v in valoraciones
        ]

    @classmethod
    async def obtener_mi_valoracion(cls, db: AsyncSession, cliente_id: int, producto_id: int) -> Optional[ValoracionResponse]:
        from app.apps.gestion_usuarios.models import Cliente

        stmt = (
            select(ValoracionProducto)
            .options(joinedload(ValoracionProducto.cliente).joinedload(Cliente.usuario))
            .where(
                ValoracionProducto.cliente_id == cliente_id,
                ValoracionProducto.producto_id == producto_id
            )
        )
        res = await db.execute(stmt)
        val = res.scalar_one_or_none()
        if not val:
            return None
        return ValoracionResponse(
            id=val.id,
            producto_id=val.producto_id,
            cliente_id=val.cliente_id,
            cliente_nombre=cls._formatear_nombre_cliente(val.cliente),
            puntuacion=val.puntuacion,
            comentario=val.comentario,
            estado=val.estado,
            fecha_creacion=val.fecha_creacion,
            fecha_actualizacion=val.fecha_actualizacion
        )


# ============================================
# Servicio de Recepción por Proveedor (Opción A)
# ============================================

class RecepcionService:
    """Registra ingresos de mercadería vinculados a un proveedor y sucursal."""

    @staticmethod
    def _actualizar_estado(inv: Inventario):
        if inv.cantidad <= 0:
            inv.estado = EstadoStock.AGOTADO
        elif inv.cantidad <= (inv.stock_minimo or 5):
            inv.estado = EstadoStock.PROXIMO_A_INGRESAR
        else:
            inv.estado = EstadoStock.DISPONIBLE

    @staticmethod
    async def _generar_numero(db: AsyncSession) -> str:
        from datetime import datetime as _dt
        year = _dt.now().year
        count_res = await db.execute(select(func.count()).select_from(Recepcion))
        correlativo = (count_res.scalar() or 0) + 1
        return f"REC-{year}-{correlativo:04d}"

    @staticmethod
    def _to_response(rec: Recepcion) -> RecepcionResponse:
        detalles = []
        for d in (rec.detalles or []):
            var = getattr(d, "variante_producto", None)
            prod = getattr(var, "producto", None) if var else None
            detalles.append(RecepcionDetalleItemResponse(
                id=d.id,
                variante_producto_id=d.variante_producto_id,
                cantidad=d.cantidad,
                costo_unitario=d.costo_unitario,
                sku_variante=getattr(var, "sku_variante", None),
                producto_nombre=getattr(prod, "nombre", None),
            ))
        return RecepcionResponse(
            id=rec.id,
            numero=rec.numero,
            proveedor_id=rec.proveedor_id,
            sucursal_id=rec.sucursal_id,
            fecha_hora=rec.fecha_hora,
            nro_factura=rec.nro_factura,
            observaciones=rec.observaciones,
            total_unidades=rec.total_unidades or 0,
            total_costo=rec.total_costo or Decimal("0"),
            proveedor_nombre=getattr(getattr(rec, "proveedor", None), "nombre", None),
            sucursal_nombre=getattr(getattr(rec, "sucursal", None), "nombre", None),
            detalles=detalles,
        )

    @staticmethod
    async def create(db: AsyncSession, datos: RecepcionCreate, usuario_id: Optional[int] = None) -> RecepcionResponse:
        if not datos.items:
            raise ValidationException("La recepción debe incluir al menos un item")

        prov_res = await db.execute(select(Proveedor).where(Proveedor.id == datos.proveedor_id))
        proveedor = prov_res.scalar_one_or_none()
        if not proveedor:
            raise NotFoundException(f"Proveedor con ID {datos.proveedor_id} no encontrado")

        suc_res = await db.execute(select(Sucursal).where(Sucursal.id == datos.sucursal_id))
        sucursal = suc_res.scalar_one_or_none()
        if not sucursal:
            raise NotFoundException(f"Sucursal con ID {datos.sucursal_id} no encontrada")

        variante_ids = [i.variante_producto_id for i in datos.items]
        var_res = await db.execute(
            select(VarianteProducto).options(selectinload(VarianteProducto.producto)).where(VarianteProducto.id.in_(variante_ids))
        )
        variantes = {v.id: v for v in var_res.scalars().all()}
        faltantes = [vid for vid in variante_ids if vid not in variantes]
        if faltantes:
            raise NotFoundException(f"Variantes no encontradas: {faltantes}")

        numero = await RecepcionService._generar_numero(db)
        total_unidades = sum(i.cantidad for i in datos.items)
        total_costo = sum((i.costo_unitario or Decimal("0")) * i.cantidad for i in datos.items)

        try:
            recepcion = Recepcion(
                numero=numero,
                proveedor_id=datos.proveedor_id,
                sucursal_id=datos.sucursal_id,
                nro_factura=datos.nro_factura,
                observaciones=datos.observaciones,
                total_unidades=total_unidades,
                total_costo=total_costo,
                creado_por=usuario_id,
            )
            db.add(recepcion)
            await db.flush()

            for item in datos.items:
                variante = variantes[item.variante_producto_id]

                inv_res = await db.execute(select(Inventario).where(
                    Inventario.variante_producto_id == item.variante_producto_id,
                    Inventario.sucursal_id == datos.sucursal_id,
                ))
                inventario = inv_res.scalar_one_or_none()
                if not inventario:
                    inventario = Inventario(
                        variante_producto_id=item.variante_producto_id,
                        sucursal_id=datos.sucursal_id,
                        cantidad=0,
                        cantidad_reservada=0,
                        cantidad_vendida=0,
                        stock_minimo=5,
                        estado=EstadoStock.DISPONIBLE,
                    )
                    db.add(inventario)
                    await db.flush()

                inventario.cantidad = (inventario.cantidad or 0) + item.cantidad
                RecepcionService._actualizar_estado(inventario)

                if item.costo_unitario is not None:
                    variante.costo_variante = item.costo_unitario

                db.add(MovimientoInventario(
                    inventario_id=inventario.id,
                    tipo=TipoMovimiento.RECEPCION,
                    cantidad=item.cantidad,
                    motivo=f"Recepción {numero} prov.{proveedor.nombre} fact.{datos.nro_factura or 'S/N'}",
                    usuario_id=usuario_id,
                ))
                db.add(DetalleRecepcion(
                    recepcion_id=recepcion.id,
                    variante_producto_id=item.variante_producto_id,
                    cantidad=item.cantidad,
                    costo_unitario=item.costo_unitario,
                ))

            await db.commit()
        except (NotFoundException, ValidationException, ConflictException):
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            raise BadRequestException(f"No se pudo registrar la recepción: {e}")

        res = await db.execute(
            select(Recepcion)
            .options(
                selectinload(Recepcion.proveedor),
                selectinload(Recepcion.sucursal),
                selectinload(Recepcion.detalles).selectinload(DetalleRecepcion.variante_producto).selectinload(VarianteProducto.producto),
            )
            .where(Recepcion.id == recepcion.id)
        )
        rec_full = res.scalar_one()
        return RecepcionService._to_response(rec_full)

    @staticmethod
    async def get(db: AsyncSession, recepcion_id: int) -> RecepcionResponse:
        res = await db.execute(
            select(Recepcion)
            .options(
                selectinload(Recepcion.proveedor),
                selectinload(Recepcion.sucursal),
                selectinload(Recepcion.detalles).selectinload(DetalleRecepcion.variante_producto).selectinload(VarianteProducto.producto),
            )
            .where(Recepcion.id == recepcion_id)
        )
        rec = res.scalar_one_or_none()
        if not rec:
            raise NotFoundException(f"Recepción con ID {recepcion_id} no encontrada")
        return RecepcionService._to_response(rec)

    @staticmethod
    async def list(db: AsyncSession, skip: int = 0, limit: int = 100, proveedor_id: Optional[int] = None, sucursal_id: Optional[int] = None) -> List[RecepcionResponse]:
        query = (
            select(Recepcion)
            .options(
                selectinload(Recepcion.proveedor),
                selectinload(Recepcion.sucursal),
                selectinload(Recepcion.detalles).selectinload(DetalleRecepcion.variante_producto).selectinload(VarianteProducto.producto),
            )
            .order_by(Recepcion.id.desc())
            .offset(skip)
            .limit(limit)
        )
        if proveedor_id:
            query = query.where(Recepcion.proveedor_id == proveedor_id)
        if sucursal_id:
            query = query.where(Recepcion.sucursal_id == sucursal_id)
        res = await db.execute(query)
        recs = res.scalars().all()
        return [RecepcionService._to_response(r) for r in recs]