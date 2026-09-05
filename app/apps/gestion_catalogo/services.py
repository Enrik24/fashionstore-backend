"""
Servicios de negocio para Gestión de Catálogo, Productos e Inventario.
"""
from typing import Optional, List, Tuple
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
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
    MovimientoInventarioCreate, AgregarVarianteRequest, StockPorSucursalRequest
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
        if ciudad.sucursales:
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
        
        # Verificar si tiene productos asociados
        if categoria.productos:
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
        if temporada.productos:
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
        
        if proveedor.productos:
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
                inventario = Inventario(
                    variante_producto_id=None,  # Se configurará con las variantes
                    sucursal_id=stock.sucursal_id,
                    cantidad=stock.cantidad,
                    cantidad_reservada=0,
                    cantidad_vendida=0
                )
                db.add(inventario)
        
        await db.commit()
        await db.refresh(producto)
        return producto
    
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
        await db.refresh(producto)
        return producto
    
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
        result = await db.execute(
            select(VarianteProducto).where(
                and_(
                    VarianteProducto.producto_id == producto_id,
                    VarianteProducto.talla_id == datos.talla_id,
                    VarianteProducto.color_id == datos.color_id
                )
            )
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
            selectinload(Inventario.variante_producto).selectinload(VarianteProducto.producto),
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
    ):
        """Obtiene la disponibilidad de un producto por sucursal."""
        await ProductoService.get(db, producto_id)
        
        # Construir query para variantes
        query = select(VarianteProducto).where(VarianteProducto.producto_id == producto_id)
        
        if talla_id:
            query = query.where(VarianteProducto.talla_id == talla_id)
        if color_id:
            query = query.where(VarianteProducto.color_id == color_id)
        
        result = await db.execute(query)
        variantes = result.scalars().all()
        
        disponibilidad = []
        for variante in variantes:
            # Obtener inventario por sucursal
            for inventario in variante.inventarios:
                disponibilidad.append({
                    "sucursal_id": inventario.sucursal_id,
                    "sucursal_nombre": inventario.sucursal.nombre,
                    "cantidad_disponible": inventario.cantidad_disponible,
                    "cantidad_reservada": inventario.cantidad_reservada,
                    "estado": inventario.estado
                })
        
        return disponibilidad
    
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