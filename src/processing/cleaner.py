import csv
import json
import re
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from db.manager import DatabaseManager
from exceptions import DataValidationError

_ISBN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:ISBN(?:-1[03])?\s*:?\s*)?"
    r"(\d(?:[ -]?\d){8,11}[ -]?[\dXx])(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_NON_WORD_PATTERN = re.compile(r"[\W_]+", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_string(value: str) -> str:
    """Case-fold text, replace punctuation with spaces, and collapse whitespace."""
    if not isinstance(value, str):
        raise TypeError("value must be a string")

    normalized = _NON_WORD_PATTERN.sub(" ", value.casefold())
    return _WHITESPACE_PATTERN.sub(" ", normalized).strip()


def _is_valid_isbn(isbn: str) -> bool:
    if len(isbn) == 10:
        digits = [10 if char.upper() == "X" else int(char) for char in isbn]
        checksum = sum(
            digit * weight for digit, weight in zip(digits, range(10, 0, -1))
        )
        return checksum % 11 == 0

    if len(isbn) == 13 and isbn.isdigit():
        checksum = sum(
            int(digit) * (1 if index % 2 == 0 else 3)
            for index, digit in enumerate(isbn)
        )
        return checksum % 10 == 0

    return False


def extract_isbns(value: str) -> list[str]:
    """Extract valid ISBN-10 and ISBN-13 values, normalized to unseparated text."""
    if not isinstance(value, str):
        raise TypeError("value must be a string")

    matches: list[str] = []
    seen: set[str] = set()
    for match in _ISBN_PATTERN.finditer(value):
        isbn = re.sub(r"[ -]", "", match.group(1)).upper()
        if isbn not in seen and _is_valid_isbn(isbn):
            seen.add(isbn)
            matches.append(isbn)
    return matches


@dataclass(frozen=True)
class CatalogImportResult:
    """Summary of a catalog file import and its cleaned output."""

    source_rows: int
    imported_rows: int
    duplicate_rows: int
    invalid_rows: int
    output_path: Path


class CatalogCleaner:
    """Stream, normalize, deduplicate, and import external catalog files."""

    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

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

    @classmethod
    def _iter_records(cls, source: Path) -> Iterator[Mapping[str, object]]:
        suffix = source.suffix.casefold()
        if suffix == ".csv":
            with source.open(encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                if reader.fieldnames is None:
                    raise DataValidationError("Catalog CSV is missing its header row")
                yield from reader
            return
        if suffix == ".json":
            yield from cls._iter_json_array(source)
            return
        if suffix == ".xlsx":
            dataframe = pd.read_excel(source, dtype=str)
            yield from dataframe.to_dict(orient="records")
            return
        raise DataValidationError("Supported catalog formats are CSV, JSON, and XLSX")

    @staticmethod
    def _normalize_record(
        source_row: Mapping[str, object],
    ) -> dict[str, Any] | None:
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
        isbns = extract_isbns(raw_isbn)
        if not isbns:
            return None

        record: dict[str, Any] = {
            "title": title,
            "author": author,
            "isbn": isbns[0],
        }
        year_value = row.get("published_year") or row.get("year")
        year_match = re.search(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", str(year_value or ""))
        if year_match:
            record["published_year"] = int(year_match.group())
        language = row.get("language") or row.get("lang")
        if isinstance(language, str) and language.strip():
            record["language"] = language.strip()
        return record

    def process_file(self, source: str | Path) -> CatalogImportResult:
        """Import valid unique records and write a cleaned sibling file."""
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(f"Catalog file does not exist: {source}")

        source_rows = 0
        invalid_rows = 0
        seen: set[str] = set()
        records: list[dict[str, Any]] = []
        for source_row in self._iter_records(source):
            source_rows += 1
            record = self._normalize_record(source_row)
            if record is None:
                invalid_rows += 1
                continue
            key = f"isbn:{record['isbn']}"
            if key in seen:
                continue
            seen.add(key)
            records.append(record)

        if records:
            self._database.add_works(records)

        output_path = source.with_name(f"{source.stem}_deduplicated{source.suffix}")
        dataframe = pd.DataFrame(records)
        if source.suffix.casefold() == ".csv":
            dataframe.to_csv(output_path, index=False, encoding="utf-8")
        elif source.suffix.casefold() == ".json":
            dataframe.to_json(
                output_path,
                orient="records",
                force_ascii=False,
                indent=2,
            )
        else:
            dataframe.to_excel(output_path, index=False)

        return CatalogImportResult(
            source_rows=source_rows,
            imported_rows=len(records),
            duplicate_rows=source_rows - invalid_rows - len(records),
            invalid_rows=invalid_rows,
            output_path=output_path,
        )
