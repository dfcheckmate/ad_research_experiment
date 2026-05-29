"""Experiment orchestrator — runs N trials across proxy identities × intent profiles."""

from __future__ import annotations

import argparse
import asyncio
import traceback
import uuid
from urllib.parse import urlparse

from tqdm.asyncio import tqdm

import db
from agent import run_agent
from config import (
    ACTIVE_INTENT_PROFILES,
    CONCURRENCY,
    N_TRIALS,
    PROXIES,
    PROXY_IDENTITY_META,
    PROXY_MODE,
    UPSTREAM_PROXY,
    sites_for_trial,
)
from logging_config import configure_logging, get_logger, log_result
from proxy_manager import ProxyManager

configure_logging()
logger = get_logger(__name__)

_browser_sem: asyncio.Semaphore | None = None
_VERBOSE = False


def _redact_proxy_url(url: str) -> str:
    try:
        p = urlparse(url)
        if not p.scheme:
            return url
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        return f"{p.scheme}://{host}{port}"
    except Exception:
        return "<proxy>"


def _short_id(trial_id: str) -> str:
    return trial_id[:8]


async def run_trial(pool, trial_id: str) -> int:
    sid = _short_id(trial_id)
    measurement_sites = sites_for_trial(trial_id)
    trial_meta = {
        "paired_block_id": trial_id,
        "proxy_mode": PROXY_MODE,
        "proxy_identities": sorted(PROXIES.keys()),
        "intent_profiles": list(ACTIVE_INTENT_PROFILES),
        "measurement_sites": measurement_sites,
        "measurement_site_count": len(measurement_sites),
    }

    await db.ensure_trial(pool, trial_id, meta=trial_meta)

    all_obs: list[dict] = []

    for intent_profile in ACTIVE_INTENT_PROFILES:
        if _VERBOSE:
            tqdm.write(f"[{sid}] {intent_profile} — launching {len(PROXIES)} proxies")

        async def _run_one(label: str, url: str) -> list[dict]:
            async with _browser_sem:
                return await run_agent(
                    trial_id,
                    label,
                    intent_profile,
                    url,
                    measurement_sites=measurement_sites,
                    pool=pool,
                )

        proxy_runs = list(PROXIES.items())
        tasks = [_run_one(zip_label, proxy_url) for zip_label, proxy_url in proxy_runs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        profile_obs = 0
        for (zip_label, _proxy_url), r in zip(proxy_runs, results):
            if isinstance(r, Exception):
                logger.warning("%s/%s/%s: %s: %s", trial_id, intent_profile, zip_label, type(r).__name__, r)
                traceback.print_exc()
            else:
                profile_obs += len(r)
                all_obs.extend(r)

        if _VERBOSE:
            tqdm.write(f"[{sid}] {intent_profile} — {profile_obs} ads collected")

    if not all_obs:
        raise RuntimeError(f"trial {trial_id} completed with no observations")

    await db.insert_observations(pool, all_obs)
    return len(all_obs)


MAX_TRIAL_RETRIES = 2


async def worker(queue: asyncio.Queue, pool, results: list[int]) -> None:
    while True:
        trial_id = await queue.get()
        sid = _short_id(trial_id)
        try:
            for attempt in range(MAX_TRIAL_RETRIES + 1):
                try:
                    n = await run_trial(pool, trial_id)
                    results.append(n)
                    if _VERBOSE:
                        tqdm.write(f"[{sid}] complete — {n} ads")
                    break
                except Exception as e:
                    if attempt == MAX_TRIAL_RETRIES:
                        logger.error("%s failed after %d attempts: %s", trial_id, MAX_TRIAL_RETRIES + 1, e)
                        results.append(0)
                    else:
                        backoff = 5 * (attempt + 1)
                        logger.warning("%s attempt %d failed, retry in %ds: %s", trial_id, attempt + 1, backoff, e)
                        await asyncio.sleep(backoff)
        finally:
            queue.task_done()


async def main(n_trials: int, concurrency: int, max_browsers: int) -> None:
    global _browser_sem
    _browser_sem = asyncio.Semaphore(max_browsers)
    logger.info("max simultaneous Chromium processes = %d", max_browsers)

    use_local_proxies = PROXY_MODE in ("local", "upstream_mitm")
    proxy_mgr = ProxyManager(upstream_proxy=UPSTREAM_PROXY) if use_local_proxies else None

    if proxy_mgr:
        logger.info("proxy mode = %s — starting local mitmdump", PROXY_MODE)
        proxy_mgr.start()
        await asyncio.sleep(2)
        print("Proxy URLs:")
        for label, url in proxy_mgr.proxy_urls.items():
            print(f"  {label:10s} → {url}")
    else:
        logger.info("proxy mode = %s — using external proxies", PROXY_MODE)
        for label, url in PROXIES.items():
            meta = PROXY_IDENTITY_META.get(label, {})
            city_asn = ""
            if meta:
                city_asn = f" ({meta.get('city', '?')}, {meta.get('state', '?')} {meta.get('asn', '?')} {meta.get('isp', '?')})"
            print(f"  {label:12s} → {_redact_proxy_url(url)}{city_asn}")

    pool = await db.get_pool(min_size=2, max_size=concurrency + 2)
    await db.init_db(pool)

    queue: asyncio.Queue = asyncio.Queue()
    for _ in range(n_trials):
        await queue.put(str(uuid.uuid4()))

    logger.info(
        "%d trials × %d proxy identities × %d intent profiles, concurrency=%d, max_browsers=%d",
        n_trials, len(PROXIES), len(ACTIVE_INTENT_PROFILES), concurrency, max_browsers
    )

    results: list[int] = []
    workers = [asyncio.create_task(worker(queue, pool, results)) for _ in range(concurrency)]

    with tqdm(total=n_trials, desc="trials") as pbar:
        done = 0
        while done < n_trials:
            await asyncio.sleep(1)
            current = len(results)
            pbar.update(current - done)
            done = current

    await queue.join()
    for w in workers:
        w.cancel()

    await pool.close()

    if proxy_mgr:
        proxy_mgr.stop()

    total_ads = sum(results)
    if total_ads == 0:
        raise RuntimeError("experiment completed without any ad observations")

    log_result(f"\n{n_trials} trials complete. Total ad observations: {total_ads}")
    log_result("Generate the causal estimates with:")
    log_result(
        "docker run --rm \\\n"
        '  -v "$PWD/out:/out" \\\n'
        "  --env-file .env \\\n"
        "  -e DB_URL=sqlite:////out/ads.db \\\n"
        "  ad-research-experiment:local \\\n"
        "  src/analysis.py --output /out/results"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ad-targeting audit experiment")
    parser.add_argument("--trials", type=int, default=N_TRIALS, help="Number of trials")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=CONCURRENCY,
        help="Parallel trial workers",
    )
    parser.add_argument(
        "--max-browsers",
        type=int,
        default=None,
        help="Hard cap on simultaneous Chromium processes",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show per-profile and per-trial progress details",
    )
    args = parser.parse_args()

    globals()["_VERBOSE"] = args.verbose

    n_proxies = len(PROXIES)
    default_max = min(args.concurrency * n_proxies, 6)
    max_browsers = args.max_browsers if args.max_browsers is not None else default_max

    asyncio.run(main(args.trials, args.concurrency, max_browsers))
