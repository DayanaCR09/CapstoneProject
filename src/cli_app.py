import asyncio
import csv
import json
import re
import sqlite3
import subprocess
import time
import unicodedata
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pandas as pd
from dotenv import dotenv_values

from database import DatabaseManager
from exceptions import ApplicationError, DataValidationError
from library_client import AsyncLibraryClient, SyncLibraryClient
from report_generator import ReportGenerator
from text_utils import extract_isbns, normalize_string

_CACHE_TTL_SECONDS = 24 * 60 * 60
_INVALID_QUERY_CHARACTERS = re.compile(r"[^\w\s&'’-]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


class CLIApp:
    """Interactive command-line interface for the library catalog system."""

    _SEPARATOR = "=" * 78
    _MENU_OPTIONS = frozenset({"0", "1", "2", "3", "4", "5", "6"})

    def __init__(
        self,
        database: DatabaseManager | None = None,
        client: SyncLibraryClient | None = None,
        *,
        input_func: Callable[[str], str] | None = None,
        output_func: Callable[[str], None] = print,
        project_root: str | Path | None = None,
    ) -> None:
        self.database = database or DatabaseManager()
        self.client = client or SyncLibraryClient()
        self._owns_database = database is None
        self._owns_client = client is None
        self._input = input_func or input
        self._output = output_func
        self._project_root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[1]
        )
        self._actions: dict[str, Callable[[], None]] = {
            "1": self.ingest_by_subject,
            "2": self.search_catalog,
            "3": self.process_catalog_file,
            "4": self.generate_reports,
            "5": self.system_health,
            "6": self.run_quality_diagnostics,
        }

    def display_menu(self) -> None:
        self._output(self._SEPARATOR)
        self._output("OPEN LIBRARY INTELLIGENCE & CATALOG AUTOMATION SYSTEM".center(78))
        self._output(self._SEPARATOR)
        self._output("[1] Ingest Works by Subject")
        self._output("[2] Search Books by Author/Title/ISBN")
        self._output("[3] Process & Deduplicate Local Catalog File")
        self._output("[4] Generate Analytical Reports")
        self._output("[5] System Health, Cache Maintenance & Database Administration")
        self._output("[6] Run Quality Checks & Test Suite Diagnostic")
        self._output("[0] Exit")
        self._output(self._SEPARATOR)

    def run(self) -> None:
        """Run the menu until the user chooses exit or closes the input stream."""
        try:
            while True:
                self.display_menu()
                try:
                    choice = self._input("Select an option: ").strip()
                except EOFError:
                    self._output("\nInput closed. Exiting.")
                    break
                except KeyboardInterrupt:
                    self._output("\nInterrupted. Exiting.")
                    break

                if choice not in self._MENU_OPTIONS:
                    self._output("Invalid option. Enter a number from 0 to 6.")
                    continue
                if choice == "0":
                    self._output("Goodbye.")
                    break

                try:
                    self._actions[choice]()
                except EOFError:
                    self._output("\nInput closed. Exiting.")
                    break
                except KeyboardInterrupt:
                    self._output("\nOperation cancelled.")
                except (ApplicationError, OSError, ValueError, sqlite3.Error) as exc:
                    self._output(f"Operation failed ({type(exc).__name__}): {exc}")
                except Exception as exc:
                    self._output(f"Unexpected error ({type(exc).__name__}): {exc}")
        finally:
            self.close()

    @contextmanager
    def _progress(self, message: str) -> Iterator[None]:
        self._output(f"{message} ...")
        try:
            yield
        except Exception:
            self._output(f"{message} failed.")
            raise
        else:
            self._output(f"{message} complete.")

    def _required_input(self, prompt: str) -> str:
        while True:
            value = self._input(prompt).strip()
            if value:
                return value
            self._output("This value cannot be empty.")

    def _positive_integer(
        self,
        prompt: str,
        *,
        minimum: int = 1,
        maximum: int | None = None,
    ) -> int:
        while True:
            value = self._input(prompt).strip()
            try:
                number = int(value)
            except ValueError:
                self._output("Enter a whole number.")
                continue
            if number < minimum or (maximum is not None and number > maximum):
                limit = f" and no more than {maximum}" if maximum is not None else ""
                self._output(f"Enter a number of at least {minimum}{limit}.")
                continue
            return number

    def _clean_input(self, prompt: str, *, field: str) -> str:
        while True:
            value = self._input(prompt)
            cleaned = _WHITESPACE.sub(
                " ",
                _INVALID_QUERY_CHARACTERS.sub("", unicodedata.normalize("NFKC", value)),
            ).strip()
            if cleaned:
                return cleaned
            self._output(f"Enter a valid {field} using letters or numbers.")

    @staticmethod
    def _catalog_record(
        item: Mapping[str, object],
        details: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None:
        details = details or {}
        title = item.get("title") or details.get("title")
        authors = item.get("authors") or item.get("author_name") or []
        if isinstance(authors, list) and authors:
            first_author = authors[0]
            author = (
                first_author.get("name")
                if isinstance(first_author, Mapping)
                else first_author
            )
        else:
            author = None
        if not author:
            detail_authors = details.get("authors", [])
            author = (
                detail_authors[0].get("name")
                if isinstance(detail_authors, list)
                and detail_authors
                and isinstance(detail_authors[0], Mapping)
                else None
            )

        editions = item.get("cover_edition") or details.get("cover_edition") or {}
        isbns = item.get("isbn") or []
        if isinstance(editions, Mapping):
            isbns = isbns or editions.get("isbn_13") or editions.get("isbn_10") or []
        if isinstance(isbns, str):
            isbns = [isbns]
        valid_isbns = (
            [
                isbn
                for value in isbns
                if isinstance(value, str)
                for isbn in extract_isbns(value)
            ]
            if isinstance(isbns, list)
            else []
        )
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(author, str)
            or not author.strip()
            or not valid_isbns
        ):
            return None

        year_value = item.get("first_publish_year") or details.get("first_publish_date")
        year_match = re.search(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", str(year_value or ""))
        languages = item.get("languages") or details.get("languages") or []
        language = None
        if isinstance(languages, list) and languages:
            first_language = languages[0]
            if isinstance(first_language, Mapping):
                language = first_language.get("key") or first_language.get("name")
                if isinstance(language, str):
                    language = language.rsplit("/", maxsplit=1)[-1]
            elif isinstance(first_language, str):
                language = first_language
        record: dict[str, object] = {
            "title": title.strip(),
            "author": author.strip(),
            "isbn": valid_isbns[0],
        }
        if year_match:
            record["published_year"] = int(year_match.group())
        if language:
            record["language"] = language
        return record

    def _cached_response(self, key: str, fetch: Callable[[], object]) -> object:
        response = self.database.get_cached_response(key)
        if response is not None:
            return response
        response = fetch()
        self.database.cache_response(
            key,
            response,
            ttl_seconds=_CACHE_TTL_SECONDS,
        )
        return response

    async def _fetch_subject_async(
        self,
        subject: str,
        limit: int,
        offset: int,
    ) -> object:
        cache_key = f"subject:async:{subject}:{limit}:{offset}"
        cached = self.database.get_cached_response(cache_key)
        if cached is not None:
            return cached
        async with AsyncLibraryClient(
            base_url=getattr(self.client, "_base_url", "https://openlibrary.org")
        ) as client:
            response = await client.get_subject(subject, limit=limit, offset=offset)
        self.database.cache_response(
            cache_key,
            response,
            ttl_seconds=_CACHE_TTL_SECONDS,
        )
        return response

    def ingest_by_subject(self) -> None:
        subject = self._clean_input("Subject: ", field="subject")
        limit = self._positive_integer(
            "Maximum works to fetch (1-100): ",
            maximum=100,
        )
        offset = self._positive_integer(
            "Starting result offset [0]: ",
            minimum=0,
        )
        sync_key = f"subject:sync:{subject}:{limit}:{offset}"

        with self._progress(f"Fetching works about {subject!r} synchronously"):
            started = time.perf_counter()
            response = self._cached_response(
                sync_key,
                lambda: self.client.get_subject(
                    subject,
                    limit=limit,
                    offset=offset,
                ),
            )
            sync_elapsed = time.perf_counter() - started

        if isinstance(self.client, SyncLibraryClient):
            with self._progress(f"Fetching works about {subject!r} asynchronously"):
                started = time.perf_counter()
                async_response = asyncio.run(
                    self._fetch_subject_async(subject, limit, offset)
                )
                async_elapsed = time.perf_counter() - started
            self._output(
                f"API runtime comparison: sync {sync_elapsed:.3f}s; "
                f"async {async_elapsed:.3f}s."
            )
        else:
            async_response = response
            self._output(
                "API runtime comparison unavailable for the injected test client."
            )

        if not isinstance(response, Mapping) or not isinstance(
            response.get("works"), list
        ):
            raise DataValidationError("Subject response has an invalid works list")
        if not isinstance(async_response, Mapping) or not isinstance(
            async_response.get("works"), list
        ):
            raise DataValidationError(
                "Async subject response has an invalid works list"
            )

        records: list[dict[str, object]] = []
        skipped = 0
        for work in response["works"][:limit]:
            if not isinstance(work, Mapping):
                skipped += 1
                continue
            details = None
            work_key = work.get("key")
            if isinstance(work_key, str) and re.fullmatch(r"/works/OL\d+W", work_key):
                work_id = work_key.rsplit("/", maxsplit=1)[-1]
                details = (
                    self._cached_response(
                        f"work:{work_id}",
                        lambda work_id=work_id: self.client.get_work(work_id),
                    )
                    if callable(getattr(self.client, "get_work", None))
                    else None
                )
            record = self._catalog_record(
                work,
                details if isinstance(details, Mapping) else None,
            )
            if record is None:
                skipped += 1
            else:
                records.append(record)

        if not records:
            with self._progress("Looking up subject works with catalog metadata"):
                search_response = self._cached_response(
                    f"search:subject:{subject}",
                    lambda: self.client.search(f"subject:{subject}"),
                )
            if not isinstance(search_response, Mapping) or not isinstance(
                search_response.get("docs"), list
            ):
                raise DataValidationError("Search response has an invalid docs list")
            for work in search_response["docs"][:limit]:
                record = (
                    self._catalog_record(work) if isinstance(work, Mapping) else None
                )
                if record is None:
                    skipped += 1
                else:
                    records.append(record)

        records = records[:limit]
        if records:
            with self._progress(f"Saving {len(records)} catalog record(s)"):
                self.database.add_works(records)
        self._output(
            f"Ingestion finished: {len(records)} saved; "
            f"{skipped} source record(s) lacked usable metadata."
        )

    def search_catalog(self) -> None:
        self._output("Search type: [A]uthor, [T]itle, or [I]SBN")
        while True:
            search_type = self._input("Select search type: ").strip().casefold()
            if search_type in {"a", "t", "i"}:
                break
            self._output("Choose A, T, or I.")

        query = self._clean_input(
            "Search term: ",
            field="search term",
        )
        if search_type == "i":
            compact_isbn = re.sub(r"[^0-9Xx]", "", query).upper()
            if extract_isbns(compact_isbn) != [compact_isbn]:
                raise DataValidationError("Enter a valid ISBN-10 or ISBN-13 checksum")
            api_query = f"isbn:{compact_isbn}"
            normalized_query = compact_isbn.casefold()
        else:
            normalized_query = normalize_string(query)
            api_query = f"{'author' if search_type == 'a' else 'title'}:{query}"

        local_results = [
            work
            for work in self.database.list_works()
            if (
                re.sub(r"[^0-9X]", "", str(work.get("isbn", "")).upper())
                == normalized_query.upper()
                if search_type == "i"
                else normalized_query
                in normalize_string(
                    str(work.get("author" if search_type == "a" else "title", ""))
                )
            )
        ]

        remote_results: list[object] = []
        if not local_results:
            with self._progress("Checking cached Open Library results"):
                response = self.database.get_cached_response(f"search:{api_query}")
            if response is None:
                with self._progress("Searching Open Library"):
                    response = self.client.search(api_query)
                    self.database.cache_response(
                        f"search:{api_query}",
                        response,
                        ttl_seconds=_CACHE_TTL_SECONDS,
                    )
            if not isinstance(response, Mapping) or not isinstance(
                response.get("docs"), list
            ):
                raise DataValidationError("Search response has an invalid docs list")
            remote_results = response["docs"]
            records = [
                record
                for work in remote_results
                if isinstance(work, Mapping)
                if (record := self._catalog_record(work)) is not None
            ]
            if records:
                self.database.add_works(records)

        self._output(f"\nLocal catalog matches: {len(local_results)}")
        for work in local_results[:10]:
            self._output(f"- {work['title']} - {work['author']} (ISBN: {work['isbn']})")
        self._output(f"\nOpen Library matches: {len(remote_results)}")
        for work in remote_results[:10]:
            if not isinstance(work, Mapping):
                continue
            title = work.get("title", "Untitled")
            authors = work.get("author_name", [])
            author = ", ".join(authors[:3]) if isinstance(authors, list) else "Unknown"
            isbns = work.get("isbn", [])
            isbn = isbns[0] if isinstance(isbns, list) and isbns else "N/A"
            self._output(f"- {title} - {author} (ISBN: {isbn})")

    @staticmethod
    def _iter_json_array(source: Path) -> Iterator[Mapping[str, object]]:
        decoder = json.JSONDecoder()
        with source.open(encoding="utf-8") as json_file:
            buffer = json_file.read(65536)
            position = 0
            exhausted = not buffer

            def skip_whitespace() -> None:
                nonlocal buffer, position, exhausted
                while True:
                    while position < len(buffer) and buffer[position].isspace():
                        position += 1
                    if position < len(buffer) or exhausted:
                        return
                    chunk = json_file.read(65536)
                    if not chunk:
                        exhausted = True
                    buffer += chunk

            skip_whitespace()
            if position >= len(buffer) or buffer[position] != "[":
                raise DataValidationError("Catalog JSON must contain an array of rows")
            position += 1
            has_value = False

            while True:
                skip_whitespace()
                if position >= len(buffer):
                    raise DataValidationError("Catalog JSON array is not terminated")
                if buffer[position] == "]":
                    if buffer[position + 1 :].strip() or json_file.read().strip():
                        raise DataValidationError(
                            "Unexpected content after catalog JSON array"
                        )
                    return
                if has_value:
                    if buffer[position] != ",":
                        raise DataValidationError(
                            "Catalog JSON rows must be comma-separated"
                        )
                    position += 1
                    skip_whitespace()
                    if position < len(buffer) and buffer[position] == "]":
                        raise DataValidationError("Catalog JSON has a trailing comma")

                try:
                    row, next_position = decoder.raw_decode(buffer, position)
                except json.JSONDecodeError as exc:
                    if exhausted:
                        raise DataValidationError(
                            "Catalog JSON contains an invalid or incomplete row"
                        ) from exc
                    chunk = json_file.read(65536)
                    if not chunk:
                        exhausted = True
                    buffer += chunk
                    continue

                if not isinstance(row, Mapping):
                    raise DataValidationError(
                        "Every catalog JSON row must be an object"
                    )
                yield row
                position = next_position
                has_value = True
                if position > 32768:
                    buffer = buffer[position:]
                    position = 0

    @staticmethod
    def _iter_catalog_records(source: Path) -> Iterator[Mapping[str, object]]:
        suffix = source.suffix.casefold()
        if suffix == ".csv":
            with source.open(encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                if reader.fieldnames is None:
                    raise DataValidationError("Catalog CSV is missing its header row")
                for row in reader:
                    yield row
            return
        if suffix == ".json":
            yield from CLIApp._iter_json_array(source)
            return
        if suffix == ".xlsx":
            dataframe = pd.read_excel(source, dtype=str)
            for row in dataframe.to_dict(orient="records"):
                yield row
            return
        raise DataValidationError("Supported catalog formats are CSV, JSON, and XLSX")

    @staticmethod
    def _normalize_catalog_row(
        source_row: Mapping[str, object],
    ) -> dict[str, object] | None:
        row = {
            str(key).strip().casefold(): (
                unicodedata.normalize("NFKC", value).strip()
                if isinstance(value, str)
                else value
            )
            for key, value in source_row.items()
        }
        title = row.get("title")
        author = row.get("author")
        raw_isbn = row.get("isbn")
        if not all(isinstance(value, str) and value for value in (title, author)):
            return None
        if not isinstance(raw_isbn, str):
            raw_isbn = str(raw_isbn) if raw_isbn is not None else ""
        valid_isbns = extract_isbns(raw_isbn)
        if not valid_isbns:
            return None
        record: dict[str, object] = {
            "title": title,
            "author": author,
            "isbn": valid_isbns[0],
        }

        year_value = row.get("published_year") or row.get("year")
        year_match = re.search(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", str(year_value or ""))
        if year_match:
            record["published_year"] = int(year_match.group())
        language_value = row.get("language") or row.get("lang")
        if isinstance(language_value, str) and language_value.strip():
            record["language"] = language_value.strip()
        return record

    def process_catalog_file(self) -> None:
        source = Path(self._required_input("Catalog file path (CSV, JSON, or Excel): "))
        if not source.is_file():
            raise FileNotFoundError(f"Catalog file does not exist: {source}")

        original_count = 0
        invalid_count = 0
        seen: set[str] = set()
        cleaned_records: list[dict[str, object]] = []
        with self._progress(f"Streaming and validating {source.name}"):
            for source_row in self._iter_catalog_records(source):
                original_count += 1
                record = self._normalize_catalog_row(source_row)
                if record is None:
                    invalid_count += 1
                    continue
                key = f"isbn:{record['isbn']}"
                if key in seen:
                    continue
                seen.add(key)
                cleaned_records.append(record)

        if cleaned_records:
            with self._progress(f"Upserting {len(cleaned_records)} catalog record(s)"):
                self.database.add_works(cleaned_records)

        cleaned = pd.DataFrame(cleaned_records)
        output_path = source.with_name(f"{source.stem}_deduplicated{source.suffix}")
        with self._progress(f"Writing {output_path.name}"):
            if source.suffix.casefold() == ".csv":
                cleaned.to_csv(output_path, index=False, encoding="utf-8")
            elif source.suffix.casefold() == ".json":
                cleaned.to_json(
                    output_path,
                    orient="records",
                    force_ascii=False,
                    indent=2,
                )
            else:
                cleaned.to_excel(output_path, index=False)
        self._output(
            f"Processed {original_count} row(s); imported {len(cleaned_records)}, "
            f"removed {original_count - invalid_count - len(cleaned_records)} "
            f"duplicate(s), and skipped {invalid_count} invalid row(s). "
            f"Output: {output_path}"
        )

    def generate_reports(self) -> None:
        works = self.database.list_works()
        if not works:
            self._output("The catalog is empty; add works before generating reports.")
            return
        works_frame = pd.DataFrame(works)
        author_summary = (
            works_frame.groupby("author", as_index=False)
            .agg(work_count=("isbn", "count"))
            .sort_values("work_count", ascending=False)
        )
        year_summary = (
            works_frame.dropna(subset=["published_year"])
            .groupby("published_year", as_index=False)
            .agg(work_count=("isbn", "count"))
            .sort_values("published_year", ascending=False)
        )
        language_summary = (
            works_frame.dropna(subset=["language"])
            .groupby("language", as_index=False)
            .agg(work_count=("isbn", "count"))
            .sort_values("work_count", ascending=False)
        )
        metrics = pd.DataFrame(
            [
                {"metric": "total_works", "value": len(works_frame)},
                {"metric": "total_authors", "value": works_frame["author"].nunique()},
                {
                    "metric": "works_with_publication_year",
                    "value": int(works_frame["published_year"].notna().sum()),
                },
                {
                    "metric": "works_with_language",
                    "value": int(works_frame["language"].notna().sum()),
                },
            ]
        )
        output_directory = Path(
            self._input("Report output directory [reports]: ").strip() or "reports"
        )
        filename_prefix = f"library_report_{datetime.now():%Y%m%d}"
        with self._progress("Generating Excel, CSV, and JSON reports"):
            outputs = ReportGenerator.export_reports(
                {
                    "Catalog": works_frame,
                    "By author": author_summary,
                    "By publication year": year_summary,
                    "By language": language_summary,
                    "Performance metrics": metrics,
                },
                output_directory,
                filename_prefix=filename_prefix,
            )
            handoff_path = ReportGenerator.export_json(
                works_frame,
                output_directory / f"{filename_prefix}_api_handoff.json",
                minified=True,
            )
        for name, path in outputs.items():
            self._output(f"{name}: {path}")
        self._output(f"API handoff JSON: {handoff_path}")

    def system_health(self) -> None:
        with self._progress("Checking database health and cache"):
            health = self.database.integrity_check()
            stats = self.database.get_statistics()
            removed = self.database.clear_expired_cache()
        self._output(f"Database integrity: {health}")
        self._output(
            "Catalog: {works} works, {authors} authors; cache: "
            "{cache_entries} entries.".format(**stats)
        )
        self._output(
            "Database storage: {storage_bytes} bytes; cache hits: {cache_hits}, "
            "misses: {cache_misses}, hit ratio: {cache_hit_ratio:.1%}.".format(**stats)
        )
        env_path = Path.cwd() / ".env"
        configured_values = (
            {
                key: value
                for key, value in dotenv_values(env_path).items()
                if value is not None and value.strip()
            }
            if env_path.is_file()
            else {}
        )
        self._output(
            f".env runtime state: {'present' if env_path.is_file() else 'not found'}; "
            f"{len(configured_values)} non-empty setting(s)."
        )
        self._output(f"Expired cache entries removed: {removed}")
        if health != "ok":
            self._output("Warning: database integrity check reported a problem.")

    def run_quality_diagnostics(self) -> None:
        with self._progress("Running catalog quality checks"):
            works = self.database.list_works()
            invalid_rows = [
                work
                for work in works
                if not all(
                    isinstance(work.get(field), str) and work[field].strip()
                    for field in ("title", "author", "isbn")
                )
            ]
            integrity = self.database.integrity_check()
        self._output(f"Catalog records checked: {len(works)}")
        self._output(f"Records with missing required values: {len(invalid_rows)}")
        self._output(f"Database integrity: {integrity}")
        if invalid_rows or integrity != "ok":
            self._output("Quality checks reported issues; test suite was not started.")
            return

        commands = (
            ("Ruff lint", ["uv", "run", "ruff", "check", "."]),
            (
                "Ruff format check",
                [
                    "uv",
                    "run",
                    "ruff",
                    "format",
                    "--check",
                    "src/cli_app.py",
                    "src/database.py",
                    "src/report_generator.py",
                    "tests/test_cli_app.py",
                    "tests/test_database.py",
                    "tests/test_report_generator.py",
                ],
            ),
            ("Pytest", ["uv", "run", "python", "-m", "pytest", "-q"]),
        )
        for name, command in commands:
            with self._progress(f"Running {name}"):
                result = subprocess.run(
                    command,
                    cwd=self._project_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            if result.stdout.strip():
                self._output(result.stdout.strip())
            if result.stderr.strip():
                self._output(result.stderr.strip())
            self._output(
                f"{name} passed."
                if result.returncode == 0
                else f"{name} failed with exit code {result.returncode}."
            )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
        if self._owns_database:
            self.database.close()


def main() -> None:
    CLIApp().run()


if __name__ == "__main__":
    main()
