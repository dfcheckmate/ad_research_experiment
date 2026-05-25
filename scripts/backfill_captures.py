"""
Backfill captures table from existing screenshot files on disk.

Parses filenames of the form:
    out/captures/<trial_id>/<proxy_identity>__<intent_profile>__<site>.png

Inserts one row per file into the `captures` table (skips duplicates via
ON CONFLICT DO NOTHING on file_path once thea unique index exists).

Usage:
    python backfill_captures.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

from config import CAPTURES_DIR, DB_URL

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CAPTURES_ROOT = PROJECT_ROOT / CAPTURES_DIR
USE_SQLITE = DB_URL.startswith("sqlite")
SQLITE_PATH = DB_URL.removeprefix("sqlite:///") if USE_SQLITE else None


def parse_filename(trial_id: str, filename: str) -> dict | None:
    """
    Parse  'proxy_identity__intent_profile__site.png'
    Returns a dict or None if it doesn't match the pattern.
    """
    name = filename.removesuffix(".png")
    parts = name.split("__")
    if len(parts) != 3:
        return None
    proxy_identity, intent_profile, site = parts
    return {
        "proxy_identity": proxy_identity,
        "intent_profile": intent_profile,
        "site": site,
    }


def collect_files() -> list[dict]:
    rows = []
    if not CAPTURES_ROOT.exists():
        print(f"[backfill] captures directory not found: {CAPTURES_ROOT}")
        return rows

    for trial_dir in sorted(CAPTURES_ROOT.iterdir()):
        if not trial_dir.is_dir():
            continue
        trial_id = trial_dir.name
        for png in sorted(trial_dir.glob("*.png")):
            parsed = parse_filename(trial_id, png.name)
            if not parsed:
                print(f"[skip] unrecognised filename: {png}")
                continue
            relative_path = str(png.relative_to(PROJECT_ROOT))
            try:
                size_kb = png.stat().st_size // 1024
            except OSError:
                size_kb = None
            rows.append(
                {
                    "trial_id": trial_id,
                    "proxy_identity": parsed["proxy_identity"],
                    "intent_profile": parsed["intent_profile"],
                    "site": parsed["site"],
                    "file_path": relative_path,
                    "file_size_kb": size_kb,
                    "meta": json.dumps({"backfilled": True}),
                }
            )
    return rows


async def run_pg(rows: list[dict], dry_run: bool) -> None:
    import asyncpg

    conn = await asyncpg.connect(DB_URL)

    # Ensure captures table exists (idempotent).
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS captures (
            id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            trial_id         TEXT        NOT NULL REFERENCES trials(trial_id),
            proxy_identity   TEXT        NOT NULL,
            intent_profile   TEXT        NOT NULL,
            site             TEXT        NOT NULL,
            file_path        TEXT        NOT NULL,
            file_size_kb     INTEGER,
            captured_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            meta             JSONB       NOT NULL DEFAULT '{}'
        )
        """
    )

    # Unique index on file_path so ON CONFLICT works and duplicates are skipped.
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cap_file_path ON captures(file_path)"
    )

    inserted = skipped = 0
    for r in rows:
        if dry_run:
            print(f"  [dry-run] {r['file_path']}")
            continue
        try:
            await conn.execute(
                """
                INSERT INTO captures
                    (trial_id, proxy_identity, intent_profile, site, file_path, file_size_kb, meta)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                ON CONFLICT (file_path) DO NOTHING
                """,
                r["trial_id"],
                r["proxy_identity"],
                r["intent_profile"],
                r["site"],
                r["file_path"],
                r["file_size_kb"],
                r["meta"],
            )
            inserted += 1
        except Exception as e:
            print(f"[warn] {r['file_path']}: {e}")
            skipped += 1

    await conn.close()
    if not dry_run:
        print(f"[backfill] inserted={inserted}  skipped/errors={skipped}")


async def run_sqlite(rows: list[dict], dry_run: bool) -> None:
    import aiosqlite

    async with aiosqlite.connect(SQLITE_PATH) as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS captures (
                id               TEXT PRIMARY KEY,
                trial_id         TEXT NOT NULL REFERENCES trials(trial_id),
                proxy_identity   TEXT NOT NULL,
                intent_profile   TEXT NOT NULL,
                site             TEXT NOT NULL,
                file_path        TEXT NOT NULL UNIQUE,
                file_size_kb     INTEGER,
                captured_at      TEXT NOT NULL DEFAULT (datetime('now')),
                meta             TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        await conn.commit()

        inserted = skipped = 0
        for r in rows:
            if dry_run:
                print(f"  [dry-run] {r['file_path']}")
                continue
            try:
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO captures
                        (id, trial_id, proxy_identity, intent_profile, site, file_path, file_size_kb, meta)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        r["trial_id"],
                        r["proxy_identity"],
                        r["intent_profile"],
                        r["site"],
                        r["file_path"],
                        r["file_size_kb"],
                        r["meta"],
                    ),
                )
                inserted += 1
            except Exception as e:
                print(f"[warn] {r['file_path']}: {e}")
                skipped += 1
        await conn.commit()

    if not dry_run:
        print(f"[backfill] inserted={inserted}  skipped/errors={skipped}")


async def main(dry_run: bool) -> None:
    rows = collect_files()
    print(f"[backfill] found {len(rows)} screenshot files under {CAPTURES_ROOT}")
    if not rows:
        return

    if dry_run:
        print("[backfill] --dry-run: showing files that would be inserted")

    if USE_SQLITE:
        await run_sqlite(rows, dry_run)
    else:
        await run_pg(rows, dry_run)

    if not dry_run:
        print("[backfill] done. Query with:")
        print("  SELECT proxy_identity, intent_profile, COUNT(*) FROM captures GROUP BY 1,2 ORDER BY 1,2;")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill captures table from screenshot files")
    parser.add_argument("--dry-run", action="store_true", help="Print files without inserting")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
