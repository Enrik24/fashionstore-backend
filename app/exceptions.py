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


class ValidationException(HTTPException):
    """Excepción para errores de validación."""
    def __init__(self, detail: str = "Error de validación"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class NotFoundException(HTTPException):
    """Excepción para recursos no encontrados."""
    def __init__(self, detail: str = "Recurso no encontrado"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class ConflictException(HTTPException):
    """Excepción para conflictos de datos."""
    def __init__(self, detail: str = "Conflicto de datos"):
        super().__init__(status_code=status.HTTP_409_CONCESSION, detail=detail)


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