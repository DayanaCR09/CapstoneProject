import time
from typing import Any

import httpx

from exceptions import APIError, DataValidationError

from .client_base import RETRYABLE_METHODS, LibraryClientBase


class SyncLibraryClient(LibraryClientBase):
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
        retry_method = method in RETRYABLE_METHODS

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
