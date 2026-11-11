"""Errores comunes a los proveedores de modelos."""


class QuotaExhausted(RuntimeError):
    """El proveedor se quedó sin cuota. `daily`: la diaria (no se renueva en minutos); si no,
    la por minuto siguió agotada después de los reintentos. FallbackProvider la usa para pasar al
    proveedor de respaldo."""

    def __init__(self, message: str, *, daily: bool) -> None:
        super().__init__(message)
        self.daily = daily
