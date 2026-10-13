import sqlite3
import subprocess
import sys
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from database import DatabaseManager
from exceptions import ApplicationError, DataValidationError
from library_client import SyncLibraryClient
from report_generator import ReportGenerator
from text_utils import normalize_string


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

    def ingest_by_subject(self) -> None:
        subject = self._required_input("Subject: ")
        limit = self._positive_integer("Maximum works to fetch (1-100): ", maximum=100)
        with self._progress(f"Fetching works about {subject!r}"):
            response = self.client.get_subject(subject, limit=limit)
        if not isinstance(response, Mapping) or "works" not in response:
            raise DataValidationError("Subject response is missing its works list")
        works = response["works"]
        if not isinstance(works, list):
            raise DataValidationError("Subject response has an invalid works list")

        records: list[dict[str, str]] = []
        skipped = 0
        for work in works:
            if not isinstance(work, Mapping):
                skipped += 1
                continue
            title = work.get("title")
            authors = work.get("authors", [])
            author = (
                authors[0].get("name")
                if isinstance(authors, list)
                and authors
                and isinstance(authors[0], Mapping)
                else None
            )
            identifiers = work.get("cover_edition", {})
            isbns = work.get("isbn", [])
            if not isbns and isinstance(identifiers, Mapping):
                isbns = identifiers.get("isbn_13") or identifiers.get("isbn_10") or []
            if isinstance(isbns, str):
                isbns = [isbns]
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(author, str)
                or not author.strip()
                or not isinstance(isbns, list)
            ):
                skipped += 1
                continue
            isbn = next(
                (
                    item.strip()
                    for item in isbns
                    if isinstance(item, str) and item.strip()
                ),
                None,
            )
            if isbn is None:
                skipped += 1
                continue
            records.append(
                {"title": title.strip(), "author": author.strip(), "isbn": isbn}
            )

        if not records:
            with self._progress("Looking up subject works with catalog metadata"):
                response = self.client.search(f"subject:{subject}")
            if not isinstance(response, Mapping) or not isinstance(
                response.get("docs"), list
            ):
                raise DataValidationError("Search response has an invalid docs list")
            for work in response["docs"][:limit]:
                if not isinstance(work, Mapping):
                    skipped += 1
                    continue
                title = work.get("title")
                authors = work.get("author_name", [])
                isbns = work.get("isbn", [])
                if (
                    not isinstance(title, str)
                    or not title.strip()
                    or not isinstance(authors, list)
                    or not authors
                    or not isinstance(authors[0], str)
                    or not isinstance(isbns, list)
                ):
                    skipped += 1
                    continue
                isbn = next(
                    (
                        value.strip()
                        for value in isbns
                        if isinstance(value, str) and value.strip()
                    ),
                    None,
                )
                if isbn is None:
                    skipped += 1
                    continue
                records.append(
                    {
                        "title": title.strip(),
                        "author": authors[0].strip(),
                        "isbn": isbn,
                    }
                )

        records = records[:limit]
        if records:
            with self._progress(f"Saving {len(records)} catalog record(s)"):
                self.database.add_works(records)
        self._output(
            f"Ingestion finished: {len(records)} saved; "
            f"{skipped} source record(s) lacked usable metadata."
        )

    def search_catalog(self) -> None:
        query = self._required_input("Search by author, title, or ISBN: ")
        normalized_query = normalize_string(query)
        if not normalized_query:
            raise DataValidationError("Search query must contain letters or numbers")
        local_results = [
            work
            for work in self.database.list_works()
            if normalized_query
            in normalize_string(
                " ".join(
                    str(work.get(field, "")) for field in ("title", "author", "isbn")
                )
            )
        ]
        with self._progress("Searching Open Library"):
            response = self.client.search(query)
        if not isinstance(response, Mapping) or not isinstance(
            response.get("docs"), list
        ):
            raise DataValidationError("Search response has an invalid docs list")
        remote_results = response["docs"]

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

    def process_catalog_file(self) -> None:
        source = Path(self._required_input("Catalog file path (CSV, JSON, or Excel): "))
        if not source.is_file():
            raise FileNotFoundError(f"Catalog file does not exist: {source}")
        suffix = source.suffix.casefold()
        with self._progress(f"Reading {source.name}"):
            if suffix == ".csv":
                dataframe = pd.read_csv(source, dtype=str)
            elif suffix == ".json":
                dataframe = pd.read_json(source, dtype=False)
            elif suffix == ".xlsx":
                dataframe = pd.read_excel(source, dtype=str)
            else:
                raise DataValidationError(
                    "Supported catalog formats are CSV, JSON, and XLSX"
                )

        required_columns = {"title", "author", "isbn"}
        missing_columns = required_columns - set(dataframe.columns)
        if missing_columns:
            raise DataValidationError(
                f"Catalog is missing required columns: {sorted(missing_columns)}"
            )

        def duplicate_key(row: pd.Series) -> str:
            isbn = row["isbn"]
            if pd.notna(isbn) and str(isbn).strip():
                return f"isbn:{normalize_string(str(isbn)).replace(' ', '')}"
            return "metadata:" + normalize_string(f"{row['title']} {row['author']}")

        original_count = len(dataframe)
        with self._progress("Removing duplicate catalog entries"):
            keys = dataframe.apply(duplicate_key, axis=1)
            cleaned = dataframe.loc[~keys.duplicated()].copy()

        output_path = source.with_name(f"{source.stem}_deduplicated{source.suffix}")
        with self._progress(f"Writing {output_path.name}"):
            if suffix == ".csv":
                cleaned.to_csv(output_path, index=False, encoding="utf-8")
            elif suffix == ".json":
                cleaned.to_json(
                    output_path,
                    orient="records",
                    force_ascii=False,
                    indent=2,
                )
            elif suffix == ".xlsx":
                cleaned.to_excel(output_path, index=False)
        self._output(
            f"Processed {original_count} row(s); removed "
            f"{original_count - len(cleaned)} duplicate(s). Output: {output_path}"
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
        output_directory = Path(
            self._input("Report output directory [reports]: ").strip() or "reports"
        )
        with self._progress("Generating Excel, CSV, and JSON reports"):
            outputs = ReportGenerator.export_reports(
                {"Catalog": works_frame, "By author": author_summary},
                output_directory,
                filename_prefix="library",
            )
        for name, path in outputs.items():
            self._output(f"{name}: {path}")

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

        with self._progress("Running the project test suite"):
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
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
            "Test suite passed."
            if result.returncode == 0
            else f"Test suite failed with exit code {result.returncode}."
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
