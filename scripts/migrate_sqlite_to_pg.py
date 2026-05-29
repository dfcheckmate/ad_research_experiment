#!/usr/bin/env python
"""Migrate SQLite data to PostgreSQL.

Usage:
    python scripts/migrate_sqlite_to_pg.py                     # defaults
    python scripts/migrate_sqlite_to_pg.py \
        --sqlite out/ads.db \
        --pg postgresql://researcher:research@localhost:5432/ad_research

This script:
1. Reads all rows from the SQLite database (trials, page_visits, ad_observations, captures).
2. Creates the PostgreSQL schema if it doesn't exist.
3. Inserts rows into PostgreSQL, skipping duplicates (ON CONFLICT).
4. Reports row counts before and after.

Safe to re-run — uses INSERT ... ON CONFLICT DO NOTHING for idempotency.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime

import asyncpg

# Allow importing from src/ (db, config, etc.)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Timestamp columns per table (column name → index in the row tuple)
TIMESTAMP_COLS: dict[str, dict[str, int]] = {}

TABLES = ["trials", "page_visits", "ad_observations", "captures"]

# Columns per table (SQLite order)
COLUMNS = {
    "trials": ["trial_id", "started_at", "trial_meta"],
    "page_visits": [
        "id",
        "trial_id",
        "agent_id",
        "zip_condition",
        "intent_profile",
        "phase",
        "measurement_site",
        "target_url",
        "final_url",
        "status_code",
        "error_type",
        "error_text",
        "page_load_time_ms",
        "cookie_count",
        "observed_at",
    ],
    "ad_observations": [
        "id",
        "trial_id",
        "agent_id",
        "zip_condition",
        "ad_url",
        "ad_domain",
        "ad_network",
        "measurement_site",
        "source_type",
        "intent_profile",
        "query_topic",
        "search_query",
        "ad_headline",
        "ad_description",
        "advertiser_name",
        "landing_url",
        "landing_domain",
        "inferred_topic",
        "page_title",
        "page_url",
        "screenshot_path",
        "dom_snippet",
        "page_load_time_ms",
        "observed_at",
    ],
    "captures": [
        "id",
        "trial_id",
        "proxy_identity",
        "intent_profile",
        "site",
        "file_path",
        "file_size_kb",
        "captured_at",
        "meta",
    ],
}

# Build timestamp column index map
for table, cols in COLUMNS.items():
    ts_cols = {}
    for i, col in enumerate(cols):
        if col in ("started_at", "observed_at", "captured_at"):
            ts_cols[col] = i
    TIMESTAMP_COLS[table] = ts_cols

# Tables with auto-increment IDs that need to be preserved (SQLite INTEGER PK)
AUTO_ID_TABLES = {"page_visits", "ad_observations"}

# Tables where we should NOT auto-generate IDs (trials has TEXT PK, captures has TEXT PK)
SKIP_ID_TABLES = {"trials", "captures"}


def _parse_timestamp(val: str | None) -> datetime | None:
    """Parse a SQLite timestamp string into a datetime object."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    # SQLite stores timestamps as 'YYYY-MM-DD HH:MM:SS' or with fractional seconds
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    # Fallback: return as-is (will likely fail PG insert, but better than crashing)
    return None


def _convert_timestamps(table: str, rows: list[tuple]) -> list[tuple]:
    """Convert timestamp string columns to datetime objects for asyncpg."""
    ts_cols = TIMESTAMP_COLS.get(table, {})
    if not ts_cols or not rows:
        return rows

    converted = []
    for row in rows:
        row_list = list(row)
        for col_name, idx in ts_cols.items():
            if idx < len(row_list):
                row_list[idx] = _parse_timestamp(row_list[idx])
        converted.append(tuple(row_list))
    return converted


def read_sqlite(sqlite_path: str) -> dict[str, list[tuple]]:
    """Read all rows from each table in the SQLite database."""
    conn = sqlite3.connect(sqlite_path)
    cur = conn.cursor()
    data: dict[str, list[tuple]] = {}
    for table in TABLES:
        try:
            cur.execute(f"SELECT * FROM {table}")
            rows = cur.fetchall()
            data[table] = rows
            print(f"  SQLite {table}: {len(rows)} rows")
        except sqlite3.OperationalError as e:
            print(f"  SQLite {table}: table not found ({e})")
            data[table] = []
    conn.close()
    return data


async def create_pg_schema(pg_url: str) -> asyncpg.Pool:
    """Create PostgreSQL schema and return a connection pool."""
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=4)

    from db import PG_SCHEMA, PG_CAPTURES_SCHEMA, PG_MIGRATIONS

    async with pool.acquire() as conn:
        await conn.execute(PG_SCHEMA)
        await conn.execute(PG_CAPTURES_SCHEMA)
        for sql in PG_MIGRATIONS:
            await conn.execute(sql)
    print("  PostgreSQL schema ready")
    return pool


async def insert_rows(pool: asyncpg.Pool, table: str, rows: list[tuple]) -> int:
    """Insert rows into PostgreSQL, skipping duplicates."""
    if not rows:
        return 0

    cols = COLUMNS[table]
    placeholders = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
    col_names = ", ".join(cols)

    if table in SKIP_ID_TABLES:
        # trials (TEXT PK), captures (TEXT PK) — use ON CONFLICT on primary key
        conflict_col = "trial_id" if table == "trials" else "id"
        sql = (
            f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_col}) DO NOTHING"
        )
    elif table in AUTO_ID_TABLES:
        # page_visits, ad_observations — preserve original SQLite IDs
        # Use a sequence reset after insert if needed
        sql = (
            f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT (id) DO NOTHING"
        )
    else:
        sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"

    inserted = 0
    async with pool.acquire() as conn:
        for row in rows:
            try:
                await conn.execute(sql, *row)
                inserted += 1
            except asyncpg.UniqueViolationError:
                pass  # duplicate, skip
            except Exception as e:
                print(f"  [warn] {table} insert error: {e}")

    # Reset sequences for auto-increment tables
    if table in AUTO_ID_TABLES and inserted > 0:
        async with pool.acquire() as conn:
            seq_name = f"{table}_id_seq"
            await conn.execute(
                f"SELECT setval('{seq_name}', "
                f"(SELECT COALESCE(MAX(id), 1) FROM {table}), true)"
            )

    return inserted


async def main(sqlite_path: str, pg_url: str) -> None:
    print(f"SQLite source: {sqlite_path}")
    print(f"PostgreSQL target: {pg_url}")
    print()

    # Read SQLite data
    print("Reading SQLite data...")
    data = read_sqlite(sqlite_path)
    total_sqlite = sum(len(v) for v in data.values())
    print(f"  Total rows: {total_sqlite}")
    print()

    # Create PG schema
    print("Creating PostgreSQL schema...")
    pool = await create_pg_schema(pg_url)
    print()

    # Insert data
    print("Inserting data into PostgreSQL...")
    total_inserted = 0
    for table in TABLES:
        rows = _convert_timestamps(table, data[table])
        n = await insert_rows(pool, table, rows)
        print(f"  {table}: {n} rows inserted")
        total_inserted += n
    print(f"  Total inserted: {total_inserted}")
    print()

    # Verify
    print("Verifying PostgreSQL data...")
    async with pool.acquire() as conn:
        for table in TABLES:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            print(f"  {table}: {count} rows")
    print()

    await pool.close()
    print("Migration complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL")
    parser.add_argument(
        "--sqlite", default="out/ads.db", help="Path to SQLite database"
    )
    parser.add_argument(
        "--pg",
        default="postgresql://researcher:research@localhost:5432/ad_research",
        help="PostgreSQL connection URL",
    )
    args = parser.parse_args()

    asyncio.run(main(args.sqlite, args.pg))
