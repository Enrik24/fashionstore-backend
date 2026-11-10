"""Pruebas unitarias CU05 que no requieren la base Neon de integración."""
from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.sql.elements import Null

# Importar main inicializa todos los mapeadores SQLAlchemy usados al compilar
# la consulta de inventario.
import main  # noqa: F401
from app.apps.operaciones_offline.paquete_service import PaqueteOfflineService


class _ScalarsVacios:
    def all(self) -> list[Any]:
        return []


class _ResultadoVacio:
    def scalars(self) -> _ScalarsVacios:
        return _ScalarsVacios()


class _SesionCaptura:
    def __init__(self) -> None:
        self.sentencia = None

    async def execute(self, sentencia):
        self.sentencia = sentencia
        return _ResultadoVacio()


def _criterios_por_columna(sentencia) -> dict[tuple[str, str], Any]:
    criterios: dict[tuple[str, str], Any] = {}
    for criterio in sentencia._where_criteria:
        columna = criterio.left
        valor = getattr(criterio.right, "value", criterio.right)
        criterios[(columna.table.name, columna.name)] = valor
    return criterios


@pytest.mark.asyncio
async def test_stock_sin_sucursal_usa_todas_las_activas_del_tenant() -> None:
    db = _SesionCaptura()

    resultado = await PaqueteOfflineService.obtener_stock_sucursal(db, 5, None)

    assert resultado == []
    criterios = _criterios_por_columna(db.sentencia)
    assert criterios[("inventario", "tenant_id")] == 5
    assert criterios[("sucursales", "tenant_id")] == 5
    assert criterios[("sucursales", "estado")] == "ACTIVO"
    assert isinstance(criterios[("sucursales", "deleted_at")], Null)
    assert ("inventario", "sucursal_id") not in criterios


@pytest.mark.asyncio
async def test_stock_con_sucursal_filtra_sucursal_explicita() -> None:
    db = _SesionCaptura()

    resultado = await PaqueteOfflineService.obtener_stock_sucursal(db, 5, 9)

    assert resultado == []
    criterios = _criterios_por_columna(db.sentencia)
    assert criterios[("inventario", "tenant_id")] == 5
    assert criterios[("sucursales", "tenant_id")] == 5
    assert criterios[("sucursales", "estado")] == "ACTIVO"
    assert isinstance(criterios[("sucursales", "deleted_at")], Null)
    assert criterios[("inventario", "sucursal_id")] == 9
