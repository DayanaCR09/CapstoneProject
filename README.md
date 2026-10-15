# Open Library Intelligence & Catalog Automation System

A Python command-line application for managing a local book catalog, looking up
books with the [Open Library API](https://openlibrary.org/developers/api), and
producing cleaned catalog files and analytical reports.

## Features

- Fetch works by subject from Open Library and save usable records locally.
- Search the local catalog and Open Library in one operation.
- De-duplicate CSV, JSON, and Excel catalog files.
- Export a formatted Excel workbook and per-dataset CSV and JSON reports.
- Inspect SQLite database health and remove expired cache entries.
- Run catalog quality checks and the project test suite from the CLI.
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

You can also install the dependencies in your own Python 3.14 environment and
start the application with `python main.py`.

## Using the application

Choose an option from the interactive menu:

| Option | Action |
| --- | --- |
| `1` | Fetch works for a subject (up to 100) and save records with usable title, author, and ISBN data. |
| `2` | Search local records and Open Library by title, author, or ISBN. |
| `3` | Read and de-duplicate a local CSV, JSON, or XLSX catalog. |
| `4` | Generate Excel, CSV, and JSON reports from the local catalog. |
| `5` | Check SQLite integrity, display catalog/cache counts, and remove expired cache entries. |
| `6` | Check catalog quality and database integrity; run pytest if those checks pass. |
| `0` | Exit. |

### Local catalog files

Option `3` accepts `.csv`, `.json`, and `.xlsx` files. Each file must contain
columns named exactly `title`, `author`, and `isbn` (lowercase). For example:

```csv
title,author,isbn
Pride and Prejudice,Jane Austen,9780192503350
Emma,Jane Austen,9780141439587
```

The importer keeps the first row for each normalized ISBN. If an ISBN is blank,
it uses normalized title and author to identify duplicates. The cleaned file is
written next to the source with `_deduplicated` inserted before the extension;
the source file is left unchanged. For example,
`catalog.csv` produces `catalog_deduplicated.csv`.

The repository includes [`data/sample_catalog.csv`](data/sample_catalog.csv) as
a sample input file.

### Reports

Option `4` creates two datasets from the local catalog:

- `Catalog`: all catalog records.
- `By author`: work counts grouped by author.

Enter an output directory when prompted, or press Enter to use `reports/`.
With the default directory, the application writes `library.xlsx`, plus CSV and
JSON files for each dataset (for example, `library_Catalog.csv` and
`library_By author.json`). The output directory is created if it does not exist.
No report files are generated while the catalog is empty.

## Data and configuration

- The SQLite database defaults to `data/library.db`, relative to the current
  working directory. Its tables are initialized automatically on startup.
- Open Library requests use `https://openlibrary.org` by default. The HTTP
  client has retry and timeout defaults in `src/library_client.py`.
- `src/config.py` can load settings from environment variables and a UTF-8
  `.env` file. The current application does not define configurable settings
  there.

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

GitHub Actions runs the Ruff check and pytest on pushes and pull requests. The
workflow is defined in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Project layout

```text
.
├── main.py                  # Application entry point
├── src/
│   ├── cli_app.py           # Interactive menu and workflows
│   ├── config.py            # Environment/.env settings
│   ├── database.py          # SQLite catalog and cache storage
│   ├── data_processor.py    # DataFrame transformation helpers
│   ├── library_client.py    # Sync and async Open Library HTTP clients
│   ├── report_generator.py  # Excel, CSV, and JSON exports
│   ├── text_utils.py        # Text normalization utilities
│   └── exceptions.py        # Application-specific exceptions
├── data/
│   └── sample_catalog.csv
└── tests/
```
