class UispError(Exception):
    """Error genérico hablando con UISP."""


class UispAuthError(UispError):
    """Token inválido, revocado o sin permisos de lectura."""


class UispConnectionError(UispError):
    """No se pudo alcanzar el servidor de UISP (caído, DNS, firewall, timeout)."""


class UispRequestError(UispError):
    """UISP respondió con un error a una consulta concreta."""

    def __init__(self, message: str, path: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.path = path
        self.status_code = status_code
