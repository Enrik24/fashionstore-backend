"""
Script de Seeder para FashionStore Backend.
Puebla la base de datos con datos iniciales y todos los tipos de usuarios:
- Administrador
- Encargado de Sucursal
- Cajero
- Clientes

Este script es IDEMPOTENTE: puede ejecutarse múltiples veces de forma segura
sin duplicar registros ni generar errores de unicidad.

Uso:
    python seed.py
"""
import asyncio
import sys
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import engine, Base, AsyncSessionLocal
from app.security import get_password_hash
import app.apps.gestion_ventas.models
import app.apps.servicios_intelientes.models
from app.apps.gestion_usuarios.models import (
    Usuario, Rol, Permiso, UsuarioRol, RolPermiso,
    Cliente, Administrador, EncargadoSucursal, Cajero, EstadoUsuario
)
from app.apps.gestion_catalogo.models import (
    Ciudad, Sucursal, Categoria, Talla, Color, EstadoSucursal
)
from app.apps.gestion_catalogo.services import DatosInicialesCatalogoService


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


async def seed_sucursal_por_defecto(db: AsyncSession) -> Sucursal:
    """Asegura que exista al menos una ciudad y una sucursal para asignar a Encargados y Cajeros."""
    # Ciudad
    res_ciudad = await db.execute(select(Ciudad).where(Ciudad.nombre == "Santa Cruz de la Sierra"))
    ciudad = res_ciudad.scalar_one_or_none()
    if not ciudad:
        ciudad = Ciudad(
            nombre="Santa Cruz de la Sierra",
            codigo_postal="0000",
            pais="Bolivia"
        )
        db.add(ciudad)
        await db.flush()
        print(f"  [+] Ciudad creada: {ciudad.nombre}")
    
    # Sucursal
    res_sucursal = await db.execute(select(Sucursal).where(Sucursal.nombre == "Sucursal Central"))
    sucursal = res_sucursal.scalar_one_or_none()
    if not sucursal:
        sucursal = Sucursal(
            nombre="Sucursal Central",
            direccion="Av. Monseñor Rivero #300, Santa Cruz",
            telefono="33123456",
            horario_atencion="09:00 - 21:00",
            estado=EstadoSucursal.ACTIVO,
            latitud=-17.7833,
            longitud=-63.1821,
            ciudad_id=ciudad.id
        )
        db.add(sucursal)
        await db.flush()
        print(f"  [+] Sucursal creada: {sucursal.nombre}")
    
    return sucursal


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
            
            print("\n2. Verificando datos básicos del catálogo...")
            await DatosInicialesCatalogoService.crear_datos_iniciales(session)
            sucursal = await seed_sucursal_por_defecto(session)
            
            print("\n3. Verificando y creando usuarios por tipo...")
            await seed_usuarios(session, roles, sucursal)
            
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
