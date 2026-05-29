"""Trial quality assurance — validates data integrity and flags anomalies."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from urllib.parse import urlparse

from logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

_CAPTCHA_INDICATORS = [
    "captcha",
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "challenge",
    "verify you are human",
    "security check",
    "access denied",
    "please solve the challenge",
    "prove you are not a robot",
    "unusual traffic",
    "automated requests",
    "rate limit exceeded",
]

_BLOCK_STATUS_CODES = {403, 429, 503}

_REDIRECT_DOMAINS = {
    "consent.google.com",
    "accounts.google.com",
    "login.live.com",
}


class QAResult:
    def __init__(self, trial_id: str, strict: bool = False):
        self.trial_id = trial_id
        self.strict = strict
        self.warnings: list[dict] = []
        self.errors: list[dict] = []
        self.passed: list[str] = []

    def warn(self, check: str, message: str, detail: str = ""):
        entry = {"check": check, "level": "WARN", "message": message}
        if detail:
            entry["detail"] = detail
        self.warnings.append(entry)
        print(f"  [WARN] [{check}] {message}")
        if detail:
            print(f"         {detail}")
        if self.strict:
            raise SystemExit(1)

    def error(self, check: str, message: str, detail: str = ""):
        entry = {"check": check, "level": "ERROR", "message": message}
        if detail:
            entry["detail"] = detail
        self.errors.append(entry)
        print(f"  [ERROR] [{check}] {message}")
        if detail:
            print(f"          {detail}")
        if self.strict:
            raise SystemExit(1)

    def ok(self, check: str, message: str):
        self.passed.append(check)
        print(f"  [OK]   [{check}] {message}")

    def summary(self) -> int:
        print("\n" + "=" * 60)
        print(f"QA Summary — trial {self.trial_id}")
        print("=" * 60)
        print(f"  Checks passed : {len(self.passed)}")
        print(f"  Warnings      : {len(self.warnings)}")
        print(f"  Errors        : {len(self.errors)}")
        if self.errors:
            print("\n  STATUS: FAILED")
            return 1
        if self.warnings:
            print("\n  STATUS: PASSED (with warnings)")
            return 0
        print("\n  STATUS: PASSED")
        return 0


def check_blocks(cur: sqlite3.Cursor, trial_id: str, qa: QAResult) -> None:
    blocked = cur.execute(
        "SELECT target_url, status_code, error_type, zip_condition "
        "FROM page_visits WHERE trial_id = ? AND status_code IN (403, 429, 503)",
        (trial_id,),
    ).fetchall()

    if blocked:
        for url, code, err, proxy in blocked:
            qa.warn(
                "blocks",
                f"HTTP {code} on {url}",
                f"proxy={proxy}, error={err or 'none'}",
            )
    else:
        qa.ok("blocks", "No HTTP 403/429/503 responses detected")

    captcha_hits = cur.execute(
        "SELECT page_title, ad_url, zip_condition FROM ad_observations "
        "WHERE trial_id = ? AND page_title IS NOT NULL",
        (trial_id,),
    ).fetchall()

    captcha_found = []
    for title, url, proxy in captcha_hits:
        if any(ind in title.lower() for ind in _CAPTCHA_INDICATORS):
            captcha_found.append((title, url, proxy))

    if captcha_found:
        for title, url, proxy in captcha_found[:5]:
            qa.warn(
                "blocks",
                "Possible CAPTCHA page detected",
                f"title='{title[:80]}', proxy={proxy}",
            )
    else:
        qa.ok("blocks", "No CAPTCHA-like page titles detected")


def check_redirects(cur: sqlite3.Cursor, trial_id: str, qa: QAResult) -> None:
    redirects = cur.execute(
        "SELECT target_url, final_url, zip_condition, phase "
        "FROM page_visits WHERE trial_id = ? AND final_url IS NOT NULL "
        "AND target_url != final_url",
        (trial_id,),
    ).fetchall()

    unexpected = []
    for target, final, proxy, phase in redirects:
        target_domain = urlparse(target).netloc.lower().replace("www.", "")
        final_domain = urlparse(final).netloc.lower().replace("www.", "")
        if target_domain != final_domain and final_domain in _REDIRECT_DOMAINS:
            unexpected.append((target, final, proxy, phase))

    if unexpected:
        for target, final, proxy, phase in unexpected[:5]:
            qa.warn(
                "redirects",
                "Redirect to consent/login page",
                f"target={target} -> final={final}, proxy={proxy}, phase={phase}",
            )
    else:
        qa.ok("redirects", "No unexpected redirect chains detected")


def check_manipulation(cur: sqlite3.Cursor, trial_id: str, qa: QAResult) -> None:
    anti_bot_patterns = [
        "blocked",
        "denied",
        "suspicious",
        "automated",
        "bot",
        "unusual traffic",
        "security violation",
        "rate limited",
    ]

    dom_snippets = cur.execute(
        "SELECT dom_snippet, measurement_site, zip_condition FROM ad_observations "
        "WHERE trial_id = ? AND dom_snippet IS NOT NULL",
        (trial_id,),
    ).fetchall()

    flagged = []
    for snippet, site, proxy in dom_snippets:
        if any(pat in snippet.lower() for pat in anti_bot_patterns):
            flagged.append((site, proxy, snippet[:120]))

    if flagged:
        for site, proxy, snippet in flagged[:5]:
            qa.warn(
                "manipulation",
                "Anti-bot DOM pattern detected",
                f"site={site}, proxy={proxy}, snippet='{snippet}...'",
            )
    else:
        qa.ok("manipulation", "No anti-bot DOM patterns detected")


def check_balance(cur: sqlite3.Cursor, trial_id: str, qa: QAResult) -> None:
    counts = cur.execute(
        "SELECT zip_condition, intent_profile, COUNT(*) as n "
        "FROM ad_observations WHERE trial_id = ? "
        "GROUP BY zip_condition, intent_profile ORDER BY zip_condition, intent_profile",
        (trial_id,),
    ).fetchall()

    if not counts:
        qa.error("balance", "No ad observations found for this trial")
        return

    proxies = sorted(set(r[0] for r in counts))
    profiles = sorted(set(r[1] for r in counts))

    matrix = {}
    for proxy, profile, n in counts:
        matrix[(proxy, profile)] = n

    expected = (
        sum(matrix.values()) / (len(proxies) * len(profiles))
        if proxies and profiles
        else 0
    )

    imbalanced = []
    for proxy in proxies:
        for profile in profiles:
            n = matrix.get((proxy, profile), 0)
            if expected > 0 and abs(n - expected) / expected > 0.30:
                imbalanced.append((proxy, profile, n, int(expected)))

    if imbalanced:
        for proxy, profile, actual, exp in imbalanced:
            qa.warn(
                "balance",
                f"Count imbalance: {proxy}/{profile}",
                f"expected ~{exp}, got {actual} (>{30}% deviation)",
            )
    else:
        qa.ok(
            "balance",
            f"Observation counts balanced across {len(proxies)} proxies x {len(profiles)} profiles",
        )

    missing_cells = [
        (proxy, profile)
        for proxy in proxies
        for profile in profiles
        if (proxy, profile) not in matrix
    ]

    if missing_cells:
        for proxy, profile in missing_cells:
            qa.error("balance", f"Missing cell: {proxy}/{profile}")
    else:
        qa.ok("balance", "All proxy x profile cells present")


def check_integrity(cur: sqlite3.Cursor, trial_id: str, qa: QAResult) -> None:
    obs_count = cur.execute(
        "SELECT COUNT(*) FROM ad_observations WHERE trial_id = ?",
        (trial_id,),
    ).fetchone()[0]

    if obs_count == 0:
        qa.error("integrity", "Zero ad observations — trial may have failed completely")
        return

    qa.ok("integrity", f"{obs_count} ad observations present")

    null_checks = [
        ("ad_url", "ad_observations"),
        ("zip_condition", "ad_observations"),
        ("agent_id", "ad_observations"),
        ("trial_id", "ad_observations"),
        ("target_url", "page_visits"),
        ("phase", "page_visits"),
    ]

    for col, table in null_checks:
        nulls = cur.execute(
            f"SELECT COUNT(*) FROM {table} WHERE trial_id = ? AND {col} IS NULL",
            (trial_id,),
        ).fetchone()[0]
        if nulls > 0:
            qa.warn("integrity", f"{nulls} NULL values in {table}.{col}")
        else:
            qa.ok("integrity", f"No NULL values in {table}.{col}")

    trial_exists = cur.execute(
        "SELECT COUNT(*) FROM trials WHERE trial_id = ?",
        (trial_id,),
    ).fetchone()[0]

    if trial_exists:
        qa.ok("integrity", "Trial record exists in trials table")
        meta_raw = cur.execute(
            "SELECT trial_meta FROM trials WHERE trial_id = ?",
            (trial_id,),
        ).fetchone()[0]

        if meta_raw:
            try:
                meta = json.loads(meta_raw)
                qa.ok("integrity", f"trial_meta is valid JSON ({len(meta)} keys)")
            except json.JSONDecodeError:
                qa.error("integrity", "trial_meta is malformed JSON")
        else:
            qa.warn("integrity", "trial_meta is empty")
    else:
        qa.error("integrity", "Trial record missing from trials table")

    orphan_visits = cur.execute(
        "SELECT COUNT(*) FROM page_visits v "
        "WHERE v.trial_id = ? AND v.trial_id NOT IN (SELECT trial_id FROM trials)",
        (trial_id,),
    ).fetchone()[0]

    if orphan_visits > 0:
        qa.error("integrity", f"{orphan_visits} orphaned page_visits rows")
    else:
        qa.ok("integrity", "No orphaned page_visits rows")

    orphan_obs = cur.execute(
        "SELECT COUNT(*) FROM ad_observations o "
        "WHERE o.trial_id = ? AND o.trial_id NOT IN (SELECT trial_id FROM trials)",
        (trial_id,),
    ).fetchone()[0]

    if orphan_obs > 0:
        qa.error("integrity", f"{orphan_obs} orphaned ad_observations rows")
    else:
        qa.ok("integrity", "No orphaned ad_observations rows")


def main(trial_id: str, db_url: str, strict: bool) -> int:
    if not db_url.startswith("sqlite:///"):
        logger.error("Unsupported DB URL: %s", db_url)
        return 1

    db_path = db_url.removeprefix("sqlite:///")
    logger.info("Validating trial %s", trial_id)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    trial_exists = cur.execute(
        "SELECT COUNT(*) FROM trials WHERE trial_id = ?",
        (trial_id,),
    ).fetchone()[0]

    if not trial_exists:
        logger.error("trial %s not found in database", trial_id)
        print("\nAvailable trials:")
        rows = cur.execute(
            "SELECT trial_id, started_at FROM trials ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
        for tid, ts in rows:
            print(f"  {tid}  started={ts}")
        conn.close()
        return 1

    qa = QAResult(trial_id, strict=strict)

    print("\n── Check 1: Blocking / CAPTCHA ──")
    check_blocks(cur, trial_id, qa)

    print("\n── Check 2: Redirects ──")
    check_redirects(cur, trial_id, qa)

    print("\n── Check 3: DOM Manipulation ──")
    check_manipulation(cur, trial_id, qa)

    print("\n── Check 4: Balance ──")
    check_balance(cur, trial_id, qa)

    print("\n── Check 5: Data Integrity ──")
    check_integrity(cur, trial_id, qa)

    conn.close()
    return qa.summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate a completed trial")
    parser.add_argument("--trial-id", required=True, help="Trial UUID to validate")
    parser.add_argument(
        "--strict", action="store_true", help="Fail fast on first anomaly"
    )
    parser.add_argument("--db-url", default="sqlite:///out/ads.db")
    args = parser.parse_args()
    sys.exit(main(args.trial_id, args.db_url, args.strict))
