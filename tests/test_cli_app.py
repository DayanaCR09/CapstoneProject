import subprocess

import pandas as pd

from cli_app import CLIApp
from database import DatabaseManager


class StubLibraryClient:
    def __init__(self, *, subject_response=None, search_response=None):
        self.subject_response = subject_response or {"works": []}
        self.search_response = search_response or {"docs": []}
        self.search_queries = []

    def get_subject(self, subject, *, limit):
        self.subject = subject
        self.limit = limit
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
    app = make_app(database, client, iter(["fiction", "10"]), output)

    app.ingest_by_subject()

    assert client.subject == "fiction"
    assert client.limit == 10
    assert client.search_queries == ["subject:fiction"]
    assert database.get_work("9780141439587") == {
        "id": 1,
        "title": "Emma",
        "isbn": "9780141439587",
        "author": "Jane Austen",
    }
    assert any("1 saved" in line for line in output)


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
    assert any("removed 1 duplicate" in line for line in output)


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

    assert calls
    assert calls[0][0][-2:] == ["pytest", "-q"]
    assert "Test suite passed." in output
