import sqlite3

import pytest

from database import DatabaseManager
from exceptions import DataValidationError


def test_initializes_tables_and_adds_catalog_works(tmp_path):
    database_path = tmp_path / "catalog.sqlite"
    with DatabaseManager(database_path) as database:
        first_id = database.add_work(
            "Time and social theory",
            "Barbara Adam",
            "0745614078",
        )
        second_id = database.add_work(
            "Time and social theory",
            "Barbara Adam",
            "0877227888",
        )

        assert first_id != second_id
        assert database.get_work("0745614078") == {
            "id": first_id,
            "title": "Time and social theory",
            "isbn": "0745614078",
            "author": "Barbara Adam",
        }
        assert len(database.list_works()) == 2

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"works", "authors", "api_cache"} <= tables


def test_add_work_is_idempotent_for_isbn():
    with DatabaseManager(":memory:") as database:
        first_id = database.upsert_work("Old title", "First author", "1234567890")
        second_id = database.upsert_work("New title", "Second author", "1234567890")

        assert first_id == second_id
        assert database.get_work("1234567890") == {
            "id": first_id,
            "title": "New title",
            "isbn": "1234567890",
            "author": "Second author",
        }


def test_upsert_author_is_idempotent():
    with DatabaseManager(":memory:") as database:
        first_id = database.upsert_author("Jane Austen")
        second_id = database.upsert_author("Jane Austen")

        assert first_id == second_id


def test_add_works_batches_catalog_upserts_atomically():
    with DatabaseManager(":memory:") as database:
        work_ids = database.add_works(
            [
                {
                    "title": "Pride and Prejudice",
                    "author": "Jane Austen",
                    "isbn": "9780192503350",
                },
                {
                    "title": "Emma",
                    "author": "Jane Austen",
                    "isbn": "9780141439587",
                },
            ]
        )

        assert len(work_ids) == 2
        assert len(database.list_works()) == 2

        with pytest.raises(RuntimeError):
            with database.transaction():
                database.add_work("Persuasion", "Jane Austen", "9780141439518")
                raise RuntimeError("abort batch")

        assert database.get_work("9780141439518") is None


def test_add_works_rolls_back_when_an_upsert_fails(monkeypatch):
    with DatabaseManager(":memory:") as database:
        original_upsert_work = database.upsert_work
        calls = 0

        def fail_on_second_work(title, author, isbn):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("database write failed")
            return original_upsert_work(title, author, isbn)

        monkeypatch.setattr(database, "upsert_work", fail_on_second_work)
        with pytest.raises(RuntimeError, match="database write failed"):
            database.add_works(
                [
                    {"title": "First", "author": "Author", "isbn": "111"},
                    {"title": "Second", "author": "Author", "isbn": "222"},
                ]
            )

        assert database.list_works() == []


def test_add_works_validates_batch_items():
    with DatabaseManager(":memory:") as database:
        with pytest.raises(DataValidationError, match="missing isbn"):
            database.add_works(
                [
                    {"title": "First", "author": "Author", "isbn": "111"},
                    {"title": "Second", "author": "Author"},
                ]
            )

        assert database.list_works() == []


def test_rejects_blank_work_fields():
    with DatabaseManager(":memory:") as database:
        with pytest.raises(DataValidationError, match="title"):
            database.add_work("", "Author", "1234567890")


def test_cache_stores_updates_and_expires_values():
    with DatabaseManager(":memory:") as database:
        database.cache_response("books:one", {"title": "Book"})
        assert database.get_cached_response("books:one") == {"title": "Book"}
        assert database.is_cache_valid("books:one")
        assert database.get_cache_ttl("books:one") is None

        database.cache_response("books:one", ["updated"], ttl_seconds=60)
        assert database.get_cached_response("books:one") == ["updated"]
        assert database.is_cache_valid("books:one")
        assert 0 < database.get_cache_ttl("books:one") <= 60

        database.cache_response("books:expired", {"stale": True}, ttl_seconds=0)
        assert not database.is_cache_valid("books:expired")
        assert database.get_cache_ttl("books:expired") is None
        assert database.get_cached_response("books:expired") is None
        assert database.get_cached_response("books:missing") is None


@pytest.mark.parametrize("ttl", [-1, float("nan"), float("inf"), True])
def test_rejects_invalid_cache_ttl(ttl):
    with DatabaseManager(":memory:") as database:
        with pytest.raises(DataValidationError, match="ttl_seconds"):
            database.cache_response("books:one", {}, ttl_seconds=ttl)
