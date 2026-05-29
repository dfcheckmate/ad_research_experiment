"""Proxy manager for local mitmproxy mode."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import time
from pathlib import Path

from logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

PROXY_PORTS = {
    "poor_zip": int(os.getenv("PROXY_POOR_PORT", "8181")),
    "rich_zip": int(os.getenv("PROXY_RICH_PORT", "8182")),
}

ADDON_PATH = Path(__file__).parent / "proxies" / "geoaddon.py"

MITMDUMP = Path(__file__).parent.parent / "venv" / "bin" / "mitmdump"
if not MITMDUMP.exists():
    MITMDUMP = "mitmdump"


class ProxyManager:
    """Async context manager that owns two mitmdump subprocesses."""

    def __init__(self, upstream_proxy: str | None = None):
        self.upstream_proxy = upstream_proxy or os.getenv("UPSTREAM_PROXY")
        self._procs: dict[str, subprocess.Popen] = {}

    @property
    def proxy_urls(self) -> dict[str, str]:
        return {
            label: f"http://127.0.0.1:{port}" for label, port in PROXY_PORTS.items()
        }

    async def __aenter__(self) -> "ProxyManager":
        self.start()
        await asyncio.sleep(1.5)
        return self

    async def __aexit__(self, *_) -> None:
        self.stop()

    def start(self) -> None:
        for label, port in PROXY_PORTS.items():
            cmd = self._build_cmd(label, port)
            logger.debug("mitmdump command: %s", " ".join(cmd))
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid,
            )
            self._procs[label] = proc
            logger.info("%s started PID=%s port=%s", label, proc.pid, port)

    def stop(self) -> None:
        for label, proc in self._procs.items():
            logger.info("%s stopping PID=%s", label, proc.pid)
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        self._procs.clear()

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
            "--ssl-insecure",
            "--quiet",
        ]

        if self.upstream_proxy:
            cmd += ["--mode", f"upstream:{self.upstream_proxy}"]

        return cmd


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

    print("\nProxy URLs:")
    for label, url in pm.proxy_urls.items():
        print(f"  {label:10s} → {url}")
    print("\nPress Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
            for label, proc in list(pm._procs.items()):
                if proc.poll() is not None:
                    logger.warning("%s exited (code=%s), restarting", label, proc.returncode)
                    port = PROXY_PORTS[label]
                    pm._procs[label] = subprocess.Popen(
                        pm._build_cmd(label, port),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        preexec_fn=os.setsid,
                    )
                    logger.info("%s restarted PID=%s", label, pm._procs[label].pid)
    except KeyboardInterrupt:
        pm.stop()
        logger.info("stopped")


if __name__ == "__main__":
    main()
