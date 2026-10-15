"""Shared validation, retry timing, and response handling for API clients."""

import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

from exceptions import APIError, ConfigurationError, DataValidationError

RETRYABLE_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PUT"})


def retry_after(response: httpx.Response) -> float | None:
    """Parse retry guidance from standard and rate-limit response headers."""
    retry_after_header = response.headers.get("Retry-After")
    if retry_after_header is not None:
        try:
            seconds = float(retry_after_header)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after_header)
            except TypeError, ValueError, OverflowError:
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


class LibraryClientBase:
    """Common configuration and response policy for sync and async clients."""

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
            delay = retry_after(response)
            if delay is not None:
                return delay
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
