"""OpenAlex literature search client.

Searches the OpenAlex API for academic papers on ad delivery discrimination,
proxy-based auditing, and related research. Results are cached in the local
database for future reference.

OpenAlex requires no API key. Polite pool (default) allows 100 req/10sec.
Add an email via OPENALEX_EMAIL env var for the faster pool (no hard limit).

Usage:
    python src/literature.py search --query "<terms>" [--limit N] [--year-from YYYY] [--deep]
    python src/literature.py list [--limit N]
    python src/literature.py stats
"""

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

load_dotenv()

OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "")
OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "")
OPENALEX_BASE = "https://api.openalex.org"

# ── Database cache ────────────────────────────────────────────────────────────

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


# ── OpenAlex API client ──────────────────────────────────────────────────────

def _search_openalex(query: str, limit: int = 20, year_from: int = 2018,
                     deep: bool = False) -> list[dict]:
    """Search OpenAlex API for works."""
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
        # OpenAlex 'search' parameter searches title + abstract + full text
        # Use title.search filter only for short, specific phrases
        words = query.split()
        if len(words) <= 3:
            params["filter"] = f"title.search:{query}"
            if year_from:
                params["filter"] += f",publication_year:{year_from}-"
        else:
            # For longer queries, use general search (title + abstract + full text)
            params["search"] = query
            if year_from:
                params["filter"] = f"publication_year:{year_from}-"

        try:
            resp = requests.get(
                f"{OPENALEX_BASE}/works",
                headers=headers,
                params=params,
                timeout=30,
            )
            calls_made += 1

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "5"))
                print(f"[literature] Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                print(f"[literature] API error {resp.status_code}: {resp.text[:200]}")
                break

            data = resp.json()
            results = data.get("results", [])
            if not results:
                break

            total_results.extend(results)
            cursor = data.get("meta", {}).get("next_cursor", "")
            if not cursor:
                break

            total_count = data.get("meta", {}).get("count", 0)
            print(f"[literature] Call {calls_made}/{max_pages} — {len(results)} results "
                  f"(total matching: {total_count})")

            if len(results) < page_size:
                break

        except requests.RequestException as e:
            print(f"[literature] Request failed: {e}")
            break

    print(f"[literature] Total results fetched: {len(total_results)}")
    return total_results[:limit]


def _get_venue(r: dict) -> str:
    loc = r.get("primary_location")
    if not loc:
        return ""
    source = loc.get("source")
    if not source:
        return ""
    return source.get("display_name", "")


def _parse_result(r: dict) -> dict:
    """Parse an OpenAlex work into a flat dict."""
    authors = []
    for authorship in r.get("authorships", []) or []:
        author = authorship.get("author", {})
        name = author.get("display_name", "")
        if name:
            authors.append(name)

    oa = r.get("open_access", {})
    oa_url = oa.get("oa_url") if oa else None

    abstract = r.get("abstract_inverted_index")
    abstract_text = ""
    if abstract:
        # Reconstruct abstract from inverted index
        positions = {}
        for word, idxs in abstract.items():
            for idx in idxs:
                positions[idx] = word
        abstract_text = " ".join(positions.get(i, "") for i in sorted(positions))

    return {
        "work_id": r.get("id", ""),
        "title": r.get("title", ""),
        "authors": ", ".join(authors) if authors else "",
        "year": r.get("publication_year"),
        "venue": _get_venue(r),
        "doi": r.get("doi", ""),
        "abstract": abstract_text[:500] if abstract_text else "",
        "open_access_url": oa_url,
        "citation_count": r.get("cited_by_count", 0),
    }


# ── Commands ──────────────────────────────────────────────────────────────────

def _to_bibtex(parsed: dict) -> str:
    """Convert a parsed result to a BibTeX entry."""
    # Generate a clean citation key: first_author_year_keyword
    authors = parsed["authors"].split(", ")
    first_author = authors[0].split()[-1] if authors else "Unknown"
    year = parsed["year"] or "n.d."
    # Use first 3 words of title as keyword
    title_words = re.sub(r'[^\w\s]', '', parsed["title"]).split()[:3]
    keyword = "_".join(title_words).lower()
    cite_key = f"{first_author.lower()}_{year}_{keyword}"

    # Escape special BibTeX characters
    def escape(val):
        if not val: return ""
        return val.replace("{", "\\{").replace("}", "\\}")

    entry = f"@article{{{cite_key},\n"
    entry += f"  title={{{escape(parsed['title'])}}},\n"
    entry += f"  author={{{escape(parsed['authors'])}}},\n"
    entry += f"  year={{{year}}},\n"
    if parsed["venue"]:
        entry += f"  journal={{{escape(parsed['venue'])}}},\n"
    if parsed["doi"]:
        entry += f"  doi={{{parsed['doi']}}},\n"
    if parsed["open_access_url"]:
        entry += f"  url={{{parsed['open_access_url']}}},\n"
    if parsed["abstract"]:
        entry += f"  abstract={{{escape(parsed['abstract'])}}},\n"
    entry += f"  citation_count={{{parsed['citation_count']}}}\n"
    entry += "}\n"
    return entry


def cmd_search(args):
    query = args.query
    limit = args.limit
    year_from = args.year_from
    deep = args.deep

    print(f"[literature] Searching OpenAlex: \"{query}\"")
    print(f"[literature] limit={limit}, year_from={year_from}, deep={deep}")
    if OPENALEX_EMAIL:
        print(f"[literature] Using polite pool with email: {OPENALEX_EMAIL}")
    print()

    results = _search_openalex(query, limit, year_from, deep)

    if not results:
        print("[literature] No results found.")
        return

    # Cache results
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
                    query,
                    datetime.now().isoformat(),
                ),
            )
            if cur.rowcount > 0:
                cached_count += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()

    # Display results
    print(f"\n{'=' * 80}")
    print(f"RESULTS ({len(results)} papers)")
    print(f"{'=' * 80}\n")

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

    print(f"[literature] {cached_count} new results cached in database.")

    # Export to BibTeX if requested
    if args.export_bib:
        bib_dir = "literature"
        os.makedirs(bib_dir, exist_ok=True)
        bib_path = os.path.join(bib_dir, "references.bib")

        with open(bib_path, "w", encoding="utf-8") as f:
            f.write(f"% Generated by OpenAlex search: \"{query}\"\n")
            f.write(f"% Date: {datetime.now().isoformat()}\n\n")
            for r in results:
                parsed = _parse_result(r)
                if parsed["work_id"]:
                    f.write(_to_bibtex(parsed) + "\n")

        print(f"[literature] Exported {len(results)} entries to {bib_path}")

    conn.close()


def cmd_list(args):
    """List cached literature results."""
    conn = _get_db()
    limit = args.limit
    rows = conn.execute(
        "SELECT work_id, title, authors, year, venue, citation_count, query_tag, fetched_at "
        "FROM literature_cache ORDER BY fetched_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    if not rows:
        print("[literature] No cached results.")
        conn.close()
        return

    print(f"\n{'=' * 80}")
    print(f"CACHED LITERATURE ({len(rows)} entries)")
    print(f"{'=' * 80}\n")

    for wid, title, authors, year, venue, cites, tag, fetched in rows:
        cite_str = f" ({cites} cites)" if cites else ""
        print(f"  [{year or '?'}] {title[:100]}{cite_str}")
        print(f"       {authors[:80]}")
        if venue:
            print(f"       {venue}")
        print(f"       query: {tag} | cached: {fetched}")
        print()

    conn.close()


def cmd_stats(args):
    """Show literature cache statistics."""
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM literature_cache").fetchone()[0]
    queries = conn.execute(
        "SELECT query_tag, COUNT(*) as n FROM literature_cache "
        "GROUP BY query_tag ORDER BY n DESC"
    ).fetchall()
    year_range = conn.execute(
        "SELECT MIN(year), MAX(year) FROM literature_cache WHERE year IS NOT NULL"
    ).fetchone()
    total_cites = conn.execute(
        "SELECT COALESCE(SUM(citation_count), 0) FROM literature_cache"
    ).fetchone()[0]

    print(f"\n{'=' * 60}")
    print(f"LITERATURE CACHE STATS")
    print(f"{'=' * 60}")
    print(f"  Total papers cached: {total}")
    print(f"  Total citations:     {total_cites}")
    if year_range[0]:
        print(f"  Year range: {year_range[0]} - {year_range[1]}")
    print(f"\n  Queries:")
    for tag, n in queries:
        print(f"    {tag}: {n} papers")
    print()
    conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OpenAlex literature search")
    subparsers = parser.add_subparsers(dest="command")

    # Search
    search_parser = subparsers.add_parser("search", help="Search OpenAlex")
    search_parser.add_argument("--query", required=True, help="Search query")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--year-from", type=int, default=2018)
    search_parser.add_argument("--deep", action="store_true",
                               help="Deep scan — more API calls for broader results")
    search_parser.add_argument("--export-bib", action="store_true",
                               help="Export results to literature/references.bib")
    search_parser.set_defaults(func=cmd_search)

    # List
    list_parser = subparsers.add_parser("list", help="List cached results")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.set_defaults(func=cmd_list)

    # Stats
    stats_parser = subparsers.add_parser("stats", help="Show cache statistics")
    stats_parser.set_defaults(func=cmd_stats)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
