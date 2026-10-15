# Open Library Intelligence & Catalog Automation System

A Python command-line application for managing a local book catalog, looking up
books with the [Open Library API](https://openlibrary.org/developers/api), and
producing cleaned catalog files and analytical reports.

## Features

- Fetch works for a subject synchronously and asynchronously, compare API
  runtimes, cache responses in SQLite, and save usable records.
- Search the local catalog by author, title, or checksum-validated ISBN; query
  Open Library when no local match or cached response is available.
- Stream and de-duplicate CSV and JSON catalogs (and accept XLSX files), then
  upsert valid records into SQLite.
- Export a dated Excel report, publication-year and language summaries, CSV
  and JSON datasets, and a minified API-handoff JSON file.
- Inspect SQLite integrity, storage, catalog/cache metrics, and `.env` state;
  remove expired cache entries.
- Run catalog quality checks, Ruff lint/format checks, and pytest from the CLI.
- Retry transient API failures and respect rate-limit retry guidance.

## Requirements

- Python 3.14 or newer.
- [uv](https://docs.astral.sh/uv/) to install the locked project dependencies.
- Internet access for Open Library searches and subject lookups. No API key is
  required.

## Setup

From the repository root, sync the dependencies recorded in `uv.lock`:

```powershell
uv sync --locked --no-install-project
```

Run the application:

```powershell
uv run python main.py
```

To use local overrides, create `.env` from the template:

```powershell
Copy-Item .env.example .env
```

You can also install the dependencies in your own Python 3.14 environment and
start the application with `python main.py`.

## Using the application

Choose an option from the interactive menu:

| Option | Action |
| --- | --- |
| `1` | Enter a subject, result limit, and offset; compare synchronous and asynchronous requests, cache responses, and ingest usable works. |
| `2` | Select author, title, or ISBN search. ISBN-10/13 checksums are validated; local results are preferred, with cached/API lookup on a miss. |
| `3` | Read, validate, normalize, de-duplicate, and import a CSV, JSON, or XLSX catalog; a cleaned copy is written beside the source. |
| `4` | Create a timestamped workbook, publication-year/language summaries, per-dataset CSV/JSON files, and a minified API handoff file. |
| `5` | Check SQLite integrity, storage size, cache hits/misses and hit ratio, display catalog counts and `.env` state, and remove expired entries. |
| `6` | Check catalog quality and database integrity; if clean, run Ruff lint, formatting checks, and pytest. |
| `0` | Exit. |

### Local catalog files

Option `3` accepts `.csv`, `.json`, and `.xlsx` files. The file must contain
columns named `title`, `author`, and `isbn` (case-insensitive; surrounding
whitespace is ignored). For example:

```csv
title,author,isbn
Pride and Prejudice,Jane Austen,9780192503350
Emma,Jane Austen,9780141439587
```

The importer streams CSV and JSON rows, normalizes Unicode text, validates ISBN
checksums, keeps the first row for each ISBN, and upserts accepted rows into
SQLite. Rows without a valid title, author, or ISBN are skipped and counted.
The cleaned file is written next to the source with `_deduplicated` inserted
before the extension; the source file is left unchanged. For example,
`catalog.csv` produces `catalog_deduplicated.csv`.

The repository includes [`data/sample_catalog.csv`](data/sample_catalog.csv) as
a sample input file.

### Reports

Option `4` creates a timestamped report set from the local catalog:

- `Catalog`: all catalog records.
- `By author`: work counts grouped by author.
- `By publication year` and `By language`: counts when metadata is available.
- `Performance metrics`: catalog size and metadata coverage.

Enter an output directory when prompted, or press Enter to use `data/outputs/`.
For example, it writes `library_report_YYYYMMDD.xlsx`, CSV and JSON files for
each dataset, and `library_report_YYYYMMDD_api_handoff.json`. The output
directory is created if it does not exist. No reports are generated while the
catalog is empty.

## Data and configuration

- The SQLite database defaults to `data/library.db`, relative to the current
  working directory. Its tables are initialized automatically on startup.
- Open Library requests use `https://openlibrary.org` by default. The HTTP
  client has retry and timeout defaults in `src/api/`.
- Copy `.env.example` to `.env` to configure `DATABASE_PATH`,
  `OUTPUT_DIRECTORY`, `API_BASE_URL`, and `LOG_LEVEL`. Environment variables
  can also supply these settings directly.
- Logs are written to the console and a rotating file under
  `data/outputs/logs/`.

Generated databases, de-duplicated files, and reports may contain local user
data; review them before sharing.

## Development and tests

Install/sync the locked dependencies as described in [Setup](#setup), then run
the linter and test suite:

```powershell
uv run --locked ruff check .
uv run --locked pytest
```

Tests are under `tests/`. They cover application behavior, database operations,
data processing, report generation, configuration, text utilities, exceptions,
and synchronous/asynchronous API-client behavior. API tests use mocked HTTP
transports rather than making live requests.

GitHub Actions runs Ruff lint and pytest on pushes and pull requests. The
workflow is defined in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Project layout

```text
.
├── main.py                  # Application entry point
├── src/
│   ├── api/
│   │   ├── client_base.py   # Shared validation and retry handling
│   │   ├── sync_client.py   # Synchronous Open Library API client
│   │   ├── async_client.py  # Asynchronous Open Library API client
│   │   └── catalog_service.py # API-backed catalog workflows
│   ├── cli/
│   │   └── menu.py          # Interactive menu and command routing
│   ├── db/
│   │   └── manager.py       # SQLite catalog and cache management
│   ├── processing/
│   │   ├── cleaner.py       # Input normalization and catalog importing
│   │   └── analytics.py     # DataFrame operations and report datasets
│   ├── reporting/
│   │   └── generator.py    # Excel, CSV, and JSON exports
│   ├── config.py            # Environment/.env settings
│   ├── exceptions.py        # Application-specific exceptions
│   └── logger.py            # Console and rotating-file logging
├── .env.example             # Configuration template
├── data/
│   ├── sample_catalog.csv
│   └── outputs/             # Generated reports and rotating logs
└── tests/
```

Thin top-level modules such as `src/database.py` remain as import-compatibility
wrappers; new code should import from the package paths shown above.
