from __future__ import annotations

import os
import random
import time
from collections.abc import Iterable, Mapping
from typing import Any, Callable, Literal, TypeVar

import httpx

from ._schema import to_json_schema
from .errors import HydrafetchError, HydrafetchTimeout

DEFAULT_BASE_URL = "https://api.hydrafetch.com"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_RETRIES = 2
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

Format = Literal["markdown", "html", "rawHtml", "links", "structured", "summary", "json", "brand"]

__all__ = ["AsyncHydrafetch", "Hydrafetch"]

_Sync = TypeVar("_Sync", bound="Hydrafetch")
_Async = TypeVar("_Async", bound="AsyncHydrafetch")


def _drop_none(d: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _resolve_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("HYDRAFETCH_API_KEY")
    if not key:
        raise HydrafetchError(
            "No API key. Pass api_key= or set HYDRAFETCH_API_KEY. "
            "Create a key at https://app.hydrafetch.com.",
            code="MISSING_API_KEY",
            status=401,
        )
    return key


def _headers(api_key: str) -> dict[str, str]:
    return {
        "X-API-Key": api_key,
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": "hydrafetch-python",
    }


def _unwrap(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if response.is_error or (isinstance(payload, dict) and payload.get("success") is False):
        err = payload.get("error") if isinstance(payload, dict) else None
        meta = payload.get("meta") if isinstance(payload, dict) else None
        raise HydrafetchError(
            (err or {}).get("message") or response.text[:300] or response.reason_phrase,
            code=(err or {}).get("code") or f"HTTP_{response.status_code}",
            status=response.status_code,
            request_id=(meta or {}).get("requestId"),
            details=(err or {}).get("details"),
        )

    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _backoff(attempt: int) -> float:
    return (2**attempt) * 0.5 + random.random() * 0.25


class _Base:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._api_key = _resolve_key(api_key)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries

    def _scrape_body(self, url: str, options: Mapping[str, Any]) -> dict[str, Any]:
        return {"url": url, **_drop_none(options)}

    def _styleguide_params(self, target: str, options: Mapping[str, Any]) -> dict[str, Any]:
        key = "url" if target.lower().startswith(("http://", "https://")) else "domain"
        return {key: target, **_drop_none(options)}

    def _extract_body(
        self, urls: str | Iterable[str], options: Mapping[str, Any]
    ) -> dict[str, Any]:
        url_list = [urls] if isinstance(urls, str) else list(urls)
        body = _drop_none(options)
        if "schema" in body:
            body["schema"] = to_json_schema(body["schema"])
        return {"urls": url_list, **body}

    def _job_id(self, payload: Any, *keys: str) -> str:
        if isinstance(payload, dict):
            for key in (*keys, "id"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
        raise HydrafetchError("Job accepted but returned no id.", code="NO_JOB_ID", status=502)


class Hydrafetch(_Base):
    def __init__(self, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(api_key, **kwargs)
        self._http = httpx.Client(
            base_url=self._base_url, headers=_headers(self._api_key), timeout=self._timeout
        )

    def __enter__(self: _Sync) -> _Sync:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        last: HydrafetchError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.request(
                    method, path, json=json, params=_drop_none(params or {}) or None
                )
            except httpx.TimeoutException as exc:
                raise HydrafetchTimeout(
                    f"Request to {path} exceeded {self._timeout}s. Raise timeout=, "
                    "or use the crawl and batch job endpoints for long work."
                ) from exc

            try:
                return _unwrap(response)
            except HydrafetchError as err:
                if not err.is_retryable or attempt == self._max_retries:
                    raise
                last = err
                time.sleep(_backoff(attempt))
        raise last or HydrafetchError("Request failed")

    def scrape(self, url: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/web/scrape", json=self._scrape_body(url, options))

    def map(self, url: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/web/map", json=self._scrape_body(url, options))

    def search(self, query: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/web/search", json={"query": query, **_drop_none(options)})

    def extract(self, urls: str | Iterable[str], **options: Any) -> Any:
        return self._request("POST", "/v1/web/extract", json=self._extract_body(urls, options))

    def brand(self, domain: str) -> dict[str, Any]:
        return self._request("GET", "/v1/web/brand", params={"domain": domain})

    def logo(self, domain: str, **options: Any) -> dict[str, Any]:
        return self._request("GET", "/v1/web/brand/logo", params={"domain": domain, **options})

    def styleguide(self, domain_or_url: str, **options: Any) -> dict[str, Any]:
        return self._request(
            "GET", "/v1/web/styleguide", params=self._styleguide_params(domain_or_url, options)
        )

    def screenshot(self, url: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/web/screenshot", json=self._scrape_body(url, options))

    def images(self, url: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/web/images", json=self._scrape_body(url, options))

    def links(self, url: str, **options: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/web/links", json=self._scrape_body(url, options))

    def start_crawl(self, url: str, **options: Any) -> str:
        payload = self._request("POST", "/v1/web/crawl", json=self._scrape_body(url, options))
        return self._job_id(payload, "crawlId")

    def crawl_status(self, crawl_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/web/crawl/{crawl_id}")

    def start_batch(self, urls: Iterable[str], **options: Any) -> str:
        payload = self._request(
            "POST", "/v1/web/batch", json={"urls": list(urls), **_drop_none(options)}
        )
        return self._job_id(payload, "batchId")

    def batch_status(self, batch_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/web/batch/{batch_id}")

    def crawl(
        self,
        url: str,
        *,
        poll_interval: float = 2.0,
        job_timeout: float = 300.0,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        crawl_id = self.start_crawl(url, **options)
        return self._wait(
            lambda: self.crawl_status(crawl_id), poll_interval, job_timeout, on_progress
        )

    def batch(
        self,
        urls: Iterable[str],
        *,
        poll_interval: float = 2.0,
        job_timeout: float = 300.0,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        batch_id = self.start_batch(urls, **options)
        return self._wait(
            lambda: self.batch_status(batch_id), poll_interval, job_timeout, on_progress
        )

    def _wait(
        self,
        poll: Callable[[], dict[str, Any]],
        interval: float,
        job_timeout: float,
        on_progress: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + job_timeout
        while True:
            job = poll()
            if on_progress:
                on_progress(job)
            if str(job.get("status")) in TERMINAL_STATUSES:
                return job
            if time.monotonic() + interval > deadline:
                raise HydrafetchTimeout(
                    f"Job did not finish within {job_timeout}s. It is still running "
                    "server-side; poll its status directly, or pass a webhook."
                )
            time.sleep(interval)


class AsyncHydrafetch(_Base):
    def __init__(self, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(api_key, **kwargs)
        self._http = httpx.AsyncClient(
            base_url=self._base_url, headers=_headers(self._api_key), timeout=self._timeout
        )

    async def __aenter__(self: _Async) -> _Async:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        import asyncio

        last: HydrafetchError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http.request(
                    method, path, json=json, params=_drop_none(params or {}) or None
                )
            except httpx.TimeoutException as exc:
                raise HydrafetchTimeout(
                    f"Request to {path} exceeded {self._timeout}s. Raise timeout=, "
                    "or use the crawl and batch job endpoints for long work."
                ) from exc

            try:
                return _unwrap(response)
            except HydrafetchError as err:
                if not err.is_retryable or attempt == self._max_retries:
                    raise
                last = err
                await asyncio.sleep(_backoff(attempt))
        raise last or HydrafetchError("Request failed")

    async def scrape(self, url: str, **options: Any) -> dict[str, Any]:
        return await self._request("POST", "/v1/web/scrape", json=self._scrape_body(url, options))

    async def map(self, url: str, **options: Any) -> dict[str, Any]:
        return await self._request("POST", "/v1/web/map", json=self._scrape_body(url, options))

    async def search(self, query: str, **options: Any) -> dict[str, Any]:
        return await self._request(
            "POST", "/v1/web/search", json={"query": query, **_drop_none(options)}
        )

    async def extract(self, urls: str | Iterable[str], **options: Any) -> Any:
        return await self._request(
            "POST", "/v1/web/extract", json=self._extract_body(urls, options)
        )

    async def brand(self, domain: str) -> dict[str, Any]:
        return await self._request("GET", "/v1/web/brand", params={"domain": domain})

    async def logo(self, domain: str, **options: Any) -> dict[str, Any]:
        return await self._request(
            "GET", "/v1/web/brand/logo", params={"domain": domain, **options}
        )

    async def styleguide(self, domain_or_url: str, **options: Any) -> dict[str, Any]:
        return await self._request(
            "GET", "/v1/web/styleguide", params=self._styleguide_params(domain_or_url, options)
        )

    async def screenshot(self, url: str, **options: Any) -> dict[str, Any]:
        return await self._request(
            "POST", "/v1/web/screenshot", json=self._scrape_body(url, options)
        )

    async def images(self, url: str, **options: Any) -> dict[str, Any]:
        return await self._request("POST", "/v1/web/images", json=self._scrape_body(url, options))

    async def links(self, url: str, **options: Any) -> dict[str, Any]:
        return await self._request("POST", "/v1/web/links", json=self._scrape_body(url, options))

    async def start_crawl(self, url: str, **options: Any) -> str:
        payload = await self._request("POST", "/v1/web/crawl", json=self._scrape_body(url, options))
        return self._job_id(payload, "crawlId")

    async def crawl_status(self, crawl_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/web/crawl/{crawl_id}")

    async def start_batch(self, urls: Iterable[str], **options: Any) -> str:
        payload = await self._request(
            "POST", "/v1/web/batch", json={"urls": list(urls), **_drop_none(options)}
        )
        return self._job_id(payload, "batchId")

    async def batch_status(self, batch_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/web/batch/{batch_id}")
