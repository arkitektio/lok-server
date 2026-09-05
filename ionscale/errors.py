"""Errors raised by the ionscale repositories."""


class IonscaleError(RuntimeError):
    """An ionscale call failed.

    ``code`` is the connect error code as sent by the server (``not_found``,
    ``already_exists``, ``permission_denied``, ...) or ``unavailable`` when the
    request never got an answer. Subclasses ``RuntimeError`` so the existing
    ``except Exception`` / ``except RuntimeError`` sites keep working.
    """

    def __init__(self, code: str, message: str = "", http_status: int | None = None):
        self.code = code
        self.message = message
        self.http_status = http_status
        detail = f"{code}: {message}" if message else code
        super().__init__(f"Ionscale error ({detail})")

    def is_(self, *codes: str) -> bool:
        return self.code in codes
