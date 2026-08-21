from __future__ import annotations

from typing import Any


class HydrafetchError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "UNKNOWN",
        status: int = 500,
        request_id: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.request_id = request_id
        self.details = details

    def __str__(self) -> str:
        parts = [self.message]
        if self.code and self.code != "UNKNOWN":
            parts.append(f"[{self.code}]")
        if self.request_id:
            parts.append(f"(request {self.request_id})")
        return " ".join(parts)

    @property
    def is_auth(self) -> bool:
        return self.status in (401, 403)

    @property
    def is_out_of_credits(self) -> bool:
        return self.status == 402

    @property
    def is_invalid_request(self) -> bool:
        return self.status in (400, 422)

    @property
    def is_retryable(self) -> bool:
        return self.status == 429 or self.status >= 500


class HydrafetchTimeout(HydrafetchError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="TIMEOUT", status=408)
