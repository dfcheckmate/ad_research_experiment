"""Minimal Playwright proxy probe.

This isolates Chromium/Playwright proxy behavior from the experiment logic.

Example:
  .venv/bin/python scripts/playwright_proxy_probe.py \
    --proxy 'socks5h://user:pass@host:10001' \
    --url https://www.cnn.com/
"""

from __future__ import annotations

import argparse
import asyncio
from urllib.parse import urlparse

from playwright.async_api import async_playwright


def build_playwright_proxy_config(proxy_url: str) -> dict:
    """Match the repo's current Playwright proxy config behavior."""
    parsed = urlparse(proxy_url)
    scheme = (parsed.scheme or "").lower()

    if scheme == "socks5h":
        proxy_url = proxy_url.replace("socks5h://", "socks5://", 1)
        parsed = urlparse(proxy_url)
        scheme = "socks5"

    if scheme.startswith("socks"):
        return {"server": proxy_url}

    if not parsed.hostname or not parsed.port or not parsed.scheme:
        raise ValueError(f"Invalid proxy URL: {proxy_url!r}")

    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    cfg: dict[str, str] = {"server": server}
    if parsed.username:
        cfg["username"] = parsed.username
    if parsed.password:
        cfg["password"] = parsed.password
    return cfg


async def main(proxy: str, url: str, ignore_https_errors: bool) -> int:
    proxy_cfg = build_playwright_proxy_config(proxy)
    print("proxy_cfg", proxy_cfg)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            proxy=proxy_cfg,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(ignore_https_errors=ignore_https_errors)
        page = await context.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            print("status", resp.status if resp else None)
            print("final_url", resp.url if resp else page.url)
            title = await page.title()
            print("title", title)
            print("page_url", page.url)
            await browser.close()
            return 0
        except Exception as e:
            print("error_type", type(e).__name__)
            print("error", str(e))
            print("page_url", page.url)
            await browser.close()
            return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Probe Playwright proxy behavior")
    parser.add_argument("--proxy", required=True, help="Proxy URL")
    parser.add_argument("--url", default="https://www.cnn.com/", help="Target URL")
    parser.add_argument(
        "--ignore-https-errors",
        action="store_true",
        help="Set ignore_https_errors on the browser context",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.proxy, args.url, args.ignore_https_errors)))
