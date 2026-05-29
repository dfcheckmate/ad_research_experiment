"""Playwright browser agent — warming, conditioning, measurement, ad capture."""

from __future__ import annotations

import asyncio
import os
import random
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from playwright.async_api import async_playwright, BrowserContext, Page

from config import (
    AD_NETWORK_PATTERNS,
    ACTIVE_QUERY_TOPICS,
    AD_DWELL_MS,
    CAPTURES_DIR,
    CAPTURE_DOM_SNIPPETS,
    CAPTURE_SCREENSHOTS,
    DWELL_TIME_MS,
    ENABLE_TELEMETRY,
    ENABLE_GOOGLE_SEARCH_MEASUREMENT,
    GOOGLE_DWELL_MS,
    GOOGLE_SEARCH_URL_TEMPLATE,
    HEADLESS,
    PROXY_MODE,
    INTENT_PROFILES,
    QUERIES_PER_TOPIC_PER_TRIAL,
    sites_for_trial,
    TOPIC_KEYWORDS,
    TOPIC_QUERY_SETS,
    WARMING_URLS,
    WARMING_DWELL_MS,
)
import db

# ── User-agent pool ───────────────────────────────────────────────────────────
# Real UA strings from common browser versions. Rotated per agent session.
_USER_AGENTS = [
    # Chrome 122 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome 121 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Chrome 120 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Chrome 119 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    # Firefox 123 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    # Firefox 121 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Edge 122 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    # Chrome 118 Linux (looks like a desktop Chromebook / corp Linux)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
]

_TIMEZONES = [
    "Europe/Amsterdam",
]

_LOCALES = ["en-US", "en-US", "en-US", "en-GB"]

# Screenshot API auth (optional, if configured in environment)
_SCREENSHOT_API_AUTH = os.getenv("SCREENSHOT_API_AUTH")
_SCREENSHOT_API_URL = os.getenv("SCREENSHOT_API_URL")


def _jitter(base_ms: int, pct: float = 0.20) -> int:
    """Return base_ms ± pct variation."""
    delta = int(base_ms * pct)
    return base_ms + random.randint(-delta, delta)


def _random_context_params() -> dict:
    """Return a randomised but realistic browser context configuration."""
    width = 1280 + random.choice([-40, 0, 0, 40, 80, 120])
    height = 800 + random.choice([-40, 0, 0, 40, 80])
    return {
        "user_agent": random.choice(_USER_AGENTS),
        "viewport": {"width": width, "height": height},
        "locale": random.choice(_LOCALES),
        "timezone_id": random.choice(_TIMEZONES),
    }


# JS snippet injected into every page to remove bot fingerprints.
_STEALTH_JS = """
() => {
    // Remove navigator.webdriver flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    // Spoof plugins length (headless Chromium has 0)
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    // Spoof languages
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
}
"""


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _external_screenshot(
    site_url: str, geo_city: str = "United States"
) -> bytes | None:
    """
    Request a screenshot from external screenshot API (if configured).
    Returns PNG bytes or None on failure.
    Uses asyncio.to_thread so it doesn't block the event loop.
    """
    if not _SCREENSHOT_API_AUTH or not _SCREENSHOT_API_URL:
        return None

    import requests as _requests

    def _call() -> bytes | None:
        try:
            r = _requests.post(
                _SCREENSHOT_API_URL,
                json={
                    "url": site_url,
                    "headless": "html",
                    "geo": geo_city,
                    "locale": "en-us",
                    "xhr": True,
                    "screenshot": True,
                },
                headers={
                    "accept": "application/json",
                    "content-type": "application/json",
                    "authorization": _SCREENSHOT_API_AUTH,
                },
                timeout=60,
            )
            data = r.json()
            # Returns screenshot as base64 in data.screenshot
            import base64

            b64 = data.get("screenshot") or (data.get("results") or [{}])[0].get(
                "screenshot"
            )
            if b64:
                return base64.b64decode(b64)
        except Exception:
            pass
        return None

    return await asyncio.to_thread(_call)


def extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""


def classify_network(domain: str) -> str | None:
    """Return the matching network pattern or None."""
    for pattern in AD_NETWORK_PATTERNS:
        if pattern in domain:
            return pattern
    return None


def is_ad_request(url: str) -> bool:
    domain = extract_domain(url)
    return classify_network(domain) is not None


def build_google_search_url(query: str) -> str:
    return GOOGLE_SEARCH_URL_TEMPLATE.format(query=quote_plus(query))


def parse_google_redirect(url: str) -> str:
    """Extract the advertiser landing URL from Google ad redirect URLs."""
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("adurl", "url", "q"):
            if key in query and query[key]:
                return unquote(query[key][0])
    except Exception:
        pass
    return url


def infer_topic(
    text: str = "", domain: str = "", fallback: str | None = None
) -> str | None:
    haystack = f"{text} {domain}".lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return topic
    return fallback


def queries_for_trial(trial_id: str) -> list[tuple[str, str]]:
    """Pick a stable subset of queries per topic based on the trial id."""
    try:
        offset = int(trial_id.replace("-", "")[:8], 16)
    except Exception:
        offset = 0

    selected: list[tuple[str, str]] = []
    for i, topic in enumerate(ACTIVE_QUERY_TOPICS):
        queries = TOPIC_QUERY_SETS.get(topic, [])
        if not queries:
            continue
        for j in range(min(QUERIES_PER_TOPIC_PER_TRIAL, len(queries))):
            idx = (offset + i + j) % len(queries)
            selected.append((topic, queries[idx]))
    return selected


def build_playwright_proxy_config(proxy_url: str) -> dict:
    """Build a Playwright proxy dict from a full proxy URL.

    Notes:

    - HTTP proxies: Chromium does not reliably parse credentials embedded in the
      server URL, so we pass username/password fields separately.
    - SOCKS proxies: Playwright expects credentials embedded in the server URL;
      separate fields are ignored.
    """
    parsed = urlparse(proxy_url)
    scheme = (parsed.scheme or "").lower()

    # Some providers use curl-style schemes like socks5h (remote DNS).
    # Playwright/Chromium expects socks5.
    if scheme == "socks5h":
        proxy_url = proxy_url.replace("socks5h://", "socks5://", 1)
        parsed = urlparse(proxy_url)
        scheme = "socks5"

    is_socks = scheme.startswith("socks")
    if is_socks:
        return {"server": proxy_url}

    if not parsed.hostname or not parsed.port or not parsed.scheme:
        raise ValueError(
            f"Invalid proxy URL: {proxy_url!r}. "
            f"Set PROXY_MODE=local (no proxy) or provide valid proxy credentials in .env"
        )

    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    cfg: dict = {"server": server}
    if parsed.username:
        cfg["username"] = parsed.username
    if parsed.password:
        cfg["password"] = parsed.password
    return cfg


def slugify_site(url: str) -> str:
    domain = extract_domain(url).replace("www.", "")
    return domain.replace(".", "_") or "site"


async def capture_page_context(
    page: Page,
    trial_id: str,
    zip_condition: str,
    intent_profile: str,
    measurement_site: str,
    pool=None,
) -> tuple[str | None, str | None, str | None]:
    page_title = None
    screenshot_path = None
    dom_snippet = None

    try:
        page_title = await page.title()
    except Exception:
        page_title = None

    if CAPTURE_SCREENSHOTS:
        # Resolve paths relative to project root, not src/ directory
        project_root = Path(__file__).parent.parent
        relative_dir = os.path.join(CAPTURES_DIR, trial_id)
        absolute_dir = project_root / relative_dir
        absolute_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            f"{zip_condition}__{intent_profile}__{slugify_site(measurement_site)}.png"
        )
        screenshot_path = os.path.join(relative_dir, filename)
        absolute_path = project_root / screenshot_path
        try:
            # ── Strategy 1: External screenshot API (if configured) ──
            png_bytes = await _external_screenshot(measurement_site)
            if png_bytes and len(png_bytes) > 5_000:
                with open(absolute_path, "wb") as f:
                    f.write(png_bytes)
            else:
                # ── Strategy 2: Playwright fallback with stealth wait ───────────
                try:
                    await page.wait_for_function(
                        "document.body && document.body.innerText.trim().length > 200",
                        timeout=10_000,
                    )
                except Exception:
                    pass
                try:
                    await page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight / 2)"
                    )
                    await page.wait_for_timeout(1_500)
                except Exception:
                    pass
                await page.screenshot(path=absolute_path, full_page=False)

            # Record in DB if file exists and has real content
            if absolute_path.exists() and absolute_path.stat().st_size > 5_000:
                if pool is not None:
                    size_kb = absolute_path.stat().st_size // 1024
                    await db.insert_capture(
                        pool=pool,
                        trial_id=trial_id,
                        proxy_identity=zip_condition,
                        intent_profile=intent_profile,
                        site=slugify_site(measurement_site),
                        file_path=screenshot_path,
                        file_size_kb=size_kb,
                        meta={
                            "page_title": page_title,
                            "measurement_site": measurement_site,
                            "screenshot_source": "external_api"
                            if (png_bytes and len(png_bytes) > 5_000)
                            else "playwright",
                        },
                    )
            else:
                screenshot_path = None
        except Exception:
            screenshot_path = None

    if CAPTURE_DOM_SNIPPETS:
        try:
            dom_snippet = await page.evaluate(
                r"""
                () => {
                  const text = (document.body?.innerText || '').replace(/\s+/g, ' ').trim();
                  return text.slice(0, 1500);
                }
                """
            )
        except Exception:
            dom_snippet = None

    return page_title, screenshot_path, dom_snippet


async def collect_google_search_ads(
    page: Page,
    trial_id: str,
    agent_id: str,
    zip_condition: str,
    intent_profile: str,
) -> list[dict]:
    observations: list[dict] = []

    for topic, query in queries_for_trial(trial_id):
        search_url = build_google_search_url(query)
        page_load_time_ms = None
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)

            # Capture page load timing
            try:
                timing = await page.evaluate("""
                    () => {
                        const nav = performance.getEntriesByType("navigation")[0];
                        return nav ? nav.domInteractive : null;
                    }
                """)
                if timing is not None:
                    page_load_time_ms = int(timing)
            except Exception:
                pass

            await page.wait_for_timeout(GOOGLE_DWELL_MS)
        except Exception:
            continue

        raw_ads = await page.evaluate(
            """
            () => {
              const anchors = Array.from(document.querySelectorAll('a[href*="aclk?"], a[href*="googleadservices"], a[data-pcu], [data-text-ad] a'));
              const rows = anchors.map((anchor) => {
                const card = anchor.closest('[data-text-ad], .uEierd, .v5yQqb, div[data-snc], div[role="complementary"]') || anchor.closest('div');
                const text = (card?.innerText || anchor.innerText || '').trim();
                const headline = (card?.querySelector('h3')?.innerText || anchor.innerText || '').trim();
                const advertiser = (anchor.getAttribute('data-pcu') || card?.querySelector('[data-dtld]')?.innerText || '').trim();
                return {
                  href: anchor.href || '',
                  headline,
                  text,
                  advertiser,
                };
              });

              const seen = new Set();
              return rows.filter((row) => {
                const key = `${row.href}::${row.headline}`;
                if (!row.href || !row.text || seen.has(key)) return false;
                seen.add(key);
                return true;
              });
            }
            """
        )

        for raw in raw_ads:
            landing_url = parse_google_redirect(raw.get("href", ""))
            landing_domain = extract_domain(landing_url)
            text = raw.get("text", "")
            headline = raw.get("headline", "")
            advertiser = raw.get("advertiser", "") or landing_domain

            observations.append(
                {
                    "trial_id": trial_id,
                    "agent_id": agent_id,
                    "zip_condition": zip_condition,
                    "ad_url": raw.get("href", ""),
                    "ad_domain": extract_domain(raw.get("href", "")),
                    "ad_network": classify_network(extract_domain(raw.get("href", "")))
                    or "google_search",
                    "measurement_site": "google_search",
                    "source_type": "google_search_ad",
                    "intent_profile": intent_profile,
                    "query_topic": topic,
                    "search_query": query,
                    "ad_headline": headline,
                    "ad_description": text,
                    "advertiser_name": advertiser,
                    "landing_url": landing_url,
                    "landing_domain": landing_domain,
                    "inferred_topic": infer_topic(
                        f"{headline} {text}", landing_domain, fallback=topic
                    ),
                    "page_title": None,
                    "page_url": search_url,
                    "screenshot_path": None,
                    "dom_snippet": None,
                    "page_load_time_ms": page_load_time_ms,
                }
            )

    return observations


# ── Agent ─────────────────────────────────────────────────────────────────────


async def run_agent(
    trial_id: str,
    zip_condition: str,
    intent_profile: str,
    proxy_url: str,
    measurement_sites: list[str] | None = None,
    pool=None,
) -> list[dict]:
    """
    Run one complete agent session.

    Parameters
    ----------
    trial_id       : shared trial identifier (same for paired agents)
    zip_condition  : label, e.g. 'poor_zip' or 'rich_zip'
    intent_profile : one of ACTIVE_INTENT_PROFILES
    proxy_url      : full proxy URL (http://user:pass@host:port)

    Returns
    -------
    List of ad observation dicts ready for DB insertion.
    """
    agent_id = str(uuid.uuid4())
    observations: list[dict] = []
    visit_telemetry: list[dict] = []

    _proxy_cfg = build_playwright_proxy_config(proxy_url)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            proxy=_proxy_cfg,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
                # Suppress automation infobar
                "--disable-infobars",
                # Reduce memory pressure
                "--renderer-process-limit=4",
            ],
        )

        _ctx_params = _random_context_params()
        context: BrowserContext = await browser.new_context(
            storage_state=None,
            # Local/upstream_mitm use mitmproxy to inject headers, which performs TLS MITM.
            # In those modes we allow TLS interception to avoid all navigations failing.
            ignore_https_errors=PROXY_MODE in ("local", "upstream_mitm"),
            locale=_ctx_params["locale"],
            timezone_id=_ctx_params["timezone_id"],
            viewport=_ctx_params["viewport"],
            user_agent=_ctx_params["user_agent"],
            # Mimic real browser headers
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "DNT": "1",
            },
        )

        # Inject stealth JS before any page script runs
        await context.add_init_script(_STEALTH_JS)

        page: Page = await context.new_page()
        request_context: dict[str, object | None] = {
            "phase": None,
            "measurement_site": None,
            "captured": None,
        }

        def on_any_request(request):
            url = request.url
            is_ad = is_ad_request(url)
            captured = request_context.get("captured")
            if captured is not None and is_ad:
                captured.append(url)

        page.on("request", on_any_request)

        async def _record_visit(
            *,
            phase: str,
            target_url: str,
            measurement_site: str | None = None,
            status_code: int | None = None,
            final_url: str | None = None,
            page_load_time_ms: int | None = None,
            error: Exception | None = None,
        ) -> None:
            if not ENABLE_TELEMETRY:
                return
            try:
                cookie_count = len(await context.cookies())
            except Exception:
                cookie_count = None
            visit_telemetry.append(
                {
                    "trial_id": trial_id,
                    "agent_id": agent_id,
                    "zip_condition": zip_condition,
                    "intent_profile": intent_profile,
                    "phase": phase,
                    "measurement_site": measurement_site,
                    "target_url": target_url,
                    "final_url": final_url,
                    "status_code": status_code,
                    "error_type": type(error).__name__ if error else None,
                    "error_text": (str(error)[:500] if error else None),
                    "page_load_time_ms": page_load_time_ms,
                    "cookie_count": cookie_count,
                }
            )

        # ── 0. Warming phase ─────────────────────────────────────────────────
        # Visit persona-defining URLs to establish cookie history and first-party
        # identifiers. This makes intent profiles distinguishable to ad networks.
        warming_script = WARMING_URLS.get(intent_profile, [])
        for url in warming_script:
            request_context.update(
                {"phase": "warming", "measurement_site": None, "captured": None}
            )
            try:
                resp = await page.goto(
                    url, wait_until="domcontentloaded", timeout=30_000
                )
                await _record_visit(
                    phase="warming",
                    target_url=url,
                    status_code=(resp.status if resp is not None else None),
                    final_url=(resp.url if resp is not None else page.url),
                )
                await page.wait_for_timeout(_jitter(WARMING_DWELL_MS))
                try:
                    await page.evaluate(
                        "window.scrollTo({ top: document.body.scrollHeight * 0.3, behavior: 'smooth' })"
                    )
                    await page.wait_for_timeout(random.randint(300, 700))
                except Exception:
                    pass
            except Exception as e:
                await _record_visit(
                    phase="warming",
                    target_url=url,
                    status_code=None,
                    final_url=page.url,
                    error=e,
                )
                pass

        # ── 1. Behavioural conditioning ──────────────────────────────────────
        behavior_script = INTENT_PROFILES[intent_profile]
        for url in behavior_script:
            request_context.update(
                {"phase": "conditioning", "measurement_site": None, "captured": None}
            )
            try:
                resp = await page.goto(
                    url, wait_until="domcontentloaded", timeout=30_000
                )
                await _record_visit(
                    phase="conditioning",
                    target_url=url,
                    status_code=(resp.status if resp is not None else None),
                    final_url=(resp.url if resp is not None else page.url),
                )
                await page.wait_for_timeout(_jitter(DWELL_TIME_MS))
                try:
                    await page.evaluate(
                        "window.scrollTo({ top: document.body.scrollHeight * 0.4, behavior: 'smooth' })"
                    )
                    await page.wait_for_timeout(random.randint(400, 900))
                except Exception:
                    pass
            except Exception as e:
                await _record_visit(
                    phase="conditioning",
                    target_url=url,
                    status_code=None,
                    final_url=page.url,
                    error=e,
                )
                pass

        if ENABLE_GOOGLE_SEARCH_MEASUREMENT:
            observations.extend(
                await collect_google_search_ads(
                    page=page,
                    trial_id=trial_id,
                    agent_id=agent_id,
                    zip_condition=zip_condition,
                    intent_profile=intent_profile,
                )
            )

        # ── 2. Measurement phase ─────────────────────────────────────────────
        sites = (
            list(measurement_sites)
            if measurement_sites is not None
            else sites_for_trial(trial_id)
        )
        for site in sites:
            captured: list[str] = []
            request_context.update(
                {"phase": "measurement", "measurement_site": site, "captured": captured}
            )
            page_load_time_ms = None
            nav_status: int | None = None
            nav_final_url: str | None = None
            nav_error: Exception | None = None

            try:
                resp = await page.goto(
                    site, wait_until="domcontentloaded", timeout=30_000
                )
                nav_status = resp.status if resp is not None else None
                nav_final_url = resp.url if resp is not None else page.url

                # Capture page load timing via Navigation Timing API
                try:
                    timing = await page.evaluate("""
                        () => {
                            const nav = performance.getEntriesByType("navigation")[0];
                            return nav ? nav.domInteractive : null;
                        }
                    """)
                    if timing is not None:
                        page_load_time_ms = int(timing)
                except Exception:
                    pass

                # Randomised dwell with human-like scroll to trigger lazy ad slots
                await page.wait_for_timeout(_jitter(AD_DWELL_MS))
                try:
                    await page.evaluate(
                        "window.scrollTo({ top: document.body.scrollHeight * 0.5, behavior: 'smooth' })"
                    )
                    await page.wait_for_timeout(random.randint(800, 1800))
                    await page.evaluate(
                        "window.scrollTo({ top: document.body.scrollHeight * 0.2, behavior: 'smooth' })"
                    )
                    await page.wait_for_timeout(random.randint(300, 700))
                except Exception:
                    pass
            except Exception as e:
                nav_error = e
                nav_final_url = page.url
                pass

            await _record_visit(
                phase="measurement",
                target_url=site,
                measurement_site=site,
                status_code=nav_status,
                final_url=nav_final_url,
                page_load_time_ms=page_load_time_ms,
                error=nav_error,
            )
            page_title, screenshot_path, dom_snippet = await capture_page_context(
                page=page,
                trial_id=trial_id,
                zip_condition=zip_condition,
                intent_profile=intent_profile,
                measurement_site=site,
                pool=pool,
            )

            for ad_url in captured:
                domain = extract_domain(ad_url)
                observations.append(
                    {
                        "trial_id": trial_id,
                        "agent_id": agent_id,
                        "zip_condition": zip_condition,
                        "ad_url": ad_url,
                        "ad_domain": domain,
                        "ad_network": classify_network(domain),
                        "measurement_site": site,
                        "source_type": "network_request",
                        "intent_profile": intent_profile,
                        "query_topic": None,
                        "search_query": None,
                        "ad_headline": None,
                        "ad_description": None,
                        "advertiser_name": None,
                        "landing_url": None,
                        "landing_domain": None,
                        "inferred_topic": infer_topic(ad_url, domain),
                        "page_title": page_title,
                        "page_url": site,
                        "screenshot_path": screenshot_path,
                        "dom_snippet": dom_snippet,
                        "page_load_time_ms": page_load_time_ms,
                    }
                )

            request_context.update(
                {"phase": None, "measurement_site": None, "captured": None}
            )

        page.remove_listener("request", on_any_request)
        await browser.close()

    if ENABLE_TELEMETRY and pool is not None and visit_telemetry:
        try:
            await db.insert_page_visits(pool, visit_telemetry)
        except Exception:
            pass

    return observations
