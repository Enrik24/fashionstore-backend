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
        # "esta sucursal" / "esta tienda" sin indicar cuál: preguntar en vez
        # de ejecutar el reporte con un filtro inventado.
        if re.search(r"\b(esta|la actual|la misma)\s+(sucursal|tienda)\b", texto):
            return ("¿De qué sucursal? Indícala, por ejemplo: "
                    "'de sucursal 3' o 'de Equipetrol'.")
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

    # Números en letras que suelen aparecer en solicitudes de reportes.
    _NUMEROS_TEXTO = {
        "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
        "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
        "quince": 15, "veinte": 20, "treinta": 30, "cincuenta": 50, "cien": 100,
    }

    @classmethod
    def _periodo_relativo(cls, texto: str) -> dict | None:
        """Traduce 'últimos N meses/días/semanas' a fechas concretas.

        Devuelve None si el texto no menciona un período relativo, para que
        el caller aplique su propio default.
        """
        import unicodedata

        t = "".join(
            c for c in unicodedata.normalize("NFKD", (texto or "").lower())
            if not unicodedata.combining(c)
        )
        m = re.search(r"ultimos?\s+(\d+)\s+(dia|dias|semana|semanas|mes|meses)", t)
        if not m:
            return None
        n = max(1, int(m.group(1)))
        unidad = m.group(2)
        fin = date.today() + timedelta(days=1)  # fin exclusivo
        if unidad.startswith("dia"):
            inicio = fin - timedelta(days=n)
        elif unidad.startswith("semana"):
            inicio = fin - timedelta(weeks=n)
        else:  # meses
            import calendar

            mes, anio = fin.month - n, fin.year
            while mes <= 0:
                mes += 12
                anio -= 1
            dia = min(fin.day, calendar.monthrange(anio, mes)[1])
            inicio = date(anio, mes, dia)
        return {"fecha_inicio": inicio.isoformat(), "fecha_fin": fin.isoformat()}

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
        # "últimos 3 meses" sí se traduce a fechas concretas.
        if any(p["tipo"] == "fecha" for p in plantilla["parametros"]):
            interpretacion["parametros"].update(
                cls._periodo_relativo(texto) or cls._periodo_por_defecto()
            )
        if "sucursal" in texto.lower():
            numeros = re.findall(r"sucursal\s*#?(\d+)", texto.lower())
            if numeros:
                interpretacion["parametros"]["sucursal_id"] = int(numeros[0])
        # "los diez productos" / "top 5" -> limite explícito en la heurística.
        if any(p["nombre"] == "limite" for p in plantilla["parametros"]):
            import unicodedata

            t = "".join(
                c for c in unicodedata.normalize("NFKD", texto.lower())
                if not unicodedata.combining(c)
            )
            m = re.search(r"(?:top|primeros|mejores)\s+#?(\d+)", t) or re.search(
                r"\b(" + "|".join(cls._NUMEROS_TEXTO) + r")\b\s+(?:productos|clientes|prendas)", t
            )
            if m:
                interpretacion["parametros"]["limite"] = (
                    int(m.group(1)) if m.group(1).isdigit()
                    else cls._NUMEROS_TEXTO.get(m.group(1))
                )
        return interpretacion

    @classmethod
    async def _interpretar_con_groq(cls, texto: str) -> dict | None:
        if not settings.GROQ_API_KEY:
            return None
        try:
            from app.services.groq_service import GroqService

            cliente = GroqService()
            hoy = date.today()
            manana = (hoy + timedelta(days=1)).isoformat()
            # Catálogo con parámetros válidos: evita que el LLM invente claves
            # que luego PlantillaReporteService.validar_parametros descarta.
            catalogo = "\n".join(
                f"- {p['clave']}: parámetros = "
                + ", ".join(pp["nombre"] for pp in p["parametros"])
                for p in PlantillaReporteService.listar_plantillas()
            )
            prompt = (
                "Eres un intérprete de reportes para un ERP de moda. Elige UNA plantilla "
                "del catálogo y extrae SUS parámetros definidos. Devuelve SOLO JSON con las "
                "claves plantilla, parametros (objeto con solo los parámetros de esa "
                "plantilla), confianza (0-1) y visualizacion.\n"
                "Reglas de extracción:\n"
                f"- Fecha actual: {hoy.isoformat()}. Convierte períodos relativos a fechas "
                "concretas YYYY-MM-DD: 'últimos 3 meses' -> fecha_inicio = 3 meses antes de "
                f"hoy y fecha_fin = {manana} (fin exclusivo); 'este mes' -> día 1 del mes; "
                "'esta semana' -> lunes de esta semana. Nunca devuelvas texto relativo.\n"
                "- Números en letras (diez, cinco, veinte) o 'top N' -> limite entero.\n"
                "- 'agrupados por sucursal' / 'por sucursal' junto a productos vendidos -> "
                "plantilla top_productos_sucursal; si además se nombra UNA sucursal, usa "
                "sucursal_id solo si el texto da su número (los nombres se resuelven después).\n"
                "- No inventes parámetros que la plantilla no define.\n"
                "Catálogo:\n" + catalogo + f"\n\nTexto del usuario: {texto}"
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
        texto_lower = texto.lower()
        # [FIX] El LLM puede inventar filtros que el usuario no mencionó
        # (ej. sucursal_id=1 en "stock crítico"): si el texto no menciona
        # sucursal, se descarta el filtro.
        if interpretacion.get("parametros") and "sucursal" not in texto_lower:
            interpretacion["parametros"].pop("sucursal_id", None)
        # [FIX] Alias libres del LLM -> parámetros canónicos de las plantillas
        # (top/n→limite, sucursal/tienda→sucursal_id). Valores no numéricos
        # para sucursal_id (placeholders tipo "<id>") se descartan.
        params = interpretacion.get("parametros") or {}
        for _alias_orig, _alias_dest in (("top", "limite"), ("n", "limite"),
                                         ("sucursal", "sucursal_id"),
                                         ("tienda", "sucursal_id")):
            if _alias_orig in params:
                if _alias_dest not in params:
                    params[_alias_dest] = params.pop(_alias_orig)
                else:
                    params.pop(_alias_orig)
        if "sucursal_id" in params:
            valor_suc = params["sucursal_id"]
            if isinstance(valor_suc, str) and valor_suc.strip().isdigit():
                params["sucursal_id"] = int(valor_suc)
            elif not isinstance(valor_suc, int):
                params.pop("sucursal_id", None)
        # "el producto más vendido" (singular) -> límite 1, no el top 10 por defecto.
        if ("top" not in texto_lower and "los " not in texto_lower
                and re.search(r"\b(el producto|la prenda|el servicio)\b", texto_lower)):
            params["limite"] = 1
        # [FIX] La heurística rellenaba el período por defecto, pero la ruta
        # Groq no: sin fechas, todo dictado terminaba en AMBIGUA preguntando
        # por el período aunque la validación ya tiene valores por defecto.
        plantilla_ = PlantillaReporteService.mapear_plantilla(interpretacion)
        if plantilla_ and any(p["tipo"] == "fecha" for p in plantilla_["parametros"]):
            if not params.get("fecha_inicio") and not params.get("fecha_fin"):
                params.update(cls._periodo_por_defecto())
        interpretacion["parametros"] = params
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

        # [FIX] Resolver nombres de sucursal mencionados en el texto
        # ("el producto más vendido de Equipetrol") a sucursal_id, porque el
        # LLM devuelve el nombre o placeholders en lugar del id.
        import unicodedata
        plantilla_nombre = PlantillaReporteService.mapear_plantilla(interpretacion)

        def _sin_acentos(t: str) -> str:
            return "".join(
                c for c in unicodedata.normalize("NFKD", t or "") if not unicodedata.combining(c)
            ).lower().strip()

        if plantilla_nombre and any(
                p.get("nombre") == "sucursal_id" for p in plantilla_nombre["parametros"]):
            texto_norm = _sin_acentos(interpretacion.get("texto_original") or "")
            # Solo resuelve nombres si aún no hay sucursal_id numérica.
            if not interpretacion.get("parametros", {}).get("sucursal_id"):
                from app.apps.gestion_catalogo.models import Sucursal
                from sqlalchemy import select as _select
                sucursales_tenant = (await db.execute(
                    _select(Sucursal).where(Sucursal.tenant_id == tenant_id)
                )).scalars().all()
                for suc in sucursales_tenant:
                    clave = _sin_acentos(suc.nombre or "").replace("sucursal", "").strip()
                    if clave and clave in texto_norm:
                        interpretacion.setdefault("parametros", {})["sucursal_id"] = suc.id
                        break

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
