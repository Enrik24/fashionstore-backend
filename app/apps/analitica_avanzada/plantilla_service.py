"""[EXAMEN-2] Plantillas aprobadas de reportes (CU10 / RN-009).

La IA elige una plantilla; este servicio traduce la intención a parámetros
tipados y construye la consulta parametrizada con `tenant_id` obligatorio.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from app.exceptions import ValidationException, FuncionalidadNoDisponible
from app.apps.analitica_avanzada.plantillas import (
    PLANTILLAS,
    claves_plantillas,
    obtener_plantilla,
)


class PlantillaReporteService:
    """Catálogo cerrado de reportes y construcción segura de consultas."""

    @classmethod
    def listar_plantillas(cls, tenant_id: int | None = None) -> list[dict]:
        return [
            {
                "clave": p["clave"],
                "nombre": p["nombre"],
                "descripcion": p["descripcion"],
                "categoria": p["categoria"],
                "parametros": p["parametros"],
                "columnas": p["columnas"],
                "visualizacion": p["visualizacion"],
            }
            for p in PLANTILLAS
        ]

    @classmethod
    def verificar_reporte_soportado(cls, intencion: dict | str) -> bool:
        if isinstance(intencion, str):
            return cls.mapear_plantilla(intencion) is not None
        return bool(intencion.get("plantilla")) and obtener_plantilla(intencion["plantilla"]) is not None

    @classmethod
    def mapear_plantilla(cls, intencion: dict | str) -> dict | None:
        """Selecciona la plantilla por clave o por palabras clave del texto."""
        if isinstance(intencion, str):
            texto = intencion.lower()
            mejor: tuple[int, dict | None] = (0, None)
            for plantilla in PLANTILLAS:
                aciertos = sum(1 for kw in plantilla["palabras_clave"] if kw in texto)
                if aciertos > mejor[0]:
                    mejor = (aciertos, plantilla)
            return mejor[1]
        clave = (intencion or {}).get("plantilla")
        return obtener_plantilla(clave) if clave else None

    @classmethod
    def validar_parametros(cls, plantilla: dict, parametros: dict | None) -> dict:
        """Normaliza y valida tipos, rangos y valores permitidos."""
        parametros = dict(parametros or {})
        salida: dict[str, Any] = {}

        for definicion in plantilla["parametros"]:
            nombre = definicion["nombre"]
            valor = parametros.get(nombre, definicion.get("por_defecto"))
            tipo = definicion["tipo"]

            if tipo == "fecha":
                if valor in (None, ""):
                    valor = date.today() - timedelta(days=30)
                valor = cls._a_fecha(valor, nombre)
            elif tipo == "entero":
                if valor in (None, ""):
                    valor = definicion.get("por_defecto") or 1
                try:
                    valor = int(valor)
                except (TypeError, ValueError):
                    raise ValidationException(f"El parámetro {nombre} debe ser entero")
            elif tipo == "texto":
                valor = str(valor) if valor not in (None, "") else None
            elif tipo == "decimal":
                try:
                    valor = float(valor) if valor not in (None, "") else None
                except (TypeError, ValueError):
                    raise ValidationException(f"El parámetro {nombre} debe ser numérico")

            if definicion.get("requerido") and valor in (None, ""):
                raise ValidationException(f"Falta el parámetro obligatorio {nombre}")

            salida[nombre] = valor

        # Cierra el rango de fechas si la plantilla usa fechas.
        if "fecha_inicio" in salida and "fecha_fin" not in salida:
            salida["fecha_fin"] = salida["fecha_inicio"] + timedelta(days=30)
        if "fecha_inicio" in salida and "fecha_fin" in salida:
            if salida["fecha_fin"] <= salida["fecha_inicio"]:
                raise ValidationException("fecha_fin debe ser mayor que fecha_inicio")
        if "limite" in salida:
            salida["limite"] = max(1, min(int(salida["limite"]), 100))

        return salida

    @staticmethod
    def _a_fecha(valor: Any, nombre: str) -> datetime:
        if isinstance(valor, datetime):
            return valor
        if isinstance(valor, date):
            return datetime(valor.year, valor.month, valor.day)
        texto = str(valor).strip()
        for formato in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(texto[:19], formato)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(texto.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise ValidationException(f"El parámetro {nombre} no es una fecha válida")

    @classmethod
    def construir_consulta(
        cls,
        plantilla: dict,
        parametros: dict,
        tenant_id: int | None,
    ) -> tuple[str, dict]:
        """Devuelve (sql, parámetros) con el filtro de tenant incorporado."""
        if tenant_id is None:
            raise ValidationException("El reporte requiere un tenant en contexto")

        parametros = dict(parametros or {})
        parametros["tenant_id"] = int(tenant_id)
        sql = re.sub(r"\s+", " ", plantilla["sql"]).strip()
        cls.validar_filtro_tenant(sql)
        return sql, parametros

    @classmethod
    def validar_filtro_tenant(cls, consulta: str) -> bool:
        """Rechaza cualquier consulta que no restrinja explícitamente el tenant."""
        texto = (consulta or "").lower()
        if not re.search(r"tenant_id\s*=\s*:tenant_id", texto):
            raise FuncionalidadNoDisponible(
                "La consulta del reporte no incluye el filtro de tenant (RN-009)"
            )
        if re.search(r"\bselect\s+\*", texto):
            raise ValidationException("El reporte no puede usar SELECT *")
        return True

    @classmethod
    def claves(cls) -> list[str]:
        return claves_plantillas()
