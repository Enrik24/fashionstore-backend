"""[EXAMEN-2] Herencia de `tenant_id` en filas hijas (plan 9.1.10).

Toda fila hija creada hereda el tenant del padre/contexto y ninguna acepta un
`tenant_id` distinto enviado desde el cuerpo o desde el ORM.
"""
import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.exceptions import TenantNoEncontrado
from app.tenant import clear_tenant_context, set_tenant_context
from app.apps.gestion_catalogo.models import Categoria, Color, Inventario, VarianteProducto
from app.apps.gestion_usuarios.models import Rol, UsuarioRol
from app.apps.gestion_ventas.models import (
    Carrito,
    DetalleOrden,
    EstadoCarrito,
    ItemCarrito,
    Orden,
    TipoOrden,
)


@pytest.mark.asyncio
async def test_variante_e_inventario_heredan_el_tenant_del_producto(
    client: AsyncClient, tenant_nuevo, db_session, demo_tenant
):
    tenant = await tenant_nuevo()
    sufijo = uuid.uuid4().hex[:6]

    color = await client.post(
        "/api/v1/colores/",
        json={"nombre": f"Rojo {sufijo}", "codigo_hex": "#FF0000"},
        headers=tenant["headers"],
    )
    assert color.status_code in (200, 201), color.text

    producto = await client.post(
        "/api/v1/productos/",
        json={"sku": f"SKU-{sufijo}", "nombre": "Polera", "precio": 50},
        headers=tenant["headers"],
    )
    assert producto.status_code in (200, 201), producto.text
    producto_id = producto.json()["id"]

    variante = await client.post(
        f"/api/v1/productos/{producto_id}/variantes?cantidad_inicial=4",
        json={"color_id": color.json()["id"], "sku_variante": f"VAR-{sufijo}"},
        headers=tenant["headers"],
    )
    assert variante.status_code in (200, 201), variante.text

    resultado = await db_session.execute(
        select(VarianteProducto).where(VarianteProducto.id == variante.json()["id"])
    )
    registro_variante = resultado.scalar_one()
    assert registro_variante.tenant_id == tenant["id"]

    resultado = await db_session.execute(
        select(Inventario).where(Inventario.variante_producto_id == registro_variante.id)
    )
    inventarios = resultado.scalars().all()
    assert inventarios, "La variante debe generar su fila de inventario"
    assert all(fila.tenant_id == tenant["id"] for fila in inventarios)


@pytest.mark.asyncio
async def test_usuario_rol_hereda_el_tenant(client: AsyncClient, tenant_nuevo, db_session):
    tenant = await tenant_nuevo()

    resultado = await db_session.execute(
        select(UsuarioRol).where(UsuarioRol.usuario_id == tenant["tokens"].get("usuario_id"))
    ) if tenant["tokens"].get("usuario_id") else None

    # El token no expone el id, así que se busca por el administrador creado
    from app.apps.gestion_usuarios.models import Usuario

    resultado = await db_session.execute(
        select(Usuario).where(Usuario.correo == tenant["payload"]["admin_correo"])
    )
    admin = resultado.scalar_one()

    resultado = await db_session.execute(
        select(UsuarioRol).where(UsuarioRol.usuario_id == admin.id)
    )
    asignaciones = resultado.scalars().all()
    assert asignaciones
    assert all(fila.tenant_id == tenant["id"] for fila in asignaciones)

    for asignacion in asignaciones:
        rol = await db_session.get(Rol, asignacion.rol_id)
        assert rol.nombre == "Administrador"


@pytest.mark.asyncio
async def test_orden_y_detalle_heredan_el_tenant_del_contexto(
    client: AsyncClient, tenant_nuevo, db_session
):
    tenant = await tenant_nuevo()
    clear_tenant_context()
    set_tenant_context(tenant["id"], usuario_id=None)
    try:
        orden = Orden(
            numero_orden=f"ORD-{uuid.uuid4().hex[:8]}",
            tipo=TipoOrden.DIGITAL,
            total=120,
        )
        detalle = DetalleOrden(
            cantidad=1, precio_unitario=120, subtotal=120, orden=orden
        )
        db_session.add_all([orden, detalle])
        await db_session.commit()

        assert orden.tenant_id == tenant["id"]
        assert detalle.tenant_id == tenant["id"]
    finally:
        clear_tenant_context()


@pytest.mark.asyncio
async def test_carrito_e_items_heredan_el_tenant_del_contexto(
    client: AsyncClient, tenant_nuevo, db_session
):
    tenant = await tenant_nuevo()
    clear_tenant_context()
    set_tenant_context(tenant["id"])
    try:
        color = Color(nombre=f"Azul {uuid.uuid4().hex[:6]}", codigo_hex="#0000FF")
        db_session.add(color)
        await db_session.flush()
        from app.apps.gestion_catalogo.models import Producto

        producto = Producto(
            sku=f"P-{uuid.uuid4().hex[:8]}", nombre="Pantalón", precio=80
        )
        db_session.add(producto)
        await db_session.flush()
        variante = VarianteProducto(
            producto_id=producto.id,
            color_id=color.id,
            sku_variante=f"V-{uuid.uuid4().hex[:8]}",
        )
        db_session.add(variante)
        await db_session.flush()

        carrito = Carrito(estado=EstadoCarrito.ACTIVO)
        item = ItemCarrito(
            carrito=carrito,
            variante_producto_id=variante.id,
            cantidad=2,
            precio_unitario=80,
            subtotal=160,
        )
        db_session.add_all([carrito, item])
        await db_session.commit()

        assert carrito.tenant_id == tenant["id"]
        assert item.tenant_id == tenant["id"]
        assert producto.tenant_id == tenant["id"]
        assert variante.tenant_id == tenant["id"]
        assert color.tenant_id == tenant["id"]
    finally:
        clear_tenant_context()


@pytest.mark.asyncio
async def test_hija_con_tenant_id_ajeno_es_rechazada(
    client: AsyncClient, tenant_nuevo, db_session, demo_tenant
):
    tenant = await tenant_nuevo()
    clear_tenant_context()
    set_tenant_context(tenant["id"])
    try:
        intrusa = Categoria(
            nombre=f"Intrusa {uuid.uuid4().hex[:6]}", tenant_id=demo_tenant.id
        )
        db_session.add(intrusa)
        with pytest.raises(TenantNoEncontrado):
            await db_session.flush()
    finally:
        await db_session.rollback()
        clear_tenant_context()


@pytest.mark.asyncio
async def test_tenant_id_del_cuerpo_no_sobrescribe_el_del_padre(
    client: AsyncClient, tenant_nuevo, db_session, demo_tenant
):
    tenant = await tenant_nuevo()
    sufijo = uuid.uuid4().hex[:6]

    respuesta = await client.post(
        "/api/v1/productos/",
        json={
            "sku": f"BODY-{sufijo}",
            "nombre": "Camisa",
            "precio": 70,
            "tenant_id": demo_tenant.id,  # intento de fijar otro tenant
        },
        headers=tenant["headers"],
    )

    assert respuesta.status_code in (200, 201), respuesta.text

    resultado = await db_session.execute(
        select(Categoria.id)
    )  # asegura que la sesión sigue operativa
    resultado.scalars().all()

    from app.apps.gestion_catalogo.models import Producto

    resultado = await db_session.execute(
        select(Producto).where(Producto.sku == f"BODY-{sufijo}")
    )
    producto = resultado.scalar_one()
    assert producto.tenant_id == tenant["id"]
    assert producto.tenant_id != demo_tenant.id


@pytest.mark.asyncio
async def test_detalle_de_reserva_no_acepta_tenant_ajeno(
    client: AsyncClient, tenant_nuevo, db_session, demo_tenant
):
    tenant = await tenant_nuevo()
    clear_tenant_context()
    set_tenant_context(tenant["id"])
    try:
        from app.apps.gestion_catalogo.models import Producto

        producto = Producto(sku=f"R-{uuid.uuid4().hex[:8]}", nombre="Falda", precio=90)
        color = Color(nombre=f"Verde {uuid.uuid4().hex[:6]}", codigo_hex="#00FF00")
        db_session.add_all([producto, color])
        await db_session.flush()
        variante = VarianteProducto(
            producto_id=producto.id,
            color_id=color.id,
            sku_variante=f"RV-{uuid.uuid4().hex[:8]}",
        )
        db_session.add(variante)
        await db_session.flush()

        from app.apps.gestion_ventas.models import DetalleReserva, Reserva

        reserva = Reserva(
            numero_reserva=f"RES-{uuid.uuid4().hex[:8]}",
            cliente_id=None,
            sucursal_id=None,
            fecha_reserva=date.today(),
        )
        # El detalle intenta colarse en otro tenant: debe fallar
        detalle = DetalleReserva(
            reserva=reserva,
            variante_producto_id=variante.id,
            cantidad=1,
            tenant_id=demo_tenant.id,
        )
        db_session.add_all([reserva, detalle])
        with pytest.raises(TenantNoEncontrado):
            await db_session.flush()
    finally:
        await db_session.rollback()
        clear_tenant_context()
