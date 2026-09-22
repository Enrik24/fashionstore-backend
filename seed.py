"""
Script de Seeder para FashionStore Backend.
Puebla la base de datos con los datos reales del negocio, de forma idempotente:
- Permisos y Roles (Administrador, Encargado, Cajero, Cliente)
- Ciudades y Sucursales con coordenadas
- Proveedores, Temporadas (Primavera-Verano / Otoño-Invierno) y Colecciones
- Categorías, Tallas, Colores
- Productos con costo, género, imágenes Cloudinary y asociaciones a colecciones
- Variantes (Talla x Color) e inventario por sucursal
- Promociones con alcances y Cupones
- Usuarios por tipo (Admin, Encargado, Cajero, Clientes) + tablas heredadas

IDEMPOTENTE: puede ejecutarse múltiples veces sin duplicar registros.
Los usuarios existentes NO se modifican (solo se crean los faltantes).

Uso:
    python seed.py
"""
import asyncio
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Dict, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import engine, Base, AsyncSessionLocal
from app.security import get_password_hash
import app.apps.gestion_ventas.models
import app.apps.servicios_inteligentes.models
from app.apps.gestion_usuarios.models import (
    Usuario, Rol, Permiso, UsuarioRol, RolPermiso,
    Cliente, Administrador, EncargadoSucursal, Cajero, EstadoUsuario
)
from app.apps.gestion_catalogo.models import (
    Ciudad, Sucursal, Categoria, Talla, Color, EstadoSucursal,
    Proveedor, Temporada, Coleccion, Producto, ProductoColeccion,
    VarianteProducto, Inventario, MovimientoInventario,
    EstadoProducto, GeneroProducto, EstadoStock, TipoMovimiento
)
from app.apps.gestion_ventas.models import (
    Cupon, TipoCupon, EstadoCupon
)
from app.apps.gestion_marketing.models import (
    Promocion, TipoPromocion, EstadoPromocion,
    PromocionProducto, PromocionCategoria, PromocionSucursal,
    CuponProducto, CuponCategoria
)
from app.apps.gestion_catalogo.services import DatosInicialesCatalogoService


# =====================================================================
# 1. PERMISOS Y ROLES
# =====================================================================

async def seed_permisos(db: AsyncSession) -> dict:
    """Crea los permisos del sistema si no existen."""
    permisos_data = [
        ('gestionar_usuarios', 'Permiso para gestionar usuarios'),
        ('gestionar_roles', 'Permiso para gestionar roles y permisos'),
        ('ver_bitacora', 'Permiso para ver la bitácora'),
        ('gestionar_ciudades', 'Permiso para gestionar ciudades'),
        ('gestionar_sucursales', 'Permiso para gestionar sucursales'),
        ('gestionar_productos', 'Permiso para gestionar productos'),
        ('gestionar_inventario', 'Permiso para gestionar inventario'),
        ('gestionar_categorias', 'Permiso para gestionar categorías'),
        ('gestionar_proveedores', 'Permiso para gestionar proveedores'),
        ('gestionar_ventas', 'Permiso para gestionar ventas'),
        ('gestionar_reservas', 'Permiso para gestionar reservas'),
        ('procesar_pagos', 'Permiso para procesar pagos'),
        ('generar_reportes', 'Permiso para generar reportes'),
        ('ver_kpis', 'Permiso para ver indicadores KPIs'),
        ('gestionar_promociones', 'Permiso para gestionar promociones'),
        ('gestionar_cupones', 'Permiso para gestionar cupones'),
        ('gestionar_colecciones', 'Permiso para gestionar colecciones'),
        ('gestionar_clientes', 'Permiso para gestionar clientes'),
        ('ver_reportes', 'Permiso para ver reportes'),
        ('usar_asistente_ia', 'Permiso para usar el asistente de IA'),
        ('gestionar_devoluciones', 'Permiso para gestionar devoluciones'),
    ]
    
    permisos = {}
    creados = 0
    for nombre, desc in permisos_data:
        res = await db.execute(select(Permiso).where(Permiso.nombre == nombre))
        perm = res.scalar_one_or_none()
        if not perm:
            perm = Permiso(nombre=nombre, descripcion=desc)
            db.add(perm)
            await db.flush()
            creados += 1
        permisos[nombre] = perm
    
    print(f"  [+] Permisos: {len(permisos)} verificados ({creados} creados nuevos)")
    return permisos


async def seed_roles(db: AsyncSession, permisos: dict) -> dict:
    """Crea los roles del sistema y asigna sus permisos si no existen."""
    todos = list(permisos.keys())
    roles_data = [
        ("Administrador", "Administrador del sistema con acceso completo", todos),
        ("Cliente", "Cliente de la plataforma", []),
        ("Encargado", "Encargado de sucursal", [
            "gestionar_sucursales", "gestionar_inventario",
            "gestionar_reservas", "gestionar_ventas", "ver_kpis", "ver_reportes",
            "gestionar_devoluciones", "usar_asistente_ia"
        ]),
        ("Cajero", "Cajero de sucursal", [
            "gestionar_ventas", "procesar_pagos", "usar_asistente_ia"
        ]),
    ]
    
    roles = {}
    creados = 0
    for nombre, desc, perms in roles_data:
        res = await db.execute(select(Rol).where(Rol.nombre == nombre))
        rol = res.scalar_one_or_none()
        if not rol:
            rol = Rol(nombre=nombre, descripcion=desc)
            db.add(rol)
            await db.flush()
            creados += 1
        # Asegurar permisos faltantes en roles existentes (idempotente)
        for perm_nombre in perms:
            if perm_nombre in permisos:
                rp_check = await db.execute(
                    select(RolPermiso).where(
                        RolPermiso.rol_id == rol.id,
                        RolPermiso.permiso_id == permisos[perm_nombre].id
                    )
                )
                if not rp_check.scalar_one_or_none():
                    db.add(RolPermiso(rol_id=rol.id, permiso_id=permisos[perm_nombre].id))
        await db.flush()
        roles[nombre] = rol
    
    print(f"  [+] Roles: {len(roles)} verificados ({creados} creados nuevos)")
    return roles


# =====================================================================
# 2. CIUDADES Y SUCURSALES (CON COORDENADAS)
# =====================================================================

async def seed_ciudades_y_sucursales(db: AsyncSession) -> Dict[str, Sucursal]:
    """Crea ciudades y sucursales con coordenadas geográficas."""
    ciudades_data = [
        ('Santa Cruz de la Sierra', '0000', 'Bolivia'),
        ('La Paz', '0000', 'Bolivia'),
        ('Cochabamba', '0000', 'Bolivia'),
    ]
    ciudades_map = {}
    for nombre_c, cp, pais in ciudades_data:
        res_c = await db.execute(select(Ciudad).where(Ciudad.nombre == nombre_c))
        c_obj = res_c.scalar_one_or_none()
        if not c_obj:
            c_obj = Ciudad(nombre=nombre_c, codigo_postal=cp, pais=pais)
            db.add(c_obj)
            await db.flush()
        ciudades_map[nombre_c] = c_obj

    sucursales_data = [
        ('Sucursal Central', 'Av. Monseñor Rivero #300, Santa Cruz', '33123456', '09:00 - 21:00', -17.7833, -63.1821, 'Santa Cruz de la Sierra', 'ACTIVO'),
        ('Sucursal Equipetrol', 'Av. San Martín y Calle 5 Este, Santa Cruz', '33987654', '10:00 - 22:00', -17.7654, -63.195, 'Santa Cruz de la Sierra', 'ACTIVO'),
        ('Sucursal Ventura Mall', '4to Anillo y Av. San Martín, Santa Cruz', '33456789', '10:00 - 22:00', -17.755, -63.198, 'Santa Cruz de la Sierra', 'ACTIVO'),
        ('Sucursal Calacoto', 'Av. Ballivián #1200, La Paz', '22789012', '09:00 - 20:00', -16.539, -68.089, 'La Paz', 'ACTIVO'),
        ('Sucursal Cochabamba Plaza', 'Av. Heroínas #450, Cochabamba', '44123456', '09:00 - 20:00', -17.3935, -66.157, 'Cochabamba', 'ACTIVO'),
    ]

    sucursales_map = {}
    for nombre_s, dir_s, tel_s, hor_s, lat_s, lon_s, ciud_nom, estado_s in sucursales_data:
        res_s = await db.execute(select(Sucursal).where(Sucursal.nombre == nombre_s))
        s_obj = res_s.scalar_one_or_none()
        if not s_obj:
            s_obj = Sucursal(
                nombre=nombre_s,
                direccion=dir_s,
                telefono=tel_s,
                horario_atencion=hor_s,
                estado=EstadoSucursal[estado_s],
                latitud=lat_s,
                longitud=lon_s,
                ciudad_id=ciudades_map[ciud_nom].id
            )
            db.add(s_obj)
            await db.flush()
            print(f"  [+] Sucursal creada: {s_obj.nombre}")
        else:
            if s_obj.latitud is None or s_obj.longitud is None:
                s_obj.latitud = lat_s
                s_obj.longitud = lon_s
                await db.flush()
        sucursales_map[nombre_s] = s_obj

    return sucursales_map


# =====================================================================
# 3. PROVEEDORES, TEMPORADAS Y COLECCIONES
# =====================================================================

async def seed_proveedores(db: AsyncSession) -> Dict[str, Proveedor]:
    """Crea proveedores iniciales de forma idempotente."""
    proveedores_data = [
        ('Textiles Bolivia S.A.', '1234567890', 'Lic. Joaquin Chumacer', '+59170123456', 'prendas@textiles.com', ''),
        ('CAT S.A.', '1290348765', 'Lic. Fabiola Mendez', '+59170345678', 'CAT@textiles.com', ''),
        ('Textiles Andinos S.A.', '1029384019', 'Juan Valdez', '71234567', 'ventas@textilesandinos.bo', 'Parque Industrial Mza 12, Santa Cruz'),
        ('Moda Express Bolivia S.R.L.', '2039485028', 'Elena Rojas', '72345678', 'contacto@modaexpress.bo', 'Av. Arce #2145, La Paz'),
        ('Confecciones del Valle', '3049586037', 'Mario Suarez', '73456789', 'info@confeccionesvalle.bo', 'Av. Heroínas #560, Cochabamba'),
    ]
    prov_map = {}
    creados = 0
    for nom, nit, cont, tel, corr, direc in proveedores_data:
        res = await db.execute(select(Proveedor).where(Proveedor.nit == nit))
        p = res.scalar_one_or_none()
        if not p:
            p = Proveedor(nombre=nom, nit=nit, contacto=cont, telefono=tel, correo=corr, direccion=direc)
            db.add(p)
            await db.flush()
            creados += 1
        prov_map[nom] = p
    print(f"  [+] Proveedores: {len(prov_map)} verificados ({creados} creados nuevos)")
    return prov_map


async def seed_temporadas_colecciones(db: AsyncSession):
    """Crea las temporadas vigentes y colecciones de forma idempotente."""
    temporadas_data = [
        ('Primavera - Verano 2026', date(2026, 9, 21), date(2027, 3, 20), 'Colección fresca y dinámica de prendas y accesorios para la temporada de verano 2026'),
        ('Otoño - Invierno 2026', date(2026, 3, 21), date(2026, 9, 20), None),
    ]
    temp_map = {}
    creadas = 0
    for nom, f_inicio, f_fin, desc in temporadas_data:
        res_temp = await db.execute(select(Temporada).where(Temporada.nombre == nom))
        temp = res_temp.scalar_one_or_none()
        if not temp:
            temp = Temporada(nombre=nom, fecha_inicio=f_inicio, fecha_fin=f_fin, descripcion=desc)
            db.add(temp)
            await db.flush()
            creadas += 1
        temp_map[nom] = temp
    print(f"  [+] Temporadas: {len(temp_map)} verificadas ({creadas} creadas nuevas)")

    colecciones_data = [
        ('Urban Casual & Heritage 2026', 'Prendas de alta durabilidad, estilo urbano y carácter auténtico', 'Primavera - Verano 2026'),
    ]
    col_map = {}
    for nom, desc, temp_nom in colecciones_data:
        res_col = await db.execute(select(Coleccion).where(Coleccion.nombre == nom))
        col = res_col.scalar_one_or_none()
        if not col:
            col = Coleccion(
                nombre=nom,
                descripcion=desc,
                temporada_id=temp_map[temp_nom].id if temp_nom else None
            )
            db.add(col)
            await db.flush()
            print(f"  [+] Colección creada: {col.nombre}")
        col_map[nom] = col
    return temp_map, col_map


# =====================================================================
# 4. CARACTERÍSTICAS: CATEGORÍAS, TALLAS Y COLORES
# =====================================================================

async def seed_caracteristicas(db: AsyncSession):
    """Crea categorías, tallas y colores faltantes (idempotente)."""
    categorias_data = [
        ('Camisas', 'Camisas de manga corta y larga'),
        ('Pantalones', 'Pantalones de diferentes estilos'),
        ('Vestidos', 'Vestidos formales y casuales'),
        ('Abrigos', 'Chaquetas, abrigos y sweaters'),
        ('Calzado', 'Zapatos, botas y sandalias'),
        ('Accesorios', 'Cinturones, bufandas, gorros'),
        ('Poleras', 'Prendas para épocas de calor'),
        ('Shorts', 'Prendas para épocas de calor'),
        ('Chaquetas', 'Prendas para épocas de frio'),
        ('Canguros', 'Prendas para épocas de frio'),
        ('Polos', 'Prendas para épocas de calor'),
    ]
    for nom, desc in categorias_data:
        res = await db.execute(select(Categoria).where(Categoria.nombre == nom))
        if not res.scalar_one_or_none():
            db.add(Categoria(nombre=nom, descripcion=desc))
            await db.flush()
    tallas_data = [
        ('XS', 'ropa', 'Extra Small'),
        ('S', 'ropa', 'Small'),
        ('M', 'ropa', 'Medium'),
        ('L', 'ropa', 'Large'),
        ('XL', 'ropa', 'Extra Large'),
        ('XXL', 'ropa', 'Extra Extra Large'),
        ('28', 'calzado', 'Talla 28'),
        ('29', 'calzado', 'Talla 29'),
        ('30', 'calzado', 'Talla 30'),
        ('31', 'calzado', 'Talla 31'),
        ('32', 'calzado', 'Talla 32'),
        ('33', 'calzado', 'Talla 33'),
        ('34', 'calzado', 'Talla 34'),
        ('35', 'calzado', 'Talla 35'),
        ('36', 'calzado', 'Talla 36'),
    ]
    for valor, tipo, desc in tallas_data:
        res = await db.execute(select(Talla).where(Talla.valor == valor))
        if not res.scalar_one_or_none():
            db.add(Talla(valor=valor, tipo=tipo, descripcion=desc))
            await db.flush()
    colores_data = [
        ('Negro', '#000000', None),
        ('Blanco', '#FFFFFF', None),
        ('Gris', '#808080', None),
        ('Azul', '#0000FF', None),
        ('Rojo', '#FF0000', None),
        ('Verde', '#008000', None),
        ('Amarillo', '#FFFF00', None),
        ('Naranja', '#FFA500', None),
        ('Café', '#8B4513', None),
        ('Beige', '#F5F5DC', None),
        ('Rosa', '#FFC0CB', None),
        ('Morado', '#800080', None),
        ('Celeste', '#87CEEB', None),
        ('Verde Claro', '#4fff4d', None),
    ]
    for nom, hx, img in colores_data:
        res = await db.execute(select(Color).where(Color.nombre == nom))
        if not res.scalar_one_or_none():
            db.add(Color(nombre=nom, codigo_hex=hx, imagen_muestra=img))
            await db.flush()
    print("  [+] Características verificadas (categorías, tallas, colores)")


# =====================================================================
# 5. PRODUCTOS, VARIANTES, INVENTARIO Y COLECCIONES
# =====================================================================

async def seed_productos_y_stock(db, sucursales, temp_map, col_map):
    """Registra productos, variantes, inventario y asociaciones a colecciones."""
    cats = (await db.execute(select(Categoria))).scalars().all()
    cat_map = {c.nombre: c.id for c in cats}
    tallas = (await db.execute(select(Talla))).scalars().all()
    talla_map = {t.valor: t.id for t in tallas}
    colores = (await db.execute(select(Color))).scalars().all()
    color_map = {c.nombre: c.id for c in colores}
    provs = (await db.execute(select(Proveedor))).scalars().all()
    prov_map = {p.nombre: p.id for p in provs}

    productos_catalogo = [
        {
            "sku": 'PRD-4624',
            "nombre": 'CAT Sueter W Fleece Caterpillar Pullover Mujer',
            "descripcion": 'El Caterpillar W Fleece Caterpillar Pullover Hoodie (4050106-14375) es un polerón para mujer diseñado para brindar comodidad y abrigo en el uso diario. Su confección en fleece y ajuste relajado lo convierten en una prenda ideal para climas frescos, combinando estilo y funcionalidad.',
            "precio": Decimal("500.0"),
            "costo_compra": Decimal("300.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Abrigos',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789058561/fashionstore/productos/ajs2ri0mbnrlsbp0ffjs.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789058561/fashionstore/productos/u3rdovq7ml0l8qw4twhh.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-4624-2-11',
                    "talla": 'S',
                    "color": 'Rosa',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 25, 'reservada': 0, 'vendida': 5, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-6274',
            "nombre": 'CAT Polera Small Logo Hombre',
            "descripcion": 'La Caterpillar Cat Small Logo Tee es una polera para hombre que personifica la simplicidad y la resistencia de la marca. Su diseño minimalista en un tono azul verdoso Dark Sea con detalles en gris neutro ofrece una versatilidad excepcional, proporcionando comodidad duradera gracias a su tejido de alta calidad.',
            "precio": Decimal("350.0"),
            "costo_compra": Decimal("210.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Poleras',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789102474/fashionstore/productos/ezyk5wbumilcvcfrxcht.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789102473/fashionstore/productos/kk5mdkm2qzelcrvindak.jpg"]\n',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-6274-3-6',
                    "talla": 'M',
                    "color": 'Verde',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 35, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-6274-4-6',
                    "talla": 'L',
                    "color": 'Verde',
                    "precio_variante": Decimal("249.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 20, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-7989',
            "nombre": 'CAT Polera Heritage Peoria Graphic Hombre',
            "descripcion": 'La Caterpillar Heritage Peoria Graphic es una polera para hombre que rinde homenaje al legado industrial de la marca. Su diseño clásico con gráfico frontal combina comodidad y durabilidad, convirtiéndola en una prenda esencial para un estilo casual con carácter auténtico.',
            "precio": Decimal("249.0"),
            "costo_compra": Decimal("149.4"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Poleras',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789102899/fashionstore/productos/htjhwbchptb7qjop1hv8.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789102898/fashionstore/productos/zdvbt0fo0m6l8duzifof.jpg"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-7989-3-3',
                    "talla": 'M',
                    "color": 'Gris',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 25, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-7989-4-3',
                    "talla": 'L',
                    "color": 'Gris',
                    "precio_variante": Decimal("249.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 18, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 6, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 6, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-6798',
            "nombre": 'Polera Hombre Caterpillar Logo Heather Grey Yellow',
            "descripcion": 'Polera juvenil de color Gris, de diferentes tallas',
            "precio": Decimal("180.0"),
            "costo_compra": Decimal("108.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Poleras',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789103014/fashionstore/productos/usof4dhptw8wqpvtvcgb.jpg"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-6798-3-3',
                    "talla": 'M',
                    "color": 'Gris',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 30, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-6798-2-3',
                    "talla": 'S',
                    "color": 'Gris',
                    "precio_variante": Decimal("180.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-2697',
            "nombre": 'Polera Hombre Cat Logo Detroit Blue White',
            "descripcion": 'Polera para las temporadas de verano',
            "precio": Decimal("249.0"),
            "costo_compra": Decimal("149.4"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Poleras',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789103218/fashionstore/productos/d03vioo1qir4yqpxxetu.jpg"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-2697-3-4',
                    "talla": 'M',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 25, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-2697-4-4',
                    "talla": 'L',
                    "color": 'Azul',
                    "precio_variante": Decimal("249.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 20, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 6, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 6, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-9730',
            "nombre": 'CAT Polera Heritage Peoria Graphic Hombre',
            "descripcion": 'La Caterpillar Heritage Peoria Graphic Tee (4010755-13013) es una polera para hombre que rinde homenaje al legado industrial de la marca. Su diseño clásico en color negro profundo combina comodidad y durabilidad, convirtiéndola en una prenda esencial para un estilo casual con carácter auténtico.',
            "precio": Decimal("249.0"),
            "costo_compra": Decimal("149.4"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Poleras',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789103405/fashionstore/productos/epy4rmnfuwwct1bximd3.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789103404/fashionstore/productos/dm2grh2phghazebpaiul.jpg"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-9730-3-1',
                    "talla": 'M',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 25, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-9730-4-1',
                    "talla": 'L',
                    "color": 'Negro',
                    "precio_variante": Decimal("249.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 20, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 6, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 6, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-1009',
            "nombre": 'Gorra Cat Logo Hombre Yellow',
            "descripcion": 'Gorras de varios colores',
            "precio": Decimal("210.0"),
            "costo_compra": Decimal("126.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Accesorios',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789103692/fashionstore/productos/u2pv3wukhjlizyfdytev.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789103693/fashionstore/productos/zdm8vsoyesywllqcpoal.jpg"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-1009-4-7',
                    "talla": None,
                    "color": 'Amarillo',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 25, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 6, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 6, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-1009-3-7',
                    "talla": None,
                    "color": 'Amarillo',
                    "precio_variante": Decimal("210.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 20, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-2630',
            "nombre": 'Gorra Caterpillar 100Th Hombre Pitch Black',
            "descripcion": 'Gorra de Algodon para las epocas de calor',
            "precio": Decimal("280.0"),
            "costo_compra": Decimal("168.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Accesorios',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789132323/fashionstore/productos/sacencilrabr5qtf3cwa.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789132322/fashionstore/productos/bxg2zvs8fjxlunpbdzzv.jpg"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-2630-4-1',
                    "talla": None,
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-2630-3-1',
                    "talla": None,
                    "color": 'Negro',
                    "precio_variante": Decimal("280.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-9664',
            "nombre": 'Gorra Cat Logo Hombre Barn Red-White',
            "descripcion": 'Gorra que sueles ser usadas en epocas de calor, en esta ocasion les ofrecemos una gorra de color Rojo',
            "precio": Decimal("250.0"),
            "costo_compra": Decimal("150.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Accesorios',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789133522/fashionstore/productos/uegzagcdbopu7bulx26u.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789133522/fashionstore/productos/evns90lew3tysevdar4u.jpg"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-9664-3-5',
                    "talla": None,
                    "color": 'Rojo',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-9664-4-5',
                    "talla": None,
                    "color": 'Rojo',
                    "precio_variante": Decimal("250.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 6, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 6, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 4, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 4, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-9453',
            "nombre": 'CAT Chaqueta Softshell Hombre',
            "descripcion": 'La CAT Softshell Jacket está diseñada para ofrecer protección, flexibilidad y confort en actividades dinámicas. Su construcción técnica combina resistencia al clima con una excelente movilidad, convirtiéndola en una prenda ideal para el trabajo activo y el uso diario en exteriores.\n\nEquipada con tecnología Storm Blocker™, ayuda a repeler el agua, bloquear el viento y mantener una óptima transpirabilidad. Su tejido FlexShell elástico se adapta naturalmente a cada movimiento, mientras que el interior de microforro polar proporciona una agradable sensación de abrigo ligero en condiciones frescas.\n\nIncorpora detalles funcionales como ventilación mediante cremalleras en las axilas, puños ajustables y cordón regulable en el dobladillo para mejorar el confort durante el uso. Además, los refuerzos de nailon en los codos aumentan la durabilidad en zonas de mayor desgaste, mientras que los detalles reflectantes ayudan a mejorar la visibilidad en condiciones de poca luz.',
            "precio": Decimal("990.0"),
            "costo_compra": Decimal("594.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Chaquetas',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'Textiles Bolivia S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789133785/fashionstore/productos/ybkjbqrdqvvkepg8nlwy.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789133783/fashionstore/productos/kmkeszphuo7d7z13uacr.jpg"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-9453-4-1',
                    "talla": 'L',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 20, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-9453-3-1',
                    "talla": 'M',
                    "color": 'Negro',
                    "precio_variante": Decimal("990.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-9453-5-1',
                    "talla": 'XL',
                    "color": 'Negro',
                    "precio_variante": Decimal("990.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 5, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 4, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 4, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-7338',
            "nombre": 'CAT Sueter Midweight Quarter Zip Hombre',
            "descripcion": 'La CAT Midweight 1/4 Zip Sweatshirt combina comodidad, abrigo ligero y durabilidad en una prenda versátil para el día a día. Diseñada bajo estándares de workwear, ofrece el equilibrio perfecto entre funcionalidad y estilo casual resistente.\n\nSu tejido de peso medio brinda abrigo sin generar exceso de volumen, mientras que la mezcla de algodón y poliéster proporciona suavidad, resistencia y fácil mantenimiento. Además, el interior con forro polar (fleece) ofrece mayor confort térmico en climas frescos.\n\nEl diseño incorpora cuello alto con cierre 1/4 para regular la temperatura según la necesidad, junto con una construcción resistente y costuras reforzadas que mejoran la durabilidad en uso frecuente.',
            "precio": Decimal("650.0"),
            "costo_compra": Decimal("390.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Abrigos',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'Textiles Bolivia S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789230841/fashionstore/productos/w3pb7xojhoaptmnbshbv.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789230842/fashionstore/productos/gom7eligkoj8gpbqj6ut.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-7338-T2-C10',
                    "talla": 'S',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 20, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-7338-T3-C10',
                    "talla": 'M',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-7338-T4-C10',
                    "talla": 'L',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-7338-T5-C10',
                    "talla": 'XL',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-3243',
            "nombre": 'Polera Everyday Workwear Graphic Tee 5 Pitch Black',
            "descripcion": 'Polera casual de cuello redondo, confeccionada en algodón suave y resistente con el clásico logo Caterpillar estampado en el pecho.',
            "precio": Decimal("249.0"),
            "costo_compra": Decimal("149.4"),
            "genero": 'UNISEX',
            "estado": 'ACTIVO',
            "categoria": 'Poleras',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Textiles Andinos S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789337535/fashionstore/productos/jr1qkimknvehnmnaz2dx.png"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-3243-3-1',
                    "talla": 'M',
                    "color": 'Negro',
                    "precio_variante": Decimal("249.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 30, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 14, 'reservada': 0, 'vendida': 1, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-3243-2-1',
                    "talla": 'S',
                    "color": 'Negro',
                    "precio_variante": Decimal("249.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 20, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-3243-4-1',
                    "talla": 'L',
                    "color": 'Negro',
                    "precio_variante": Decimal("249.0"),
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 25, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 8, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-4733',
            "nombre": 'CAT Camisa Lightweight Western Manga Larga Hombre',
            "descripcion": 'La CAT Lightweight Long Sleeve Western Shirt fusiona el estilo clásico del oeste con tecnología moderna para el uso activo. Ligera, transpirable y funcional, está diseñada para mantenerte fresco, protegido y cómodo durante toda la jornada.\n\nSu confección en tejido popelina ultraligero con mezcla de nylon y spandex ofrece resistencia, flexibilidad y secado rápido, convirtiéndola en una excelente opción para climas cálidos y actividades al aire libre. Además, incorpora protección solar UPF 50+ para una mayor protección frente a los rayos UV.',
            "precio": Decimal("750.0"),
            "costo_compra": Decimal("450.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Camisas',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789435332/fashionstore/productos/vs8i0yzkn6f045qsbnll.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789435333/fashionstore/productos/f4v9oz8gybbzue9v9mij.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-4733-T2-C10',
                    "talla": 'S',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 25, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4733-T3-C10',
                    "talla": 'M',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4733-T4-C10',
                    "talla": 'L',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4733-T5-C10',
                    "talla": 'XL',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-7726',
            "nombre": 'Canguro Fleece Cat Logo Hombre Cedar Green-White',
            "descripcion": 'El Canguro Fleece Cat Logo Hombre Cedar Green-White es un polerón cómodo y versátil, ideal para el uso diario.',
            "precio": Decimal("610.0"),
            "costo_compra": Decimal("366.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Canguros',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789611841/fashionstore/productos/s3rrhtdhlo9ahienxlle.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789611842/fashionstore/productos/hnlcn5hengq7pkdqkq92.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-7726-T2-C6',
                    "talla": 'S',
                    "color": 'Verde',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-7726-T3-C6',
                    "talla": 'M',
                    "color": 'Verde',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-7726-T4-C6',
                    "talla": 'L',
                    "color": 'Verde',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-7726-T4-C1',
                    "talla": 'L',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-8697',
            "nombre": 'CAT Canguro Fleece Cat Logo Pullover Hombre',
            "descripcion": 'El Caterpillar Fleece Cat Logo Pullover Hoodie es un polerón cómodo y versátil, ideal para el uso diario. Su diseño clásico con capucha y tejido fleece proporciona abrigo y confort, convirtiéndolo en una prenda esencial para climas frescos.',
            "precio": Decimal("610.0"),
            "costo_compra": Decimal("366.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Canguros',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'Textiles Andinos S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789612277/fashionstore/productos/wondwid1m4yy0qi7hyyr.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789612276/fashionstore/productos/rxybbibkypmmv97fehcs.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-8697-T3-C2',
                    "talla": 'M',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-8697-T4-C2',
                    "talla": 'L',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-4771',
            "nombre": 'Canguro Microfleece Full Zip Hombre Pitch Black',
            "descripcion": 'Composicion 100% algodon ideal para temporadas de frio',
            "precio": Decimal("600.0"),
            "costo_compra": Decimal("360.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Canguros',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": None,
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789612721/fashionstore/productos/bhdqorecjtiupmu51s6y.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789612722/fashionstore/productos/orjcvynqcgcctjnsflfj.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-4771-T1-C1',
                    "talla": 'XS',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4771-T4-C1',
                    "talla": 'L',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-4113',
            "nombre": 'Canguro Fleece Cat Logo Full Zip Hombre Detroit Blue-Black',
            "descripcion": 'Abrígate con el estilo clásico de CAT. Este canguro (hoodie) para hombre está confeccionado en fleece de alta calidad, ofreciendo abrigo y comodidad. Su diseño presenta un cierre frontal completo (Full Zip), capucha ajustable y bolsillos tipo canguro. Destaca por su color azul oscuro con detalles en negro y el icónico logo de CAT en el frente y la manga.',
            "precio": Decimal("850.0"),
            "costo_compra": Decimal("510.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Canguros',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'Moda Express Bolivia S.R.L.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789614962/fashionstore/productos/zgsribl0hpwwvyfmycbq.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789614960/fashionstore/productos/vdkzrcmn1jl4rnybv21r.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-4113-T2-C4',
                    "talla": 'S',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4113-T3-C4',
                    "talla": 'M',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 4, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4113-T4-C4',
                    "talla": 'L',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4113-T5-C4',
                    "talla": 'XL',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-5127',
            "nombre": 'Canguro Fleece Cat Logo Full Zip Hombre Russet-White',
            "descripcion": 'Sumá estilo y comodidad a tu día a día con este canguro de la marca CAT. Confeccionado en fleece de alta calidad, cuenta con un diseño de cierre frontal completo (Full Zip) y capucha ajustable. Se destaca por su llamativo color marrón rojizo (Russet) con detalles en blanco, incluyendo el icónico logo de CAT tanto en el pecho como en la manga.',
            "precio": Decimal("820.0"),
            "costo_compra": Decimal("492.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Canguros',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'Textiles Andinos S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789704753/fashionstore/productos/wbc1tyoyifxwfwb9aued.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789704755/fashionstore/productos/qocff66fuinhkvozb05k.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-5127-T4-C5',
                    "talla": 'L',
                    "color": 'Rojo',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-5127-T5-C5',
                    "talla": 'XL',
                    "color": 'Rojo',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-5127-T3-C5',
                    "talla": 'M',
                    "color": 'Rojo',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-2535',
            "nombre": 'CAT Canguro Fleece Cat Patch Full Zip Hombre',
            "descripcion": 'El Caterpillar Fleece Cat Patch Full Zip Hoodie es un polerón para hombre diseñado para brindar abrigo y comodidad en el uso diario. Su confección en fleece y cierre completo lo convierten en una prenda práctica y versátil para climas frescos.',
            "precio": Decimal("610.0"),
            "costo_compra": Decimal("366.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Canguros',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'Moda Express Bolivia S.R.L.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789704984/fashionstore/productos/touhsvkedcbnep9fpioi.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789704985/fashionstore/productos/ad3iiyhjpebjghx4fjur.jpg"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-2535-T4-C1',
                    "talla": 'L',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-2535-T5-C1',
                    "talla": 'XL',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-2535-T3-C1',
                    "talla": 'M',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-3018',
            "nombre": 'CAT Chaqueta Softshell Hombre',
            "descripcion": 'La CAT Softshell Jacket está diseñada para ofrecer protección, flexibilidad y confort en actividades dinámicas. Su construcción técnica combina resistencia al clima con una excelente movilidad, convirtiéndola en una prenda ideal para el trabajo activo y el uso diario en exteriores.\n\nEquipada con tecnología Storm Blocker™, ayuda a repeler el agua, bloquear el viento y mantener una óptima transpirabilidad. Su tejido FlexShell elástico se adapta naturalmente a cada movimiento, mientras que el interior de microforro polar proporciona una agradable sensación de abrigo ligero en condiciones frescas.\n\nIncorpora detalles funcionales como ventilación mediante cremalleras en las axilas, puños ajustables y cordón regulable en el dobladillo para mejorar el confort durante el uso. Además, los refuerzos de nailon en los codos aumentan la durabilidad en zonas de mayor desgaste, mientras que los detalles reflectantes ayudan a mejorar la visibilidad en condiciones de poca luz.',
            "precio": Decimal("1000.0"),
            "costo_compra": Decimal("600.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Abrigos',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'Confecciones del Valle',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789705379/fashionstore/productos/swif5yawaylusaacmm98.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789705376/fashionstore/productos/gch8zr3jfv4sycvxrbkk.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-3018-T1-C1',
                    "talla": 'XS',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-3018-T3-C1',
                    "talla": 'M',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-3018-T4-C1',
                    "talla": 'L',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 9, 'reservada': 0, 'vendida': 1, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-4928',
            "nombre": 'Pantalon Hombre Ripstop Cargo Khaki',
            "descripcion": 'Optimizá tu día a día con este pantalón cargo para hombre de la marca CAT. Confeccionado con tejido Ripstop, ofrece una resistencia superior al desgarro, siendo la opción ideal tanto para el trabajo pesado como para el uso diario. Su diseño en color Khaki incluye prácticos bolsillos utilitarios y un calce cómodo y duradero.',
            "precio": Decimal("715.0"),
            "costo_compra": Decimal("429.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Pantalones',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789709556/fashionstore/productos/r3vbnn077hszqu8lp1hi.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789709557/fashionstore/productos/wzavcvsnynpobfdlbgcw.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-4928-T3-C10',
                    "talla": '33',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4928-T4-C10',
                    "talla": '35',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4928-T5-C10',
                    "talla": '36',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-5600',
            "nombre": 'Pantalon Hombre Ripstop Cargo Black',
            "descripcion": 'Sumá resistencia y estilo a tu look diario con este pantalón cargo para hombre de la marca CAT. Confeccionado con tejido Ripstop, está diseñado para soportar el uso exigente manteniendo la comodidad. Su diseño en color negro incluye prácticos bolsillos utilitarios y un calce duradero, ideal tanto para el trabajo como para el uso casual.',
            "precio": Decimal("720.0"),
            "costo_compra": Decimal("432.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Pantalones',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Textiles Andinos S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789709813/fashionstore/productos/tsw3mlcgywmbhaecb4vr.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789709815/fashionstore/productos/bqzl6bihtans5kj99qvb.jpg"]',
            "colecciones": ['Urban Casual & Heritage 2026'],
            "variantes": [
                {
                    "sku": 'PRD-5600-T9-C1',
                    "talla": '30',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 15, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-5600-T11-C1',
                    "talla": '32',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-5600-T13-C1',
                    "talla": '34',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-5600-T15-C1',
                    "talla": '36',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-8408',
            "nombre": 'Pantalon Cat Hombre Rigid Chino Pant Orion Blue',
            "descripcion": 'Elevá tu estilo casual con este pantalón chino para hombre de la marca CAT. Su confección rígida y corte clásico ofrecen un look estructurado y elegante, ideal para el uso diario o la oficina. El color Orion Blue (azul grisáceo) aporta un toque moderno y versátil que combina con todo.',
            "precio": Decimal("550.0"),
            "costo_compra": Decimal("330.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Pantalones',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Textiles Bolivia S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789710941/fashionstore/productos/e9is4sevpzdznwz1yzte.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789710941/fashionstore/productos/u2jzcr99rdj4xdnn3hjr.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-8408-T7-C4',
                    "talla": '28',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 12, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-8408-T9-C4',
                    "talla": '30',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-8408-T11-C4',
                    "talla": '32',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-5228',
            "nombre": 'Zapatilla Hombre Hex Lite Leather Black',
            "descripcion": 'Combiná resistencia y estilo urbano con esta zapatilla para hombre de la marca CAT. Su modelo Hex Lite presenta una parte superior de cuero (Leather) de alta durabilidad y una suela con tecnología de amortiguación que garantiza máxima comodidad en cada paso. Su diseño en color negro es versátil y perfecto para el uso diario.',
            "precio": Decimal("810.0"),
            "costo_compra": Decimal("486.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Calzado',
            "temporada": None,
            "proveedor": 'Moda Express Bolivia S.R.L.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789792461/fashionstore/productos/dkg4hmm2258emli5qz5d.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789792480/fashionstore/productos/bvr7ogfsblit1vukjrxx.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789792490/fashionstore/productos/wdewnqsowuwakdc8zwrh.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789792496/fashionstore/productos/gtex8sdila7ezfantsim.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789792502/fashionstore/productos/cx8qxanh0jna6uzk5r4h.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789792507/fashionstore/productos/zhckm2jb2hwvgtwthf52.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-5228-T15-C1',
                    "talla": '36',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-5228-T14-C1',
                    "talla": '35',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-7795',
            "nombre": 'Zapatilla Hombre Puma Zap Club Ii Sd White',
            "descripcion": '',
            "precio": Decimal("720.0"),
            "costo_compra": Decimal("432.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Calzado',
            "temporada": None,
            "proveedor": 'Textiles Bolivia S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789793494/fashionstore/productos/pdcaclaf7j6pbotb4wp1.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-7795-T14-C2',
                    "talla": '35',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-7795-T15-C2',
                    "talla": '36',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 2, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-5614',
            "nombre": 'Short Cat Hombre Foundation Cargo Short Dusty Olive',
            "descripcion": '',
            "precio": Decimal("450.0"),
            "costo_compra": Decimal("270.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Shorts',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Textiles Bolivia S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789797730/fashionstore/productos/ur9gppuaxeffgnnzlbjv.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789797730/fashionstore/productos/zj3ukrraesvtaucvlexb.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-5614-T13-C6',
                    "talla": '34',
                    "color": 'Verde',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 2, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-9745',
            "nombre": 'Polera Mujer Wardrobe Ess Baby W Black',
            "descripcion": '',
            "precio": Decimal("180.0"),
            "costo_compra": Decimal("108.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Poleras',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Moda Express Bolivia S.R.L.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789798176/fashionstore/productos/cq7xo6dqshqncixrxwqg.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789798176/fashionstore/productos/cmxx837hq3oblj44wxha.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-9745-T2-C1',
                    "talla": 'S',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-9745-T3-C1',
                    "talla": 'M',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-9745-T4-C1',
                    "talla": 'L',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-4738',
            "nombre": 'Polera Mujer Wardrobe Ess Baby W White',
            "descripcion": '',
            "precio": Decimal("180.0"),
            "costo_compra": Decimal("108.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Poleras',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Confecciones del Valle',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789798370/fashionstore/productos/o15qbss6phfcplhzxmom.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789798370/fashionstore/productos/byme7womn7baloqtrlxb.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-4738-T1-C2',
                    "talla": 'XS',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4738-T2-C2',
                    "talla": 'S',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4738-T3-C2',
                    "talla": 'M',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 2, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4738-T4-C2',
                    "talla": 'L',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-5817',
            "nombre": 'Solera Mujer Tad Essential Sleeveless Tank W Black',
            "descripcion": '',
            "precio": Decimal("200.0"),
            "costo_compra": Decimal("120.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Poleras',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Confecciones del Valle',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789798576/fashionstore/productos/tztnsxcdqjunmrkpzj2t.png", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789798576/fashionstore/productos/oeeeqkb5zxeyi2wzcdex.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-5817-T1-C1',
                    "talla": 'XS',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-5817-T2-C1',
                    "talla": 'S',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-5817-T3-C1',
                    "talla": 'M',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 9, 'reservada': 0, 'vendida': 1, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-5817-T4-C1',
                    "talla": 'L',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-7545',
            "nombre": 'Basic Seamless Legging Mujer Everlast Black',
            "descripcion": '',
            "precio": Decimal("180.0"),
            "costo_compra": Decimal("108.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Pantalones',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Textiles Bolivia S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789799578/fashionstore/productos/ytocyfmgdram1rvozon2.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789799577/fashionstore/productos/lbsom2ekbayw3qqr2kt4.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-7545-T2-C1',
                    "talla": 'S',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-7545-T3-C1',
                    "talla": 'M',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-8608',
            "nombre": 'Legging Mujer Everlast Seamlees Basic Ps',
            "descripcion": '',
            "precio": Decimal("450.0"),
            "costo_compra": Decimal("270.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Pantalones',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Moda Express Bolivia S.R.L.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789799773/fashionstore/productos/u11izsrwy9sxftyzutbt.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789799772/fashionstore/productos/nauzp8gtu3bavqbymt3i.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-8608-T2-C4',
                    "talla": 'S',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 20, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-8608-T3-C4',
                    "talla": 'M',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-5548',
            "nombre": 'Chaqueta W Foundation Denim Mujer Vintage Dark Wash',
            "descripcion": '',
            "precio": Decimal("920.0"),
            "costo_compra": Decimal("552.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Chaquetas',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'Textiles Bolivia S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789799952/fashionstore/productos/q4zhqtko6ukdoxbcijp3.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789799951/fashionstore/productos/jncvia11abykqeskmew3.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789799952/fashionstore/productos/ci2jufoceb9c2t2ucl7c.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-5548-T2-C4',
                    "talla": 'S',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 9, 'reservada': 0, 'vendida': 1, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-2375',
            "nombre": 'CAT Sueter W Relaxed French Terry Crew Mujer',
            "descripcion": '',
            "precio": Decimal("430.0"),
            "costo_compra": Decimal("258.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Abrigos',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789800126/fashionstore/productos/ytkwdklgadwnzoupif4n.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789800126/fashionstore/productos/kasdbhceay6bxf5ixavt.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-2375-T1-C2',
                    "talla": 'XS',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-2375-T2-C2',
                    "talla": 'S',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-2375-T3-C2',
                    "talla": 'M',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-4644',
            "nombre": 'Chaqueta Original Trucker Jeanie Woman Azul Claro',
            "descripcion": '',
            "precio": Decimal("1050.0"),
            "costo_compra": Decimal("630.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Chaquetas',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Moda Express Bolivia S.R.L.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789800327/fashionstore/productos/u5cctnfipqp6zi83lktu.png", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789800328/fashionstore/productos/vidcvo5i9vgg8b46cwgj.png", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789800329/fashionstore/productos/bipsrzyu6gfxqyxre27n.png"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-4644-T1-C4',
                    "talla": 'XS',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-4644-T3-C4',
                    "talla": 'M',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-71262388',
            "nombre": 'Chaqueta W Microfleece Full Zip Mujer Dusty Pink',
            "descripcion": '',
            "precio": Decimal("420.0"),
            "costo_compra": Decimal("252.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Chaquetas',
            "temporada": 'Otoño - Invierno 2026',
            "proveedor": 'Confecciones del Valle',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789800742/fashionstore/productos/limxx1xbe6aqmzxunhxr.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789800739/fashionstore/productos/voisewcvjcer8m3mz3do.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-71262388-T3-C11',
                    "talla": 'M',
                    "color": 'Rosa',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-85572627',
            "nombre": 'Short Mujer Triblend Stretch Denim Short Light Faded',
            "descripcion": '',
            "precio": Decimal("480.0"),
            "costo_compra": Decimal("288.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Shorts',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Moda Express Bolivia S.R.L.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789800999/fashionstore/productos/vezqwxmrgmiwgvgpjxix.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801000/fashionstore/productos/ot5b9o7ojvlf6r5cneje.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801000/fashionstore/productos/fksrfv4kl48dseyoqr0r.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-85572627-T7-C4',
                    "talla": '28',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-85572627-T9-C4',
                    "talla": '30',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 9, 'reservada': 0, 'vendida': 1, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-85572627-T11-C4',
                    "talla": '32',
                    "color": 'Azul',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-03526539',
            "nombre": 'Pantalon Converse Mujer Parachute Nutty Granola',
            "descripcion": '',
            "precio": Decimal("650.0"),
            "costo_compra": Decimal("390.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Pantalones',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Moda Express Bolivia S.R.L.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789801206/fashionstore/productos/qgymopnrhncg0ipr7c6b.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801205/fashionstore/productos/zaff2vowj0kpltdur7uh.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-03526539-T3-C10',
                    "talla": 'M',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-03526539-T4-C10',
                    "talla": 'L',
                    "color": 'Beige',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-23858114',
            "nombre": 'Jean Mujer 314 Shaping Straight River Rock Grey',
            "descripcion": '',
            "precio": Decimal("890.0"),
            "costo_compra": Decimal("534.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Pantalones',
            "temporada": 'Primavera - Verano 2026',
            "proveedor": 'Moda Express Bolivia S.R.L.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789801382/fashionstore/productos/jidrufyyz2xr1fe7ax3m.png", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801389/fashionstore/productos/bbpiwrus7nveniamofki.png", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801396/fashionstore/productos/bwf3unhhac068k8izou6.png"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-23858114-T7-C3',
                    "talla": '28',
                    "color": 'Gris',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 9, 'reservada': 0, 'vendida': 1, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-23858114-T8-C3',
                    "talla": '29',
                    "color": 'Gris',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-49634042',
            "nombre": 'Zapatilla Cat Mujer Amp Canvas Bright White',
            "descripcion": '',
            "precio": Decimal("710.0"),
            "costo_compra": Decimal("426.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Calzado',
            "temporada": None,
            "proveedor": 'CAT S.A.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789801711/fashionstore/productos/qmtkwaa5pbblvy8exufm.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801722/fashionstore/productos/mkka2wlikvcknmqkbd4x.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801731/fashionstore/productos/da2hvdmrkvohtksvnc3f.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801736/fashionstore/productos/b8pjonguy39umddh7uty.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801742/fashionstore/productos/qvtq16aqmllwhd0v4xvq.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801746/fashionstore/productos/qttqzzh4dwp17z3znxsh.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-49634042-T14-C2',
                    "talla": '35',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-49634042-T15-C2',
                    "talla": '36',
                    "color": 'Blanco',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-77082283',
            "nombre": 'SKECHERS Zapatilla Cordova Classic Mujer',
            "descripcion": '',
            "precio": Decimal("710.0"),
            "costo_compra": Decimal("426.0"),
            "genero": 'MUJER',
            "estado": 'ACTIVO',
            "categoria": 'Calzado',
            "temporada": None,
            "proveedor": 'Moda Express Bolivia S.R.L.',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789801902/fashionstore/productos/qbvpsrniknjdeghdxusv.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801916/fashionstore/productos/zq9x2khwusyjdfsrsaia.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801920/fashionstore/productos/tl8o8hea9veshxjk7d3p.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801926/fashionstore/productos/x0e3vip9tn30ub0blec5.jpg", "https://res.cloudinary.com/dw9etiykm/image/upload/v1789801931/fashionstore/productos/c9ir2drueqwnrevlgmxe.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-77082283-T14-C1',
                    "talla": '35',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 1, 'vendida': 0, 'minimo': 5}, 'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Calacoto': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-77082283-T15-C1',
                    "talla": '36',
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
        {
            "sku": 'PRD-16039380',
            "nombre": 'CHAOREN Cinturón de cuero para hombre de 1 3/8 pulgadas',
            "descripcion": 'Construido para durar: hecho de cuero de calidad con bordes reforzados de doble costura, este cinturón de vestir para hombre está diseñado para un uso duradero. Resiste el agrietamiento y el pelado a través del uso diario, mientras que la hebilla sólida ofrece un ajuste seguro y un acabado pulido.',
            "precio": Decimal("70.0"),
            "costo_compra": Decimal("42.0"),
            "genero": 'HOMBRE',
            "estado": 'ACTIVO',
            "categoria": 'Accesorios',
            "temporada": None,
            "proveedor": 'Confecciones del Valle',
            "imagenes": '["https://res.cloudinary.com/dw9etiykm/image/upload/v1789802226/fashionstore/productos/ezzwaujwctoak70sy0jk.jpg"]',
            "colecciones": [],
            "variantes": [
                {
                    "sku": 'PRD-16039380-ST-C1',
                    "talla": None,
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Central': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
                {
                    "sku": 'PRD-16039380-T1-C1',
                    "talla": None,
                    "color": 'Negro',
                    "precio_variante": None,
                    "costo_variante": None,
                    "stock": {'Sucursal Equipetrol': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Ventura Mall': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}, 'Sucursal Cochabamba Plaza': {'cantidad': 10, 'reservada': 0, 'vendida': 0, 'minimo': 5}},
                },
            ],
        },
    ]

    total_prods = 0
    total_vars = 0
    total_invs = 0

    for item in productos_catalogo:
        res_p = await db.execute(select(Producto).where(Producto.sku == item["sku"]))
        prod = res_p.scalar_one_or_none()
        if not prod:
            prod = Producto(
                sku=item["sku"],
                nombre=item["nombre"],
                descripcion=item["descripcion"],
                precio=item["precio"],
                costo_compra=item["costo_compra"],
                genero=GeneroProducto[item["genero"]],
                estado=EstadoProducto[item["estado"]],
                imagenes=item["imagenes"],
                categoria_id=cat_map.get(item["categoria"]),
                temporada_id=temp_map[item["temporada"]].id if item["temporada"] in temp_map else None,
                proveedor_id=prov_map.get(item["proveedor"])
            )
            db.add(prod)
            await db.flush()
            total_prods += 1
            print(f"  [+] Producto creado: {prod.sku}")
        else:
            prod.imagenes = item["imagenes"]
            prod.precio = item["precio"]
            prod.costo_compra = item["costo_compra"]
            if item["categoria"] in cat_map:
                prod.categoria_id = cat_map[item["categoria"]]
            await db.flush()

        for col_nom in item["colecciones"]:
            if col_nom in col_map:
                res_pc = await db.execute(select(ProductoColeccion).where(
                    ProductoColeccion.producto_id == prod.id,
                    ProductoColeccion.coleccion_id == col_map[col_nom].id))
            if not res_pc.scalar_one_or_none():
                db.add(ProductoColeccion(producto_id=prod.id, coleccion_id=col_map[col_nom].id))
                await db.flush()

        for v_item in item["variantes"]:
            t_id = talla_map.get(v_item["talla"])
            c_id = color_map.get(v_item["color"])
            res_v = await db.execute(
                select(VarianteProducto).where(VarianteProducto.sku_variante == v_item["sku"]))
            var_obj = res_v.scalar_one_or_none()
            if not var_obj:
                var_obj = VarianteProducto(
                    producto_id=prod.id,
                    talla_id=t_id,
                    color_id=c_id,
                    sku_variante=v_item["sku"],
                    precio_variante=v_item["precio_variante"],
                    costo_variante=v_item["costo_variante"]
                )
                db.add(var_obj)
                await db.flush()
                total_vars += 1

            for suc_nombre, st in v_item["stock"].items():
                if suc_nombre not in sucursales:
                    continue
                suc_obj = sucursales[suc_nombre]
                res_inv = await db.execute(select(Inventario).where(
                    Inventario.variante_producto_id == var_obj.id,
                    Inventario.sucursal_id == suc_obj.id))
                # first() en vez de scalar_one_or_none: tolera duplicados históricos
                inv_obj = res_inv.scalars().first()
                if not inv_obj:
                    inv_obj = Inventario(
                        variante_producto_id=var_obj.id,
                        sucursal_id=suc_obj.id,
                        cantidad=st["cantidad"],
                        cantidad_reservada=st.get("reservada", 0),
                        cantidad_vendida=st.get("vendida", 0),
                        stock_minimo=st.get("minimo", 5),
                        estado=EstadoStock.DISPONIBLE
                    )
                    db.add(inv_obj)
                    await db.flush()
                    total_invs += 1
                    mov = MovimientoInventario(
                        inventario_id=inv_obj.id,
                        tipo=TipoMovimiento.RECEPCION,
                        cantidad=st["cantidad"],
                        motivo="Carga inicial de inventario - Seeder"
                    )
                    db.add(mov)
                    await db.flush()

    print(f"  [OK] Catálogo sincronizado: {len(productos_catalogo)} productos "
          f"({total_prods} nuevos, {total_vars} variantes nuevas, {total_invs} inventarios creados)")


# =====================================================================
# 6. PROMOCIONES Y CUPONES
# =====================================================================

async def seed_promociones(db: AsyncSession):
    """Crea promociones con sus alcances (productos, categorías, sucursales)."""
    cats = {c.nombre: c.id for c in (await db.execute(select(Categoria))).scalars().all()}
    sucs = {s.nombre: s.id for s in (await db.execute(select(Sucursal))).scalars().all()}
    prods = {p.sku: p.id for p in (await db.execute(select(Producto))).scalars().all()}
    promociones_data = [
        {
            "nombre": 'Dia del Estudiante',
            "descripcion": 'Descuento especial para jovenes estudiantes',
            "tipo": 'PORCENTAJE',
            "valor": Decimal("10.0"),
            "fecha_inicio": datetime(2026, 9, 21, 0, 54),
            "fecha_fin": datetime(2026, 9, 27, 0, 54),
            "condiciones": 'Valido para compras online',
            "estado": 'ACTIVA',
            "categorias": ['Camisas', 'Pantalones', 'Poleras'],
            "sucursales": ['Sucursal Central'],
            "productos": [],
        },
    ]
    for item in promociones_data:
        res = await db.execute(select(Promocion).where(Promocion.nombre == item["nombre"]))
        promo = res.scalar_one_or_none()
        if not promo:
            promo = Promocion(
                nombre=item["nombre"],
                descripcion=item["descripcion"],
                tipo=TipoPromocion[item["tipo"]],
                valor=item["valor"],
                fecha_inicio=item["fecha_inicio"],
                fecha_fin=item["fecha_fin"],
                condiciones=item["condiciones"],
                estado=EstadoPromocion[item["estado"]]
            )
            db.add(promo)
            await db.flush()
            print(f"  [+] Promoción creada: {promo.nombre}")
        for cn in item["categorias"]:
            if cn in cats:
                rx = await db.execute(select(PromocionCategoria).where(
                    PromocionCategoria.promocion_id == promo.id,
                    PromocionCategoria.categoria_id == cats[cn]))
                if not rx.scalar_one_or_none():
                    db.add(PromocionCategoria(promocion_id=promo.id, categoria_id=cats[cn]))
                    await db.flush()
        for sn in item["sucursales"]:
            if sn in sucs:
                rx = await db.execute(select(PromocionSucursal).where(
                    PromocionSucursal.promocion_id == promo.id,
                    PromocionSucursal.sucursal_id == sucs[sn]))
                if not rx.scalar_one_or_none():
                    db.add(PromocionSucursal(promocion_id=promo.id, sucursal_id=sucs[sn]))
                    await db.flush()
        for sk in item["productos"]:
            if sk in prods:
                rx = await db.execute(select(PromocionProducto).where(
                    PromocionProducto.promocion_id == promo.id,
                    PromocionProducto.producto_id == prods[sk]))
                if not rx.scalar_one_or_none():
                    db.add(PromocionProducto(promocion_id=promo.id, producto_id=prods[sk]))
                    await db.flush()
    print(f"  [+] Promociones verificadas: {len(promociones_data)}")


async def seed_cupones(db: AsyncSession):
    """Crea cupones con su aplicabilidad (productos, categorías)."""
    cats = {c.nombre: c.id for c in (await db.execute(select(Categoria))).scalars().all()}
    prods = {p.sku: p.id for p in (await db.execute(select(Producto))).scalars().all()}
    cupones_data = [
        {
            "codigo": 'FASHION-PIMG',
            "tipo": 'PORCENTAJE',
            "valor": Decimal("10.0"),
            "descripcion": 'Promocion verano 2026',
            "fecha_inicio": datetime(2026, 9, 12, 17, 31),
            "fecha_fin": datetime(2026, 11, 12, 17, 31),
            "usos_maximos": 50,
            "monto_minimo": Decimal("900.0"),
            "estado": 'ACTIVO',
        },
    ]
    for item in cupones_data:
        res = await db.execute(select(Cupon).where(Cupon.codigo == item["codigo"]))
        cup = res.scalar_one_or_none()
        if not cup:
            cup = Cupon(
                codigo=item["codigo"],
                tipo=TipoCupon[item["tipo"]],
                valor=item["valor"],
                descripcion=item["descripcion"],
                fecha_inicio=item["fecha_inicio"],
                fecha_fin=item["fecha_fin"],
                usos_maximos=item["usos_maximos"],
                usos_actuales=0,
                monto_minimo=item["monto_minimo"],
                estado=EstadoCupon[item["estado"]]
            )
            db.add(cup)
            await db.flush()
            print(f"  [+] Cupón creado: {cup.codigo}")
    print(f"  [+] Cupones verificados: {len(cupones_data)}")


# =====================================================================
# 7. USUARIOS POR ROL (solo crea faltantes, no modifica existentes)
# =====================================================================

async def _asegurar_usuario(
    db: AsyncSession,
    nombre: str,
    apellido: str,
    correo: str,
    telefono: str,
    contrasena_plana: str,
    rol_nombre: str,
    roles_dict: dict
) -> tuple[Usuario, bool]:
    """Crea o recupera un usuario y le asigna su rol si no lo tiene."""
    res = await db.execute(select(Usuario).where(Usuario.correo == correo))
    usuario = res.scalar_one_or_none()
    creado = False
    
    if not usuario:
        usuario = Usuario(
            nombre=nombre,
            apellido=apellido,
            correo=correo,
            telefono=telefono,
            contrasena_hash=get_password_hash(contrasena_plana),
            estado=EstadoUsuario.ACTIVO
        )
        db.add(usuario)
        await db.flush()
        creado = True
    
    if rol_nombre in roles_dict:
        rol = roles_dict[rol_nombre]
        ur_check = await db.execute(
            select(UsuarioRol).where(
                UsuarioRol.usuario_id == usuario.id,
                UsuarioRol.rol_id == rol.id
            )
        )
        if not ur_check.scalar_one_or_none():
            db.add(UsuarioRol(usuario_id=usuario.id, rol_id=rol.id))
            await db.flush()
    
    return usuario, creado


async def seed_usuarios(db: AsyncSession, roles: dict, sucursal: Sucursal):
    """Crea usuarios faltantes (Admin, Encargado, Cajero, Clientes). Nunca modifica existentes."""
    admin_user, admin_creado = await _asegurar_usuario(
        db, nombre="Administrador", apellido="Sistema", correo="admin@fashionstore.com",
        telefono="70000001", contrasena_plana="Admin123!", rol_nombre="Administrador", roles_dict=roles)
    res_adm = await db.execute(select(Administrador).where(Administrador.usuario_id == admin_user.id))
    if not res_adm.scalar_one_or_none():
        db.add(Administrador(usuario_id=admin_user.id))
    print(f"  {'[+] Creado' if admin_creado else '[=] Existente'} Admin: {admin_user.correo} (Contraseña: Admin123!)")

    encargado_user, enc_creado = await _asegurar_usuario(
        db, nombre="Juan", apellido="Pérez", correo="encargado@fashionstore.com",
        telefono="70000002", contrasena_plana="Encargado123!", rol_nombre="Encargado", roles_dict=roles)
    res_enc = await db.execute(select(EncargadoSucursal).where(EncargadoSucursal.usuario_id == encargado_user.id))
    if not res_enc.scalar_one_or_none():
        db.add(EncargadoSucursal(usuario_id=encargado_user.id, sucursal_id=sucursal.id))
    print(f"  {'[+] Creado' if enc_creado else '[=] Existente'} Encargado: {encargado_user.correo} (Contraseña: Encargado123!)")

    cajero_user, caj_creado = await _asegurar_usuario(
        db, nombre="María", apellido="López", correo="cajero@fashionstore.com",
        telefono="70000003", contrasena_plana="Cajero123!", rol_nombre="Cajero", roles_dict=roles)
    res_caj = await db.execute(select(Cajero).where(Cajero.usuario_id == cajero_user.id))
    if not res_caj.scalar_one_or_none():
        db.add(Cajero(usuario_id=cajero_user.id, sucursal_id=sucursal.id))
    print(f"  {'[+] Creado' if caj_creado else '[=] Existente'} Cajero: {cajero_user.correo} (Contraseña: Cajero123!)")

    clientes_data = [
        ('Ana', 'Morales', 'ana.morales@fashionstore.com', '70000045', "Cliente123!", '8765432-1B', 'Calle Murillo #789, La Paz', None, None),
        ('Lucía', 'Fernández', 'lucia.fernandez@fashionstore.com', '70000056', "Cliente123!", '7654321-1C', 'Av. San Martín #1020, Cochabamba', None, None),
        ('Carlos', 'Gómez', 'carlos.gomez@fashionstore.com', '70000024', "Cliente123!", '9876543-1A', 'Av. Las Americas 456, Santa Cruz, Santa Cruz, Santa Cruz', date(2000, 9, 24), {'tallas_habituales': ['L', 'M'], 'estilos_preferidos': ['Urbano'], 'recibir_ofertas': True, 'notificaciones_whatsapp': True}),
    ]
    
    for nom, ape, corr, tel, pwd, nit, dir_env, fec_nac, prefs in clientes_data:
        # Anti-duplicado por NIT: si el cliente ya existe (ej. con correo @test.com), se conserva tal cual
        res_nit = await db.execute(select(Cliente).where(Cliente.nit_ci == nit))
        if res_nit.scalar_one_or_none():
            print(f"  [=] Existente Cliente (NIT {nit}): se conserva sin cambios")
            continue
        cli_user, cli_creado = await _asegurar_usuario(
            db, nombre=nom, apellido=ape, correo=corr, telefono=tel,
            contrasena_plana=pwd, rol_nombre="Cliente", roles_dict=roles)
        res_cli = await db.execute(select(Cliente).where(Cliente.usuario_id == cli_user.id))
        if not res_cli.scalar_one_or_none():
            db.add(Cliente(
                usuario_id=cli_user.id,
                nit_ci=nit,
                direccion_envio=dir_env,
                preferencias=prefs  # columna JSON: dict directo
            ))
            if fec_nac:
                cli_user.fecha_nacimiento = fec_nac
                await db.flush()
        print(f"  {'[+] Creado' if cli_creado else '[=] Existente'} Cliente: {cli_user.correo} (NIT/CI: {nit} | Contraseña: {pwd})")


# =====================================================================
# 8. EJECUTOR PRINCIPAL
# =====================================================================

async def run_seeders():
    """Ejecuta el proceso completo de seeding."""
    print("=" * 65)
    print(" FashionStore - Ejecución de Seeder")
    print("=" * 65)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncSessionLocal() as session:
        try:
            print("\n1. Verificando permisos y roles...")
            permisos = await seed_permisos(session)
            roles = await seed_roles(session, permisos)
            
            print("\n2. Verificando datos básicos del catálogo...")
            await DatosInicialesCatalogoService.crear_datos_iniciales(session)
            await seed_caracteristicas(session)
            
            print("\n3. Verificando Ciudades y Sucursales...")
            sucursales_map = await seed_ciudades_y_sucursales(session)
            sucursal_central = sucursales_map["Sucursal Central"]
            
            print("\n4. Verificando Proveedores, Temporadas y Colecciones...")
            proveedores = await seed_proveedores(session)
            temp_map, col_map = await seed_temporadas_colecciones(session)
            
            print("\n5. Verificando Catálogo de Productos...")
            await seed_productos_y_stock(session, sucursales_map, temp_map, col_map)
            
            print("\n6. Verificando Promociones y Cupones...")
            await seed_promociones(session)
            await seed_cupones(session)
            
            print("\n7. Verificando usuarios por tipo...")
            await seed_usuarios(session, roles, sucursal_central)
            
            await session.commit()
            print("\n" + "=" * 65)
            print(" [OK] Seeder completado exitosamente sin duplicados.")
            print("=" * 65)
        except Exception as e:
            await session.rollback()
            print(f"\n[ERROR] Ocurrió un error durante el seeding: {e}")
            raise e
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_seeders())
