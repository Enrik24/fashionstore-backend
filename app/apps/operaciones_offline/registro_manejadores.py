"""
[EXAMEN-2] Registro de manejadores del motor de sincronización (plan 4.7).

El Paquete 2 no importa a los Paquetes 4 ni 5. Cada paquete registra sus
manejadores al arrancar y el despachador solo consulta este registro.
"""
from typing import Any, Callable, Dict

from app.exceptions import ManejadorNoRegistrado

Manejador = Callable[..., Any]

_REGISTRO: Dict[str, Manejador] = {}


def inicializar_registro() -> None:
    """Limpia el registro (pruebas y reinicio del proceso)."""
    _REGISTRO.clear()


def registrar_manejador(tipo_operacion: str, funcion: Manejador) -> None:
    """Asocia un tipo de operación con la función que la procesa."""
    clave = str(tipo_operacion).upper()
    if not callable(funcion):
        raise TypeError("El manejador debe ser invocable")
    _REGISTRO[clave] = funcion


def obtener_manejador(tipo_operacion: str) -> Manejador:
    """Devuelve el manejador registrado o lanza `ManejadorNoRegistrado`."""
    clave = str(tipo_operacion).upper()
    manejador = _REGISTRO.get(clave)
    if manejador is None:
        raise ManejadorNoRegistrado(clave)
    return manejador


def listar_manejadores() -> list[str]:
    """Tipos actualmente registrados, útil para diagnóstico y pruebas."""
    return sorted(_REGISTRO.keys())
