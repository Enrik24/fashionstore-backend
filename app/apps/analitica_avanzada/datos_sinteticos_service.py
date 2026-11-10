"""[EXAMEN-2] Generador de datos sintéticos (RF-023 / CU12).

Produce clientes, ventas y movimientos coherentes entre sí, siempre marcados
con `es_sintetico = true`. El dataset generado vive en memoria hasta que se
siembra; el borrado elimina únicamente registros sintéticos del tenant.
"""
from __future__ import annotations

import json
import math
import random
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundException, ValidationException
from app.apps.gestion_catalogo.models import (
    Inventario,
    Sucursal,
    TipoMovimiento,
    VarianteProducto,
)
from app.apps.gestion_catalogo.models import MovimientoInventario
from app.apps.gestion_usuarios.models import Cliente
from app.apps.gestion_ventas.models import (
    Comprobante,
    DetalleOrden,
    EstadoOrden,
    Orden,
    TipoComprobante,
    TipoOrden,
)

# Registro en proceso: dataset_id -> dataset completo.
_DATASETS: dict[str, dict[str, Any]] = {}

CONFIG_DEFECTO = {
    "cantidad_clientes": 15,
    "cantidad_ventas": 120,
    "dias": 120,
    "semilla": 2026,
    "sucursal_id": None,
}


def limpiar_datasets() -> None:
    """Limpia el registro en memoria (pruebas e inicio de proceso)."""
    _DATASETS.clear()


class DatosSinteticosService:
    """Generación, validación, siembra y limpieza de datasets sintéticos."""

    # ------------------------------------------------------------------
    # Catálogo base
    # ------------------------------------------------------------------

    @classmethod
    async def _catalogo_disponible(
        cls, db: AsyncSession, tenant_id: int, sucursal_id: int | None = None
    ) -> list[dict]:
        condiciones = [
            Inventario.tenant_id == tenant_id,
            Sucursal.tenant_id == tenant_id,
            Sucursal.deleted_at.is_(None),
            Inventario.cantidad > 0,
        ]
        if sucursal_id is not None:
            condiciones.append(Inventario.sucursal_id == sucursal_id)

        resultado = await db.execute(
            select(
                Inventario.id,
                Inventario.variante_producto_id,
                Inventario.sucursal_id,
                Inventario.cantidad,
                VarianteProducto.precio_variante,
            )
            .join(Sucursal, Sucursal.id == Inventario.sucursal_id)
            .join(
                VarianteProducto,
                VarianteProducto.id == Inventario.variante_producto_id,
            )
            .where(*condiciones)
        )
        catalogo = []
        for fila in resultado.all():
            catalogo.append({
                "inventario_id": fila.id,
                "variante_id": fila.variante_producto_id,
                "sucursal_id": fila.sucursal_id,
                "disponible": int(fila.cantidad or 0),
                "precio": float(fila.precio_variante or 0),
            })
        if not catalogo:
            raise ValidationException(
                "El tenant no tiene variantes con stock para generar datos sintéticos"
            )
        return catalogo

    # ------------------------------------------------------------------
    # Generación
    # ------------------------------------------------------------------

    @classmethod
    async def generar_dataset(
        cls,
        db: AsyncSession,
        tenant_id: int,
        configuracion: dict | None = None,
    ) -> dict:
        configuracion = {**CONFIG_DEFECTO, **(configuracion or {})}
        cantidad_clientes = max(1, min(int(configuracion["cantidad_clientes"]), 500))
        cantidad_ventas = max(10, min(int(configuracion["cantidad_ventas"]), 5000))
        dias = max(30, min(int(configuracion["dias"]), 730))
        semilla = int(configuracion["semilla"])
        sucursal_id = configuracion.get("sucursal_id")

        catalogo = await cls._catalogo_disponible(db, tenant_id, sucursal_id)

        dataset_id = uuid.uuid4().hex
        # [BUGFIX] Los identificadores sintéticos deben ser únicos por
        # dataset, no por semilla: con la misma semilla (la UI siempre usa
        # 2026) el segundo `sembrar` violaba UNIQUE(tenant, nit_ci) y
        # UNIQUE(tenant, numero_orden) → 500 Internal Server Error sin CORS.
        sufijo_dataset = dataset_id[:8]
        clientes = cls.generar_clientes_sinteticos(
            cantidad_clientes, semilla=semilla, sufijo=sufijo_dataset
        )
        ventas = cls.generar_ventas_sinteticas(
            cantidad_ventas,
            dias=dias,
            catalogo=catalogo,
            cantidad_clientes=len(clientes),
            semilla=semilla,
            sufijo=sufijo_dataset,
        )
        movimientos = cls.generar_movimientos_sinteticos(ventas)

        dataset = {
            "dataset_id": dataset_id,
            "tenant_id": tenant_id,
            "configuracion": {**configuracion, "dias": dias},
            "clientes": clientes,
            "ventas": ventas,
            "movimientos": movimientos,
            "stock_inicial": {
                str(item["inventario_id"]): item["disponible"] for item in catalogo
            },
            "creado_en": datetime.utcnow().isoformat(),
            "sembrado": False,
        }
        dataset = cls.etiquetar_sinteticos(dataset)
        dataset["validacion"] = cls.validar_coherencia(dataset)
        if not dataset["validacion"]["ok"]:
            raise ValidationException(
                "El dataset generado no es coherente: "
                + "; ".join(dataset["validacion"]["errores"][:3])
            )
        _DATASETS[dataset_id] = dataset
        return cls._resumen_dataset(dataset)

    @classmethod
    def generar_clientes_sinteticos(
        cls, cantidad: int, semilla: int = 2026, sufijo: str | None = None
    ) -> list[dict]:
        rng = random.Random(semilla)
        nombres = ["Ana", "Luis", "María", "Carlos", "Sofía", "Diego", "Lucía", "Jorge", "Elena", "Pedro"]
        apellidos = ["Quispe", "Mamani", "Rojas", "Flores", "Vargas", "Cruz", "Choque", "López"]
        clientes = []
        # Con `sufijo` (dataset_id[:8]) cada generación es única aunque
        # comparta semilla; sin sufijo se conserva el formato legado.
        marca = sufijo or str(semilla)
        for indice in range(cantidad):
            codigo = f"{marca}{indice:04d}"
            clientes.append({
                "indice": indice,
                "nit_ci": f"SYN-{codigo}",
                "correo": f"sintetico{codigo}@example.com",
                "telefono": f"7{rng.randint(1000000, 9999999)}",
                "direccion_envio": f"Calle Sintética {rng.randint(1, 999)}",
                "nombre": f"{rng.choice(nombres)} {rng.choice(apellidos)}",
                "es_sintetico": True,
            })
        return clientes

    @classmethod
    def generar_ventas_sinteticas(
        cls,
        cantidad: int,
        dias: int = 120,
        catalogo: list[dict] | None = None,
        cantidad_clientes: int = 10,
        semilla: int = 2026,
        sufijo: str | None = None,
    ) -> list[dict]:
        if not catalogo:
            raise ValidationException("Se requiere catálogo con stock para generar ventas")
        rng = random.Random(semilla + 1)
        hoy = date.today()
        uso_por_inventario: dict[int, int] = {}
        ventas: list[dict] = []

        for indice in range(cantidad):
            # Estacionalidad: más ventas hacia el final del período y los fines de semana.
            sesgo = rng.random() ** 0.75
            dias_atras = int((1 - sesgo) * dias)
            fecha = hoy - timedelta(days=dias_atras)
            if fecha.weekday() >= 5 and rng.random() < 0.65:
                fecha = fecha - timedelta(days=rng.randint(0, 2))
            hora = rng.choices(
                [9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20],
                weights=[3, 5, 6, 8, 6, 5, 6, 7, 8, 6, 3],
            )[0]
            fecha_hora = datetime.combine(fecha, time(hour=hora, minute=rng.randint(0, 59)))

            lineas = []
            usados: set[int] = set()
            for _ in range(rng.randint(1, 3)):
                candidatos = [
                    item for item in catalogo
                    if item["inventario_id"] not in usados
                    and uso_por_inventario.get(item["inventario_id"], 0) < item["disponible"]
                ]
                if not candidatos:
                    break
                item = rng.choice(candidatos)
                usados.add(item["inventario_id"])
                cantidad_linea = rng.randint(1, 2)
                disponible_restante = (
                    item["disponible"] - uso_por_inventario.get(item["inventario_id"], 0)
                )
                cantidad_linea = min(cantidad_linea, disponible_restante)
                if cantidad_linea <= 0:
                    continue
                precio = round(item["precio"] * rng.uniform(0.95, 1.05), 2)
                subtotal = round(precio * cantidad_linea, 2)
                uso_por_inventario[item["inventario_id"]] = (
                    uso_por_inventario.get(item["inventario_id"], 0) + cantidad_linea
                )
                lineas.append({
                    "inventario_id": item["inventario_id"],
                    "variante_id": item["variante_id"],
                    "sucursal_id": item["sucursal_id"],
                    "cantidad": cantidad_linea,
                    "precio_unitario": precio,
                    "subtotal": subtotal,
                })

            if not lineas:
                continue

            total = round(sum(linea["subtotal"] for linea in lineas), 2)
            ventas.append({
                "indice": len(ventas),
                "numero_orden": f"SYN-{sufijo or semilla}-{indice:05d}",
                "fecha": fecha_hora.isoformat(),
                "cliente_indice": rng.randrange(max(1, cantidad_clientes)),
                "sucursal_id": lineas[0]["sucursal_id"],
                "total": total,
                "impuestos": round(total * 0.13, 2),
                "descuentos": 0.0,
                "detalles": lineas,
                "es_sintetico": True,
            })

        if len(ventas) < 10:
            raise ValidationException(
                "No hay stock suficiente para generar al menos 10 ventas sintéticas"
            )
        return ventas

    @classmethod
    def generar_movimientos_sinteticos(cls, ventas: list[dict]) -> list[dict]:
        movimientos = []
        for venta in ventas:
            for linea in venta["detalles"]:
                movimientos.append({
                    "inventario_id": linea["inventario_id"],
                    "variante_id": linea["variante_id"],
                    "sucursal_id": linea["sucursal_id"],
                    "tipo": TipoMovimiento.VENTA.value,
                    "cantidad": -int(linea["cantidad"]),
                    "motivo": f"Venta sintética {venta['numero_orden']}",
                    "fecha": venta["fecha"],
                    "es_sintetico": True,
                })
        return movimientos

    @classmethod
    def etiquetar_sinteticos(cls, registros):
        """Marca todo registro generado con `es_sintetico = true`."""
        if isinstance(registros, dict):
            registros["es_sintetico"] = True
            for clave in ("clientes", "ventas", "movimientos"):
                if isinstance(registros.get(clave), list):
                    for registro in registros[clave]:
                        registro["es_sintetico"] = True
                        for detalle in registro.get("detalles", []) or []:
                            detalle["es_sintetico"] = True
            return registros
        for registro in registros:
            registro["es_sintetico"] = True
        return registros

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    @classmethod
    def validar_coherencia(cls, dataset: dict) -> dict:
        errores: list[str] = []
        ventas = dataset.get("ventas") or []
        clientes = dataset.get("clientes") or []
        movimientos = dataset.get("movimientos") or []
        stock_inicial = dataset.get("stock_inicial") or {}

        if not ventas:
            errores.append("El dataset no contiene ventas")
        if not clientes:
            errores.append("El dataset no contiene clientes")

        hoy = datetime.utcnow()
        uso: dict[str, int] = {}
        total_general = 0.0
        for venta in ventas:
            suma = round(sum(float(d.get("subtotal", 0)) for d in venta.get("detalles", [])), 2)
            if abs(suma - float(venta.get("total", 0))) > 0.01:
                errores.append(f"La venta {venta.get('numero_orden')} no cuadra con sus líneas")
            total_general += float(venta.get("total", 0))
            if int(venta.get("cliente_indice", -1)) >= len(clientes):
                errores.append(f"La venta {venta.get('numero_orden')} referencia un cliente inexistente")
            try:
                fecha = datetime.fromisoformat(venta["fecha"])
                if fecha > hoy + timedelta(days=1):
                    errores.append(f"La venta {venta.get('numero_orden')} tiene fecha futura")
            except (KeyError, TypeError, ValueError):
                errores.append(f"La venta {venta.get('numero_orden')} no tiene fecha válida")
            for detalle in venta.get("detalles", []):
                if int(detalle.get("cantidad", 0)) <= 0:
                    errores.append("Hay líneas con cantidad no positiva")
                clave = str(detalle.get("inventario_id"))
                uso[clave] = uso.get(clave, 0) + int(detalle.get("cantidad", 0))

        for clave, usado in uso.items():
            disponible = int(stock_inicial.get(clave, 0))
            if usado > disponible:
                errores.append(
                    f"El inventario {clave} usaría {usado} unidades y solo tiene {disponible}"
                )

        if len(movimientos) != sum(len(v.get("detalles", [])) for v in ventas):
            errores.append("Los movimientos no corresponden a las líneas de venta")

        return {
            "ok": not errores,
            "errores": errores,
            "clientes": len(clientes),
            "ventas": len(ventas),
            "movimientos": len(movimientos),
            "total_vendido": round(total_general, 2),
        }

    # ------------------------------------------------------------------
    # Siembra y limpieza
    # ------------------------------------------------------------------

    @classmethod
    async def sembrar_dataset(
        cls, db: AsyncSession, tenant_id: int, dataset_id: str | None = None
    ) -> dict:
        dataset = cls._obtener_dataset(dataset_id, tenant_id)
        validacion = cls.validar_coherencia(dataset)
        if not validacion["ok"]:
            raise ValidationException("El dataset no es coherente y no puede sembrarse")

        # [BUGFIX] Siembra idempotente: reutiliza clientes/órdenes ya
        # sembradas en vez de violar UNIQUE(tenant,nit_ci) /
        # UNIQUE(tenant,numero_orden) y responder 500.
        nit_a_cliente: dict[str, Cliente] = {}
        existentes = (await db.execute(
            select(Cliente).where(
                Cliente.tenant_id == tenant_id,
                Cliente.nit_ci.in_([d["nit_ci"] for d in dataset["clientes"]]),
            )
        )).scalars().all()
        for existente in existentes:
            nit_a_cliente[existente.nit_ci] = existente

        clientes_creados: list[Cliente] = []
        for datos in dataset["clientes"]:
            if datos["nit_ci"] in nit_a_cliente:
                clientes_creados.append(nit_a_cliente[datos["nit_ci"]])
                continue
            cliente = Cliente(
                nit_ci=datos["nit_ci"],
                correo=datos["correo"],
                telefono=datos["telefono"],
                direccion_envio=datos["direccion_envio"],
                tenant_id=tenant_id,
                es_sintetico=True,
            )
            db.add(cliente)
            clientes_creados.append(cliente)
            nit_a_cliente[datos["nit_ci"]] = cliente
        await db.flush()

        numeros_existentes = set((await db.execute(
            select(Orden.numero_orden).where(
                Orden.tenant_id == tenant_id,
                Orden.numero_orden.in_([v["numero_orden"] for v in dataset["ventas"]]),
            )
        )).scalars().all())

        ordenes_creadas = 0
        detalles_creados = 0
        numeros_nuevos: set[str] = set()
        for venta in dataset["ventas"]:
            if venta["numero_orden"] in numeros_existentes:
                continue
            numeros_nuevos.add(venta["numero_orden"])
            cliente = clientes_creados[int(venta["cliente_indice"])]
            orden = Orden(
                numero_orden=venta["numero_orden"],
                cliente_id=cliente.id,
                sucursal_id=venta["sucursal_id"],
                fecha=datetime.fromisoformat(venta["fecha"]),
                tipo=TipoOrden.PRESENCIAL,
                estado=EstadoOrden.PAGADO,
                total=Decimal(str(venta["total"])),
                impuestos=Decimal(str(venta["impuestos"])),
                descuentos=Decimal(str(venta["descuentos"])),
                tenant_id=tenant_id,
                es_sintetico=True,
            )
            db.add(orden)
            await db.flush()
            ordenes_creadas += 1

            detalles = []
            for linea in venta["detalles"]:
                detalle = DetalleOrden(
                    orden_id=orden.id,
                    variante_producto_id=linea["variante_id"],
                    cantidad=int(linea["cantidad"]),
                    precio_unitario=Decimal(str(linea["precio_unitario"])),
                    subtotal=Decimal(str(linea["subtotal"])),
                    tenant_id=tenant_id,
                )
                db.add(detalle)
                detalles.append(detalle)
                detalles_creados += 1
            await db.flush()

            if detalles:
                comprobante = Comprobante(
                    numero=f"SYN-{orden.id}-{uuid.uuid4().hex[:6]}",
                    tipo=TipoComprobante.RECIBO,
                    monto_total=Decimal(str(venta["total"])),
                    orden_id=orden.id,
                    tenant_id=tenant_id,
                )
                db.add(comprobante)

        movimientos_creados = 0
        for movimiento in dataset["movimientos"]:
            # Idempotencia: no duplica movimientos de órdenes ya sembradas.
            _numero = str(movimiento.get("motivo", "")).rsplit(" ", 1)[-1]
            if numeros_existentes and _numero in numeros_existentes:
                continue
            db.add(MovimientoInventario(
                inventario_id=movimiento["inventario_id"],
                tipo=TipoMovimiento(movimiento["tipo"]),
                cantidad=int(movimiento["cantidad"]),
                motivo=movimiento["motivo"],
                fecha_hora=datetime.fromisoformat(movimiento["fecha"]),
                tenant_id=tenant_id,
                es_sintetico=True,
            ))
            movimientos_creados += 1

        await db.flush()
        dataset["sembrado"] = True
        dataset["fecha_siembra"] = datetime.utcnow().isoformat()
        return {
            "dataset_id": dataset["dataset_id"],
            "sembrado": True,
            "clientes": len(clientes_creados),
            "ordenes": ordenes_creadas,
            "detalles": detalles_creados,
            "movimientos": movimientos_creados,
            "validacion": validacion,
        }

    @classmethod
    async def eliminar_dataset(
        cls, db: AsyncSession, tenant_id: int, dataset_id: str | None = None
    ) -> dict:
        dataset = None
        if dataset_id:
            dataset = cls._obtener_dataset(dataset_id, tenant_id)

        ids_ordenes = list((await db.execute(
            select(Orden.id).where(
                Orden.tenant_id == tenant_id,
                Orden.es_sintetico.is_(True),
            )
        )).scalars().all())

        detalles = 0
        comprobantes = 0
        if ids_ordenes:
            resultado = await db.execute(
                delete(DetalleOrden).where(DetalleOrden.orden_id.in_(ids_ordenes))
            )
            detalles = int(resultado.rowcount or 0)
            resultado = await db.execute(
                delete(Comprobante).where(Comprobante.orden_id.in_(ids_ordenes))
            )
            comprobantes = int(resultado.rowcount or 0)
            await db.execute(
                delete(Orden).where(Orden.id.in_(ids_ordenes))
            )

        resultado = await db.execute(
            delete(MovimientoInventario).where(
                MovimientoInventario.tenant_id == tenant_id,
                MovimientoInventario.es_sintetico.is_(True),
            )
        )
        movimientos = int(resultado.rowcount or 0)

        resultado = await db.execute(
            delete(Cliente).where(
                Cliente.tenant_id == tenant_id,
                Cliente.es_sintetico.is_(True),
            )
        )
        clientes = int(resultado.rowcount or 0)

        if dataset is not None:
            _DATASETS.pop(dataset["dataset_id"], None)

        return {
            "dataset_id": dataset_id,
            "eliminado": True,
            "ordenes": len(ids_ordenes),
            "detalles": detalles,
            "comprobantes": comprobantes,
            "movimientos": movimientos,
            "clientes": clientes,
        }

    # ------------------------------------------------------------------
    # Exportación y listado
    # ------------------------------------------------------------------

    @classmethod
    def exportar_dataset(
        cls, dataset_id: str, tenant_id: int, formato: str = "JSON"
    ) -> tuple[bytes, str, str]:
        dataset = cls._obtener_dataset(dataset_id, tenant_id)
        formato = (formato or "JSON").upper()
        if formato != "JSON":
            raise ValidationException("El dataset sintético solo se exporta en JSON")
        contenido = json.dumps(dataset, ensure_ascii=False, default=str, indent=2).encode("utf-8")
        nombre = f"dataset_sintetico_{dataset_id[:8]}.json"
        return contenido, "application/json", nombre

    @classmethod
    def listar_datasets(cls, tenant_id: int) -> list[dict]:
        return [
            cls._resumen_dataset(dataset)
            for dataset in _DATASETS.values()
            if int(dataset.get("tenant_id")) == int(tenant_id)
        ]

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    @classmethod
    def _obtener_dataset(cls, dataset_id: str | None, tenant_id: int) -> dict:
        # Clientes viejos (frontend) envían `String(undefined)` = "undefined"
        # por el desfasaje dataset_id/id; se trata como "último pendiente".
        if isinstance(dataset_id, str):
            dataset_id = dataset_id.strip() or None
            if dataset_id and dataset_id.lower() in ("undefined", "null"):
                dataset_id = None
        if dataset_id:
            dataset = _DATASETS.get(dataset_id)
            if dataset is None or int(dataset["tenant_id"]) != int(tenant_id):
                raise NotFoundException("Dataset sintético no encontrado")
            return dataset
        candidatos = [
            dataset for dataset in _DATASETS.values()
            if int(dataset["tenant_id"]) == int(tenant_id) and not dataset.get("sembrado")
        ]
        if not candidatos:
            raise NotFoundException("No hay un dataset sintético pendiente de sembrar")
        return max(candidatos, key=lambda item: item["creado_en"])

    @classmethod
    def _resumen_dataset(cls, dataset: dict) -> dict:
        return {
            "dataset_id": dataset["dataset_id"],
            "tenant_id": dataset["tenant_id"],
            "configuracion": dataset["configuracion"],
            "clientes": len(dataset["clientes"]),
            "ventas": len(dataset["ventas"]),
            "movimientos": len(dataset["movimientos"]),
            "sembrado": bool(dataset.get("sembrado")),
            "creado_en": dataset.get("creado_en"),
            "validacion": dataset.get("validacion"),
        }
