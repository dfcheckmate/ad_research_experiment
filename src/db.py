"""
Database layer — supports SQLite (default) and PostgreSQL.

Backend is chosen by DB_URL in config:
  sqlite:///out/ads.db → SQLite  (default, zero extra config)
  postgresql://...    → PostgreSQL (asyncpg)
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

# Keep DB_URL as a module-level knob so tests can monkeypatch `db.DB_URL`
# without needing to reload the module.
try:
    from config import DB_URL as _CONFIG_DB_URL
except Exception:  # pragma: no cover
    _CONFIG_DB_URL = "sqlite:///out/ads.db"

# Prefer the live environment value (tests monkeypatch this).
DB_URL = os.getenv("DB_URL", _CONFIG_DB_URL)


def _use_sqlite(db_url: str | None = None) -> bool:
    url = db_url or DB_URL
    return url.startswith("sqlite")


def _sqlite_path(db_url: str | None = None) -> str:
    url = db_url or DB_URL
    # Expected forms in this repo/tests:
    #   sqlite:///relative-or-absolute-path.db
    #   sqlite:///:memory:
    if not url.startswith("sqlite:///"):
        raise ValueError(f"Unsupported SQLite URL format: {url!r}")
    return url.removeprefix("sqlite:///")


def _ensure_sqlite_parent(path: str) -> None:
    if path == ":memory:":
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


# ── SQLite schema ─────────────────────────────────────────────────────────────
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
    trial_id    TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    trial_meta  TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS page_visits (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id           TEXT NOT NULL REFERENCES trials(trial_id),
    agent_id           TEXT NOT NULL,
    zip_condition      TEXT NOT NULL,
    intent_profile     TEXT,
    phase              TEXT NOT NULL,
    measurement_site   TEXT,
    target_url         TEXT NOT NULL,
    final_url          TEXT,
    status_code        INTEGER,
    error_type         TEXT,
    error_text         TEXT,
    page_load_time_ms  INTEGER,
    cookie_count       INTEGER,
    observed_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_vis_trial ON page_visits(trial_id);
CREATE INDEX IF NOT EXISTS idx_vis_zip   ON page_visits(zip_condition);
CREATE INDEX IF NOT EXISTS idx_vis_phase ON page_visits(phase);
CREATE TABLE IF NOT EXISTS ad_observations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id         TEXT NOT NULL REFERENCES trials(trial_id),
    agent_id         TEXT NOT NULL,
    zip_condition    TEXT NOT NULL,
    ad_url           TEXT NOT NULL,
    ad_domain        TEXT,
    ad_network       TEXT,
    measurement_site TEXT,
    source_type      TEXT,
    intent_profile   TEXT,
    query_topic      TEXT,
    search_query     TEXT,
    ad_headline      TEXT,
    ad_description   TEXT,
    advertiser_name  TEXT,
    landing_url      TEXT,
    landing_domain   TEXT,
    inferred_topic   TEXT,
    page_title       TEXT,
    page_url         TEXT,
    screenshot_path  TEXT,
    dom_snippet      TEXT,
    page_load_time_ms INTEGER,
    ad_rank          INTEGER,
    ad_placement     TEXT,
    observed_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_obs_zip   ON ad_observations(zip_condition);
CREATE INDEX IF NOT EXISTS idx_obs_trial ON ad_observations(trial_id);
"""

# ── PostgreSQL schema ─────────────────────────────────────────────────────────
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
    trial_id    TEXT        PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    trial_meta  JSONB       NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS page_visits (
    id                BIGSERIAL   PRIMARY KEY,
    trial_id           TEXT        NOT NULL REFERENCES trials(trial_id),
    agent_id           TEXT        NOT NULL,
    zip_condition      TEXT        NOT NULL,
    intent_profile     TEXT,
    phase              TEXT        NOT NULL,
    measurement_site   TEXT,
    target_url         TEXT        NOT NULL,
    final_url          TEXT,
    status_code        INTEGER,
    error_type         TEXT,
    error_text         TEXT,
    page_load_time_ms  INTEGER,
    cookie_count       INTEGER,
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_vis_trial ON page_visits(trial_id);
CREATE INDEX IF NOT EXISTS idx_vis_zip   ON page_visits(zip_condition);
CREATE INDEX IF NOT EXISTS idx_vis_phase ON page_visits(phase);
CREATE TABLE IF NOT EXISTS ad_observations (
    id               SERIAL      PRIMARY KEY,
    trial_id         TEXT        NOT NULL REFERENCES trials(trial_id),
    agent_id         TEXT        NOT NULL,
    zip_condition    TEXT        NOT NULL,
    ad_url           TEXT        NOT NULL,
    ad_domain        TEXT,
    ad_network       TEXT,
    measurement_site TEXT,
    source_type      TEXT,
    intent_profile   TEXT,
    query_topic      TEXT,
    search_query     TEXT,
    ad_headline      TEXT,
    ad_description   TEXT,
    advertiser_name  TEXT,
    landing_url      TEXT,
    landing_domain   TEXT,
    inferred_topic   TEXT,
    page_title       TEXT,
    page_url         TEXT,
    screenshot_path  TEXT,
    dom_snippet      TEXT,
    page_load_time_ms INTEGER,
    ad_rank          INTEGER,
    ad_placement     TEXT,
    observed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_obs_zip   ON ad_observations(zip_condition);
CREATE INDEX IF NOT EXISTS idx_obs_trial ON ad_observations(trial_id);
"""

# ── PostgreSQL captures table ─────────────────────────────────────────────────
PG_CAPTURES_SCHEMA = """
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
);
CREATE INDEX IF NOT EXISTS idx_cap_trial ON captures(trial_id);
CREATE INDEX IF NOT EXISTS idx_cap_proxy ON captures(proxy_identity);
CREATE INDEX IF NOT EXISTS idx_cap_meta  ON captures USING GIN(meta);
"""

# ── SQLite captures table ─────────────────────────────────────────────────────
SQLITE_CAPTURES_SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id               TEXT        PRIMARY KEY,
    trial_id         TEXT        NOT NULL REFERENCES trials(trial_id),
    proxy_identity   TEXT        NOT NULL,
    intent_profile   TEXT        NOT NULL,
    site             TEXT        NOT NULL,
    file_path        TEXT        NOT NULL,
    file_size_kb     INTEGER,
    captured_at      TEXT        NOT NULL DEFAULT (datetime('now')),
    meta             TEXT        NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_cap_trial ON captures(trial_id);
CREATE INDEX IF NOT EXISTS idx_cap_proxy ON captures(proxy_identity);
"""

SQLITE_MIGRATIONS = [
    "ALTER TABLE trials ADD COLUMN trial_meta TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE ad_observations ADD COLUMN source_type TEXT",
    "ALTER TABLE ad_observations ADD COLUMN intent_profile TEXT",
    "ALTER TABLE ad_observations ADD COLUMN query_topic TEXT",
    "ALTER TABLE ad_observations ADD COLUMN search_query TEXT",
    "ALTER TABLE ad_observations ADD COLUMN ad_headline TEXT",
    "ALTER TABLE ad_observations ADD COLUMN ad_description TEXT",
    "ALTER TABLE ad_observations ADD COLUMN advertiser_name TEXT",
    "ALTER TABLE ad_observations ADD COLUMN landing_url TEXT",
    "ALTER TABLE ad_observations ADD COLUMN landing_domain TEXT",
    "ALTER TABLE ad_observations ADD COLUMN inferred_topic TEXT",
    "ALTER TABLE ad_observations ADD COLUMN page_title TEXT",
    "ALTER TABLE ad_observations ADD COLUMN page_url TEXT",
    "ALTER TABLE ad_observations ADD COLUMN screenshot_path TEXT",
    "ALTER TABLE ad_observations ADD COLUMN dom_snippet TEXT",
    "ALTER TABLE ad_observations ADD COLUMN page_load_time_ms INTEGER",
]

PG_MIGRATIONS = [
    "ALTER TABLE trials ADD COLUMN IF NOT EXISTS trial_meta JSONB NOT NULL DEFAULT '{}'",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS source_type TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS intent_profile TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS query_topic TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS search_query TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS ad_headline TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS ad_description TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS advertiser_name TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS landing_url TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS landing_domain TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS inferred_topic TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS page_title TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS page_url TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS screenshot_path TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS dom_snippet TEXT",
    "ALTER TABLE ad_observations ADD COLUMN IF NOT EXISTS page_load_time_ms INTEGER",
]

# ── SQLite async helpers ──────────────────────────────────────────────────────
_sqlite_lock = asyncio.Lock()


async def _sqlite_execute(sql: str, params: tuple = ()) -> None:
    import aiosqlite

    async with _sqlite_lock:
        async with aiosqlite.connect(_sqlite_path()) as conn:
            await conn.execute(sql, params)
            await conn.commit()


async def _sqlite_executemany(sql: str, params_list: list[tuple]) -> None:
    import aiosqlite

    async with _sqlite_lock:
        async with aiosqlite.connect(_sqlite_path()) as conn:
            await conn.executemany(sql, params_list)
            await conn.commit()


async def _sqlite_init() -> None:
    import aiosqlite

    path = _sqlite_path()
    _ensure_sqlite_parent(path)
    async with aiosqlite.connect(path) as conn:
        await conn.executescript(SQLITE_SCHEMA)
        await conn.executescript(SQLITE_CAPTURES_SCHEMA)
        for sql in SQLITE_MIGRATIONS:
            try:
                await conn.execute(sql)
            except Exception:
                pass
        await conn.commit()
    logger.info("SQLite schema ready → %s", path)


# ── Pool shim for SQLite ──────────────────────────────────────────────────────
class _SqlitePool:
    """asyncpg-like pool interface over aiosqlite connections."""

    def __init__(self, db_url: str):
        self._db_url = db_url

    @asynccontextmanager
    async def acquire(self):
        import aiosqlite

        async with aiosqlite.connect(_sqlite_path(self._db_url)) as conn:
            yield conn

    async def close(self):
        # Connections are opened/closed per-acquire.
        return


# ── Public API ────────────────────────────────────────────────────────────────


async def get_pool(min_size: int = 2, max_size: int = 10):
    if _use_sqlite():
        return _SqlitePool(DB_URL)
    import asyncpg

    return await asyncpg.create_pool(DB_URL, min_size=min_size, max_size=max_size)


async def init_db(pool) -> None:
    if _use_sqlite():
        await _sqlite_init()
        return
    async with pool.acquire() as conn:
        await conn.execute(PG_SCHEMA)
        await conn.execute(PG_CAPTURES_SCHEMA)
        for sql in PG_MIGRATIONS:
            await conn.execute(sql)
    logger.info("PostgreSQL schema ready")


async def ensure_trial(pool, trial_id: str, meta: dict | None = None) -> None:
    import json

    meta_json = json.dumps(meta or {})
    if _use_sqlite():
        await _sqlite_execute(
            "INSERT OR IGNORE INTO trials (trial_id, trial_meta) VALUES (?, ?)",
            (trial_id, meta_json),
        )
        await _sqlite_execute(
            "UPDATE trials SET trial_meta = ? WHERE trial_id = ?",
            (meta_json, trial_id),
        )
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO trials (trial_id, trial_meta)
               VALUES ($1, $2::jsonb)
               ON CONFLICT (trial_id) DO UPDATE SET trial_meta = EXCLUDED.trial_meta""",
            trial_id,
            meta_json,
        )


async def insert_capture(
    pool,
    trial_id: str,
    proxy_identity: str,
    intent_profile: str,
    site: str,
    file_path: str,
    file_size_kb: int | None = None,
    meta: dict | None = None,
) -> None:
    """
    Insert one screenshot capture record.
    `meta` is stored as JSONB (PG) or JSON text (SQLite) and can carry
    arbitrary per-capture attributes: ad_count, page_title, dom_hash, etc.
    """
    import json

    meta_json = json.dumps(meta or {})
    if _use_sqlite():
        import uuid

        await _sqlite_execute(
            """INSERT OR IGNORE INTO captures
               (id, trial_id, proxy_identity, intent_profile, site, file_path, file_size_kb, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                trial_id,
                proxy_identity,
                intent_profile,
                site,
                file_path,
                file_size_kb,
                meta_json,
            ),
        )
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO captures
               (trial_id, proxy_identity, intent_profile, site, file_path, file_size_kb, meta)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
               ON CONFLICT DO NOTHING""",
            trial_id,
            proxy_identity,
            intent_profile,
            site,
            file_path,
            file_size_kb,
            meta_json,
        )


async def insert_observations(pool, rows: list[dict]) -> None:
    if not rows:
        return
    params = [
        (
            r["trial_id"],
            r["agent_id"],
            r["zip_condition"],
            r["ad_url"],
            r.get("ad_domain"),
            r.get("ad_network"),
            r.get("measurement_site"),
            r.get("source_type"),
            r.get("intent_profile"),
            r.get("query_topic"),
            r.get("search_query"),
            r.get("ad_headline"),
            r.get("ad_description"),
            r.get("advertiser_name"),
            r.get("landing_url"),
            r.get("landing_domain"),
            r.get("inferred_topic"),
            r.get("page_title"),
            r.get("page_url"),
            r.get("screenshot_path"),
            r.get("dom_snippet"),
            r.get("page_load_time_ms"),
            r.get("ad_rank"),
            r.get("ad_placement"),
        )
        for r in rows
    ]
    if _use_sqlite():
        await _sqlite_executemany(
            """INSERT INTO ad_observations
               (trial_id, agent_id, zip_condition, ad_url, ad_domain, ad_network, measurement_site,
                source_type, intent_profile, query_topic, search_query, ad_headline, ad_description, advertiser_name,
                landing_url, landing_domain, inferred_topic, page_title, page_url, screenshot_path, dom_snippet,
                page_load_time_ms, ad_rank, ad_placement)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO ad_observations
               (trial_id, agent_id, zip_condition, ad_url, ad_domain, ad_network, measurement_site,
                source_type, intent_profile, query_topic, search_query, ad_headline, ad_description, advertiser_name,
                landing_url, landing_domain, inferred_topic, page_title, page_url, screenshot_path, dom_snippet,
                page_load_time_ms, ad_rank, ad_placement)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24)""",
            params,
        )


async def insert_page_visits(pool, rows: list[dict]) -> None:
    """
    Insert per-navigation telemetry (used to diagnose blocking / rate limiting).
    """
    if not rows:
        return
    params = [
        (
            r["trial_id"],
            r["agent_id"],
            r["zip_condition"],
            r.get("intent_profile"),
            r["phase"],
            r.get("measurement_site"),
            r["target_url"],
            r.get("final_url"),
            r.get("status_code"),
            r.get("error_type"),
            r.get("error_text"),
            r.get("page_load_time_ms"),
            r.get("cookie_count"),
        )
        for r in rows
    ]
    if _use_sqlite():
        await _sqlite_executemany(
            """INSERT INTO page_visits
               (trial_id, agent_id, zip_condition, intent_profile, phase, measurement_site,
                target_url, final_url, status_code, error_type, error_text, page_load_time_ms, cookie_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO page_visits
               (trial_id, agent_id, zip_condition, intent_profile, phase, measurement_site,
                target_url, final_url, status_code, error_type, error_text, page_load_time_ms, cookie_count)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)""",
            params,
        )
