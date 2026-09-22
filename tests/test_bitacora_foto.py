"""Tests del snapshot de bitácora (sin BD)."""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
import enum

from app.apps.gestion_usuarios.services import BitacoraService


class Color(enum.Enum):
    ROJO = "ROJO"


def test_foto_excluye_secretos_y_serializa():
    obj = SimpleNamespace(
        id=5,
        nombre="Ana",
        contrasena_hash="hash-supersecreto",
        precio=Decimal("10.50"),
        fecha=datetime(2026, 1, 2, 3, 4, 5),
        estado=Color.ROJO,
        nulo=None,
    )
    # Simular __table__.columns
    obj.__table__ = SimpleNamespace(columns=[
        SimpleNamespace(key="id"),
        SimpleNamespace(key="nombre"),
        SimpleNamespace(key="contrasena_hash"),
        SimpleNamespace(key="precio"),
        SimpleNamespace(key="fecha"),
        SimpleNamespace(key="estado"),
        SimpleNamespace(key="nulo"),
    ])
    foto = BitacoraService.foto(obj)
    assert foto["id"] == 5
    assert foto["nombre"] == "Ana"
    assert "contrasena_hash" not in foto
    assert foto["precio"] == 10.5
    assert foto["fecha"] == "2026-01-02T03:04:05"
    assert foto["estado"] == "ROJO"
    assert foto["nulo"] is None


def test_foto_none_y_excluir_extra():
    assert BitacoraService.foto(None) is None
    obj = SimpleNamespace(a=1, b=2)
    obj.__table__ = SimpleNamespace(columns=[
        SimpleNamespace(key="a"), SimpleNamespace(key="b")])
    assert BitacoraService.foto(obj, excluir={"b"}) == {"a": 1}
