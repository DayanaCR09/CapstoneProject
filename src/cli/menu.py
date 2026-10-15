import logging
import re
import sqlite3
import subprocess
import unicodedata
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from dotenv import dotenv_values

from api.catalog_service import CatalogService
from api.sync_client import SyncLibraryClient
from config import load_settings
from db.manager import DatabaseManager
from exceptions import ApplicationError
from logger import setup_logging
from processing.analytics import build_catalog_analytics
from processing.cleaner import CatalogCleaner
from reporting.generator import ReportGenerator

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
        settings = load_settings()
        self.database = database or DatabaseManager(settings.database_path)
        self.client = client or SyncLibraryClient(settings.api_base_url)
        self._owns_database = database is None
        self._owns_client = client is None
        self._input = input_func or input
        self._output = output_func
        self._project_root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self._logger = None
        self._output_directory = Path(settings.output_directory)
        self._catalog_service = CatalogService(
            self.database,
            self.client,
            required_input=self._required_input,
            clean_input=self._clean_input,
            positive_integer=self._positive_integer,
            output=self._output,
            progress=self._progress,
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
        self._logger = setup_logging(
            log_directory=self._project_root / self._output_directory / "logs",
            level=getattr(logging, load_settings().log_level.upper(), logging.INFO),
        )
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
                    self._logger.exception("CLI operation failed")
                    self._output(f"Operation failed ({type(exc).__name__}): {exc}")
                except Exception as exc:
                    self._logger.exception("Unexpected CLI failure")
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

    def ingest_by_subject(self) -> None:
        self._catalog_service.ingest_by_subject()

    def search_catalog(self) -> None:
        self._catalog_service.search_catalog()

    def process_catalog_file(self) -> None:
        source = Path(self._required_input("Catalog file path (CSV, JSON, or Excel): "))
        with self._progress(f"Importing and cleaning {source.name}"):
            result = CatalogCleaner(self.database).process_file(source)
        self._output(
            f"Processed {result.source_rows} row(s); imported "
            f"{result.imported_rows}, removed {result.duplicate_rows} duplicate(s), "
            f"and skipped {result.invalid_rows} invalid row(s). "
            f"Output: {result.output_path}"
        )

    def generate_reports(self) -> None:
        works = self.database.list_works()
        if not works:
            self._output("The catalog is empty; add works before generating reports.")
            return
        datasets = build_catalog_analytics(works)
        output_directory = Path(
            self._input("Report output directory [data/outputs]: ").strip()
            or self._output_directory
        )
        filename_prefix = f"library_report_{datetime.now():%Y%m%d}"
        with self._progress("Generating Excel, CSV, and JSON reports"):
            outputs = ReportGenerator.export_reports(
                datasets,
                output_directory,
                filename_prefix=filename_prefix,
            )
            handoff_path = ReportGenerator.export_json(
                datasets["Catalog"],
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
                    "src/cli/menu.py",
                    "src/db/manager.py",
                    "src/reporting/generator.py",
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
