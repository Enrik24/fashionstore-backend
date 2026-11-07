"""[EXAMEN-2] Reportes generativos seguros (CU10).

Flujo obligatorio del plan 9.3.4: interpretar → validar plan → elegir plantilla
aprobada → construir parámetros → ejecutar con límite de filas → guardar la
solicitud con trazabilidad. La IA nunca ejecuta SQL libre.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import FuncionalidadNoDisponible, NotFoundException, ValidationException
from app.tenant import get_suscripcion_activa
from app.apps.analitica_avanzada.models import (
    EstadoSolicitudReporteIA,
    SolicitudReporteIA,
)
from app.apps.analitica_avanzada.plantilla_service import PlantillaReporteService

logger = logging.getLogger(__name__)

UMBRAL_CONFIANZA = 0.55
PERIODOS_AMBIGUOS = ("este mes", "lo de siempre", "últimamente", "ultimamente", "estos días", "estos dias")


def _json_safe(valor: Any) -> Any:
    """Convierte tipos de base de datos a valores serializables en JSON."""
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, dict):
        return {str(clave): _json_safe(item) for clave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_json_safe(item) for item in valor]
    return valor


class ReporteGenerativoService:
    """Orquesta la interpretación y ejecución de reportes en lenguaje natural."""

    # ------------------------------------------------------------------
    # Interpretación
    # ------------------------------------------------------------------

    @classmethod
    async def transcribir_audio(cls, archivo: bytes, filename: str = "audio.webm") -> str:
        from app.services.groq_service import GroqService

        return await GroqService.transcribir_audio(archivo, filename)

    @classmethod
    def detectar_ambiguedad(cls, interpretacion: dict) -> str | None:
        texto = (interpretacion.get("texto_original") or "").lower()
        if any(frase in texto for frase in PERIODOS_AMBIGUOS):
            return "¿A qué período exacto te refieres? Indica fecha inicial y final."
        if not interpretacion.get("plantilla"):
            return "No pude identificar el reporte. ¿Quieres ventas, stock, reservas o devoluciones?"
        parametros = interpretacion.get("parametros") or {}
        plantilla = PlantillaReporteService.mapear_plantilla(interpretacion)
        if plantilla and any(p["tipo"] == "fecha" for p in plantilla["parametros"]):
            if not parametros.get("fecha_inicio") and not parametros.get("fecha_fin"):
                return "¿Qué período quieres consultar? Por ejemplo: este mes o del 1 al 15 de octubre."
        return None

    @classmethod
    def validar_confianza_interpretacion(cls, interpretacion: dict) -> bool:
        return float(interpretacion.get("confianza") or 0) >= UMBRAL_CONFIANZA

    @classmethod
    def solicitar_aclaracion(cls, pregunta: str) -> dict:
        return {"requiere_aclaracion": True, "pregunta": pregunta}

    @classmethod
    def _periodo_por_defecto(cls) -> dict:
        hoy = date.today()
        inicio = hoy.replace(day=1)
        return {"fecha_inicio": inicio.isoformat(), "fecha_fin": (hoy + timedelta(days=1)).isoformat()}

    @classmethod
    def _interpretar_heuristico(cls, texto: str) -> dict:
        plantilla = PlantillaReporteService.mapear_plantilla(texto)
        interpretacion = {
            "texto_original": texto,
            "plantilla": plantilla["clave"] if plantilla else None,
            "confianza": 0.85 if plantilla else 0.25,
            "parametros": {},
            "visualizacion": plantilla["visualizacion"] if plantilla else "TABLA",
        }
        if plantilla is None:
            return interpretacion

        # Período explícito simple: "del 1 al 15 de octubre" no se intenta
        # adivinar; se usa el mes en curso y el usuario puede ajustar.
        if any(p["tipo"] == "fecha" for p in plantilla["parametros"]):
            interpretacion["parametros"].update(cls._periodo_por_defecto())
        if "sucursal" in texto.lower():
            numeros = re.findall(r"sucursal\s*#?(\d+)", texto.lower())
            if numeros:
                interpretacion["parametros"]["sucursal_id"] = int(numeros[0])
        return interpretacion

    @classmethod
    async def _interpretar_con_groq(cls, texto: str) -> dict | None:
        if not settings.GROQ_API_KEY:
            return None
        try:
            from app.services.groq_service import GroqService

            cliente = GroqService()
            prompt = (
                "Eres un intérprete de reportes. Devuelve SOLO JSON con las claves "
                "plantilla (una de: " + ", ".join(PlantillaReporteService.claves()) + "), "
                "parametros (objeto), confianza (0-1) y visualizacion. "
                f"Texto del usuario: {texto}"
            )
            respuesta = await cliente.client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                response_format={"type": "json_object"},
            )
            import json

            datos = json.loads(respuesta.choices[0].message.content)
            datos["texto_original"] = texto
            return datos
        except Exception as exc:  # pragma: no cover - depende del proveedor
            logger.warning("Groq no disponible para interpretar el reporte: %s", exc)
            return None

    @classmethod
    async def interpretar_solicitud(
        cls,
        tenant_id: int,
        usuario_id: int | None,
        texto: str | None,
        audio: bytes | None = None,
    ) -> dict:
        if audio:
            texto = await cls.transcribir_audio(audio)
        texto = (texto or "").strip()
        if not texto:
            raise ValidationException("La solicitud debe incluir texto o audio")

        interpretacion = await cls._interpretar_con_groq(texto) or cls._interpretar_heuristico(texto)
        if not interpretacion.get("plantilla"):
            plantilla = PlantillaReporteService.mapear_plantilla(texto)
            if plantilla:
                interpretacion["plantilla"] = plantilla["clave"]
                interpretacion["confianza"] = max(0.8, float(interpretacion.get("confianza") or 0))

        if not cls.validar_confianza_interpretacion(interpretacion):
            interpretacion["confianza"] = 0.3
            interpretacion["requiere_aclaracion"] = True
            interpretacion["pregunta"] = cls.detectar_ambiguedad(interpretacion) or (
                "No entendí bien el reporte. ¿Puedes indicar métrica y período?"
            )
            return interpretacion

        pregunta = cls.detectar_ambiguedad(interpretacion)
        if pregunta:
            interpretacion["requiere_aclaracion"] = True
            interpretacion["pregunta"] = pregunta
            interpretacion["parametros"] = interpretacion.get("parametros") or {}
            return interpretacion

        plantilla = PlantillaReporteService.mapear_plantilla(interpretacion)
        interpretacion["parametros"] = PlantillaReporteService.validar_parametros(
            plantilla, interpretacion.get("parametros")
        )
        interpretacion["requiere_aclaracion"] = False
        interpretacion["visualizacion"] = plantilla["visualizacion"]
        return interpretacion

    # ------------------------------------------------------------------
    # Plan y parámetros
    # ------------------------------------------------------------------

    @classmethod
    async def validar_plan_ia(cls, db: AsyncSession, tenant_id: int) -> bool:
        suscripcion = await get_suscripcion_activa(db, tenant_id)
        if suscripcion is None or not getattr(suscripcion.plan, "permite_reportes_ia", False):
            raise FuncionalidadNoDisponible(
                "El plan actual no incluye reportes con inteligencia artificial"
            )
        return True

    @classmethod
    def construir_parametros(cls, interpretacion: dict, tenant_id: int) -> dict:
        plantilla = PlantillaReporteService.mapear_plantilla(interpretacion)
        if plantilla is None:
            raise ValidationException("La interpretación no tiene una plantilla aprobada")
        return PlantillaReporteService.validar_parametros(
            plantilla, interpretacion.get("parametros")
        )

    @classmethod
    def construir_consulta(
        cls, plantilla: dict, parametros: dict, tenant_id: int
    ) -> tuple[str, dict]:
        return PlantillaReporteService.construir_consulta(plantilla, parametros, tenant_id)

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------

    @classmethod
    async def ejecutar_consulta(
        cls, db: AsyncSession, sql: str, parametros: dict, limite: int | None = None
    ) -> tuple[list[dict], int]:
        limite = min(int(limite or settings.IA_MAX_FILAS), settings.IA_MAX_FILAS)
        inicio = time.perf_counter()
        resultado = await db.execute(text(sql), parametros)
        filas = resultado.mappings().fetchmany(limite)
        datos = [{clave: _json_safe(valor) for clave, valor in fila.items()} for fila in filas]
        duracion_ms = int((time.perf_counter() - inicio) * 1000)
        return datos, duracion_ms

    @classmethod
    def seleccionar_visualizacion(cls, resultado: list[dict], intencion: dict) -> str:
        if intencion.get("visualizacion"):
            return intencion["visualizacion"]
        columnas = set(resultado[0].keys()) if resultado else set()
        if "fecha" in columnas:
            return "LINEAS"
        if len(resultado) <= 2:
            return "CIRCULAR"
        if "total" in columnas:
            return "BARRAS"
        return "TABLA"

    @classmethod
    def generar_resumen_narrativo(cls, resultado: list[dict], intencion: dict) -> str:
        if not resultado:
            return "No se encontraron datos para los filtros solicitados."
        totales = [fila.get("total") for fila in resultado if isinstance(fila.get("total"), (int, float))]
        if totales:
            return (
                f"Se encontraron {len(resultado)} filas. El total acumulado es "
                f"{sum(totales):.2f} y el mayor registro alcanza {max(totales):.2f}."
            )
        return f"Se encontraron {len(resultado)} filas para la consulta solicitada."

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    @classmethod
    async def guardar_solicitud(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int | None,
        prompt: str,
        plantilla: str | None,
        parametros: dict | None,
        sql: str | None,
        estado: EstadoSolicitudReporteIA,
        duracion: int | None = None,
        resultado: list[dict] | None = None,
        resumen: dict | None = None,
        error: str | None = None,
    ) -> SolicitudReporteIA:
        solicitud = SolicitudReporteIA(
            tenant_id=tenant_id,
            usuario_id=usuario_id,
            prompt_original=prompt,
            plantilla_usada=plantilla,
            parametros_json=_json_safe(parametros),
            query_sql_generada=sql,
            estado=estado,
            tiempo_ms=duracion,
            resultado_json=_json_safe(resultado),
            resultado_resumen_json=_json_safe(resumen),
            error_detalle=error,
        )
        db.add(solicitud)
        await db.commit()
        await db.refresh(solicitud)
        return solicitud

    @classmethod
    async def solicitar(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int | None,
        texto: str | None,
        audio: bytes | None = None,
    ) -> dict:
        await cls.validar_plan_ia(db, tenant_id)
        interpretacion = await cls.interpretar_solicitud(tenant_id, usuario_id, texto, audio)

        if interpretacion.get("requiere_aclaracion"):
            estado = (
                EstadoSolicitudReporteIA.AMBIGUA
                if interpretacion.get("plantilla")
                else EstadoSolicitudReporteIA.NO_SOPORTADA
            )
            solicitud = await cls.guardar_solicitud(
                db, tenant_id, usuario_id, interpretacion.get("texto_original", ""),
                interpretacion.get("plantilla"), interpretacion.get("parametros"),
                None, estado,
            )
            return {
                "solicitud_id": solicitud.id,
                "estado": estado.value,
                "plantilla": interpretacion.get("plantilla"),
                "requiere_aclaracion": True,
                "pregunta": interpretacion.get("pregunta"),
                "interpretacion": interpretacion,
            }

        plantilla = PlantillaReporteService.mapear_plantilla(interpretacion)
        parametros = interpretacion["parametros"]
        sql, binds = cls.construir_consulta(plantilla, parametros, tenant_id)
        datos, duracion = await cls.ejecutar_consulta(db, sql, binds)
        resumen = {"filas": len(datos), "visualizacion": interpretacion["visualizacion"]}
        resumen["narrativa"] = cls.generar_resumen_narrativo(datos, interpretacion)

        solicitud = await cls.guardar_solicitud(
            db, tenant_id, usuario_id, interpretacion.get("texto_original", ""),
            plantilla["clave"], parametros, sql, EstadoSolicitudReporteIA.EJECUTADA,
            duracion, datos, resumen,
        )
        return cls._serializar(solicitud, interpretacion)

    @classmethod
    async def ejecutar_solicitud(
        cls, db: AsyncSession, solicitud_id: int, tenant_id: int
    ) -> dict:
        solicitud = await cls.obtener_solicitud(db, solicitud_id, tenant_id)
        if solicitud.estado == EstadoSolicitudReporteIA.EJECUTADA:
            return cls._serializar(solicitud, {})
        if not solicitud.plantilla_usada:
            raise ValidationException("La solicitud no tiene una plantilla aprobada")

        plantilla = PlantillaReporteService.mapear_plantilla(solicitud.plantilla_usada)
        parametros = PlantillaReporteService.validar_parametros(
            plantilla, solicitud.parametros_json or {}
        )
        sql, binds = cls.construir_consulta(plantilla, parametros, tenant_id)
        datos, duracion = await cls.ejecutar_consulta(db, sql, binds)
        solicitud.query_sql_generada = sql
        solicitud.parametros_json = parametros
        solicitud.resultado_json = datos
        solicitud.tiempo_ms = duracion
        solicitud.estado = EstadoSolicitudReporteIA.EJECUTADA
        solicitud.resultado_resumen_json = {
            "filas": len(datos),
            "visualizacion": plantilla["visualizacion"],
            "narrativa": cls.generar_resumen_narrativo(datos, {}),
        }
        await db.commit()
        await db.refresh(solicitud)
        return cls._serializar(solicitud, {"visualizacion": plantilla["visualizacion"]})

    @classmethod
    async def obtener_solicitud(
        cls, db: AsyncSession, solicitud_id: int, tenant_id: int
    ) -> SolicitudReporteIA:
        result = await db.execute(
            select(SolicitudReporteIA).where(
                SolicitudReporteIA.id == solicitud_id,
                SolicitudReporteIA.tenant_id == tenant_id,
            )
        )
        solicitud = result.scalar_one_or_none()
        if solicitud is None:
            raise NotFoundException("Solicitud de reporte no encontrada")
        return solicitud

    @classmethod
    async def listar_historial(
        cls, db: AsyncSession, tenant_id: int, skip: int = 0, limit: int = 50
    ) -> list[SolicitudReporteIA]:
        result = await db.execute(
            select(SolicitudReporteIA)
            .where(SolicitudReporteIA.tenant_id == tenant_id)
            .order_by(SolicitudReporteIA.fecha_ejecucion.desc())
            .offset(skip)
            .limit(min(limit, 100))
        )
        return list(result.scalars().all())

    @classmethod
    def listar_plantillas(cls, tenant_id: int | None = None) -> list[dict]:
        return PlantillaReporteService.listar_plantillas(tenant_id)

    @classmethod
    def manejar_fallo_ia(cls, error: Exception | str) -> dict:
        return {
            "error": "IA_NO_DISPONIBLE",
            "mensaje": "No fue posible interpretar la solicitud. Puedes elegir una plantilla manualmente.",
            "detalle": str(error),
            "plantillas": cls.listar_plantillas(),
        }

    @classmethod
    def registrar_metrica_uso(cls, tenant_id: int, exito: bool, duracion: int | None) -> dict:
        logger.info(
            "reporte_ia tenant_id=%s exito=%s duracion_ms=%s", tenant_id, exito, duracion
        )
        return {"tenant_id": tenant_id, "exito": exito, "duracion_ms": duracion}

    @classmethod
    def _serializar(cls, solicitud: SolicitudReporteIA, interpretacion: dict) -> dict:
        resumen = solicitud.resultado_resumen_json or {}
        return {
            "solicitud_id": solicitud.id,
            "estado": solicitud.estado.value if hasattr(solicitud.estado, "value") else solicitud.estado,
            "plantilla": solicitud.plantilla_usada,
            "parametros": solicitud.parametros_json,
            "sql": solicitud.query_sql_generada,
            "resultado": solicitud.resultado_json or [],
            "resumen": resumen,
            "visualizacion": resumen.get("visualizacion") or interpretacion.get("visualizacion", "TABLA"),
            "tiempo_ms": solicitud.tiempo_ms,
            "requiere_aclaracion": False,
        }
