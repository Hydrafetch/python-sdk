from __future__ import annotations

from typing import Any


class HydrafetchError(Exception):
    """Any failure from the Hydrafetch API, carrying the server's own code and request id."""

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
        """401 and 403. Retrying without changing the key is pointless."""
        return self.status in (401, 403)

    @property
    def is_out_of_credits(self) -> bool:
        """402. The workspace has no credits left."""
        return self.status == 402

    @property
    def is_invalid_request(self) -> bool:
        """400 and 422. The request is wrong; a repeat fails the same way and costs another call."""
        return self.status in (400, 422)

    @property
    def is_retryable(self) -> bool:
        """429 and 5xx. Worth another attempt after a wait."""
        return self.status == 429 or self.status >= 500


class HydrafetchTimeout(HydrafetchError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="TIMEOUT", status=408)
