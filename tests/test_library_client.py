import httpx
import pytest

from exceptions import APIError, ConfigurationError, DataValidationError
from library_client import AsyncLibraryClient, SyncLibraryClient


def test_sync_client_retries_server_error(monkeypatch):
    attempts = 0
    delays = []

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr("library_client.time.sleep", delays.append)
    with SyncLibraryClient(
        "https://library.example",
        transport=httpx.MockTransport(handler),
    ) as client:
        response = client.get("/books")

    assert response.json() == {"ok": True}
    assert attempts == 2
    assert delays == [0.5]


def test_sync_client_honors_retry_after(monkeypatch):
    attempts = 0
    delays = []

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "2"},
                request=request,
            )
        return httpx.Response(200, json=[], request=request)

    monkeypatch.setattr("library_client.time.sleep", delays.append)
    with SyncLibraryClient(
        "https://library.example",
        max_retry_delay=0.1,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.request_json("GET", "/books") == []

    assert delays == [2.0]


def test_sync_client_raises_api_error_after_retry_limit():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(503, request=request)
    )
    with SyncLibraryClient(
        "https://library.example",
        max_retries=1,
        backoff_factor=0,
        transport=transport,
    ) as client:
        with pytest.raises(APIError) as error:
            client.get("/books")

    assert error.value.status_code == 503


def test_sync_client_rejects_invalid_json():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="not json", request=request)
    )
    with SyncLibraryClient("https://library.example", transport=transport) as client:
        with pytest.raises(DataValidationError):
            client.request_json("GET", "/books")


def test_client_rejects_invalid_retry_configuration():
    with pytest.raises(ConfigurationError):
        SyncLibraryClient("https://library.example", max_retries=-1)


@pytest.mark.asyncio
async def test_async_client_retries_server_error(monkeypatch):
    attempts = 0
    delays = []

    async def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("library_client.asyncio.sleep", fake_sleep)
    async with AsyncLibraryClient(
        "https://library.example",
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client.get("/books")

    assert response.json() == {"ok": True}
    assert attempts == 2
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_async_client_raises_api_error_for_rate_limit():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            429,
            headers={"Retry-After": "0"},
            request=request,
        )
    )
    async with AsyncLibraryClient(
        "https://library.example",
        max_retries=0,
        transport=transport,
    ) as client:
        with pytest.raises(APIError) as error:
            await client.get("/books")

    assert error.value.status_code == 429
