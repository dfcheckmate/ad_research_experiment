"""Proxy manager for local mitmproxy mode.

Starts and stops two local ``mitmdump`` instances (one per ZIP condition). Optionally
chains through an upstream proxy.

Usage (context manager)::

    async with ProxyManager() as pm:
        proxies = pm.proxy_urls

Usage (standalone): ``python src/proxy_manager.py [--socks5 socks5://host:port]``

Addon script: ``src/proxies/geoaddon.py``
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# ── Port assignments ─────────────────────────────────────────────────────────
PROXY_PORTS = {
    "poor_zip": int(os.getenv("PROXY_POOR_PORT", "8181")),
    "rich_zip": int(os.getenv("PROXY_RICH_PORT", "8182")),
}

ADDON_PATH = Path(__file__).parent / "proxies" / "geoaddon.py"

# Path to mitmdump in the sibling venv
MITMDUMP = Path(__file__).parent.parent / "venv" / "bin" / "mitmdump"
if not MITMDUMP.exists():
    # Fallback: search PATH
    MITMDUMP = "mitmdump"


class ProxyManager:
    """
    Async context manager that owns two mitmdump subprocesses.

    Parameters
    ----------
    upstream_proxy : str | None
        Optional upstream HTTP/SOCKS5 proxy URL, e.g.

          "socks5://127.0.0.1:1080"

          "http://user:pass@host:8080"

        When set, mitmdump forwards all traffic through it, giving you
        a real different exit IP per chain if the upstream supports it.
    """

    def __init__(self, upstream_proxy: str | None = None):
        self.upstream_proxy = upstream_proxy or os.getenv("UPSTREAM_PROXY")
        self._procs: dict[str, subprocess.Popen] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def proxy_urls(self) -> dict[str, str]:
        return {
            label: f"http://127.0.0.1:{port}" for label, port in PROXY_PORTS.items()
        }

    async def __aenter__(self) -> "ProxyManager":
        self.start()
        await asyncio.sleep(1.5)  # give mitmdump a moment to bind
        return self

    async def __aexit__(self, *_) -> None:
        self.stop()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        for label, port in PROXY_PORTS.items():
            cmd = self._build_cmd(label, port)
            print(f"[proxy_manager] starting {label} on port {port}")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid,
            )
            self._procs[label] = proc
            print(f"[proxy_manager] {label} PID={proc.pid}")

    def stop(self) -> None:
        for label, proc in self._procs.items():
            print(f"[proxy_manager] stopping {label} (PID={proc.pid})")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        self._procs.clear()

    # ── Command builder ───────────────────────────────────────────────────────

    def _build_cmd(self, zip_label: str, port: int) -> list[str]:
        cmd = [
            str(MITMDUMP),
            "--listen-port",
            str(port),
            "--listen-host",
            "127.0.0.1",
            "--scripts",
            str(ADDON_PATH),
            "--set",
            f"zip_label={zip_label}",
            # SSL: trust / ignore cert errors for self-signed targets
            "--ssl-insecure",
            # Quiet output
            "--quiet",
        ]

        if self.upstream_proxy:
            proto = self.upstream_proxy.split("://")[0].lower()
            if proto in ("socks5", "socks4"):
                cmd += ["--mode", f"upstream:{self.upstream_proxy}"]
            else:
                cmd += ["--mode", f"upstream:{self.upstream_proxy}"]

        return cmd


# ── Standalone launcher ───────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Start experiment proxy pair")
    parser.add_argument(
        "--socks5",
        default=None,
        metavar="URL",
        help="Upstream SOCKS5 proxy, e.g. socks5://127.0.0.1:1080",
    )
    parser.add_argument(
        "--upstream",
        default=None,
        metavar="URL",
        help="Upstream HTTP proxy, e.g. http://user:pass@host:8080",
    )
    args = parser.parse_args()

    upstream = args.socks5 or args.upstream or os.getenv("UPSTREAM_PROXY")
    pm = ProxyManager(upstream_proxy=upstream)
    pm.start()

    print("\n[proxy_manager] Running. Proxy URLs:")
    for label, url in pm.proxy_urls.items():
        print(f"  {label:10s}  →  {url}")
    print("\nPress Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
            # Restart any dead processes
            for label, proc in list(pm._procs.items()):
                if proc.poll() is not None:
                    print(f"[proxy_manager] {label} died, restarting …")
                    port = PROXY_PORTS[label]
                    pm._procs[label] = subprocess.Popen(
                        pm._build_cmd(label, port),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        preexec_fn=os.setsid,
                    )
    except KeyboardInterrupt:
        pm.stop()
        print("\n[proxy_manager] stopped.")


if __name__ == "__main__":
    main()
