"""
Tests for proxy manager module (proxy_manager.py).
"""

from pathlib import Path

import pytest


def test_proxy_manager_imports():
    """Test that proxy_manager module imports successfully."""
    import proxy_manager

    assert proxy_manager is not None


def test_proxy_ports_configuration():
    """Test that proxy ports are defined."""
    from proxy_manager import PROXY_PORTS

    assert isinstance(PROXY_PORTS, dict)
    assert len(PROXY_PORTS) >= 2
    assert "poor_zip" in PROXY_PORTS or "poor" in PROXY_PORTS
    assert "rich_zip" in PROXY_PORTS or "rich" in PROXY_PORTS

    # Ports should be integers in valid range
    for port in PROXY_PORTS.values():
        assert isinstance(port, int)
        assert 1024 <= port <= 65535


def test_addon_path_exists():
    """Test that geo addon script exists."""
    from proxy_manager import ADDON_PATH

    # Check the path is defined
    assert ADDON_PATH is not None
    assert isinstance(ADDON_PATH, Path)

    # Check filename is correct
    assert "geo" in ADDON_PATH.name
    assert ADDON_PATH.suffix == ".py"

    assert ADDON_PATH.exists(), f"Addon script not found at {ADDON_PATH}"


def test_proxy_manager_initialization():
    """Test ProxyManager initialization."""
    from proxy_manager import ProxyManager

    mgr = ProxyManager()

    assert mgr.proxy_urls is not None
    assert isinstance(mgr.proxy_urls, dict)
    assert len(mgr.proxy_urls) == 2


def test_proxy_manager_with_upstream():
    """Test ProxyManager with upstream proxy."""
    from proxy_manager import ProxyManager

    mgr = ProxyManager(upstream_proxy="socks5://127.0.0.1:1080")

    assert mgr.upstream_proxy == "socks5://127.0.0.1:1080"
    assert mgr.proxy_urls is not None


def test_proxy_manager_url_format():
    """Test that proxy URLs are correctly formatted."""
    from proxy_manager import ProxyManager

    mgr = ProxyManager()

    for label, url in mgr.proxy_urls.items():
        assert url.startswith("http://127.0.0.1:")
        # Extract port
        port = int(url.split(":")[-1])
        assert 1024 <= port <= 65535


@pytest.mark.skip(reason="Requires mitmdump binary and may interfere with other tests")
def test_proxy_manager_start_stop():
    """Test starting and stopping proxy manager."""
    from proxy_manager import ProxyManager
    import time

    mgr = ProxyManager()

    try:
        mgr.start()
        time.sleep(2)  # Wait for proxies to start

        # Check processes are running
        assert len(mgr.processes) == 2
        for proc in mgr.processes.values():
            assert proc.poll() is None, "Proxy process died"

    finally:
        mgr.stop()
        time.sleep(1)

        # Check processes are stopped
        for proc in mgr.processes.values():
            assert proc.poll() is not None, "Proxy process still running"
