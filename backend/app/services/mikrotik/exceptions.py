class MikrotikError(Exception):
    """Error genérico hablando con un equipo RouterOS."""


class MikrotikAuthError(MikrotikError):
    """Usuario o clave inválidos, o el usuario no tiene el permiso `api`/`rest-api`."""


class MikrotikConnectionError(MikrotikError):
    """No se pudo abrir la conexión (host caído, puerto cerrado, firewall, timeout)."""


class MikrotikCommandError(MikrotikError):
    """RouterOS respondió !trap / error HTTP a un comando."""

    def __init__(self, message: str, command: str | None = None, category: str | None = None):
        super().__init__(message)
        self.message = message
        self.command = command
        self.category = category
