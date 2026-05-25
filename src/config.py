"""Experiment configuration.

Proxy modes (set `PROXY_MODE` in `.env` or the environment):

- `residential` (default): Three static residential ISP proxies (robustness/generalization).
- `local`: Two local `mitmdump` instances with per-condition geo-header injection.
- `socks5`: Two direct SOCKS5 endpoints (`PROXY_POOR`/`PROXY_RICH`).
- `upstream`: Two direct HTTP proxy endpoints (`PROXY_POOR`/`PROXY_RICH`).
- `upstream_mitm`: Local `mitmdump` instances that chain through `UPSTREAM_PROXY`.
"""

import json
import os
import hashlib
from urllib.parse import urlsplit, urlunsplit
from dotenv import load_dotenv

load_dotenv()

# ── Database ──────────────────────────────────────────────────────────────────
# Default: SQLite file in the project directory (zero extra config).
# Override with a postgresql:// URL once you have PostgreSQL available.
DB_URL = os.getenv("DB_URL", "sqlite:///ads.db")

# ── Proxy mode ───────────────────────────────────────────────────────────────
# residential | local | socks5 | upstream | upstream_mitm
PROXY_MODE = os.getenv("PROXY_MODE", "residential")

# ── Local mitmdump ports (used when PROXY_MODE = local | upstream_mitm) ──────
PROXY_POOR_PORT = int(os.getenv("PROXY_POOR_PORT", "8181"))
PROXY_RICH_PORT = int(os.getenv("PROXY_RICH_PORT", "8182"))

# ── Legacy 2-proxy URLs (used when PROXY_MODE = upstream | socks5) ───────────
PROXY_POOR_URL = os.getenv("PROXY_POOR", f"http://127.0.0.1:{PROXY_POOR_PORT}")
PROXY_RICH_URL = os.getenv("PROXY_RICH", f"http://127.0.0.1:{PROXY_RICH_PORT}")

# Optional upstream to chain through mitmdump (upstream_mitm mode)
UPSTREAM_PROXY = os.getenv("UPSTREAM_PROXY", None)

# ── Residential ISP proxy identities ─────────────────────────────────────────
# Three static residential proxies — each represents one isolated household.
# The URL scheme controls transport: http:// or socks5://
# Format: http://user:pass@proxy-host:port or socks5://user:pass@proxy-host:port
#
# Default labels are generic to avoid implying geographic control when your
# provider assigns endpoints opportunistically.
RESIDENTIAL_PROXY_DEFAULTS: dict[str, str] = {
    "res_1": os.getenv("PROXY_RESIDENTIAL_1", "") or os.getenv("PROXY_OREM_UT", ""),
    "res_2": os.getenv("PROXY_RESIDENTIAL_2", "") or os.getenv("PROXY_BOSTON_MA", ""),
    "res_3": os.getenv("PROXY_RESIDENTIAL_3", "") or os.getenv("PROXY_NYC_NY", ""),
}

# Metadata for each proxy identity (optional; used for reporting only).
# Set PROXY_IDENTITY_META_JSON to a JSON dict like:
#   {"res_1": {"city": "...", "state": "..."}, "res_2": {...}, "res_3": {...}}
_meta_raw = os.getenv("PROXY_IDENTITY_META_JSON", "")
if _meta_raw:
    try:
        PROXY_IDENTITY_META: dict[str, dict] = json.loads(_meta_raw) or {}
    except Exception:
        PROXY_IDENTITY_META = {}
else:
    PROXY_IDENTITY_META = {}


# ── Resolved proxy map (used by agent.py and experiment.py) ──────────────────
def build_proxy_map() -> dict[str, str]:
    """
    Returns the PROXIES dict consumed by agent.py.
    Keys are identity labels; values are proxy URLs (http:// or socks5://).
    """
    if PROXY_MODE == "residential":
        return dict(RESIDENTIAL_PROXY_DEFAULTS)
    if PROXY_MODE in ("local", "upstream_mitm"):
        # Lab / offline dev: 2 local mitmdump instances with header spoofing.
        return {
            "poor_zip": f"http://127.0.0.1:{PROXY_POOR_PORT}",
            "rich_zip": f"http://127.0.0.1:{PROXY_RICH_PORT}",
        }
    # socks5 or upstream (legacy 2-proxy path)
    return {
        "poor_zip": PROXY_POOR_URL,
        "rich_zip": PROXY_RICH_URL,
    }


PROXIES = build_proxy_map()

# ── Warming phase URLs ──────────────────────────────────────────────────────
# Persona-defining sites visited BEFORE behavioral conditioning to establish
# cookie history and first-party identifiers. These sites drop tracking cookies
# that make the intent profiles distinguishable to ad networks.
WARMING_URLS = {
    "high_income": [
        "https://www.bloomberg.com/",
        "https://www.robbreport.com/",
        "https://www.charterworld.com/",
        "https://www.wsj.com/",
        "https://www.architecturaldigest.com/",
    ],
    "low_income": [
        "https://www.frugalcouponliving.com/",
        "https://www.benefits.gov/",
        "https://www.daveramsey.com/",
        "https://www.povertyusa.org/",
        "https://www.indeed.com/",
    ],
    "neutral": [
        "https://www.wikipedia.org/",
        "https://www.weather.com/",
        "https://www.britannica.com/",
    ],
}

# ── Behaviour / intent profiles ──────────────────────────────────────────────
# Each profile is a fixed, repeatable browsing script used before measurement.
INTENT_PROFILES = {
    "high_income": [
        "https://www.zillow.com/homes/for_sale/",
        "https://www.bankrate.com/mortgages/mortgage-calculator/",
        "https://www.investopedia.com/retirement-planning-4689695",
        "https://www.nerdwallet.com/mortgages",
        "https://www.fool.com/investing/",
    ],
    "low_income": [
        "https://www.nerdwallet.com/article/finance/how-to-budget",
        "https://www.bankrate.com/loans/personal-loans/",
        "https://www.creditkarma.com/personal-loans/i/personal-loans-for-bad-credit",
        "https://www.indeed.com/career-advice/pay-salary/minimum-wage-by-state",
        "https://www.gobankingrates.com/saving-money/budgeting/",
    ],
    "neutral": [
        "https://www.wikipedia.org/",
        "https://www.weather.com/",
        "https://www.britannica.com/",
        "https://www.nationalgeographic.com/",
        "https://www.reuters.com/world/us/",
    ],
}

ACTIVE_INTENT_PROFILES = ["high_income", "low_income", "neutral"]

# ── Representative measurement site strata ──────────────────────────────────
# Trials should measure against a representative slice of the Dutch web, not
# only ad-heavy pages, so each proxy identity sees the same balanced cross-
# section of news, commerce, regional, utility, and niche domains.
DEFAULT_AD_SITE_STRATA = {
    "news": [
        "https://www.nu.nl",
        "https://www.trouw.nl",
        "https://www.volkskrant.nl",
        "https://www.ad.nl",
        "https://www.telegraaf.nl",
        "https://www.nrc.nl",
        "https://www.parool.nl",
        "https://www.rtlnieuws.nl",
        "https://www.blikopnieuws.nl",
        "https://www.metronieuws.nl",
        "https://www.bd.nl",
        "https://www.z24.nl",
        "https://www.limburger.nl",
        "https://www.dutchnews.nl",
    ],
    "commerce": [
        "https://www.marktplaats.nl",
        "https://www.bol.com",
        "https://www.coolblue.nl",
        "https://www.wehkamp.nl",
        "https://www.funda.nl",
    ],
    "regional": [
        "https://www.omroepbrabant.nl",
        "https://www.omroepgelderland.nl",
        "https://www.rijnmond.nl",
        "https://www.vi.nl",
        "https://www.nufoto.nl",
    ],
    "utility": [
        "https://www.rijksoverheid.nl",
        "https://www.kvk.nl",
        "https://www.postnl.nl",
        "https://www.ns.nl",
        "https://www.veiligheid.nl",
        "https://www.ns.nl/reisinformatie",
    ],
    "niche": [
        "https://www.techpulse.nl",
        "https://www.retailtrends.nl",
        "https://www.emerce.nl",
        "https://www.adformatie.nl",
        "https://www.marketingtribune.nl",
        "https://www.zorgvisie.nl",
        "https://www.expatica.com/nl",
        "https://www.fok.nl",
        "https://www.nujij.nl",
        "https://www.sporza.be",
        "https://www.hln.be",
        "https://www.flightlevel.nl",
        "https://www.iamexpat.nl",
        "https://www.nu.nl/binnenland",
        "https://www.nu.nl/buitenland",
        "https://www.nu.nl/tech",
        "https://www.nu.nl/economie",
        "https://www.nu.nl/sport",
        "https://www.ad.nl/utrecht",
    ],
}


def _split_env_list(value: str) -> list[str]:
    parts: list[str] = []
    for chunk in value.replace(",", "\n").splitlines():
        item = chunk.strip()
        if item:
            parts.append(item)
    return parts


def _normalize_site_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""

    if "://" not in value:
        value = f"https://{value}"

    parsed = urlsplit(value)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    if not netloc:
        return ""

    return urlunsplit((scheme, netloc, path, "", ""))


def _normalize_site_pool(values: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for site in values:
        url = _normalize_site_url(site)
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized


def _load_ad_site_pool(env_key: str, default_values: list[str]) -> list[str]:
    raw = os.getenv(env_key, "")
    raw_sites = _split_env_list(raw) if raw else []
    candidates = raw_sites or list(default_values)
    return _normalize_site_pool(candidates)


def _load_stratified_site_pool() -> dict[str, list[str]]:
    strata: dict[str, list[str]] = {}
    for stratum, defaults in DEFAULT_AD_SITE_STRATA.items():
        env_key = f"AD_SITE_POOL_{stratum.upper()}"
        strata[stratum] = _load_ad_site_pool(env_key, defaults)
    return strata


AD_SITE_STRATA = _load_stratified_site_pool()
AD_SITE_POOL = [site for domains in AD_SITE_STRATA.values() for site in domains]
MEASUREMENT_SITES_PER_TRIAL = int(os.getenv("MEASUREMENT_SITES_PER_TRIAL", "10"))

# Backward-compatible alias used by older tests/code paths. The runtime now uses
# `sites_for_trial(...)` for deterministic stratified sampling.
AD_SITES = list(AD_SITE_POOL)


def _hash_to_int(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest(), 16)


def sites_for_trial(trial_id: str) -> list[str]:
    """Return a deterministic stratified site subset for a given trial.

    All proxy identities in the same trial see the same representative site mix.
    Counts are allocated approximately proportional to stratum size with a
    minimum of one site per non-empty stratum when feasible.
    """
    strata = {k: list(v) for k, v in AD_SITE_STRATA.items() if v}
    if not strata:
        return []

    total_domains = sum(len(v) for v in strata.values())
    sample_n = max(1, min(MEASUREMENT_SITES_PER_TRIAL, total_domains))

    ordered = sorted(strata.items())
    counts: dict[str, int] = {k: 0 for k, _ in ordered}
    allocated = 0

    # Base proportional allocation with at least one per non-empty stratum when possible.
    for stratum, domains in ordered:
        if allocated >= sample_n:
            break
        proportion = len(domains) / total_domains
        count = int(round(sample_n * proportion))
        if sample_n >= len(ordered):
            count = max(1, count)
        count = min(count, len(domains))
        counts[stratum] = count
        allocated += count

    # Top up any shortfall using strata with spare capacity.
    if allocated < sample_n:
        for stratum, domains in ordered:
            while allocated < sample_n and counts[stratum] < len(domains):
                counts[stratum] += 1
                allocated += 1
            if allocated >= sample_n:
                break

    # Trim over-allocation from the largest strata first if rounding overshot.
    if allocated > sample_n:
        for stratum, _domains in sorted(ordered, key=lambda item: len(item[1]), reverse=True):
            while allocated > sample_n and counts[stratum] > 0:
                counts[stratum] -= 1
                allocated -= 1
            if allocated <= sample_n:
                break

    selected: list[str] = []
    for stratum, domains in ordered:
        count = counts[stratum]
        if count <= 0:
            continue
        offset = _hash_to_int(f"{trial_id}:{stratum}") % len(domains)
        rotated = domains[offset:] + domains[:offset]
        selected.extend(rotated[:count])

    return selected[:sample_n]

# ── Experiment parameters ────────────────────────────────────────────────────
N_TRIALS = 200  # number of trials
WARMING_DWELL_MS = 4_000  # ms to spend on each warming phase URL
DWELL_TIME_MS = 6_000  # ms to spend on each behaviour page
AD_DWELL_MS = 8_000  # ms to spend on each measurement site
GOOGLE_DWELL_MS = 6_000  # ms to spend on each Google query results page
# Keep concurrency low to avoid OOM with 3 residential proxies.
# 2 trial workers × 3 proxies = 6 Chromium processes at peak (with --max-browsers 6).
# Drop to 1 on hosts with < 12 GB RAM.
CONCURRENCY = 2
HEADLESS = True
CAPTURE_SCREENSHOTS = os.getenv("CAPTURE_SCREENSHOTS", "1") == "1"
CAPTURE_DOM_SNIPPETS = os.getenv("CAPTURE_DOM_SNIPPETS", "0") == "1"
CAPTURES_DIR = os.getenv("CAPTURES_DIR", "captures")
# ↑ Path is relative to PROJECT ROOT (not src/ directory)

# ── Telemetry (debugging / QC) ───────────────────────────────────────────────
# When enabled, the agent records per-navigation metadata (HTTP status, final URL,
# cookie count, navigation failures) into the DB. This does not change what ads
# are captured; it's for diagnosing rate limiting / blocking differences by proxy.
ENABLE_TELEMETRY = os.getenv("ENABLE_TELEMETRY", "0") == "1"

# ── Google search measurement ────────────────────────────────────────────────
ENABLE_GOOGLE_SEARCH_MEASUREMENT = (
    os.getenv("ENABLE_GOOGLE_SEARCH_MEASUREMENT", "0") == "1"
)
GOOGLE_SEARCH_URL_TEMPLATE = (
    "https://www.google.com/search?q={query}&gl=us&hl=en&pws=0&num=10"
)
QUERIES_PER_TOPIC_PER_TRIAL = int(os.getenv("QUERIES_PER_TOPIC_PER_TRIAL", "1"))

# Topic-based search queries. These are high-intent queries designed to surface
# ads tied to purchasing power, financial intent, or lifestyle segmentation.
TOPIC_QUERY_SETS = {
    "Books & Literature": [
        "best ebook subscription for avid readers",
        "buy hardcover collector editions online",
    ],
    "Food": [
        "meal delivery service order online",
        "premium grocery delivery near me",
    ],
    "Clothing": [
        "buy designer clothing online",
        "best clothing stores for workwear",
    ],
    "Home Furniture": [
        "buy luxury sectional sofa online",
        "best financing for bedroom furniture",
    ],
    "Mobile Phones & Accessories": [
        "buy iphone pro max with trade in",
        "best unlimited mobile plan premium phone",
    ],
    "Women's Clothing": [
        "buy women's designer dresses online",
        "women's workwear brands free shipping",
    ],
    "Games": [
        "buy gaming laptop for competitive gaming",
        "best gaming chair financing online",
    ],
    "Vitamins & Supplements": [
        "buy vitamins online subscription",
        "best supplements for daily health",
    ],
    "Laptop Computers": [
        "buy business laptop with financing",
        "best premium laptop for remote work",
    ],
    "Kitchen & Dining": [
        "buy cookware set online premium",
        "best kitchen appliance deals financing",
    ],
    "Hotels, Motels & Resorts": [
        "book luxury resort in miami",
        "best hotel deals with free cancellation",
    ],
    "Investing": [
        "open brokerage account for investing",
        "best robo advisor for taxable account",
    ],
    "Banking": [
        "best high yield savings account",
        "open checking account bonus online",
    ],
    "Pets & Animals": [
        "pet insurance compare plans online",
        "buy premium dog food subscription",
    ],
    "Building Construction & Maintenance": [
        "home renovation loan contractors near me",
        "roof replacement financing estimate",
    ],
    "Photo Software": [
        "buy photo editing software subscription",
        "best software for photographers online",
    ],
    "Restaurants": [
        "fine dining reservations near me",
        "restaurant delivery app promo code",
    ],
    "Music & Audio": [
        "buy premium headphones online",
        "best music streaming family plan",
    ],
    "Motor Vehicles": [
        "buy suv with financing near me",
        "best lease deals luxury sedan",
    ],
    "Social Networks & Online Communities": [
        "best creator platform monetization tools",
        "professional networking premium subscription",
    ],
}

# Default active subset focused on income-sensitive commercial intent.
ACTIVE_QUERY_TOPICS = [
    "Investing",
    "Banking",
    "Hotels, Motels & Resorts",
    "Motor Vehicles",
    "Home Furniture",
    "Laptop Computers",
    "Building Construction & Maintenance",
    "Mobile Phones & Accessories",
]

# Lightweight keyword map used to infer ad topic from ad text / landing domain.
TOPIC_KEYWORDS = {
    "Investing": ["brokerage", "invest", "stock", "etf", "advisor", "wealth"],
    "Banking": ["bank", "checking", "savings", "loan", "credit card", "mortgage"],
    "Hotels, Motels & Resorts": ["hotel", "resort", "booking", "travel", "vacation"],
    "Motor Vehicles": ["car", "truck", "suv", "lease", "dealer", "auto"],
    "Home Furniture": ["sofa", "mattress", "furniture", "table", "chair"],
    "Laptop Computers": ["laptop", "notebook", "macbook", "thinkpad", "dell"],
    "Building Construction & Maintenance": [
        "contractor",
        "roof",
        "renovation",
        "hvac",
        "plumbing",
    ],
    "Mobile Phones & Accessories": ["iphone", "galaxy", "phone", "wireless", "carrier"],
    "Kitchen & Dining": ["cookware", "kitchen", "appliance", "dining"],
    "Vitamins & Supplements": ["vitamin", "supplement", "protein", "wellness"],
    "Music & Audio": ["headphones", "speaker", "audio", "streaming"],
    "Books & Literature": ["book", "ebook", "audiobook", "literature"],
    "Clothing": ["clothing", "apparel", "fashion", "wear"],
    "Women's Clothing": ["dress", "women", "workwear", "blouse"],
    "Games": ["gaming", "game", "console", "esports"],
    "Pets & Animals": ["pet", "dog", "cat", "insurance"],
    "Photo Software": ["photo", "editing", "lightroom", "photoshop"],
    "Restaurants": ["restaurant", "dining", "food delivery", "takeout"],
    "Social Networks & Online Communities": [
        "community",
        "creator",
        "networking",
        "social",
    ],
}

# ── Known ad-network domains (for classification) ───────────────────────────
AD_NETWORK_PATTERNS = [
    "doubleclick.net",
    "googlesyndication.com",
    "amazon-adsystem.com",
    "adsystem.com",
    "adservice.google",
    "scorecardresearch.com",
    "taboola.com",
    "outbrain.com",
    "pubmatic.com",
    "rubiconproject.com",
    "openx.net",
    "appnexus.com",
    "criteo.com",
    "moatads.com",
    "adsrvr.org",
    "advertising.com",
    "adnxs.com",
]
