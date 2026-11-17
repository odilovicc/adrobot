class DomainError(Exception):
    """Business-rule violation shown to the user as is."""

    status_code = 400

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409
