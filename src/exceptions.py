class ApplicationError(Exception):
    """Base class for application-specific errors."""


class APIError(ApplicationError):
    """Raised when an API request fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DataValidationError(ApplicationError):
    """Raised when input or retrieved data does not meet expectations."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class ConfigurationError(ApplicationError):
    """Raised when application configuration is invalid or incomplete."""
