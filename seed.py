"""
Script de Seeder para FashionStore Backend.
Puebla la base de datos con datos iniciales completos e idempotentes:
- Permisos y Roles (incluyendo Administrador, Encargado, Cajero, Cliente)
- Datos básicos de catálogo (Categorías, Tallas, Colores)
- Ciudades y Sucursales con coordenadas geográficas
- Proveedores, Temporadas y Colecciones
- Catálogo de Productos reales con URLs de Cloudinary
- Variantes de Producto (combinación Talla x Color)
- Inventario distribuido por Sucursal
- Usuarios por tipo (Admin, Encargado, Cajero, Clientes de prueba)

Este script es IDEMPOTENTE: puede ejecutarse múltiples veces de forma segura
sin duplicar registros ni generar errores de unicidad.

Uso:
    python seed.py
"""
import asyncio
from datetime import date
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
    EstadoProducto, EstadoStock, TipoMovimiento
)
from app.apps.gestion_catalogo.services import DatosInicialesCatalogoService


# =====================================================================
# 1. PERMISOS Y ROLES
# =====================================================================

async def seed_permisos(db: AsyncSession) -> dict:
    """Crea los permisos del sistema si no existen."""
    permisos_data = [
        # Gestión de usuarios
        ("gestionar_usuarios", "Permiso para gestionar usuarios"),
        ("gestionar_roles", "Permiso para gestionar roles y permisos"),
        ("ver_bitacora", "Permiso para ver la bitácora"),
        # Gestión de catálogo
        ("gestionar_ciudades", "Permiso para gestionar ciudades"),
        ("gestionar_sucursales", "Permiso para gestionar sucursales"),
        ("gestionar_productos", "Permiso para gestionar productos"),
        ("gestionar_inventario", "Permiso para gestionar inventario"),
        ("gestionar_categorias", "Permiso para gestionar categorías"),
        ("gestionar_proveedores", "Permiso para gestionar proveedores"),
        # Gestión de ventas
        ("gestionar_ventas", "Permiso para gestionar ventas"),
        ("gestionar_reservas", "Permiso para gestionar reservas"),
        ("procesar_pagos", "Permiso para procesar pagos"),
        # Reportes
        ("generar_reportes", "Permiso para generar reportes"),
        ("ver_kpis", "Permiso para ver indicadores KPIs"),
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
    roles_data = [
        ("Administrador", "Administrador del sistema con acceso completo", list(permisos.keys())),
        ("Cliente", "Cliente de la plataforma", []),
        ("Encargado", "Encargado de sucursal", [
            "gestionar_sucursales", "gestionar_inventario",
            "gestionar_reservas", "gestionar_ventas", "ver_kpis"
        ]),
        ("Cajero", "Cajero de sucursal", [
            "gestionar_ventas", "procesar_pagos"
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
            
            for perm_nombre in perms:
                if perm_nombre in permisos:
                    rp = RolPermiso(rol_id=rol.id, permiso_id=permisos[perm_nombre].id)
                    db.add(rp)
        else:
            # Asegurar permisos faltantes en roles existentes
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
        
        roles[nombre] = rol
    
    print(f"  [+] Roles: {len(roles)} verificados ({creados} creados nuevos)")
    return roles


# =====================================================================
# 2. CIUDADES Y SUCURSALES (CON COORDENADAS)
# =====================================================================

async def seed_ciudades_y_sucursales(db: AsyncSession) -> Dict[str, Sucursal]:
    """Crea ciudades y sucursales con coordenadas geográficas."""
    ciudades_data = [
        ("Santa Cruz de la Sierra", "0000", "Bolivia"),
        ("La Paz", "0000", "Bolivia"),
        ("Cochabamba", "0000", "Bolivia"),
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
        ("Sucursal Central", "Av. Monseñor Rivero #300, Santa Cruz", "33123456", "09:00 - 21:00", -17.7833, -63.1821, "Santa Cruz de la Sierra"),
        ("Sucursal Equipetrol", "Av. San Martín y Calle 5 Este, Santa Cruz", "33987654", "10:00 - 22:00", -17.7654, -63.1950, "Santa Cruz de la Sierra"),
        ("Sucursal Ventura Mall", "4to Anillo y Av. San Martín, Santa Cruz", "33456789", "10:00 - 22:00", -17.7550, -63.1980, "Santa Cruz de la Sierra"),
        ("Sucursal Calacoto", "Av. Ballivián #1200, La Paz", "22789012", "09:00 - 20:00", -16.5390, -68.0890, "La Paz"),
        ("Sucursal Cochabamba Plaza", "Av. Heroínas #450, Cochabamba", "44123456", "09:00 - 20:00", -17.3935, -66.1570, "Cochabamba"),
    ]

    sucursales_map = {}
    for nombre_s, dir_s, tel_s, hor_s, lat_s, lon_s, ciud_nom in sucursales_data:
        res_s = await db.execute(select(Sucursal).where(Sucursal.nombre == nombre_s))
        s_obj = res_s.scalar_one_or_none()
        if not s_obj:
            s_obj = Sucursal(
                nombre=nombre_s,
                direccion=dir_s,
                telefono=tel_s,
                horario_atencion=hor_s,
                estado=EstadoSucursal.ACTIVO,
                latitud=lat_s,
                longitud=lon_s,
                ciudad_id=ciudades_map[ciud_nom].id
            )
            db.add(s_obj)
            await db.flush()
            print(f"  [+] Sucursal creada: {s_obj.nombre}")
        else:
            # Actualizar coordenadas si no las tenía
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
    """Crea proveedores iniciales."""
    proveedores_data = [
        ("Textiles Andinos S.A.", "1029384019", "Juan Valdez", "71234567", "ventas@textilesandinos.bo", "Parque Industrial Mza 12, Santa Cruz"),
        ("Moda Express Bolivia S.R.L.", "2039485028", "Elena Rojas", "72345678", "contacto@modaexpress.bo", "Av. Arce #2145, La Paz"),
        ("Confecciones del Valle", "3049586037", "Mario Suarez", "73456789", "info@confeccionesvalle.bo", "Av. Heroínas #560, Cochabamba"),
    ]
    prov_map = {}
    for nom, nit, cont, tel, corr, direc in proveedores_data:
        res = await db.execute(select(Proveedor).where(Proveedor.nit == nit))
        p = res.scalar_one_or_none()
        if not p:
            p = Proveedor(nombre=nom, nit=nit, contacto=cont, telefono=tel, correo=corr, direccion=direc)
            db.add(p)
            await db.flush()
            print(f"  [+] Proveedor creado: {p.nombre}")
        prov_map[nom] = p
    return prov_map


async def seed_temporadas_colecciones(db: AsyncSession) -> tuple[Temporada, Coleccion]:
    """Crea temporadas y colecciones iniciales."""
    res_temp = await db.execute(select(Temporada).where(Temporada.nombre == "Primavera - Verano 2026"))
    temp = res_temp.scalar_one_or_none()
    if not temp:
        temp = Temporada(
            nombre="Primavera - Verano 2026",
            fecha_inicio=date(2026, 1, 1),
            fecha_fin=date(2026, 6, 30),
            descripcion="Colección fresca y dinámica de prendas y accesorios para la temporada de verano 2026"
        )
        db.add(temp)
        await db.flush()
        print(f"  [+] Temporada creada: {temp.nombre}")

    res_col = await db.execute(select(Coleccion).where(Coleccion.nombre == "Urban Casual & Heritage 2026"))
    col = res_col.scalar_one_or_none()
    if not col:
        col = Coleccion(
            nombre="Urban Casual & Heritage 2026",
            descripcion="Prendas de alta durabilidad, estilo urbano y carácter auténtico",
            temporada_id=temp.id
        )
        db.add(col)
        await db.flush()
        print(f"  [+] Colección creada: {col.nombre}")

    return temp, col


# =====================================================================
# 4. PRODUCTOS CON IMÁGENES CLOUDINARY, VARIANTES E INVENTARIOS
# =====================================================================

async def seed_productos_y_stock(
    db: AsyncSession,
    sucursales: Dict[str, Sucursal],
    proveedor: Proveedor,
    temporada: Temporada,
    coleccion: Coleccion
):
    """
    Registra los 10 productos reales con sus URLs de Cloudinary,
    sus variantes de tallas y colores, e inventario en cada sucursal.
    """
    # Mapeo de Categorías
    cats = (await db.execute(select(Categoria))).scalars().all()
    cat_map = {c.nombre: c.id for c in cats}

    # Mapeo de Tallas y Colores
    tallas = (await db.execute(select(Talla))).scalars().all()
    talla_map = {t.valor: t.id for t in tallas}

    colores = (await db.execute(select(Color))).scalars().all()
    color_map = {c.nombre: c.id for c in colores}

    # Catálogo de Productos Reales (con Cloudinary URLs y descripciones exactas)
    productos_catalogo = [
        {
            "sku": "PRD-3243",
            "nombre": "Polera Everyday Workwear Graphic Tee 5 Pitch Black",
            "descripcion": "Polera casual de cuello redondo, confeccionada en algodón suave y resistente con el clásico logo Caterpillar estampado en el pecho.",
            "precio": Decimal("249.00"),
            "categoria": "Poleras",
            "imagenes": [
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789337535/fashionstore/productos/jr1qkimknvehnmnaz2dx.png"
            ],
            "variantes": [
                {"sku": "PRD-3243-3-1", "talla": "M", "color": "Negro", "stock": {"Sucursal Central": 30, "Sucursal Equipetrol": 15, "Sucursal Ventura Mall": 15, "Sucursal Calacoto": 10, "Sucursal Cochabamba Plaza": 10}},
                {"sku": "PRD-3243-2-1", "talla": "S", "color": "Negro", "stock": {"Sucursal Central": 20, "Sucursal Equipetrol": 10, "Sucursal Ventura Mall": 10, "Sucursal Calacoto": 8, "Sucursal Cochabamba Plaza": 8}},
                {"sku": "PRD-3243-4-1", "talla": "L", "color": "Negro", "stock": {"Sucursal Central": 25, "Sucursal Equipetrol": 12, "Sucursal Ventura Mall": 12, "Sucursal Calacoto": 8, "Sucursal Cochabamba Plaza": 8}},
            ]
        },
        {
            "sku": "PRD-6274",
            "nombre": "Polera Hombre Caterpillar Trademark Logo Tee Laurel Green",
            "descripcion": "Polera de corte regular en tono verde laurel, ideal para el uso diario al aire libre o actividades casuales.",
            "precio": Decimal("249.00"),
            "categoria": "Poleras",
            "imagenes": [
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789102474/fashionstore/productos/ezyk5wbumilcvcfrxcht.jpg",
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789102473/fashionstore/productos/kk5mdkm2qzelcrvindak.jpg"
            ],
            "variantes": [
                {"sku": "PRD-6274-3-6", "talla": "M", "color": "Verde", "stock": {"Sucursal Central": 35, "Sucursal Equipetrol": 15, "Sucursal Ventura Mall": 15, "Sucursal Calacoto": 10, "Sucursal Cochabamba Plaza": 10}},
                {"sku": "PRD-6274-4-6", "talla": "L", "color": "Verde", "stock": {"Sucursal Central": 20, "Sucursal Equipetrol": 10, "Sucursal Ventura Mall": 10, "Sucursal Calacoto": 8, "Sucursal Cochabamba Plaza": 8}},
            ]
        },
        {
            "sku": "PRD-7989",
            "nombre": "CAT Polera Heritage Peoria Graphic Hombre",
            "descripcion": "La Caterpillar Heritage Peoria Graphic es una polera para hombre que rinde homenaje al legado industrial de la marca. Su diseño clásico con gráfico frontal combina comodidad y durabilidad, convirtiéndola en una prenda esencial para un estilo casual con carácter auténtico.",
            "precio": Decimal("249.00"),
            "categoria": "Poleras",
            "imagenes": [
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789102899/fashionstore/productos/htjhwbchptb7qjop1hv8.jpg",
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789102898/fashionstore/productos/zdvbt0fo0m6l8duzifof.jpg"
            ],
            "variantes": [
                {"sku": "PRD-7989-3-3", "talla": "M", "color": "Gris", "stock": {"Sucursal Central": 25, "Sucursal Equipetrol": 12, "Sucursal Ventura Mall": 12, "Sucursal Calacoto": 8, "Sucursal Cochabamba Plaza": 8}},
                {"sku": "PRD-7989-4-3", "talla": "L", "color": "Gris", "stock": {"Sucursal Central": 18, "Sucursal Equipetrol": 10, "Sucursal Ventura Mall": 10, "Sucursal Calacoto": 6, "Sucursal Cochabamba Plaza": 6}},
            ]
        },
        {
            "sku": "PRD-6798",
            "nombre": "Polera Hombre Caterpillar Logo Heather Grey Yellow",
            "descripcion": "Polera juvenil de color Gris con acentos amarillos, suave textura y máxima frescura para el día a día.",
            "precio": Decimal("180.00"),
            "categoria": "Poleras",
            "imagenes": [
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789103014/fashionstore/productos/usof4dhptw8wqpvtvcgb.jpg"
            ],
            "variantes": [
                {"sku": "PRD-6798-3-3", "talla": "M", "color": "Gris", "stock": {"Sucursal Central": 30, "Sucursal Equipetrol": 15, "Sucursal Ventura Mall": 15, "Sucursal Calacoto": 10, "Sucursal Cochabamba Plaza": 10}},
                {"sku": "PRD-6798-2-3", "talla": "S", "color": "Gris", "stock": {"Sucursal Central": 15, "Sucursal Equipetrol": 8, "Sucursal Ventura Mall": 8, "Sucursal Calacoto": 5, "Sucursal Cochabamba Plaza": 5}},
            ]
        },
        {
            "sku": "PRD-2697",
            "nombre": "Polera Hombre Cat Logo Detroit Blue White",
            "descripcion": "Polera para las temporadas de verano en tono azul Detroit, confeccionada en algodón premium respirable.",
            "precio": Decimal("249.00"),
            "categoria": "Poleras",
            "imagenes": [
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789103218/fashionstore/productos/d03vioo1qir4yqpxxetu.jpg"
            ],
            "variantes": [
                {"sku": "PRD-2697-3-4", "talla": "M", "color": "Azul", "stock": {"Sucursal Central": 25, "Sucursal Equipetrol": 12, "Sucursal Ventura Mall": 12, "Sucursal Calacoto": 8, "Sucursal Cochabamba Plaza": 8}},
                {"sku": "PRD-2697-4-4", "talla": "L", "color": "Azul", "stock": {"Sucursal Central": 20, "Sucursal Equipetrol": 10, "Sucursal Ventura Mall": 10, "Sucursal Calacoto": 6, "Sucursal Cochabamba Plaza": 6}},
            ]
        },
        {
            "sku": "PRD-9730",
            "nombre": "CAT Polera Heritage Peoria Graphic Hombre Negro",
            "descripcion": "La Caterpillar Heritage Peoria Graphic Tee es una polera para hombre que rinde homenaje al legado industrial de la marca. Su diseño clásico en color negro profundo combina comodidad y durabilidad.",
            "precio": Decimal("249.00"),
            "categoria": "Poleras",
            "imagenes": [
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789103405/fashionstore/productos/epy4rmnfuwwct1bximd3.jpg",
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789103404/fashionstore/productos/dm2grh2phghazebpaiul.jpg"
            ],
            "variantes": [
                {"sku": "PRD-9730-3-1", "talla": "M", "color": "Negro", "stock": {"Sucursal Central": 25, "Sucursal Equipetrol": 12, "Sucursal Ventura Mall": 12, "Sucursal Calacoto": 8, "Sucursal Cochabamba Plaza": 8}},
                {"sku": "PRD-9730-4-1", "talla": "L", "color": "Negro", "stock": {"Sucursal Central": 20, "Sucursal Equipetrol": 10, "Sucursal Ventura Mall": 10, "Sucursal Calacoto": 6, "Sucursal Cochabamba Plaza": 6}},
            ]
        },
        {
            "sku": "PRD-1009",
            "nombre": "Gorra Cat Logo Hombre Yellow",
            "descripcion": "Gorra clásica ajustable con visera curva y logo CAT bordado en amarillo brillante de alta densidad.",
            "precio": Decimal("210.00"),
            "categoria": "Accesorios",
            "imagenes": [
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789103692/fashionstore/productos/u2pv3wukhjlizyfdytev.jpg",
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789103693/fashionstore/productos/zdm8vsoyesywllqcpoal.jpg"
            ],
            "variantes": [
                {"sku": "PRD-1009-4-7", "talla": "L", "color": "Amarillo", "stock": {"Sucursal Central": 25, "Sucursal Equipetrol": 10, "Sucursal Ventura Mall": 12, "Sucursal Calacoto": 6, "Sucursal Cochabamba Plaza": 6}},
                {"sku": "PRD-1009-3-7", "talla": "M", "color": "Amarillo", "stock": {"Sucursal Central": 20, "Sucursal Equipetrol": 8, "Sucursal Ventura Mall": 10, "Sucursal Calacoto": 5, "Sucursal Cochabamba Plaza": 5}},
            ]
        },
        {
            "sku": "PRD-2630",
            "nombre": "Gorra Caterpillar 100Th Hombre Pitch Black",
            "descripcion": "Gorra de algodón edición especial 100Th aniversario, resistente y ventilada para climas cálidos y uso rudo.",
            "precio": Decimal("280.00"),
            "categoria": "Accesorios",
            "imagenes": [
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789132323/fashionstore/productos/sacencilrabr5qtf3cwa.jpg",
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789132322/fashionstore/productos/bxg2zvs8fjxlunpbdzzv.jpg"
            ],
            "variantes": [
                {"sku": "PRD-2630-4-1", "talla": "L", "color": "Negro", "stock": {"Sucursal Central": 15, "Sucursal Equipetrol": 8, "Sucursal Ventura Mall": 10, "Sucursal Calacoto": 5, "Sucursal Cochabamba Plaza": 5}},
                {"sku": "PRD-2630-3-1", "talla": "M", "color": "Negro", "stock": {"Sucursal Central": 15, "Sucursal Equipetrol": 8, "Sucursal Ventura Mall": 8, "Sucursal Calacoto": 5, "Sucursal Cochabamba Plaza": 5}},
            ]
        },
        {
            "sku": "PRD-9664",
            "nombre": "Gorra Cat Logo Hombre Barn Red-White",
            "descripcion": "Gorra trucker bicolor en rojo granero y blanco con malla transpirable trasera y broche snapback regulable.",
            "precio": Decimal("250.00"),
            "categoria": "Accesorios",
            "imagenes": [
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789133522/fashionstore/productos/uegzagcdbopu7bulx26u.jpg",
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789133522/fashionstore/productos/evns90lew3tysevdar4u.jpg"
            ],
            "variantes": [
                {"sku": "PRD-9664-3-5", "talla": "M", "color": "Rojo", "stock": {"Sucursal Central": 15, "Sucursal Equipetrol": 8, "Sucursal Ventura Mall": 8, "Sucursal Calacoto": 5, "Sucursal Cochabamba Plaza": 5}},
                {"sku": "PRD-9664-4-5", "talla": "L", "color": "Rojo", "stock": {"Sucursal Central": 12, "Sucursal Equipetrol": 6, "Sucursal Ventura Mall": 6, "Sucursal Calacoto": 4, "Sucursal Cochabamba Plaza": 4}},
            ]
        },
        {
            "sku": "PRD-9453",
            "nombre": "CAT Chaqueta Softshell Hombre",
            "descripcion": "La CAT Softshell Jacket está diseñada para ofrecer protección, flexibilidad y confort en actividades dinámicas. Su construcción técnica combina resistencia al clima con una excelente movilidad, convirtiéndola en una prenda ideal para el trabajo activo y el uso diario en exteriores. Equipada con tecnología Storm Blocker®, repele el agua, bloquea el viento y mantiene una óptima transpirabilidad.",
            "precio": Decimal("990.00"),
            "categoria": "Abrigos",
            "imagenes": [
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789133785/fashionstore/productos/ybkjbqrdqvvkepg8nlwy.jpg",
                "https://res.cloudinary.com/dw9etiykm/image/upload/v1789133783/fashionstore/productos/kmkeszphuo7d7z13uacr.jpg"
            ],
            "variantes": [
                {"sku": "PRD-9453-4-1", "talla": "L", "color": "Negro", "stock": {"Sucursal Central": 20, "Sucursal Equipetrol": 10, "Sucursal Ventura Mall": 10, "Sucursal Calacoto": 8, "Sucursal Cochabamba Plaza": 8}},
                {"sku": "PRD-9453-3-1", "talla": "M", "color": "Negro", "stock": {"Sucursal Central": 15, "Sucursal Equipetrol": 8, "Sucursal Ventura Mall": 8, "Sucursal Calacoto": 5, "Sucursal Cochabamba Plaza": 5}},
                {"sku": "PRD-9453-5-1", "talla": "XL", "color": "Negro", "stock": {"Sucursal Central": 10, "Sucursal Equipetrol": 5, "Sucursal Ventura Mall": 5, "Sucursal Calacoto": 4, "Sucursal Cochabamba Plaza": 4}},
            ]
        },
    ]

    total_prods = 0
    total_vars = 0
    total_invs = 0

    for item in productos_catalogo:
        # 1. Asegurar Producto
        res_p = await db.execute(select(Producto).where(Producto.sku == item["sku"]))
        prod = res_p.scalar_one_or_none()
        cat_id = cat_map.get(item["categoria"])

        if not prod:
            prod = Producto(
                sku=item["sku"],
                nombre=item["nombre"],
                descripcion=item["descripcion"],
                precio=item["precio"],
                imagenes=item["imagenes"],
                estado=EstadoProducto.ACTIVO,
                categoria_id=cat_id,
                temporada_id=temporada.id if temporada else None,
                proveedor_id=proveedor.id if proveedor else None
            )
            db.add(prod)
            await db.flush()
            total_prods += 1
            print(f"  [+] Producto creado: {prod.sku} - {prod.nombre[:40]}")
        else:
            # Asegurar que las imágenes y categoria estén al día
            prod.imagenes = item["imagenes"]
            if cat_id:
                prod.categoria_id = cat_id
            await db.flush()

        # Asociar a Colección si no está
        if coleccion:
            res_pc = await db.execute(
                select(ProductoColeccion).where(
                    ProductoColeccion.producto_id == prod.id,
                    ProductoColeccion.coleccion_id == coleccion.id
                )
            )
            if not res_pc.scalar_one_or_none():
                db.add(ProductoColeccion(producto_id=prod.id, coleccion_id=coleccion.id))
                await db.flush()

        # 2. Variantes de Producto
        for v_item in item["variantes"]:
            t_id = talla_map.get(v_item["talla"])
            c_id = color_map.get(v_item["color"])

            res_v = await db.execute(
                select(VarianteProducto).where(VarianteProducto.sku_variante == v_item["sku"])
            )
            var_obj = res_v.scalar_one_or_none()
            if not var_obj:
                var_obj = VarianteProducto(
                    producto_id=prod.id,
                    talla_id=t_id,
                    color_id=c_id,
                    sku_variante=v_item["sku"],
                    precio_variante=item["precio"]
                )
                db.add(var_obj)
                await db.flush()
                total_vars += 1

            # 3. Inventario por Sucursal
            for suc_nombre, cant in v_item["stock"].items():
                if suc_nombre not in sucursales:
                    continue
                suc_obj = sucursales[suc_nombre]

                res_inv = await db.execute(
                    select(Inventario).where(
                        Inventario.variante_producto_id == var_obj.id,
                        Inventario.sucursal_id == suc_obj.id
                    )
                )
                inv_obj = res_inv.scalar_one_or_none()
                if not inv_obj:
                    inv_obj = Inventario(
                        variante_producto_id=var_obj.id,
                        sucursal_id=suc_obj.id,
                        cantidad=cant,
                        cantidad_reservada=0,
                        cantidad_vendida=0,
                        stock_minimo=5,
                        estado=EstadoStock.DISPONIBLE
                    )
                    db.add(inv_obj)
                    await db.flush()
                    total_invs += 1

                    # Registrar movimiento de recepción inicial
                    mov = MovimientoInventario(
                        inventario_id=inv_obj.id,
                        tipo=TipoMovimiento.RECEPCION,
                        cantidad=cant,
                        motivo="Carga inicial de inventario - Catálogo FashionStore"
                    )
                    db.add(mov)
                    await db.flush()

    print(f"  [OK] Catálogo sincronizado: {len(productos_catalogo)} productos ({total_prods} nuevos, {total_vars} variantes nuevas, {total_invs} inventarios creados)")


# =====================================================================
# 5. USUARIOS POR ROL
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
    
    # Asignar rol
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
    """Crea los distintos tipos de usuarios en el sistema (Admin, Encargado, Cajero, Clientes)."""
    
    # 1. Administrador
    admin_user, admin_creado = await _asegurar_usuario(
        db,
        nombre="Administrador",
        apellido="Sistema",
        correo="admin@fashionstore.com",
        telefono="70000001",
        contrasena_plana="Admin123!",
        rol_nombre="Administrador",
        roles_dict=roles
    )
    res_adm = await db.execute(select(Administrador).where(Administrador.usuario_id == admin_user.id))
    if not res_adm.scalar_one_or_none():
        db.add(Administrador(usuario_id=admin_user.id))
    print(f"  {'[+] Creado' if admin_creado else '[=] Existente'} Admin: {admin_user.correo} (Contraseña: Admin123!)")

    # 2. Encargado de Sucursal
    encargado_user, enc_creado = await _asegurar_usuario(
        db,
        nombre="Juan",
        apellido="Pérez",
        correo="encargado@fashionstore.com",
        telefono="70000002",
        contrasena_plana="Encargado123!",
        rol_nombre="Encargado",
        roles_dict=roles
    )
    res_enc = await db.execute(select(EncargadoSucursal).where(EncargadoSucursal.usuario_id == encargado_user.id))
    if not res_enc.scalar_one_or_none():
        db.add(EncargadoSucursal(usuario_id=encargado_user.id, sucursal_id=sucursal.id))
    print(f"  {'[+] Creado' if enc_creado else '[=] Existente'} Encargado: {encargado_user.correo} (Contraseña: Encargado123!)")

    # 3. Cajero
    cajero_user, caj_creado = await _asegurar_usuario(
        db,
        nombre="María",
        apellido="López",
        correo="cajero@fashionstore.com",
        telefono="70000003",
        contrasena_plana="Cajero123!",
        rol_nombre="Cajero",
        roles_dict=roles
    )
    res_caj = await db.execute(select(Cajero).where(Cajero.usuario_id == cajero_user.id))
    if not res_caj.scalar_one_or_none():
        db.add(Cajero(usuario_id=cajero_user.id, sucursal_id=sucursal.id))
    print(f"  {'[+] Creado' if caj_creado else '[=] Existente'} Cajero: {cajero_user.correo} (Contraseña: Cajero123!)")

    # 4. Clientes de prueba
    clientes_data = [
        ("Carlos", "Gómez", "carlos.gomez@test.com", "70000004", "Cliente123!", "9876543-1A", "Av. Las Americas 456, Santa Cruz"),
        ("Ana", "Morales", "ana.morales@test.com", "70000005", "Cliente123!", "8765432-1B", "Calle Murillo #789, La Paz"),
        ("Lucía", "Fernández", "lucia.fernandez@test.com", "70000006", "Cliente123!", "7654321-1C", "Av. San Martín #1020, Cochabamba"),
    ]
    
    for nom, ape, corr, tel, pwd, nit, dir_env in clientes_data:
        cli_user, cli_creado = await _asegurar_usuario(
            db,
            nombre=nom,
            apellido=ape,
            correo=corr,
            telefono=tel,
            contrasena_plana=pwd,
            rol_nombre="Cliente",
            roles_dict=roles
        )
        res_cli = await db.execute(select(Cliente).where(Cliente.usuario_id == cli_user.id))
        if not res_cli.scalar_one_or_none():
            db.add(Cliente(
                usuario_id=cli_user.id,
                nit_ci=nit,
                direccion_envio=dir_env
            ))
        print(f"  {'[+] Creado' if cli_creado else '[=] Existente'} Cliente: {cli_user.correo} (NIT/CI: {nit} | Contraseña: {pwd})")


# =====================================================================
# 6. EJECUTOR PRINCIPAL
# =====================================================================

async def run_seeders():
    """Ejecuta el proceso completo de seeding."""
    print("=" * 65)
    print(" FashionStore - Ejecución de Seeder")
    print("=" * 65)
    
    # Asegurar que las tablas existan
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncSessionLocal() as session:
        try:
            print("\n1. Verificando permisos y roles...")
            permisos = await seed_permisos(session)
            roles = await seed_roles(session, permisos)
            
            print("\n2. Verificando datos básicos del catálogo (Categorías, Tallas, Colores)...")
            await DatosInicialesCatalogoService.crear_datos_iniciales(session)
            
            print("\n3. Verificando Ciudades y Sucursales con coordenadas...")
            sucursales_map = await seed_ciudades_y_sucursales(session)
            sucursal_central = sucursales_map["Sucursal Central"]
            
            print("\n4. Verificando Proveedores, Temporadas y Colecciones...")
            proveedores = await seed_proveedores(session)
            prov_principal = proveedores["Textiles Andinos S.A."]
            temporada, coleccion = await seed_temporadas_colecciones(session)
            
            print("\n5. Verificando y poblando Catálogo de Productos con Cloudinary...")
            await seed_productos_y_stock(
                session,
                sucursales=sucursales_map,
                proveedor=prov_principal,
                temporada=temporada,
                coleccion=coleccion
            )
            
            print("\n6. Verificando y creando usuarios por tipo...")
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
