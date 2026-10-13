import asyncio
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from exceptions import APIError, ConfigurationError, DataValidationError

_RETRYABLE_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PUT"})


def _retry_after(response: httpx.Response) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            seconds = float(retry_after)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
            except (TypeError, ValueError, OverflowError):
                retry_at = None
            if retry_at is not None:
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
            else:
                seconds = -1
        if math.isfinite(seconds) and seconds >= 0:
            return seconds

    reset = response.headers.get("X-RateLimit-Reset")
    if reset is not None:
        try:
            reset_at = float(reset)
        except ValueError:
            return None
        if math.isfinite(reset_at):
            return max(0.0, reset_at - time.time())
    return None


class _LibraryClientBase:
    @staticmethod
    def _required_value(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DataValidationError(
                f"{field} must be a non-empty string",
                field=field,
            )
        return value.strip()

    @staticmethod
    def _positive_integer(value: int, field: str, *, allow_zero: bool = False) -> int:
        minimum = 0 if allow_zero else 1
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            qualifier = "non-negative" if allow_zero else "positive"
            raise DataValidationError(
                f"{field} must be a {qualifier} integer",
                field=field,
            )
        return value

    @classmethod
    def _path_identifier(cls, value: str, field: str) -> str:
        return quote(cls._required_value(value, field), safe="")

    def __init__(
        self,
        base_url: str = "https://openlibrary.org",
        *,
        timeout: float,
        max_retries: int,
        backoff_factor: float,
        max_retry_delay: float,
    ) -> None:
        if not base_url:
            raise ConfigurationError("base_url must not be empty")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int):
            raise ConfigurationError("max_retries must be a non-negative integer")
        if max_retries < 0:
            raise ConfigurationError("max_retries must be a non-negative integer")
        if not math.isfinite(backoff_factor) or backoff_factor < 0:
            raise ConfigurationError(
                "backoff_factor must be a finite non-negative number"
            )
        if not math.isfinite(max_retry_delay) or max_retry_delay < 0:
            raise ConfigurationError(
                "max_retry_delay must be a finite non-negative number"
            )

        self._base_url = base_url
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._max_retry_delay = max_retry_delay

    def _delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        if response is not None and response.status_code == 429:
            rate_limit_delay = _retry_after(response)
            if rate_limit_delay is not None:
                return rate_limit_delay
        return min(self._backoff_factor * (2**attempt), self._max_retry_delay)

    @staticmethod
    def _check_response(response: httpx.Response) -> httpx.Response:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise APIError(
                f"API request failed with status {response.status_code}",
                status_code=response.status_code,
            ) from exc
        return response

    @staticmethod
    def _is_retryable(response: httpx.Response) -> bool:
        return response.status_code == 429 or response.status_code >= 500


class SyncLibraryClient(_LibraryClientBase):
    """Synchronous HTTP client for a JSON library API."""

    def __init__(
        self,
        base_url: str = "https://openlibrary.org",
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        max_retry_delay: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            max_retry_delay=max_retry_delay,
        )
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        )

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        method = method.upper()
        retry_method = method in _RETRYABLE_METHODS

        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                if retry_method and attempt < self._max_retries:
                    time.sleep(self._delay(attempt))
                    continue
                raise APIError(f"API request failed: {exc}") from exc

            if (
                self._is_retryable(response)
                and (response.status_code == 429 or retry_method)
                and attempt < self._max_retries
            ):
                delay = self._delay(attempt, response)
                response.close()
                time.sleep(delay)
                continue
            return self._check_response(response)

        raise RuntimeError("Request retry loop exited unexpectedly")

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self.request(method, url, **kwargs)
        try:
            return response.json()
        except (ValueError, UnicodeError) as exc:
            raise DataValidationError(
                "API response body is not valid JSON",
                field="response",
            ) from exc

    def search(self, query: str, *, page: int = 1) -> dict[str, Any]:
        """Search Open Library and return the search response."""
        query = self._required_value(query, "query")
        page = self._positive_integer(page, "page")
        return self.request_json(
            "GET",
            "/search.json",
            params={"q": query, "page": page},
        )

    def get_work(self, work_id: str) -> dict[str, Any]:
        """Fetch an Open Library work by its work identifier."""
        work_id = self._path_identifier(work_id, "work_id")
        return self.request_json("GET", f"/works/{work_id}.json")

    def get_author_works(self, author_id: str) -> dict[str, Any]:
        """Fetch the works associated with an Open Library author identifier."""
        author_id = self._path_identifier(author_id, "author_id")
        return self.request_json("GET", f"/authors/{author_id}/works.json")

    def get_subject(
        self,
        subject: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Fetch works for a subject with pagination controls."""
        subject = self._path_identifier(subject, "subject")
        limit = self._positive_integer(limit, "limit")
        offset = self._positive_integer(offset, "offset", allow_zero=True)
        return self.request_json(
            "GET",
            f"/subjects/{subject}.json",
            params={"limit": limit, "offset": offset},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SyncLibraryClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class AsyncLibraryClient(_LibraryClientBase):
    """Asynchronous HTTP client for a JSON library API."""

    def __init__(
        self,
        base_url: str = "https://openlibrary.org",
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        max_retry_delay: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            max_retry_delay=max_retry_delay,
        )
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        )

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        method = method.upper()
        retry_method = method in _RETRYABLE_METHODS

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                if retry_method and attempt < self._max_retries:
                    await asyncio.sleep(self._delay(attempt))
                    continue
                raise APIError(f"API request failed: {exc}") from exc

            if (
                self._is_retryable(response)
                and (response.status_code == 429 or retry_method)
                and attempt < self._max_retries
            ):
                delay = self._delay(attempt, response)
                await response.aclose()
                await asyncio.sleep(delay)
                continue
            return self._check_response(response)

        raise RuntimeError("Request retry loop exited unexpectedly")

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        response = await self.request(method, url, **kwargs)
        try:
            return response.json()
        except (ValueError, UnicodeError) as exc:
            raise DataValidationError(
                "API response body is not valid JSON",
                field="response",
            ) from exc

    async def search(self, query: str, *, page: int = 1) -> dict[str, Any]:
        """Search Open Library and return the search response."""
        query = self._required_value(query, "query")
        page = self._positive_integer(page, "page")
        return await self.request_json(
            "GET",
            "/search.json",
            params={"q": query, "page": page},
        )

    async def get_work(self, work_id: str) -> dict[str, Any]:
        """Fetch an Open Library work by its work identifier."""
        work_id = self._path_identifier(work_id, "work_id")
        return await self.request_json("GET", f"/works/{work_id}.json")

    async def get_author_works(self, author_id: str) -> dict[str, Any]:
        """Fetch the works associated with an Open Library author identifier."""
        author_id = self._path_identifier(author_id, "author_id")
        return await self.request_json("GET", f"/authors/{author_id}/works.json")

    async def get_subject(
        self,
        subject: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Fetch works for a subject with pagination controls."""
        subject = self._path_identifier(subject, "subject")
        limit = self._positive_integer(limit, "limit")
        offset = self._positive_integer(offset, "offset", allow_zero=True)
        return await self.request_json(
            "GET",
            f"/subjects/{subject}.json",
            params={"limit": limit, "offset": offset},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncLibraryClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
