"""Application-level errors safe to expose through the API."""


class AppError(RuntimeError):
    def __init__(self, message: str, code: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class DependencyUnavailableError(AppError):
    def __init__(self, dependency: str):
        super().__init__(
            message=f"{dependency} is unavailable or not configured.",
            code="dependency_unavailable",
            status_code=503,
        )
