"""Safe local error codes; upstream bodies and exception strings are never public."""


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        # A failure/timeout does not prove the provider did not execute or bill.
        self.retryable = False

    def as_response(self, request_id: str) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            },
            "requestId": request_id,
        }
