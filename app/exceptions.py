"""
Excepciones personalizadas para el manejo de errores de la aplicación.
"""
from fastapi import HTTPException, status


class AuthenticationException(HTTPException):
    """Excepción para errores de autenticación."""
    def __init__(self, detail: str = "Error de autenticación"):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


class AuthorizationException(HTTPException):
    """Excepción para errores de autorización."""
    def __init__(self, detail: str = "No autorizado"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


# Alias común
ForbiddenException = AuthorizationException


class ValidationException(HTTPException):
    """Excepción para errores de validación."""
    def __init__(self, detail: str = "Error de validación"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


# Alias común
BadRequestException = ValidationException


class NotFoundException(HTTPException):
    """Excepción para recursos no encontrados."""
    def __init__(self, detail: str = "Recurso no encontrado"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class ConflictException(HTTPException):
    """Excepción para conflictos de datos."""
    def __init__(self, detail: str = "Conflicto de datos"):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class ExternalServiceException(HTTPException):
    """Excepción para errores de servicios externos."""
    def __init__(self, detail: str = "Error en servicio externo"):
        super().__init__(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


class InventoryException(HTTPException):
    """Excepción para errores de inventario."""
    def __init__(self, detail: str = "Error de inventario"):
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


class PaymentException(HTTPException):
    """Excepción para errores de pago."""
    def __init__(self, detail: str = "Error de pago"):
        super().__init__(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=detail)


class RateLimitException(HTTPException):
    """Excepción para límites de tasa excedidos."""
    def __init__(self, detail: str = "Límite de solicitudes excedido"):
        super().__init__(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)


# ============================================================
# [EXAMEN-2] Errores de negocio SaaS / offline (plan, capítulo 14)
# ============================================================

class BusinessException(HTTPException):
    """Error de negocio con el contrato uniforme del capítulo 14.

    Respuesta: `codigo`, `mensaje`, `detalle`, `reintentar`, `datos`.
    """

    def __init__(
        self,
        mensaje: str,
        codigo: str = "ERROR_DE_NEGOCIO",
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detalle: str | None = None,
        reintentar: bool = False,
        datos: dict | None = None,
    ):
        super().__init__(status_code=status_code, detail=mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.detalle = detalle
        self.reintentar = reintentar
        self.datos = datos

    def to_dict(self) -> dict:
        return {
            "codigo": self.codigo,
            "mensaje": self.mensaje,
            "detalle": self.detalle,
            "reintentar": self.reintentar,
            "datos": self.datos,
        }


class TenantNoEncontrado(BusinessException):
    def __init__(self, mensaje: str = "Tenant no encontrado"):
        super().__init__(mensaje, "RECURSO_NO_ENCONTRADO", status.HTTP_404_NOT_FOUND)


class TenantSuspendido(BusinessException):
    def __init__(self, mensaje: str = "El tenant está suspendido o cancelado"):
        super().__init__(mensaje, "TENANT_SUSPENDIDO", status.HTTP_403_FORBIDDEN)


class LimitePlanExcedido(BusinessException):
    def __init__(self, mensaje: str = "Se alcanzó el límite del plan contratado"):
        super().__init__(mensaje, "LIMITE_PLAN_EXCEDIDO", status.HTTP_409_CONFLICT)


class FuncionalidadNoDisponible(BusinessException):
    def __init__(self, mensaje: str = "El plan no incluye esta funcionalidad"):
        super().__init__(mensaje, "FUNCIONALIDAD_NO_DISPONIBLE", status.HTTP_403_FORBIDDEN)


class SuscripcionVencida(BusinessException):
    def __init__(self, mensaje: str = "La suscripción está vencida o no está activa"):
        super().__init__(mensaje, "SUSCRIPCION_REQUERIDA", status.HTTP_402_PAYMENT_REQUIRED)


class DispositivoRevocado(BusinessException):
    def __init__(self, mensaje: str = "El dispositivo fue revocado"):
        super().__init__(mensaje, "DISPOSITIVO_REVOCADO", status.HTTP_403_FORBIDDEN)


class SesionOfflineVencida(BusinessException):
    def __init__(self, mensaje: str = "La sesión offline venció"):
        super().__init__(mensaje, "SESION_OFFLINE_VENCIDA", status.HTTP_403_FORBIDDEN)


class PinNoConfigurado(BusinessException):
    def __init__(self, mensaje: str = "El usuario no configuró un PIN offline"):
        super().__init__(mensaje, "PIN_NO_CONFIGURADO", status.HTTP_403_FORBIDDEN)


class PinIncorrecto(BusinessException):
    def __init__(self, mensaje: str = "PIN incorrecto"):
        super().__init__(mensaje, "PIN_INCORRECTO", status.HTTP_401_UNAUTHORIZED)


class PinBloqueado(BusinessException):
    def __init__(self, mensaje: str = "Se superaron los intentos permitidos de PIN"):
        super().__init__(mensaje, "PIN_BLOQUEADO", 423)


class PagoDiferidoNoDisponible(BusinessException):
    def __init__(self, mensaje: str = "El plan o la configuración no permiten pago diferido"):
        super().__init__(mensaje, "PAGO_DIFERIDO_NO_DISPONIBLE", status.HTTP_402_PAYMENT_REQUIRED)


class CarritoNoHabilitado(BusinessException):
    def __init__(self, mensaje: str = "El tenant deshabilitó el carrito offline"):
        super().__init__(mensaje, "CARRITO_NO_HABILITADO", status.HTTP_409_CONFLICT)


class IdempotenciaRequerida(BusinessException):
    def __init__(self, mensaje: str = "Se requiere el encabezado Idempotency-Key"):
        super().__init__(mensaje, "DATOS_INVALIDOS", status.HTTP_400_BAD_REQUEST)


# ============================================================
# [EXAMEN-2] Fase 2: sincronización, conflictos y pago diferido
# ============================================================

class ManejadorNoRegistrado(BusinessException):
    """No existe un manejador para el tipo de operación offline (plan, 4.7)."""

    def __init__(self, tipo_operacion: str):
        super().__init__(
            f"No hay un manejador registrado para el tipo {tipo_operacion}",
            "MANEJADOR_NO_REGISTRADO",
            status.HTTP_409_CONFLICT,
            datos={"tipo_operacion": tipo_operacion},
        )
        self.tipo_operacion = tipo_operacion


class OperacionRechazada(BusinessException):
    """Rechazo de negocio de una operación offline, con conflicto asociado."""

    def __init__(
        self,
        motivo: str,
        tipo_conflicto: str | None = None,
        estrategia: str = "RECHAZO",
        datos: dict | None = None,
    ):
        codigo = "CONFLICTO_MANUAL" if tipo_conflicto else "DATOS_INVALIDOS"
        super().__init__(
            motivo,
            codigo,
            status.HTTP_409_CONFLICT,
            datos=datos,
        )
        self.tipo_conflicto = tipo_conflicto
        self.estrategia = estrategia


class AceptacionRequerida(BusinessException):
    """RN-007/RN-014: el total recalculado exige aceptación del cliente."""

    def __init__(self, total_anterior, total_nuevo, diferencias: list | None = None):
        super().__init__(
            "El precio central cambió; se requiere aceptar el nuevo total",
            "PRECIO_DESACTUALIZADO",
            status.HTTP_409_CONFLICT,
            datos={
                "total_anterior": float(total_anterior),
                "total_nuevo": float(total_nuevo),
                "diferencias": diferencias or [],
                "requiere_aceptacion": True,
            },
        )
        self.total_anterior = total_anterior
        self.total_nuevo = total_nuevo
        self.diferencias = diferencias or []


class OperacionEnProceso(BusinessException):
    def __init__(self, mensaje: str = "La operación ya está siendo procesada"):
        super().__init__(mensaje, "OPERACION_EN_PROCESO", status.HTTP_409_CONFLICT, reintentar=True)


class StockInsuficiente(BusinessException):
    def __init__(self, mensaje: str = "Stock insuficiente en la sucursal", datos: dict | None = None):
        super().__init__(mensaje, "STOCK_INSUFICIENTE", status.HTTP_409_CONFLICT, datos=datos)


class PagoDuplicado(BusinessException):
    def __init__(self, mensaje: str = "Ya existe un pago para esta orden"):
        super().__init__(mensaje, "PAGO_DUPLICADO", status.HTTP_409_CONFLICT)


class PagoNoConfirmado(BusinessException):
    def __init__(self, mensaje: str = "El pago aún no fue confirmado por la pasarela"):
        super().__init__(mensaje, "PAGO_NO_CONFIRMADO", status.HTTP_409_CONFLICT, reintentar=True)


class FirmaWebhookInvalida(BusinessException):
    def __init__(self, mensaje: str = "La firma del webhook no es válida"):
        super().__init__(mensaje, "FIRMA_WEBHOOK_INVALIDA", status.HTTP_400_BAD_REQUEST)


class PagoNoEncontrado(BusinessException):
    def __init__(self, mensaje: str = "Pago no encontrado"):
        super().__init__(mensaje, "RECURSO_NO_ENCONTRADO", status.HTTP_404_NOT_FOUND)