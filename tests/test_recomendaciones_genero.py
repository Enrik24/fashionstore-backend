"""Tests del filtro duro por género en recomendaciones (sin BD)."""
from types import SimpleNamespace

from app.apps.servicios_inteligentes.services import (
    _genero_dominante,
    _filtrar_productos_por_genero,
)


class FakeEnum:
    def __init__(self, value):
        self.value = value


def prod(id_, genero):
    return SimpleNamespace(id=id_, genero=genero)


def hist(*generos):
    return [{"genero": g} for g in generos]


def test_dominante_hombre_deja_hombre_y_unisex():
    productos = [prod(1, FakeEnum("HOMBRE")), prod(2, FakeEnum("MUJER")),
                 prod(3, FakeEnum("UNISEX")), prod(4, None), prod(5, "HOMBRE")]
    out = _filtrar_productos_por_genero(
        productos, _genero_dominante(hist("HOMBRE", "HOMBRE", "MUJER")))
    assert sorted(p.id for p in out) == [1, 3, 4, 5]


def test_dominante_mujer_deja_mujer_y_unisex():
    productos = [prod(1, FakeEnum("HOMBRE")), prod(2, "MUJER"),
                 prod(3, FakeEnum("UNISEX")), prod(4, None)]
    out = _filtrar_productos_por_genero(
        productos, _genero_dominante(hist("MUJER", "MUJER", "MUJER", "HOMBRE")))
    assert sorted(p.id for p in out) == [2, 3, 4]


def test_empate_deja_los_tres_generos():
    productos = [prod(1, "HOMBRE"), prod(2, "MUJER"), prod(3, "UNISEX")]
    assert _genero_dominante(hist("HOMBRE", "MUJER")) is None
    out = _filtrar_productos_por_genero(productos, None)
    assert sorted(p.id for p in out) == [1, 2, 3]


def test_historial_vacio_deja_todo_como_antes():
    productos = [prod(1, "HOMBRE"), prod(2, "MUJER")]
    assert _genero_dominante([]) is None
    assert _genero_dominante(None) is None
    out = _filtrar_productos_por_genero(productos, _genero_dominante([]))
    assert sorted(p.id for p in out) == [1, 2]


def test_solo_unisex_no_define_dominante():
    assert _genero_dominante(hist("UNISEX", "UNISEX", None)) is None
