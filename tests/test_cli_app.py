import json
import subprocess

import httpx
import pandas as pd
import pytest

from cli_app import CLIApp
from database import DatabaseManager
from exceptions import DataValidationError
from library_client import SyncLibraryClient


class StubLibraryClient:
    def __init__(self, *, subject_response=None, search_response=None):
        self.subject_response = subject_response or {"works": []}
        self.search_response = search_response or {"docs": []}
        self.search_queries = []

    def get_subject(self, subject, *, limit, offset=0):
        self.subject = subject
        self.limit = limit
        self.offset = offset
        return self.subject_response

    def search(self, query):
        self.search_queries.append(query)
        return self.search_response


def make_app(database, client, inputs, output):
    return CLIApp(
        database,
        client,
        input_func=lambda _prompt: next(inputs),
        output_func=output.append,
    )


def test_menu_rejects_invalid_choice_and_loops_until_exit():
    output = []
    app = make_app(
        DatabaseManager(":memory:"),
        StubLibraryClient(),
        iter(["invalid", "0"]),
        output,
    )

    app.run()

    assert sum("[1] Ingest Works by Subject" in line for line in output) == 2
    assert any("Invalid option" in line for line in output)
    assert output[-1] == "Goodbye."


def test_ingest_enriches_subject_works_then_saves_catalog_records():
    database = DatabaseManager(":memory:")
    client = StubLibraryClient(
        subject_response={
            "works": [{"title": "Emma", "authors": [{"key": "/authors/OL1A"}]}]
        },
        search_response={
            "docs": [
                {
                    "title": "Emma",
                    "author_name": ["Jane Austen"],
                    "isbn": ["9780141439587"],
                }
            ]
        },
    )
    output = []
    app = make_app(database, client, iter(["fiction", "10", "0"]), output)

    app.ingest_by_subject()

    assert client.subject == "fiction"
    assert client.limit == 10
    assert client.offset == 0
    assert client.search_queries == ["subject:fiction"]
    assert database.get_work("9780141439587") == {
        "id": 1,
        "title": "Emma",
        "isbn": "9780141439587",
        "author": "Jane Austen",
    }
    assert any("1 saved" in line for line in output)


def test_subject_ingestion_compares_sync_async_and_caches_api_results(monkeypatch):
    work = {
        "title": "Emma",
        "authors": [{"name": "Jane Austen"}],
        "isbn": ["9780141439587"],
    }
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"works": [work]}, request=request)

    class AsyncStub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_subject(self, subject, *, limit, offset):
            assert (subject, limit, offset) == ("fiction", 10, 0)
            return {"works": [work]}

    monkeypatch.setattr("cli_app.AsyncLibraryClient", lambda **_kwargs: AsyncStub())
    database = DatabaseManager(":memory:")
    output = []
    app = make_app(
        database,
        SyncLibraryClient(
            transport=httpx.MockTransport(handler),
            max_retries=0,
        ),
        iter(["fiction", "10", "0"]),
        output,
    )

    app.ingest_by_subject()

    assert len(requests) == 1
    assert database.get_work("9780141439587") is not None
    assert database.get_cached_response("subject:sync:fiction:10:0") == {
        "works": [work]
    }
    assert database.get_cached_response("subject:async:fiction:10:0") == {
        "works": [work]
    }
    assert any("API runtime comparison: sync" in line for line in output)


def test_process_catalog_file_writes_deduplicated_csv(tmp_path):
    source = tmp_path / "catalog.csv"
    pd.DataFrame(
        [
            {"title": "Emma", "author": "Jane Austen", "isbn": "978-0141439587"},
            {
                "title": "Emma duplicate",
                "author": "Jane Austen",
                "isbn": "9780141439587",
            },
            {"title": "Persuasion", "author": "Jane Austen", "isbn": "9780141439518"},
        ]
    ).to_csv(source, index=False)
    output = []
    app = make_app(
        DatabaseManager(":memory:"),
        StubLibraryClient(),
        iter([str(source)]),
        output,
    )

    app.process_catalog_file()

    cleaned = pd.read_csv(tmp_path / "catalog_deduplicated.csv", dtype=str)
    assert cleaned["title"].tolist() == ["Emma", "Persuasion"]
    assert len(app.database.list_works()) == 2
    assert any("removed 1 duplicate" in line for line in output)


def test_process_catalog_file_streams_json_and_saves_utf8_metadata(tmp_path):
    source = tmp_path / "catalog.json"
    source.write_text(
        '[{"title":"Crème brûlée","author":"A. Writer",'
        '"isbn":"9780306406157","year":2024,"language":"eng"}]',
        encoding="utf-8",
    )
    database = DatabaseManager(":memory:")
    app = make_app(
        database,
        StubLibraryClient(),
        iter([str(source)]),
        [],
    )

    app.process_catalog_file()

    assert database.list_works()[0]["title"] == "Crème brûlée"
    assert database.list_works()[0]["published_year"] == 2024
    assert database.list_works()[0]["language"] == "eng"
    assert (tmp_path / "catalog_deduplicated.json").is_file()


def test_json_row_generator_handles_rows_spanning_read_chunks(tmp_path):
    source = tmp_path / "large.json"
    source.write_text(
        json.dumps([{"title": "A" * 70000}, {"title": "Emma"}]),
        encoding="utf-8",
    )

    rows = list(CLIApp._iter_catalog_records(source))

    assert len(rows) == 2
    assert len(rows[0]["title"]) == 70000
    assert rows[1]["title"] == "Emma"


def test_generate_reports_writes_timestamped_summaries_and_minified_handoff(
    tmp_path,
):
    database = DatabaseManager(":memory:")
    database.add_works(
        [
            {
                "title": "Emma",
                "author": "Jane Austen",
                "isbn": "9780141439587",
                "published_year": 1815,
                "language": "eng",
            }
        ]
    )
    output = []
    app = make_app(database, StubLibraryClient(), iter([str(tmp_path)]), output)

    app.generate_reports()

    prefix = f"library_report_{pd.Timestamp.now():%Y%m%d}"
    workbook = tmp_path / f"{prefix}.xlsx"
    handoff = tmp_path / f"{prefix}_api_handoff.json"
    assert workbook.is_file()
    assert handoff.read_text(encoding="utf-8").startswith('[{"id":1,"title":"Emma"')
    assert (tmp_path / f"{prefix}_By publication year.csv").is_file()
    assert (tmp_path / f"{prefix}_By language.csv").is_file()
    assert any("API handoff JSON:" in line for line in output)


def test_search_validates_isbn_and_uses_api_cache_for_misses():
    database = DatabaseManager(":memory:")
    client = StubLibraryClient(
        search_response={
            "docs": [
                {
                    "title": "Emma",
                    "author_name": ["Jane Austen"],
                    "isbn": ["9780141439587"],
                }
            ]
        }
    )
    output = []

    with pytest.raises(DataValidationError, match="valid ISBN"):
        make_app(
            database, client, iter(["i", "9780306406158"]), output
        ).search_catalog()

    app = make_app(database, client, iter(["a", "Jane Austen"]), output)
    app.search_catalog()
    assert client.search_queries == ["author:Jane Austen"]
    assert database.get_work("9780141439587") is not None

    make_app(database, client, iter(["a", "Jane Austen"]), output).search_catalog()
    assert client.search_queries == ["author:Jane Austen"]


def test_search_uses_the_selected_field_for_local_matches():
    database = DatabaseManager(":memory:")
    database.add_work("Emma", "Jane Austen", "9780141439587")
    client = StubLibraryClient()
    output = []

    make_app(database, client, iter(["t", "Jane"]), output).search_catalog()

    assert client.search_queries == ["title:Jane"]
    assert any("Local catalog matches: 0" in line for line in output)


def test_system_health_cleans_expired_cache_and_reports_integrity():
    database = DatabaseManager(":memory:")
    database.cache_response("expired", {"value": 1}, ttl_seconds=0)
    output = []
    app = make_app(database, StubLibraryClient(), iter([]), output)

    app.system_health()

    assert database.get_statistics()["cache_entries"] == 0
    assert "Database integrity: ok" in output
    assert "Expired cache entries removed: 1" in output


def test_quality_diagnostics_runs_test_suite_after_successful_checks(
    monkeypatch,
):
    database = DatabaseManager(":memory:")
    output = []
    app = make_app(database, StubLibraryClient(), iter([]), output)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "3 passed", "")

    monkeypatch.setattr("cli_app.subprocess.run", fake_run)

    app.run_quality_diagnostics()

    assert calls[0][0] == ["uv", "run", "ruff", "check", "."]
    assert calls[1][0][:5] == [
        "uv",
        "run",
        "ruff",
        "format",
        "--check",
    ]
    assert calls[2][0] == ["uv", "run", "python", "-m", "pytest", "-q"]
    assert "Ruff lint passed." in output
    assert "Ruff format check passed." in output
    assert "Pytest passed." in output
