# Open Library Intelligence & Catalog Automation System

Run the interactive application from the repository root:

```powershell
python main.py
```

The menu supports subject-based ingestion into the local SQLite catalog, combined
local/Open Library searches, CSV/JSON/Excel catalog deduplication, analytical
report exports, database health and expired-cache maintenance, and catalog
quality checks followed by the project test suite. Reports are written to the
`reports` directory by default; the database is stored at `data/library.db`.

Catalog files processed by the CLI must contain `title`, `author`, and `isbn`
columns. Supported file formats are CSV, JSON, and XLSX. A cleaned copy is
written next to the source file with `_deduplicated` appended to its filename.