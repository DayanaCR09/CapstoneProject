"""Asynchronous Open Library HTTP client."""

import asyncio
from typing import Any

import httpx

from exceptions import APIError, DataValidationError

from .client_base import RETRYABLE_METHODS, LibraryClientBase


class AsyncLibraryClient(LibraryClientBase):
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
        retry_method = method in RETRYABLE_METHODS

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


__all__ = ["AsyncLibraryClient"]
