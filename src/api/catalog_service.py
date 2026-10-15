"""Catalog-oriented workflows backed by the Open Library API."""

import asyncio
import re
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager

from api.async_client import AsyncLibraryClient
from api.sync_client import SyncLibraryClient
from db.manager import DatabaseManager
from exceptions import DataValidationError
from processing.cleaner import extract_isbns, normalize_string

_CACHE_TTL_SECONDS = 24 * 60 * 60


class CatalogService:
    """Coordinate API search/ingestion, caching, and local catalog updates."""

    def __init__(
        self,
        database: DatabaseManager,
        client: SyncLibraryClient,
        *,
        required_input: Callable[[str], str],
        clean_input: Callable[..., str],
        positive_integer: Callable[..., int],
        output: Callable[[str], None],
        progress: Callable[[str], AbstractContextManager[None]],
    ) -> None:
        self._database = database
        self._client = client
        self._required_input = required_input
        self._clean_input = clean_input
        self._positive_integer = positive_integer
        self._output = output
        self._progress = progress

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
        response = self._database.get_cached_response(key)
        if response is not None:
            return response
        response = fetch()
        self._database.cache_response(
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
        cached = self._database.get_cached_response(cache_key)
        if cached is not None:
            return cached
        async with AsyncLibraryClient(
            base_url=getattr(self._client, "_base_url", "https://openlibrary.org")
        ) as client:
            response = await client.get_subject(subject, limit=limit, offset=offset)
        self._database.cache_response(
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
                lambda: self._client.get_subject(
                    subject,
                    limit=limit,
                    offset=offset,
                ),
            )
            sync_elapsed = time.perf_counter() - started

        if isinstance(self._client, SyncLibraryClient):
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
                        lambda work_id=work_id: self._client.get_work(work_id),
                    )
                    if callable(getattr(self._client, "get_work", None))
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
                    lambda: self._client.search(f"subject:{subject}"),
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
                self._database.add_works(records)
        self._output(
            f"Ingestion finished: {len(records)} saved; "
            f"{skipped} source record(s) lacked usable metadata."
        )

    def search_catalog(self) -> None:
        self._output("Search type: [A]uthor, [T]itle, or [I]SBN")
        while True:
            search_type = self._required_input("Select search type: ").casefold()
            if search_type in {"a", "t", "i"}:
                break
            self._output("Choose A, T, or I.")

        query = self._clean_input("Search term: ", field="search term")
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
            for work in self._database.list_works()
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
                response = self._database.get_cached_response(f"search:{api_query}")
            if response is None:
                with self._progress("Searching Open Library"):
                    response = self._client.search(api_query)
                    self._database.cache_response(
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
                self._database.add_works(records)

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
