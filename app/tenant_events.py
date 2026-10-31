"""
[EXAMEN-2] Eventos de SQLAlchemy para el aislamiento multitenant.

Este módulo se importa desde `app.database` y **no** importa `app.security` en
tiempo de importación, para evitar el ciclo
``database -> tenant -> security -> database``.

El plan (4.4) exige que todo servicio filtre por tenant. Para no reescribir los
~5.000 líneas de servicios heredados del Examen 1, se instala una red de
seguridad a nivel de sesión:

* ``SELECT``: agrega ``with_loader_criteria`` a toda entidad con ``tenant_id``.
* ``INSERT``: copia el ``tenant_id`` del contexto y rechaza uno distinto.

Los servicios nuevos de ``gestion_saas`` no se ven afectados porque sus modelos
raíz (`Tenant`, `Plan`) no tienen ``tenant_id``.
"""
from sqlalchemy import event as _sa_event
from sqlalchemy.orm import Session as _SaSession, with_loader_criteria as _wlc

from app.exceptions import TenantNoEncontrado


def _tenant_actual():
    """Import diferido para romper el ciclo de importación."""
    from app.tenant import tenant_actual

    return tenant_actual()


def _modelos_con_tenant():
    from app.database import Base

    return [
        mapper.class_
        for mapper in Base.registry.mappers
        if hasattr(mapper.class_, "tenant_id")
        # [EXAMEN-2] Los roles del sistema son un catálogo global compartido
        # (`roles.nombre` único global, plan 3.6). Si se filtraran por el
        # tenant de la petición, un tenant nuevo no encontraría el rol
        # `Administrador` que reutiliza del seed.
        and mapper.class_.__tablename__ != "roles"
    ]


def _criterio_tenant(tenant_id: int):
    """[EXAMEN-2] Criterio cache-safe: el tenant es una variable de clausura.

    SQLAlchemy incluye las variables de clausura en la clave de caché de las
    sentencias compiladas. Si se pasara como argumento por defecto, dos
    tenants distintos reutilizarían el SQL del primero y se rompería el
    aislamiento.
    """
    def _crit(cls):
        return cls.tenant_id == tenant_id

    return _crit


@_sa_event.listens_for(_SaSession, "do_orm_execute")
def _filtrar_por_tenant(execute_state):
    """Aplica el aislamiento multitenant a cada SELECT de la sesión."""
    if not execute_state.is_select or execute_state.is_column_load:
        return
    tenant_id = _tenant_actual()
    if tenant_id is None:
        return
    if execute_state.execution_options.get("ignorar_tenant"):
        return
    for modelo in _modelos_con_tenant():
        execute_state.statement = execute_state.statement.options(
            _wlc(
                modelo,
                _criterio_tenant(tenant_id),
                include_aliases=True,
            )
        )


@_sa_event.listens_for(_SaSession, "before_flush")
def _asignar_tenant(session, flush_context, instances):
    """Copia el tenant del contexto a las filas nuevas y valida las existentes."""
    tenant_id = _tenant_actual()
    if tenant_id is None:
        return
    for obj in session.new:
        if not hasattr(obj, "tenant_id"):
            continue
        # El rol de plataforma no pertenece a ningún tenant (plan, 3.7.1)
        if obj.__class__.__name__ == "Rol" and getattr(obj, "nombre", None) == "Super Administrador":
            continue
        valor = getattr(obj, "tenant_id", None)
        if valor is None:
            obj.tenant_id = tenant_id
        elif int(valor) != int(tenant_id):
            raise TenantNoEncontrado(
                "El tenant del cuerpo no coincide con el tenant autenticado"
            )
