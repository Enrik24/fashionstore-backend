"""
Servicios de negocio para Gestión de Catálogo, Productos e Inventario.
"""
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, exists
from sqlalchemy.orm import selectinload

from app.apps.gestion_catalogo.models import (
    Ciudad, Sucursal, Categoria, Talla, Color, Temporada, Coleccion,
    Proveedor, Producto, VarianteProducto, Inventario, MovimientoInventario,
    EstadoStock, TipoMovimiento, EstadoSucursal, EstadoProducto
)
from app.apps.gestion_catalogo.schemas import (
    CiudadCreate, CiudadUpdate, SucursalCreate, SucursalUpdate,
    CategoriaCreate, CategoriaUpdate, TallaCreate, TallaResponse, ColorCreate, ColorResponse,
    TemporadaCreate, TemporadaUpdate,
    ColeccionCreate, ColeccionUpdate, ProveedorCreate, ProveedorUpdate,
    ProductoCreate, ProductoUpdate, VarianteProductoCreate, InventarioCreate,
    MovimientoInventarioCreate, AgregarVarianteRequest, StockPorSucursalRequest,
    ProductoFilter
)
from app.exceptions import (
    NotFoundException, ConflictException, ValidationException, InventoryException
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
    async def get_all(db: AsyncSession, tipo: Optional[str] = None) -> List[Talla]:
        query = select(Talla)
        if tipo:
            query = query.where(Talla.tipo == tipo)
        result = await db.execute(query)
        return result.scalars().all()


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
    async def get_all(db: AsyncSession) -> List[Color]:
        result = await db.execute(select(Color))
        return result.scalars().all()


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
                    if not variante:
                        talla_part = f"T{talla_id}" if talla_id is not None else "ST"
                        sku_var = f"{producto.sku}-{talla_part}-C{color_id}"
                        variante = VarianteProducto(
                            producto_id=producto.id,
                            talla_id=talla_id,
                            color_id=color_id,
                            sku_variante=sku_var
                        )
                        db.add(variante)
                        await db.flush()
                    
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
                selectinload(Producto.variantes).selectinload(VarianteProducto.talla),
                selectinload(Producto.variantes).selectinload(VarianteProducto.color)
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
        
        # Paginación
        query = query.offset(skip).limit(limit)
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
                selectinload(Producto.variantes).selectinload(VarianteProducto.talla),
                selectinload(Producto.variantes).selectinload(VarianteProducto.color)
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
            precio_variante=datos.precio_variante
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
        
        # Paginación
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        
        return result.scalars().all(), total
    
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
        tipo: Optional[TipoMovimiento] = None,
        fecha_inicio: Optional[datetime] = None,
        fecha_fin: Optional[datetime] = None
    ) -> Tuple[List[MovimientoInventario], int]:
        query = select(MovimientoInventario).options(
            selectinload(MovimientoInventario.inventario)
        ).order_by(MovimientoInventario.fecha_hora.desc())
        
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
                selectinload(Inventario.variante_producto).selectinload(VarianteProducto.producto),
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
        ]
        
        for nombre, hex_code in colores_data:
            result = await db.execute(select(Color).where(Color.nombre == nombre))
            if not result.scalar_one_or_none():
                color = Color(nombre=nombre, codigo_hex=hex_code)
                db.add(color)
        
        await db.commit()