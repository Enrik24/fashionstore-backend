"""
Servicio de Inteligencia Artificial utilizando la API de Groq (GPT-OSS / Llama).
Proporciona:
- Recomendaciones personalizadas de productos (CU13)
- Asistente virtual de moda para clientes (CU23)
- Interpretación de comandos de voz para reportes gerenciales (CU22)
- Análisis de tendencias de moda (CU20)
"""
import io
import json
import logging
from typing import List, Dict, Any, Optional
import httpx
from groq import AsyncGroq, Groq

from app.config import settings

logger = logging.getLogger(__name__)


class GroqService:
    """Servicio para interacción con modelos LLM a través de Groq Cloud."""
    
    # Nota: Groq movió "llama-3.3-70b-versatile" y "llama-3.1-8b-instant" a su
    # plan Enterprise ("Contact Sales"). Con una API key estándar/Developer ya
    # devuelven 404 model_not_found, por eso se usan los modelos GPT-OSS públicos.
    MODELO_DEFAULT = "openai/gpt-oss-120b"
    MODELO_RAPIDO = "openai/gpt-oss-20b"
    MODELO_STT = "whisper-large-v3-turbo"

    def __init__(self):
        self.api_key = settings.GROQ_API_KEY
        self.client = (
            AsyncGroq(api_key=self.api_key, http_client=httpx.AsyncClient())
            if self.api_key else None
        )

    def _verificar_cliente(self):
        if not self.client:
            if settings.GROQ_API_KEY:
                self.api_key = settings.GROQ_API_KEY
                self.client = AsyncGroq(api_key=self.api_key, http_client=httpx.AsyncClient())
            else:
                logger.warning("GROQ_API_KEY no configurada. Usando respuestas simuladas/fallback.")

    async def get_recomendaciones(
        self,
        cliente_nombre: str,
        historial_compras: List[Dict[str, Any]],
        productos_catalogo: List[Dict[str, Any]],
        preferencias: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Genera recomendaciones personalizadas basadas en el historial del cliente y el catálogo disponible.
        """
        self._verificar_cliente()
        if not self.client:
            # Fallback inteligente si no hay API Key
            top_prods = productos_catalogo[:4] if productos_catalogo else []
            return {
                "recomendaciones": [
                    {
                        "producto_id": p.get("id"),
                        "nombre": p.get("nombre"),
                        "razon": f"Popular en nuestra colección {p.get('categoria', 'general')}"
                    }
                    for p in top_prods
                ],
                "mensaje_personalizado": f"¡Hola {cliente_nombre}! Te sugerimos estas prendas destacadas seleccionadas para ti.",
                "estilo_detectado": "Casual Contemporáneo"
            }

        prompt_sistema = """Eres el estilista de moda y motor de recomendaciones inteligente de FashionStore.
Tu objetivo es analizar el historial de compras previas o gustos del cliente y seleccionar las mejores prendas complementarias del catálogo disponible.

REGLAS DE RECOMENDACIÓN DE MODA:
1. Combinación y Cross-selling: Si el cliente compró una prenda específica (ej. un pantalón o jean), prioriza recomendar prendas que completen el outfit o hagan match (ej. calzado, poleras, camisas, chaquetas, cinturones o accesorios).
2. Coherencia de género y estilo: Si el cliente suele comprar prendas de hombre o mujer, sugiere prendas afines a su estilo detectado.
3. Catálogo Real: Utiliza ÚNICAMENTE los IDs y nombres de los productos que aparecen en la lista "Catálogo disponible actualmente". NO inventes productos ni IDs.
4. Razón persuasiva y elegante: En el campo "razon", redacta una breve frase que explique por qué combina perfectamente (ej. "Combina idealmente con tu pantalón para un estilo urbano y fresco").

Debes responder ESTRICTAMENTE en formato JSON con la siguiente estructura:
{
  "recomendaciones": [
    {
      "producto_id": 1,
      "nombre": "Nombre de la prenda",
      "razon": "Por qué combina con su compra previa o estilo"
    }
  ],
  "mensaje_personalizado": "Mensaje cordial para el cliente destacando cómo estas sugerencias complementan sus compras recientes",
  "estilo_detectado": "Estilo inferido (ej. Urbano Contemporáneo, Smart Casual, Elegante Noche, etc.)"
}"""

        prompt_usuario = f"""Cliente: {cliente_nombre}
Preferencias expresadas: {preferencias or 'Ninguna'}
Historial de compras recientes: {json.dumps(historial_compras, ensure_ascii=False)}

Catálogo disponible actualmente:
{json.dumps(productos_catalogo, ensure_ascii=False)}

Por favor, selecciona hasta 5 productos recomendados y genera la respuesta JSON."""

        try:
            response = await self.client.chat.completions.create(
                model=self.MODELO_DEFAULT,
                messages=[
                    {"role": "system", "content": prompt_sistema},
                    {"role": "user", "content": prompt_usuario}
                ],
                temperature=0.4,
                response_format={"type": "json_object"}
            )
            raw_content = response.choices[0].message.content
            return json.loads(raw_content)
        except Exception as e:
            logger.error(f"Error al llamar a Groq en get_recomendaciones: {e}")
            top_prods = productos_catalogo[:3] if productos_catalogo else []
            return {
                "recomendaciones": [
                    {
                        "producto_id": p.get("id"),
                        "nombre": p.get("nombre"),
                        "razon": "Recomendación destacada de temporada"
                    }
                    for p in top_prods
                ],
                "mensaje_personalizado": f"¡Hola {cliente_nombre}! Descubre estas tendencias exclusivas.",
                "estilo_detectado": "Tendencia Actual"
            }

    async def chat_asistente(
        self,
        mensaje: str,
        historial_conversacion: List[Dict[str, str]],
        catalogo_resumen: Optional[List[Dict[str, Any]]] = None,
        contexto_cliente: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Asistente de compras interactivo para el cliente.
        """
        self._verificar_cliente()
        if not self.client:
            return {
                "respuesta": "¡Hola! Soy tu asistente de moda en FashionStore. Actualmente estoy en modo de demostración. ¿En qué prenda o estilo te gustaría que te ayude hoy?",
                "sugerencias": ["Ver vestidos de fiesta", "Ropa casual de temporada", "¿Cómo saber mi talla?"],
                "productos_mencionados": [],
                "tipo_respuesta": "texto"
            }

        prompt_sistema = """Eres el Asistente Virtual Inteligente de 'FashionStore'.
Eres un asesor de imagen, estilista y asistente personal de compras para los clientes de FashionStore, amable, elegante, moderno y servicial.
Ayudas a los clientes a encontrar prendas ideales, combinar atuendos según ocasiones (bodas, trabajo, citas, verano, etc.), responder sobre tallas, telas, cuidados, promociones, gestionar su carrito de compras y consultar o generar reportes de sus compras y reservas en tienda.

REGLAS DE PRESENTACIÓN Y ACCIONES (MUY IMPORTANTES):
- NUNCA muestres IDs, códigos internos, SKUs ni referencias técnicas al cliente en el texto de `respuesta`.
- Cuando el cliente pida VER productos (ej. "muéstrame las gorras"): escribe una introducción breve y amigable (máximo 2 frases) y NO listes los productos en el texto; la interfaz mostrará tarjetas visuales con imagen, nombre y precio automáticamente.
- Si el cliente menciona una categoría concreta (gorras, camisas, pantalones, vestidos, etc.), menciona SOLO productos que correspondan a esa categoría.
- Si el cliente pide combinar prendas o armar un look/outfit, propón 2 a 4 prendas complementarias del catálogo y usa "tipo_respuesta": "outfit".
- ACCIÓN DE AGREGAR AL CARRITO: Si el cliente pide agregar el look, outfit o prendas al carrito (ej. "agrega este outfit al carrito", "añade las prendas a mi carrito", "lo quiero comprar todo", "agregar al carrito"):
  1. Devuelve `"accion": "agregar_carrito"`.
  2. En `"productos_mencionados"` coloca la lista de IDs de los productos a agregar (los del outfit o mencionados previamente).
  3. En `"respuesta"` confirma amablemente que has procedido a agregar el look a su carrito.

- ACCIÓN DE REPORTE DE COMPRAS: Si el cliente pide un reporte, historial, resumen o balance de sus compras/pedidos/gastos (ej. "genera un reporte de mis compras", "reporte de mis pedidos en pdf", "cuánto he gastado", "mis compras en excel"):
  1. Devuelve `"accion": "reporte_compras"`.
  2. Usa `"tipo_respuesta": "reporte"`.
  3. Si el cliente mencionó un formato específico ("pdf", "excel", "csv"), colócalo en `"formato_reporte"`; si no, usa `"pdf"`.
  4. En `"respuesta"`, ofrece un resumen conversacional de sus compras (cuántos pedidos tiene, total gastado o estado de sus últimos pedidos) basándote en la información de `Contexto del cliente`, e indícale amablemente que puede descargar su reporte completo con los botones interactivos que aparecen abajo.

- ACCIÓN DE REPORTE DE RESERVAS: Si el cliente pide un reporte, historial o consulta de sus reservas en tienda (ej. "reporte de mis reservas", "qué reservas tengo pendientes", "descargar mis reservas en excel"):
  1. Devuelve `"accion": "reporte_reservas"`.
  2. Usa `"tipo_respuesta": "reporte"`.
  3. Si el cliente mencionó un formato específico ("pdf", "excel", "csv"), colócalo en `"formato_reporte"`; si no, usa `"pdf"`.
  4. En `"respuesta"`, resume brevemente el estado de sus reservas (cuántas activas tiene, en qué sucursal) e indícale que puede descargar el comprobante/reporte con los botones de abajo.

Responde ESTRICTAMENTE en formato JSON con la siguiente estructura:
{
  "respuesta": "Texto de tu respuesta al cliente en tono cercano y profesional, sin IDs ni códigos técnicos",
  "sugerencias": ["Pregunta sugerida 1", "Pregunta sugerida 2", "Pregunta sugerida 3"],
  "productos_mencionados": [1, 2],
  "tipo_respuesta": "texto" | "catalogo" | "producto" | "outfit" | "reporte",
  "accion": "agregar_carrito" | "reporte_compras" | "reporte_reservas" | null,
  "formato_reporte": "pdf" | "excel" | "csv" | null
}"""

        messages = [{"role": "system", "content": prompt_sistema}]

        if catalogo_resumen:
            messages.append({
                "role": "system",
                "content": (
                    "Catálogo disponible de la tienda:\n"
                    f"{json.dumps(catalogo_resumen[:30], ensure_ascii=False)}\n"
                    "Usa la 'descripcion' de cada producto para describir materiales, colores y estilo."
                )
            })

        if contexto_cliente:
            messages.append({
                "role": "system",
                "content": f"Contexto del cliente: {json.dumps(contexto_cliente, ensure_ascii=False)}"
            })

        for msg in historial_conversacion[-6:]:
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

        messages.append({"role": "user", "content": mensaje})

        try:
            response = await self.client.chat.completions.create(
                model=self.MODELO_DEFAULT,
                messages=messages,
                temperature=0.6,
                response_format={"type": "json_object"}
            )
            raw_content = response.choices[0].message.content
            return json.loads(raw_content)
        except Exception as e:
            logger.error(f"Error al procesar chat con Groq: {e}")
            return {
                "respuesta": "Disculpa, hubo una pequeña pausa en mi conexión. ¿Podrías indicarme de nuevo qué estilo o prenda buscas?",
                "sugerencias": ["Ver vestidos", "Ver camisas", "Ofertas del mes"],
                "productos_mencionados": [],
                "tipo_respuesta": "texto"
            }

    async def procesar_comando_voz(
        self,
        transcripcion: str,
        contexto_sistema: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Interpreta un comando o pregunta por voz de un administrador/gerente para generar reportes
        o consultar indicadores.
        """
        self._verificar_cliente()
        if not self.client:
            # Fallback simple basado en palabras clave
            txt = transcripcion.lower()
            tipo = "VENTAS"
            if "inventario" in txt or "stock" in txt:
                tipo = "INVENTARIO"
            elif "reserva" in txt:
                tipo = "RESERVAS"
            elif "cliente" in txt:
                tipo = "CLIENTES"
            elif "financier" in txt or "ganancia" in txt:
                tipo = "FINANCIERO"

            return {
                "tipo_reporte": tipo,
                "accion": "generar_reporte",
                "parametros": {"filtro": "general"},
                "resumen_interpretacion": f"Generación de reporte de {tipo.lower()} a partir del comando de voz: '{transcripcion}'"
            }

        prompt_sistema = """Eres un asistente de Business Intelligence para administradores de la tienda FashionStore.
Tu tarea es interpretar un comando de voz o consulta en lenguaje natural y determinar qué reporte, KPI o análisis estadístico necesita el usuario.

Los tipos de reporte válidos son:
- "VENTAS" (ventas totales, productos más vendidos, métodos de pago, ingresos por fecha)
- "INVENTARIO" (stock bajo, rotación de productos, stock por categoría)
- "RESERVAS" (tasa de conversión, reservas expiradas vs recogidas)
- "CLIENTES" (nuevos registros, clientes más recurrentes)
- "FINANCIERO" (ingresos netos, ticket promedio, desglose por canal presencial/online)

Responde ESTRICTAMENTE en formato JSON con la siguiente estructura:
{
  "tipo_reporte": "VENTAS" | "INVENTARIO" | "RESERVAS" | "CLIENTES" | "FINANCIERO",
  "accion": "generar_reporte" | "consultar_kpi" | "analisis_tendencia",
  "parametros": {
    "fecha_inicio": "YYYY-MM-DD" o null,
    "fecha_fin": "YYYY-MM-DD" o null,
    "categoria_id": int o null,
    "sucursal_id": int o null,
    "limite": int o 10
  },
  "resumen_interpretacion": "Explicación en 1 o 2 oraciones de lo que se consultó y generó."
}"""

        prompt_usuario = f"Comando transcrito: \"{transcripcion}\"\nContexto adicional: {json.dumps(contexto_sistema or {}, ensure_ascii=False)}"

        try:
            response = await self.client.chat.completions.create(
                model=self.MODELO_RAPIDO,
                messages=[
                    {"role": "system", "content": prompt_sistema},
                    {"role": "user", "content": prompt_usuario}
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            raw_content = response.choices[0].message.content
            return json.loads(raw_content)
        except Exception as e:
            logger.error(f"Error procesando comando de voz con Groq: {e}")
            return {
                "tipo_reporte": "VENTAS",
                "accion": "generar_reporte",
                "parametros": {},
                "resumen_interpretacion": f"Reporte de ventas interpretado a partir de: {transcripcion}"
            }

    async def get_tendencias(
        self,
        ventas_resumen: List[Dict[str, Any]],
        temporadas_activas: List[str]
    ) -> Dict[str, Any]:
        """
        Genera un análisis cualitativo y cuantitativo de tendencias de moda basado en datos de ventas.
        """
        self._verificar_cliente()
        if not self.client:
            return {
                "tendencias_destacadas": [
                    {"tendencia": "Colores Tierra y Minimalismo", "impacto": "Alto", "recomendacion": "Aumentar inventario de lino y tonos neutros."},
                    {"tendencia": "Prendas Oversize Urbanas", "impacto": "Medio", "recomendacion": "Mantener variedad en tallas M y L."}
                ],
                "categorias_en_alza": ["Vestidos de Verano", "Camisas Casuales"],
                "prediccion_demanda": "Se proyecta un incremento del 15% en prendas ligeras para las próximas semanas."
            }

        prompt_sistema = """Eres un analista de mercado y tendencias globales de la industria de la moda (Fashion Forecaster).
Analiza las estadísticas de ventas y temporadas provistas para generar un reporte predictivo de tendencias de moda para FashionStore.

Responde ESTRICTAMENTE en formato JSON con la siguiente estructura:
{
  "tendencias_destacadas": [
    {
      "tendencia": "Nombre de la tendencia",
      "impacto": "Alto" | "Medio" | "Bajo",
      "recomendacion": "Recomendación práctica para abastecimiento o marketing"
    }
  ],
  "categorias_en_alza": ["Nombre Categoria 1", "Nombre Categoria 2"],
  "prediccion_demanda": "Resumen del pronóstico de demanda a corto y mediano plazo"
}"""

        prompt_usuario = f"Datos de ventas recientes: {json.dumps(ventas_resumen, ensure_ascii=False)}\nTemporadas vigentes: {', '.join(temporadas_activas)}"

        try:
            response = await self.client.chat.completions.create(
                model=self.MODELO_DEFAULT,
                messages=[
                    {"role": "system", "content": prompt_sistema},
                    {"role": "user", "content": prompt_usuario}
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            raw_content = response.choices[0].message.content
            return json.loads(raw_content)
        except Exception as e:
            logger.error(f"Error al analizar tendencias con Groq: {e}")
            return {
                "tendencias_destacadas": [
                    {"tendencia": "Moda Urbana Versátil", "impacto": "Alto", "recomendacion": "Reforzar stock de básicos y complementos."}
                ],
                "categorias_en_alza": ["Casual", "Temporada actual"],
                "prediccion_demanda": "Demanda sostenida con crecimiento en ventas online."
            }




    async def transcribir_audio(self, audio_bytes: bytes, filename: str = "audio.webm") -> str:
        """
        Transcribe audio a texto usando Whisper de Groq.
        Reemplazo del webkitSpeechRecognition del navegador: el backend
        recibe el blob de audio grabado por getUserMedia/MediaRecorder y
        devuelve el texto transcrito.
        """
        self._verificar_cliente()
        if not self.client:
            logger.warning("GROQ_API_KEY no configurada. No se puede transcribir audio.")
            return ""

        try:
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = filename

            transcript = await self.client.audio.transcriptions.create(
                file=audio_file,
                model=self.MODELO_STT,
                language="es",
                prompt="Comandos de reportes de ventas, inventario, reservas, clientes y financiero en PDF o Excel.",
                temperature=0.0
            )

            texto = (transcript.text or "").strip()

            # Filtrar alucinaciones conocidas de Whisper causadas por silencio o ruido ambiente
            alucinaciones = [
                "suscribete al canal",
                "suscríbete al canal",
                "suscribete",
                "suscríbete",
                "subtitulos por la comunidad",
                "subtítulos por la comunidad",
                "amara.org",
                "gracias por ver el video",
                "gracias por ver",
                "hasta la proxima",
                "hasta la próxima"
            ]
            texto_norm = texto.lower().replace("¡", "").replace("!", "").replace(".", "").strip()
            if any(h in texto_norm for h in alucinaciones) and len(texto) < 40:
                logger.info(f"Alucinación de Whisper por silencio/ambiente descartada: '{texto}'")
                return ""

            logger.info(f"Whisper transcribió: '{texto[:50]}...' ({len(texto)} caracteres)")
            return texto
        except Exception as e:
            logger.error(f"Error transcribiendo audio con Whisper: {e}")
            return ""


groq_service = GroqService()
