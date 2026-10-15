"""Backward-compatible import for the API package."""

import asyncio
import time

from api.async_client import AsyncLibraryClient
from api.sync_client import SyncLibraryClient

__all__ = ["AsyncLibraryClient", "SyncLibraryClient", "asyncio", "time"]
