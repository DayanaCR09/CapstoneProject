import json
import math
import sqlite3
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from exceptions import DataValidationError


class DatabaseManager:
    """Manage the local SQLite catalog and API response cache."""

    def __init__(
        self,
        database_path: str | Path = "data/library.db",
        *,
        timeout: float = 5.0,
    ) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:" and not self.database_path.startswith(
            "file:"
        ):
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

        self._connection = sqlite3.connect(
            self.database_path,
            timeout=timeout,
            uri=self.database_path.startswith("file:"),
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._transaction_depth = 0
        self.initialize()

    def initialize(self) -> None:
        """Create the catalog and cache tables if they do not exist."""
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS authors (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0)
            );

            CREATE TABLE IF NOT EXISTS works (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL CHECK (length(trim(title)) > 0),
                author_id INTEGER NOT NULL
                    REFERENCES authors (id) ON DELETE RESTRICT,
                isbn TEXT NOT NULL UNIQUE CHECK (length(trim(isbn)) > 0)
            );

            CREATE INDEX IF NOT EXISTS idx_works_author_id
                ON works (author_id);

            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY CHECK (length(trim(cache_key)) > 0),
                value_json TEXT NOT NULL,
                cached_at REAL NOT NULL,
                expires_at REAL
            );
            """
        )

    @contextmanager
    def transaction(self) -> Iterator["DatabaseManager"]:
        """Run operations atomically, using savepoints for nested transactions."""
        savepoint = f"database_manager_{self._transaction_depth}"
        if self._transaction_depth == 0:
            self._connection.execute("BEGIN")
        else:
            self._connection.execute(f"SAVEPOINT {savepoint}")
        self._transaction_depth += 1

        try:
            yield self
        except BaseException:
            self._transaction_depth -= 1
            if self._transaction_depth == 0:
                self._connection.rollback()
            else:
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            self._transaction_depth -= 1
            if self._transaction_depth == 0:
                self._connection.commit()
            else:
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")

    @staticmethod
    def _required_text(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DataValidationError(
                f"{field} must be a non-empty string",
                field=field,
            )
        return value.strip()

    def upsert_author(self, name: str) -> int:
        """Insert or update an author by its unique name and return its ID."""
        name = self._required_text(name, "author")
        with self.transaction():
            self._connection.execute(
                "INSERT INTO authors (name) VALUES (?) ON CONFLICT (name) DO NOTHING",
                (name,),
            )
            row = self._connection.execute(
                "SELECT id FROM authors WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                raise sqlite3.IntegrityError("Failed to resolve inserted author")
            return int(row["id"])

    def upsert_work(self, title: str, author: str, isbn: str) -> int:
        """Insert or update a work by ISBN and return its work ID."""
        title = self._required_text(title, "title")
        author = self._required_text(author, "author")
        isbn = self._required_text(isbn, "isbn")

        with self.transaction():
            author_id = self.upsert_author(author)
            self._connection.execute(
                """
                INSERT INTO works (title, author_id, isbn)
                VALUES (?, ?, ?)
                ON CONFLICT (isbn) DO UPDATE SET
                    title = excluded.title,
                    author_id = excluded.author_id
                """,
                (title, author_id, isbn),
            )
            row = self._connection.execute(
                "SELECT id FROM works WHERE isbn = ?", (isbn,)
            ).fetchone()
            if row is None:
                raise sqlite3.IntegrityError("Failed to resolve inserted work")
            return int(row["id"])

    def add_work(self, title: str, author: str, isbn: str) -> int:
        """Insert or update a work and its author; return the work ID."""
        return self.upsert_work(title, author, isbn)

    def add_works(self, works: Iterable[Mapping[str, str]]) -> list[int]:
        """Upsert works atomically; roll back the whole batch if an operation fails."""
        validated_works = []
        for index, work in enumerate(works):
            if not isinstance(work, Mapping):
                raise DataValidationError(
                    f"work at index {index} must be a mapping",
                    field=f"works[{index}]",
                )
            try:
                title = work["title"]
                author = work["author"]
                isbn = work["isbn"]
            except KeyError as exc:
                raise DataValidationError(
                    f"work at index {index} is missing {exc.args[0]}",
                    field=f"works[{index}].{exc.args[0]}",
                ) from exc
            validated_works.append((title, author, isbn))

        with self.transaction():
            return [
                self.upsert_work(title, author, isbn)
                for title, author, isbn in validated_works
            ]

    def get_work(self, isbn: str) -> dict[str, Any] | None:
        """Return a work with its author name, or None if the ISBN is unknown."""
        isbn = self._required_text(isbn, "isbn")
        row = self._connection.execute(
            """
            SELECT works.id, works.title, works.isbn, authors.name AS author
            FROM works
            JOIN authors ON authors.id = works.author_id
            WHERE works.isbn = ?
            """,
            (isbn,),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_works(self) -> list[dict[str, Any]]:
        """Return all works with their author names, ordered by title and ISBN."""
        rows = self._connection.execute(
            """
            SELECT works.id, works.title, works.isbn, authors.name AS author
            FROM works
            JOIN authors ON authors.id = works.author_id
            ORDER BY works.title, works.isbn
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def cache_response(
        self,
        cache_key: str,
        value: Any,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        """Store a JSON-serializable value, optionally expiring it after a TTL."""
        cache_key = self._required_text(cache_key, "cache_key")
        if ttl_seconds is not None and (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or ttl_seconds < 0
        ):
            raise DataValidationError(
                "ttl_seconds must be a finite non-negative number or None",
                field="ttl_seconds",
            )

        value_json = json.dumps(value)
        cached_at = time.time()
        expires_at = cached_at + ttl_seconds if ttl_seconds is not None else None
        with self.transaction():
            self._connection.execute(
                """
                INSERT INTO api_cache (cache_key, value_json, cached_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (cache_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    cached_at = excluded.cached_at,
                    expires_at = excluded.expires_at
                """,
                (cache_key, value_json, cached_at, expires_at),
            )

    def is_cache_valid(self, cache_key: str) -> bool:
        """Return whether a cache entry exists and has not expired."""
        cache_key = self._required_text(cache_key, "cache_key")
        row = self._connection.execute(
            "SELECT expires_at FROM api_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return False
        if row["expires_at"] is not None and row["expires_at"] <= time.time():
            with self.transaction():
                self._connection.execute(
                    "DELETE FROM api_cache WHERE cache_key = ?", (cache_key,)
                )
            return False
        return True

    def get_cache_ttl(self, cache_key: str) -> float | None:
        """Return remaining seconds, None for missing/expired/unlimited entries."""
        cache_key = self._required_text(cache_key, "cache_key")
        row = self._connection.execute(
            "SELECT expires_at FROM api_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None or row["expires_at"] is None:
            return None
        remaining = row["expires_at"] - time.time()
        if remaining <= 0:
            with self.transaction():
                self._connection.execute(
                    "DELETE FROM api_cache WHERE cache_key = ?", (cache_key,)
                )
            return None
        return remaining

    def get_cached_response(self, cache_key: str) -> Any | None:
        """Return a cached JSON value, deleting and missing expired entries."""
        cache_key = self._required_text(cache_key, "cache_key")
        row = self._connection.execute(
            """
            SELECT value_json, expires_at
            FROM api_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] is not None and row["expires_at"] <= time.time():
            with self.transaction():
                self._connection.execute(
                    "DELETE FROM api_cache WHERE cache_key = ?", (cache_key,)
                )
            return None
        return json.loads(row["value_json"])

    def get_statistics(self) -> dict[str, int]:
        """Return catalog and cache counts, including cache entries past expiry."""
        works = self._connection.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        authors = self._connection.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        cache_entries = self._connection.execute(
            "SELECT COUNT(*) FROM api_cache"
        ).fetchone()[0]
        expired_cache_entries = self._connection.execute(
            "SELECT COUNT(*) FROM api_cache WHERE expires_at <= ?", (time.time(),)
        ).fetchone()[0]
        return {
            "works": int(works),
            "authors": int(authors),
            "cache_entries": int(cache_entries),
            "expired_cache_entries": int(expired_cache_entries),
        }

    def clear_expired_cache(self) -> int:
        """Delete expired API cache entries and return the number removed."""
        with self.transaction():
            cursor = self._connection.execute(
                "DELETE FROM api_cache WHERE expires_at <= ?", (time.time(),)
            )
            return cursor.rowcount

    def integrity_check(self) -> str:
        """Run SQLite's quick integrity check and return its status."""
        row = self._connection.execute("PRAGMA quick_check").fetchone()
        return str(row[0])

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._connection.close()

    def __enter__(self) -> "DatabaseManager":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
