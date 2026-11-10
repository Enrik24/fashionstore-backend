"""[EXAMEN-2] Machine learning del dashboard analítico (CU12).

Entrena solo con datos del tenant y excluye los registros sintéticos cuando
existen datos reales: nunca se mezclan ambos orígenes en un mismo
entrenamiento. Cada modelo guarda su artefacto `.joblib` bajo
`ML_RUTA_MODELOS` y sus métricas en `ModeloMachineLearning`.
"""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import NotFoundException, ValidationException
from app.apps.analitica_avanzada.models import (
    ModeloMachineLearning,
    TipoModeloML,
)
from app.apps.gestion_usuarios.models import Cliente
from app.apps.gestion_ventas.models import (
    EstadoOrden,
    Orden,
    SolicitudDevolucion,
)

FEATURES = [
    "dia_semana",
    "dia",
    "mes",
    "fin_semana",
    "tendencia",
    "media_7",
    "devoluciones",
]

OBJETIVOS_SUPERVISADOS = {"VENTAS", "VENTAS_DIARIAS", "PREDICCION_VENTAS"}
OBJETIVOS_NO_SUPERVISADOS = {"SEGMENTACION", "SEGMENTACION_CLIENTES", "ANOMALIAS", "ANOMALIAS_VENTAS"}


def _ruta_modelos() -> Path:
    ruta = Path(settings.ML_RUTA_MODELOS)
    if not ruta.is_absolute():
        ruta = Path.cwd() / ruta
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def _float(valor) -> float:
    return float(valor or 0)


class MLService:
    """Entrenamiento, persistencia y aplicación de modelos del tenant."""

    # ------------------------------------------------------------------
    # Datos
    # ------------------------------------------------------------------

    @classmethod
    async def _medir_origen(
        cls, db: AsyncSession, tenant_id: int, es_sintetico: bool
    ) -> dict:
        """Mide un origen (real o sintético) sin mezclar ambos."""
        resp = await db.execute(
            select(
                func.min(Orden.fecha),
                func.max(Orden.fecha),
                func.count(Orden.id),
                func.count(func.distinct(func.date(Orden.fecha))),
            ).where(
                Orden.tenant_id == tenant_id,
                Orden.es_sintetico.is_(es_sintetico),
            )
        )
        primera, ultima, total, dias_distintos = resp.one()
        if primera is None:
            return {
                "primera": None, "ultima": None, "total": 0,
                "dias_distintos": 0, "meses": 0.0, "suficientes": False,
            }
        p = primera.replace(tzinfo=None) if isinstance(primera, datetime) and primera.tzinfo else primera
        u = ultima.replace(tzinfo=None) if isinstance(ultima, datetime) and ultima.tzinfo else ultima
        dias = max(0, (u.date() - p.date()).days) if p and u else 0
        meses = dias / 30
        total = int(total or 0)
        dias_distintos = int(dias_distintos or 0)
        return {
            "primera": primera, "ultima": ultima, "total": total,
            "dias_distintos": dias_distintos,
            "meses": meses,
            "suficientes": (
                meses >= settings.ML_MIN_MESES_HISTORIAL
                and total >= 30
                and dias_distintos >= 14
            ),
        }

    @classmethod
    async def _origen_datos(cls, db: AsyncSession, tenant_id: int) -> bool:
        """Devuelve `True` si debe usar sintéticos (las reales no alcanzan).

        Se mide el origen real por sí solo. La versión anterior delegaba en
        ``validar_datos_suficientes`` —que ya aplica fallback a sintéticos— y
        por tanto concluía «suficientes» aunque las reales no lo fueran,
        dejando al entrenamiento con 1-3 ventas reales (<14 días).
        """
        reales = await cls._medir_origen(db, tenant_id, False)
        if reales["suficientes"]:
            return False
        sinteticos = await cls._medir_origen(db, tenant_id, True)
        return int(sinteticos["total"] or 0) > 0

    @classmethod
    async def preparar_dataset_ventas(
        cls,
        db: AsyncSession,
        tenant_id: int,
        fecha_inicio: date | datetime | None = None,
        fecha_fin: date | datetime | None = None,
    ) -> pd.DataFrame:
        """Extrae y agrega ventas y devoluciones por día."""
        usar_sinteticos = await cls._origen_datos(db, tenant_id)
        condiciones = [
            Orden.tenant_id == tenant_id,
            Orden.es_sintetico.is_(usar_sinteticos),
        ]
        if fecha_inicio is not None:
            condiciones.append(Orden.fecha >= fecha_inicio)
        if fecha_fin is not None:
            condiciones.append(Orden.fecha < fecha_fin)

        resultado = await db.execute(
            select(
                func.date(Orden.fecha).label("fecha"),
                func.coalesce(func.sum(Orden.total), 0).label("total"),
                func.count(Orden.id).label("ordenes"),
            )
            .where(*condiciones)
            .group_by(func.date(Orden.fecha))
            .order_by(func.date(Orden.fecha))
        )
        filas = [
            {"fecha": fila.fecha, "total": _float(fila.total), "ordenes": int(fila.ordenes or 0)}
            for fila in resultado.all()
        ]

        devoluciones = await db.execute(
            select(
                func.date(SolicitudDevolucion.fecha_solicitud).label("fecha"),
                func.count(SolicitudDevolucion.id).label("devoluciones"),
            )
            .where(SolicitudDevolucion.tenant_id == tenant_id)
            .group_by(func.date(SolicitudDevolucion.fecha_solicitud))
        )
        mapa_devoluciones = {
            fila.fecha: int(fila.devoluciones or 0) for fila in devoluciones.all()
        }

        for fila in filas:
            fila["devoluciones"] = mapa_devoluciones.get(fila["fecha"], 0)

        dataset = pd.DataFrame(filas, columns=["fecha", "total", "ordenes", "devoluciones"])
        if not dataset.empty:
            dataset["fecha"] = pd.to_datetime(dataset["fecha"], utc=True)
        return dataset

    @classmethod
    def construir_features(cls, dataset: pd.DataFrame) -> pd.DataFrame:
        """Agrega variables de calendario, tendencia y media móvil."""
        if dataset is None or dataset.empty:
            return pd.DataFrame(columns=["fecha", "total", *FEATURES])

        datos = dataset.copy()
        datos["fecha"] = pd.to_datetime(datos["fecha"], utc=True)
        datos = datos.sort_values("fecha").reset_index(drop=True)
        datos["dia_semana"] = datos["fecha"].dt.dayofweek.astype(int)
        datos["dia"] = datos["fecha"].dt.day.astype(int)
        datos["mes"] = datos["fecha"].dt.month.astype(int)
        datos["fin_semana"] = (datos["dia_semana"] >= 5).astype(int)
        datos["tendencia"] = np.arange(len(datos), dtype=float)
        datos["media_7"] = (
            datos["total"].shift(1).rolling(window=7, min_periods=1).mean().fillna(0.0)
        )
        datos["devoluciones"] = (
            pd.to_numeric(datos["devoluciones"], errors="coerce").fillna(0.0)
            if "devoluciones" in datos
            else 0.0
        )
        datos["total"] = pd.to_numeric(datos["total"], errors="coerce").fillna(0.0)
        return datos

    @classmethod
    async def validar_datos_suficientes(cls, db: AsyncSession, tenant_id: int) -> dict:
        """Exige `ML_MIN_MESES_HISTORIAL` meses, 30 ventas y 14 días con venta.

        Si hay ventas reales insuficientes (ej: 13), se reintenta con las
        sintéticas sembradas (RF-023); si estas alcanzan el mínimo, el
        entrenamiento usa sintéticos en vez de bloquearse. Devuelve además
        ``origen``/``usar_sinteticos`` para que el caller entrene con el
        mismo origen medido (nunca se mezclan).
        """
        locales = await cls._medir_origen(db, tenant_id, False)
        if locales["suficientes"]:
            return {
                "suficientes": True,
                "meses": round(float(locales["meses"]), 2),
                "ordenes": int(locales["total"]),
                "dias_distintos": int(locales["dias_distintos"]),
                "minimo_meses": settings.ML_MIN_MESES_HISTORIAL,
                "origen": "reales",
                "usar_sinteticos": False,
                "motivo": None,
            }

        sinteticos = await cls._medir_origen(db, tenant_id, True)
        if sinteticos["suficientes"]:
            return {
                "suficientes": True,
                "meses": round(float(sinteticos["meses"]), 2),
                "ordenes": int(sinteticos["total"]),
                "dias_distintos": int(sinteticos["dias_distintos"]),
                "minimo_meses": settings.ML_MIN_MESES_HISTORIAL,
                "origen": "sinteticos",
                "usar_sinteticos": True,
                "motivo": None,
            }

        # Ni reales ni sintéticos alcanzan: reporta el origen con más ventas
        # (útil para el mensaje) sin marcarlo como suficiente.
        elegido = locales if locales["total"] >= sinteticos["total"] else sinteticos
        if elegido["primera"] is None:
            return {
                "suficientes": False,
                "meses": 0,
                "ordenes": 0,
                "dias_distintos": 0,
                "minimo_meses": settings.ML_MIN_MESES_HISTORIAL,
                "origen": None,
                "usar_sinteticos": False,
                "motivo": "El tenant no tiene ventas registradas",
            }
        return {
            "suficientes": False,
            "meses": round(float(elegido["meses"]), 2),
            "ordenes": int(elegido["total"]),
            "dias_distintos": int(elegido["dias_distintos"]),
            "minimo_meses": settings.ML_MIN_MESES_HISTORIAL,
            "origen": "reales" if elegido is locales else "sinteticos",
            "usar_sinteticos": int(sinteticos["total"]) > 0,
            "motivo": "Historial insuficiente para entrenar",
        }

    @classmethod
    def dividir_temporal(
        cls, dataset: pd.DataFrame, proporcion: float = 0.8
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Separa entrenamiento y prueba respetando el orden temporal."""
        if dataset is None or len(dataset) < 2:
            raise ValidationException("Se necesitan al menos 2 períodos para entrenar")
        proporcion = min(max(float(proporcion), 0.5), 0.95)
        corte = max(1, min(len(dataset) - 1, int(len(dataset) * proporcion)))
        return dataset.iloc[:corte].copy(), dataset.iloc[corte:].copy()

    @classmethod
    def calcular_metricas(
        cls, objetivo: str, reales, predicciones, extras: dict | None = None
    ) -> dict:
        """Calcula RMSE, MAE, R², silhouette o tasa de anomalías."""
        extras = extras or {}
        objetivo = (objetivo or "").upper()
        if objetivo in {"SEGMENTACION", "SEGMENTACION_CLIENTES"}:
            return {
                "silhouette": round(float(extras.get("silhouette", -1)), 4),
                "inercia": round(float(extras.get("inercia", 0)), 4),
            }
        if objetivo in {"ANOMALIAS", "ANOMALIAS_VENTAS"}:
            return {
                "tasa_anomalias": round(float(extras.get("tasa_anomalias", 0)), 4),
                "anomalias": int(extras.get("anomalias", 0)),
                "total": int(extras.get("total", 0)),
            }

        reales = np.asarray(reales, dtype=float)
        predicciones = np.asarray(predicciones, dtype=float)
        if reales.size == 0:
            return {"rmse": 0.0, "mae": 0.0, "r2": 0.0, "accuracy": 0.0, "mape": 0.0}
        errores = reales - predicciones
        rmse = float(np.sqrt(np.mean(np.square(errores))))
        mae = float(np.mean(np.abs(errores)))
        if reales.size > 1 and float(np.var(reales)) > 0:
            r2 = float(1 - (np.sum(np.square(errores)) / np.sum(np.square(reales - reales.mean()))))
        else:
            r2 = 0.0
        # Accuracy para regresión: 1 - MAPE (sin contar reales cero).
        # Es el porcentaje de acierto relativo; complementa a RMSE/R² (CU12).
        mask = np.abs(reales) > 1e-9
        if mask.any():
            mape = float(np.mean(np.abs(errores[mask] / reales[mask])))
        else:
            mape = 0.0
        accuracy = max(0.0, min(1.0, 1.0 - mape))
        return {
            "rmse": round(rmse, 4),
            "mae": round(mae, 4),
            "r2": round(r2, 4),
            "accuracy": round(accuracy, 4),
            "mape": round(mape, 4),
            "filas_prueba": int(reales.size),
        }

    @classmethod
    def comparar_modelos(cls, modelos: list[dict]) -> dict:
        """Elige el mejor modelo supervisado por menor RMSE."""
        if not modelos:
            raise ValidationException("No hay modelos para comparar")
        return min(modelos, key=lambda item: float(item["metricas"].get("rmse", math.inf)))

    # ------------------------------------------------------------------
    # Persistencia de artefactos
    # ------------------------------------------------------------------

    @classmethod
    async def guardar_modelo(
        cls,
        db: AsyncSession,
        tenant_id: int,
        nombre: str,
        tipo: TipoModeloML | str,
        algoritmo: str,
        metricas: dict,
        artefacto: dict,
        datos: dict | None = None,
        objetivo: str = "VENTAS",
        version: str = "1.0",
    ) -> ModeloMachineLearning:
        tipo = tipo if isinstance(tipo, TipoModeloML) else TipoModeloML(str(tipo).upper())
        carpeta = _ruta_modelos() / f"tenant_{tenant_id}"
        carpeta.mkdir(parents=True, exist_ok=True)
        archivo = carpeta / f"{nombre}_{uuid.uuid4().hex[:8]}.joblib"
        joblib.dump(artefacto, archivo)

        modelo = ModeloMachineLearning(
            tenant_id=tenant_id,
            nombre=nombre,
            tipo=tipo,
            version=version,
            metricas_json=metricas,
            estado="ACTIVO",
            algoritmo=algoritmo,
            objetivo=objetivo.upper(),
            ruta_artefacto=str(archivo),
            datos_entrenamiento_json=datos,
        )
        db.add(modelo)
        await db.flush()
        return modelo

    @classmethod
    async def cargar_modelo(
        cls, db: AsyncSession, modelo_id: int, tenant_id: int
    ) -> dict:
        modelo = await cls.obtener_modelo(db, modelo_id, tenant_id)
        ruta = Path(modelo.ruta_artefacto)
        if not ruta.exists():
            raise NotFoundException("El artefacto del modelo no está disponible")
        artefacto = joblib.load(ruta)
        return {"modelo": modelo, "artefacto": artefacto}

    # ------------------------------------------------------------------
    # Entrenamiento supervisado
    # ------------------------------------------------------------------

    @classmethod
    async def entrenar_modelo_supervisado(
        cls,
        db: AsyncSession,
        tenant_id: int,
        objetivo: str = "VENTAS",
        hiperparametros: dict | None = None,
    ) -> dict:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.linear_model import LinearRegression

        validacion = await cls.validar_datos_suficientes(db, tenant_id)
        if not validacion["suficientes"]:
            raise ValidationException(
                f"Datos insuficientes: se requieren {settings.ML_MIN_MESES_HISTORIAL} meses "
                f"de historial y al menos 30 ventas (actual: {validacion['meses']} meses, "
                f"{validacion['ordenes']} ventas)"
            )

        crudo = await cls.preparar_dataset_ventas(db, tenant_id)
        dataset = cls.construir_features(crudo)
        if len(dataset) < 14:
            raise ValidationException("Se requieren al menos 14 días con ventas para entrenar")

        hiperparametros = hiperparametros or {}
        entrenamiento, prueba = cls.dividir_temporal(
            dataset, float(hiperparametros.get("proporcion", 0.8))
        )
        X_train = entrenamiento[FEATURES].astype(float)
        y_train = entrenamiento["total"].astype(float)
        X_test = prueba[FEATURES].astype(float)
        y_test = prueba["total"].astype(float)

        semilla = int(hiperparametros.get("semilla", 42))
        candidatos: list[dict] = []

        bosque = RandomForestRegressor(
            n_estimators=int(hiperparametros.get("n_estimators", 120)),
            max_depth=hiperparametros.get("max_depth"),
            random_state=semilla,
            n_jobs=1,
        )
        bosque.fit(X_train, y_train)
        prediccion_bosque = bosque.predict(X_test)
        candidatos.append({
            "algoritmo": "RandomForestRegressor",
            "modelo": bosque,
            "metricas": cls.calcular_metricas(objetivo, y_test, prediccion_bosque),
        })

        regresion = LinearRegression()
        regresion.fit(X_train, y_train)
        prediccion_lineal = regresion.predict(X_test)
        candidatos.append({
            "algoritmo": "LinearRegression",
            "modelo": regresion,
            "metricas": cls.calcular_metricas(objetivo, y_test, prediccion_lineal),
        })

        mejor = cls.comparar_modelos(candidatos)
        metricas = dict(mejor["metricas"])
        metricas["calidad_baja"] = metricas.get("r2", 0.0) < 0.3
        metricas["comparativa"] = {
            c["algoritmo"]: c["metricas"] for c in candidatos
        }
        metricas["semilla"] = semilla
        metricas["filas_entrenamiento"] = int(len(entrenamiento))
        metricas["filas_totales"] = int(len(dataset))
        if metricas["calidad_baja"]:
            metricas["mensaje"] = (
                "El modelo tiene calidad baja (R² < 0,3); las predicciones deben "
                "interpretarse como tendencia, no como certeza."
            )

        artefacto = {
            "modelo": mejor["modelo"],
            "features": FEATURES,
            "objetivo": objetivo.upper(),
            "rmse": metricas.get("rmse", 0.0),
            "ultima_fecha": str(dataset["fecha"].max().date()),
            "ultima_media_7": float(dataset["media_7"].iloc[-1]),
            "ultima_tendencia": float(dataset["tendencia"].iloc[-1]),
            "semilla": semilla,
            "version": "1.0",
        }
        modelo = await cls.guardar_modelo(
            db,
            tenant_id,
            nombre=f"ventas_{objetivo.lower()}_{uuid.uuid4().hex[:6]}",
            tipo=TipoModeloML.SUPERVISADO,
            algoritmo=mejor["algoritmo"],
            metricas=metricas,
            artefacto=artefacto,
            datos={
                "filas": int(len(dataset)),
                "desde": str(dataset["fecha"].min().date()),
                "hasta": str(dataset["fecha"].max().date()),
            },
            objetivo=objetivo,
        )
        return {
            "modelo_id": modelo.id,
            "nombre": modelo.nombre,
            "algoritmo": modelo.algoritmo,
            "objetivo": modelo.objetivo,
            "metricas": metricas,
            "mensaje": cls.explicar_resultado(modelo, metricas),
        }

    # ------------------------------------------------------------------
    # Entrenamiento no supervisado
    # ------------------------------------------------------------------

    @classmethod
    async def entrenar_modelo_no_supervisado(
        cls,
        db: AsyncSession,
        tenant_id: int,
        objetivo: str = "SEGMENTACION_CLIENTES",
        hiperparametros: dict | None = None,
    ) -> dict:
        objetivo_norm = (objetivo or "").upper()
        if objetivo_norm in {"SEGMENTACION", "SEGMENTACION_CLIENTES"}:
            datos = await cls.segmentar_clientes(
                db,
                tenant_id,
                n_clusters=int((hiperparametros or {}).get("n_clusters", 4)),
                persistir=True,
            )
            return datos
        if objetivo_norm in {"ANOMALIAS", "ANOMALIAS_VENTAS"}:
            return await cls.detectar_anomalias(
                db,
                tenant_id,
                periodo_dias=int((hiperparametros or {}).get("periodo_dias", 90)),
                persistir=True,
            )
        raise ValidationException(
            "Objetivo no supervisado no soportado; use SEGMENTACION_CLIENTES o ANOMALIAS_VENTAS"
        )

    # ------------------------------------------------------------------
    # Predicción
    # ------------------------------------------------------------------

    @classmethod
    async def predecir_ventas(
        cls,
        db: AsyncSession,
        tenant_id: int,
        modelo_id: int,
        horizonte: int = 7,
    ) -> dict:
        horizonte = max(1, min(int(horizonte), 90))
        cargado = await cls.cargar_modelo(db, modelo_id, tenant_id)
        modelo = cargado["modelo"]
        artefacto = cargado["artefacto"]

        ultima_fecha = date.fromisoformat(artefacto["ultima_fecha"])
        media = float(artefacto.get("ultima_media_7", 0.0))
        tendencia = float(artefacto.get("ultima_tendencia", 0.0))
        rmse = float(artefacto.get("rmse", 0.0))
        predicciones: list[dict] = []

        for paso in range(1, horizonte + 1):
            fecha = ultima_fecha + timedelta(days=paso)
            tendencia += 1
            fila = {
                "dia_semana": fecha.weekday(),
                "dia": fecha.day,
                "mes": fecha.month,
                "fin_semana": int(fecha.weekday() >= 5),
                "tendencia": tendencia,
                "media_7": media,
                "devoluciones": 0.0,
            }
            X = pd.DataFrame([fila], columns=FEATURES).astype(float)
            valor = max(0.0, float(artefacto["modelo"].predict(X)[0]))
            predicciones.append({
                "fecha": fecha.isoformat(),
                "prediccion": round(valor, 2),
                "limite_inferior": round(max(0.0, valor - 1.96 * rmse), 2),
                "limite_superior": round(valor + 1.96 * rmse, 2),
            })
            media = round((media * 6 + valor) / 7, 4)

        return {
            "modelo_id": modelo_id,
            "algoritmo": modelo.algoritmo,
            "horizonte": horizonte,
            "predicciones": predicciones,
            "total_estimado": round(sum(p["prediccion"] for p in predicciones), 2),
            "intervalo_confianza": 0.95,
            "mensaje": cls.explicar_resultado(modelo, modelo.metricas_json or {}),
        }

    # ------------------------------------------------------------------
    # Segmentación y anomalías
    # ------------------------------------------------------------------

    @classmethod
    async def segmentar_clientes(
        cls, db: AsyncSession, tenant_id: int, n_clusters: int = 4, persistir: bool = False
    ) -> dict:
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score
        from sklearn.preprocessing import StandardScaler

        usar_sinteticos = await cls._origen_datos(db, tenant_id)
        resultado = await db.execute(
            select(
                Orden.cliente_id,
                func.count(Orden.id).label("frecuencia"),
                func.coalesce(func.sum(Orden.total), 0).label("monto"),
                func.coalesce(func.avg(Orden.total), 0).label("ticket"),
                func.max(Orden.fecha).label("ultima"),
            )
            .where(
                Orden.tenant_id == tenant_id,
                Orden.cliente_id.is_not(None),
                Orden.es_sintetico.is_(usar_sinteticos),
            )
            .group_by(Orden.cliente_id)
        )
        filas = resultado.all()
        if len(filas) < 2:
            raise ValidationException(
                "Se necesitan al menos 2 clientes con compras para segmentar"
            )

        hoy = datetime.utcnow()
        registros = []
        for fila in filas:
            ultima = fila.ultima
            if isinstance(ultima, datetime) and ultima.tzinfo is not None:
                ultima = ultima.replace(tzinfo=None)
            recencia = max(0, (hoy - ultima).days) if ultima else 999
            registros.append({
                "cliente_id": fila.cliente_id,
                "frecuencia": int(fila.frecuencia or 0),
                "recencia": recencia,
                "monto": float(fila.monto or 0),
                "ticket_promedio": float(fila.ticket or 0),
            })

        X = np.array([
            [r["frecuencia"], r["recencia"], r["monto"], r["ticket_promedio"]]
            for r in registros
        ], dtype=float)
        X_escalado = StandardScaler().fit_transform(X)

        k = max(2, min(int(n_clusters), len(registros)))
        kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
        etiquetas = kmeans.fit_predict(X_escalado)

        silhouette = -1.0
        if 1 < k < len(registros):
            silhouette = float(silhouette_score(X_escalado, etiquetas))

        for registro, etiqueta in zip(registros, etiquetas):
            registro["segmento"] = int(etiqueta)

        resumen = []
        for etiqueta in range(k):
            miembros = [r for r in registros if r["segmento"] == etiqueta]
            if not miembros:
                continue
            resumen.append({
                "segmento": etiqueta,
                "clientes": len(miembros),
                "frecuencia_promedio": round(sum(r["frecuencia"] for r in miembros) / len(miembros), 2),
                "recencia_promedio_dias": round(sum(r["recencia"] for r in miembros) / len(miembros), 2),
                "monto_promedio": round(sum(r["monto"] for r in miembros) / len(miembros), 2),
                "ticket_promedio": round(sum(r["ticket_promedio"] for r in miembros) / len(miembros), 2),
            })

        metricas = cls.calcular_metricas(
            "SEGMENTACION_CLIENTES", [], [], {"silhouette": silhouette, "inercia": kmeans.inertia_}
        )
        mensaje = (
            "No se observan patrones claros de segmentación (silhouette < 0,3)."
            if silhouette < 0.3
            else "Los segmentos muestran una separación aceptable."
        )

        modelo_id = None
        if persistir:
            artefacto = {
                "modelo": kmeans,
                "scaler": StandardScaler().fit(X),
                "features": ["frecuencia", "recencia", "monto", "ticket_promedio"],
                "centroides": kmeans.cluster_centers_.tolist(),
            }
            modelo = await cls.guardar_modelo(
                db,
                tenant_id,
                nombre=f"segmentacion_{uuid.uuid4().hex[:6]}",
                tipo=TipoModeloML.NO_SUPERVISADO,
                algoritmo="KMeans",
                metricas={**metricas, "n_clusters": k, "mensaje": mensaje},
                artefacto=artefacto,
                datos={"clientes": len(registros)},
                objetivo="SEGMENTACION_CLIENTES",
            )
            modelo_id = modelo.id

        return {
            "modelo_id": modelo_id,
            "algoritmo": "KMeans",
            "n_clusters": k,
            "silhouette": round(silhouette, 4),
            "patrones_claros": silhouette >= 0.3,
            "segmentos": resumen,
            "clientes": registros[:200],
            "mensaje": mensaje,
        }

    @classmethod
    async def detectar_anomalias(
        cls,
        db: AsyncSession,
        tenant_id: int,
        periodo_dias: int = 90,
        persistir: bool = False,
    ) -> dict:
        from sklearn.ensemble import IsolationForest

        desde = datetime.utcnow() - timedelta(days=max(7, int(periodo_dias)))
        resultado = await db.execute(
            select(Orden.id, Orden.total, Orden.fecha, Orden.sucursal_id)
            .where(
                Orden.tenant_id == tenant_id,
                Orden.fecha >= desde,
            )
            .order_by(Orden.fecha.asc())
        )
        filas = resultado.all()
        if len(filas) < 5:
            raise ValidationException(
                "Se necesitan al menos 5 ventas en el período para detectar anomalías"
            )

        montos = np.array([float(fila.total or 0) for fila in filas], dtype=float)
        horas = np.array([
            (fila.fecha.hour if isinstance(fila.fecha, datetime) else 12) for fila in filas
        ], dtype=float)
        dias = np.array([
            (fila.fecha.weekday() if isinstance(fila.fecha, datetime) else 0) for fila in filas
        ], dtype=float)
        sucursales = np.array([float(fila.sucursal_id or 0) for fila in filas], dtype=float)

        X = np.column_stack([montos, horas, dias, sucursales])
        bosque = IsolationForest(random_state=42, contamination="auto")
        etiquetas = bosque.fit_predict(X)

        media = float(montos.mean())
        desviacion = float(montos.std()) or 1.0
        anomalias = []
        for indice, fila in enumerate(filas):
            if etiquetas[indice] != -1:
                continue
            motivos = []
            if abs(float(fila.total or 0) - media) > 2 * desviacion:
                motivos.append("Monto atípico respecto al promedio del período")
            hora = horas[indice]
            if 0 <= hora < 6:
                motivos.append("Venta registrada en horario inusual (madrugada)")
            if dias[indice] >= 5 and float(fila.total or 0) > media * 1.5:
                motivos.append("Monto alto concentrado en fin de semana")
            anomalias.append({
                "orden_id": fila.id,
                "fecha": fila.fecha.isoformat() if isinstance(fila.fecha, datetime) else str(fila.fecha),
                "monto": round(float(fila.total or 0), 2),
                "sucursal_id": fila.sucursal_id,
                "motivos": motivos or ["Patrón poco frecuente detectado por Isolation Forest"],
            })

        tasa = len(anomalias) / len(filas) if filas else 0.0
        metricas = cls.calcular_metricas(
            "ANOMALIAS_VENTAS",
            [],
            [],
            {"tasa_anomalias": tasa, "anomalias": len(anomalias), "total": len(filas)},
        )
        mensaje = (
            f"Se detectaron {len(anomalias)} ventas atípicas de {len(filas)} "
            f"({tasa * 100:.1f}%)."
        )

        modelo_id = None
        if persistir:
            artefacto = {
                "modelo": bosque,
                "features": ["monto", "hora", "dia_semana", "sucursal_id"],
                "media_monto": media,
            }
            modelo = await cls.guardar_modelo(
                db,
                tenant_id,
                nombre=f"anomalias_{uuid.uuid4().hex[:6]}",
                tipo=TipoModeloML.NO_SUPERVISADO,
                algoritmo="IsolationForest",
                metricas={**metricas, "mensaje": mensaje},
                artefacto=artefacto,
                datos={"ventas": len(filas), "periodo_dias": int(periodo_dias)},
                objetivo="ANOMALIAS_VENTAS",
            )
            modelo_id = modelo.id

        return {
            "modelo_id": modelo_id,
            "algoritmo": "IsolationForest",
            "periodo_dias": int(periodo_dias),
            "total_ventas": len(filas),
            "anomalias_detectadas": len(anomalias),
            "tasa_anomalias": round(tasa, 4),
            "anomalias": anomalias[:100],
            "mensaje": mensaje,
        }

    # ------------------------------------------------------------------
    # Consulta y administración de modelos
    # ------------------------------------------------------------------

    @classmethod
    async def listar_modelos(cls, db: AsyncSession, tenant_id: int) -> list[dict]:
        resultado = await db.execute(
            select(ModeloMachineLearning)
            .where(ModeloMachineLearning.tenant_id == tenant_id)
            .order_by(ModeloMachineLearning.fecha_entrenamiento.desc())
        )
        return [cls._serializar_modelo(modelo) for modelo in resultado.scalars().all()]

    @classmethod
    async def obtener_modelo(
        cls, db: AsyncSession, modelo_id: int, tenant_id: int
    ) -> ModeloMachineLearning:
        resultado = await db.execute(
            select(ModeloMachineLearning).where(
                ModeloMachineLearning.id == modelo_id,
                ModeloMachineLearning.tenant_id == tenant_id,
            )
        )
        modelo = resultado.scalar_one_or_none()
        if modelo is None:
            raise NotFoundException("Modelo no encontrado")
        return modelo

    @classmethod
    async def desactivar_modelo(
        cls, db: AsyncSession, modelo_id: int, tenant_id: int
    ) -> dict:
        modelo = await cls.obtener_modelo(db, modelo_id, tenant_id)
        modelo.estado = "INACTIVO"
        await db.flush()
        return cls._serializar_modelo(modelo)

    @classmethod
    async def reentrenar_modelo(
        cls, db: AsyncSession, modelo_id: int, tenant_id: int
    ) -> dict:
        anterior = await cls.obtener_modelo(db, modelo_id, tenant_id)
        objetivo = anterior.objetivo.upper()
        anterior.estado = "INACTIVO"
        await db.flush()
        if objetivo in OBJETIVOS_SUPERVISADOS:
            return await cls.entrenar_modelo_supervisado(db, tenant_id, objetivo)
        return await cls.entrenar_modelo_no_supervisado(db, tenant_id, objetivo)

    @classmethod
    def explicar_resultado(cls, modelo: ModeloMachineLearning, metricas: dict) -> str:
        objetivo = (modelo.objetivo or "").upper()
        if objetivo in OBJETIVOS_NO_SUPERVISADOS:
            if "silhouette" in metricas:
                if float(metricas["silhouette"]) < 0.3:
                    return "No se observan patrones claros entre los clientes agrupados."
                return "La segmentación separa grupos con perfiles distintos."
            return metricas.get("mensaje", "Resultado del análisis no supervisado.")

        r2 = float(metricas.get("r2", 0.0))
        rmse = float(metricas.get("rmse", 0.0))
        accuracy = float(metricas.get("accuracy", 0.0))
        if r2 < 0.3:
            return (
                f"El modelo {modelo.algoritmo} explica poco de la variación (R²={r2:.2f}, "
                f"accuracy={accuracy * 100:.1f}%); el error medio es {rmse:.2f}. "
                f"Precisión baja: se sugiere recolectar más datos o cambiar variables. "
                f"Úsese como referencia."
            )
        return (
            f"El modelo {modelo.algoritmo} explica la variación con R²={r2:.2f}, "
            f"accuracy={accuracy * 100:.1f}% y un error medio (RMSE) de {rmse:.2f}."
        )

    @staticmethod
    def _serializar_modelo(modelo: ModeloMachineLearning) -> dict:
        return {
            "id": modelo.id,
            "nombre": modelo.nombre,
            "tipo": modelo.tipo.value if hasattr(modelo.tipo, "value") else modelo.tipo,
            "version": modelo.version,
            "algoritmo": modelo.algoritmo,
            "objetivo": modelo.objetivo,
            "estado": modelo.estado,
            "metricas": modelo.metricas_json or {},
            "fecha_entrenamiento": (
                modelo.fecha_entrenamiento.isoformat()
                if modelo.fecha_entrenamiento
                else None
            ),
            "datos_entrenamiento": modelo.datos_entrenamiento_json,
        }
