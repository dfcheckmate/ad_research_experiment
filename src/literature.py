"""OpenAlex literature search client."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

from logging_config import configure_logging, get_logger

load_dotenv()
configure_logging()
logger = get_logger(__name__)

OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "")
OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "")
OPENALEX_BASE = "https://api.openalex.org"

LITERATURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS literature_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id         TEXT UNIQUE NOT NULL,
    title           TEXT,
    authors         TEXT,
    year            INTEGER,
    venue           TEXT,
    doi             TEXT,
    abstract        TEXT,
    open_access_url TEXT,
    citation_count  INTEGER,
    query_tag       TEXT,
    fetched_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_lit_query ON literature_cache(query_tag);
CREATE INDEX IF NOT EXISTS idx_lit_year  ON literature_cache(year);
"""


def _get_db():
    db_url = os.getenv("DB_URL", "sqlite:///out/ads.db")
    if db_url.startswith("sqlite:///"):
        db_path = db_url.removeprefix("sqlite:///")
    else:
        db_path = "out/ads.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(LITERATURE_SCHEMA)
    return conn


def _search_openalex(
    query: str, limit: int = 20, year_from: int = 2018, deep: bool = False
) -> list[dict]:
    max_pages = 30 if deep else 10
    page_size = min(limit, 200)
    total_results: list[dict] = []
    cursor = "*"

    headers = {}
    if OPENALEX_API_KEY:
        headers["api-key"] = OPENALEX_API_KEY
    if OPENALEX_EMAIL:
        headers["User-Agent"] = f"mailto:{OPENALEX_EMAIL}"

    calls_made = 0
    while calls_made < max_pages and len(total_results) < limit:
        params = {
            "per_page": page_size,
            "cursor": cursor,
            "sort": "cited_by_count:desc",
        }
        words = query.split()
        if len(words) <= 3:
            params["filter"] = f"title.search:{query}"
            if year_from:
                params["filter"] += f",publication_year:{year_from}-"
        else:
            params["search"] = query
            if year_from:
                params["filter"] = f"publication_year:{year_from}-"

        try:
            resp = requests.get(
                f"{OPENALEX_BASE}/works",
                params=params,
                headers=headers,
                timeout=30,
            )
            calls_made += 1

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                logger.error("API error %s: %s", resp.status_code, resp.text[:200])
                break

            data = resp.json()
            results = data.get("results", [])
            total_results.extend(results)

            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor or cursor == "*":
                break

            time.sleep(0.1)

        except requests.RequestException as e:
            logger.error("Request failed: %s", e)
            break

    logger.info("Total results fetched: %d", len(total_results))
    return total_results


def _parse_result(r: dict) -> dict:
    work_id = r.get("id", "").replace("https://openalex.org/", "")
    title = r.get("title", "") or ""
    doi = r.get("doi", "")

    authors = []
    for a in r.get("authorships", [])[:5]:
        name = a.get("author", {}).get("display_name", "")
        if name:
            authors.append(name)
    authors_str = "; ".join(authors)

    year = r.get("publication_year")
    venue = r.get("primary_location", {}).get("source", {}).get("display_name", "") or ""
    citation_count = r.get("cited_by_count", 0) or 0

    abstract = ""
    if "abstract_inverted_index" in r and r["abstract_inverted_index"]:
        abstract = " ".join(sorted(
            r["abstract_inverted_index"].keys(),
            key=lambda k: min(r["abstract_inverted_index"][k])
        ))

    open_access_url = ""
    oa = r.get("open_access", {})
    if oa.get("is_oa") and oa.get("oa_url"):
        open_access_url = oa["oa_url"]

    return {
        "work_id": work_id,
        "title": title,
        "authors": authors_str,
        "year": year,
        "venue": venue,
        "doi": doi,
        "abstract": abstract,
        "open_access_url": open_access_url,
        "citation_count": citation_count,
    }


def _to_bibtex(parsed: dict) -> str:
    key = parsed["work_id"].replace("/", "_")
    entry = f"@article{{{key},\n"
    entry += f"  title={{{parsed['title']}}},\n"
    if parsed["authors"]:
        entry += f"  author={{{parsed['authors']}}},\n"
    if parsed["year"]:
        entry += f"  year={{{parsed['year']}}},\n"
    if parsed["venue"]:
        entry += f"  journal={{{parsed['venue']}}},\n"
    if parsed["doi"]:
        entry += f"  doi={{{parsed['doi']}}},\n"
    if parsed["open_access_url"]:
        entry += f"  url={{{parsed['open_access_url']}}},\n"
    entry += "}\n"
    return entry


def cmd_search(args):
    logger.info('Searching OpenAlex: "%s"', args.query)
    logger.info("limit=%d, year_from=%d, deep=%s", args.limit, args.year_from, args.deep)
    if OPENALEX_EMAIL:
        logger.info("Using polite pool with email: %s", OPENALEX_EMAIL)

    results = _search_openalex(args.query, args.limit, args.year_from, args.deep)

    if not results:
        logger.warning("No results found.")
        return

    conn = _get_db()
    cur = conn.cursor()
    cached_count = 0

    for r in results:
        parsed = _parse_result(r)
        if not parsed["work_id"]:
            continue

        try:
            cur.execute(
                "INSERT OR IGNORE INTO literature_cache "
                "(work_id, title, authors, year, venue, doi, "
                "abstract, open_access_url, citation_count, query_tag, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    parsed["work_id"],
                    parsed["title"],
                    parsed["authors"],
                    parsed["year"],
                    parsed["venue"],
                    parsed["doi"],
                    parsed["abstract"],
                    parsed["open_access_url"],
                    parsed["citation_count"],
                    args.query,
                    datetime.now().isoformat(),
                ),
            )
            if cur.rowcount > 0:
                cached_count += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()

    print("\n" + "=" * 80)
    print(f"RESULTS ({len(results)} papers)")
    print("=" * 80 + "\n")

    for i, r in enumerate(results, 1):
        parsed = _parse_result(r)
        print(f"[{i}] {parsed['title']}")
        if parsed["authors"]:
            print(f"    Authors: {parsed['authors']}")
        if parsed["year"]:
            print(f"    Year:    {parsed['year']}")
        if parsed["venue"]:
            print(f"    Venue:   {parsed['venue']}")
        if parsed["citation_count"]:
            print(f"    Citations: {parsed['citation_count']}")
        if parsed["doi"]:
            print(f"    DOI:     {parsed['doi']}")
        if parsed["abstract"]:
            print(f"    Abstract: {parsed['abstract'][:200]}...")
        if parsed["open_access_url"]:
            print(f"    OA:      {parsed['open_access_url']}")
        print()

    logger.info("%d new results cached in database", cached_count)

    if args.export_bib:
        bib_dir = "literature"
        os.makedirs(bib_dir, exist_ok=True)
        bib_path = os.path.join(bib_dir, "references.bib")

        with open(bib_path, "w", encoding="utf-8") as f:
            f.write(f'% Generated by OpenAlex search: "{args.query}"\n')
            f.write(f"% Date: {datetime.now().isoformat()}\n\n")
            for r in results:
                parsed = _parse_result(r)
                if parsed["work_id"]:
                    f.write(_to_bibtex(parsed) + "\n")

        logger.info("Exported %d entries to %s", len(results), bib_path)


def cmd_list(args):
    conn = _get_db()
    cur = conn.cursor()

    limit = args.limit or 50
    rows = cur.execute(
        "SELECT title, authors, year, venue, query_tag, fetched_at "
        "FROM literature_cache ORDER BY year DESC, citation_count DESC LIMIT ?",
        (limit,),
    ).fetchall()

    if not rows:
        logger.warning("No cached results.")
        return

    print("\n" + "=" * 80)
    print(f"CACHED LITERATURE ({len(rows)} entries)")
    print("=" * 80 + "\n")

    for title, authors, year, venue, tag, fetched in rows:
        cite_str = ""
        print(f"  [{year or '?'}] {title[:100]}{cite_str}")
        print(f"       {authors[:80]}")
        if venue:
            print(f"       {venue}")
        print(f"       query: {tag} | cached: {fetched}")
        print()


def cmd_stats(args):
    conn = _get_db()
    cur = conn.cursor()

    total = cur.execute("SELECT COUNT(*) FROM literature_cache").fetchone()[0]
    total_cites = cur.execute("SELECT SUM(citation_count) FROM literature_cache").fetchone()[0] or 0
    year_range = cur.execute("SELECT MIN(year), MAX(year) FROM literature_cache").fetchone()
    queries = cur.execute(
        "SELECT query_tag, COUNT(*) as n FROM literature_cache GROUP BY query_tag ORDER BY n DESC"
    ).fetchall()

    print("\n" + "=" * 60)
    print("LITERATURE CACHE STATS")
    print("=" * 60)
    print(f"  Total papers cached: {total}")
    print(f"  Total citations:     {total_cites}")
    if year_range[0]:
        print(f"  Year range: {year_range[0]} - {year_range[1]}")
    print("\n  Queries:")
    for tag, n in queries[:10]:
        print(f"    {tag}: {n} papers")
    print()


def main():
    parser = argparse.ArgumentParser(description="OpenAlex literature client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Search OpenAlex")
    search.add_argument("--query", required=True, help="Search query")
    search.add_argument("--limit", type=int, default=20, help="Max results")
    search.add_argument("--year-from", type=int, default=2018, help="Minimum year")
    search.add_argument("--deep", action="store_true", help="Fetch more pages")
    search.add_argument("--export-bib", action="store_true", help="Export to BibTeX")
    search.set_defaults(func=cmd_search)

    list_parser = subparsers.add_parser("list", help="List cached papers")
    list_parser.add_argument("--limit", type=int, help="Max entries to show")
    list_parser.set_defaults(func=cmd_list)

    stats = subparsers.add_parser("stats", help="Show cache statistics")
    stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
