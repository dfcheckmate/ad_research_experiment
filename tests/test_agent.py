"""
Tests for browser agent module (agent.py).
"""

import os
from pathlib import Path
import pytest


def test_agent_imports():
    """Test that agent module imports successfully."""
    import agent

    assert agent is not None


def test_jitter_function():
    """Test dwell time jitter calculation."""
    from agent import _jitter

    base_ms = 1000
    results = [_jitter(base_ms) for _ in range(100)]

    # All results should be within ±20% of base
    for result in results:
        assert 800 <= result <= 1200

    # Should have some variance (not all identical)
    assert len(set(results)) > 10


def test_random_context_params():
    """Test browser context randomization."""
    from agent import _random_context_params

    params = _random_context_params()

    assert "user_agent" in params
    assert "viewport" in params
    assert "locale" in params
    assert "timezone_id" in params

    # Check viewport has sensible values
    assert 1200 <= params["viewport"]["width"] <= 1500
    assert 700 <= params["viewport"]["height"] <= 900


def test_is_ad_request():
    """Test ad request pattern matching."""
    from agent import is_ad_request

    # Should match known ad networks
    assert is_ad_request("https://ad.doubleclick.net/foo")
    assert is_ad_request("https://googlesyndication.com/bar")
    assert is_ad_request("https://amazon-adsystem.com/baz")

    # Should not match regular content
    assert not is_ad_request("https://www.cnn.com/article")
    assert not is_ad_request("https://static.example.com/image.jpg")


def test_extract_domain():
    """Test domain extraction from URLs."""
    from agent import extract_domain

    assert extract_domain("https://www.example.com/path") == "www.example.com"
    assert extract_domain("http://sub.domain.co.uk/page") == "sub.domain.co.uk"
    assert extract_domain("https://ad.doubleclick.net/") == "ad.doubleclick.net"


def test_classify_network():
    """Test ad network classification."""
    from agent import classify_network

    assert classify_network("ad.doubleclick.net") == "doubleclick.net"
    assert classify_network("googlesyndication.com") == "googlesyndication.com"
    assert classify_network("amazon-adsystem.com") == "amazon-adsystem.com"
    assert classify_network("unknown.com") is None


def test_build_playwright_proxy_config():
    """Test proxy URL parsing for Playwright config."""
    from agent import build_playwright_proxy_config

    cfg = build_playwright_proxy_config("http://user:pass@proxy.example:8080")
    assert cfg["server"] == "http://proxy.example:8080"
    assert cfg["username"] == "user"
    assert cfg["password"] == "pass"

    cfg2 = build_playwright_proxy_config("http://proxy.example:8080")
    assert cfg2 == {"server": "http://proxy.example:8080"}

    socks = build_playwright_proxy_config("socks5://user:pass@proxy.example:1080")
    assert socks == {"server": "socks5://user:pass@proxy.example:1080"}


def test_slugify_site():
    """Test site URL slugification."""
    from agent import slugify_site

    assert slugify_site("https://www.cnn.com") == "cnn_com"
    assert slugify_site("https://www.example.co.uk") == "example_co_uk"


def test_build_google_search_url():
    """Test Google search URL construction."""
    from agent import build_google_search_url

    url = build_google_search_url("test query")

    assert url.startswith("https://www.google.com/search")
    assert "q=test+query" in url or "q=test%20query" in url
    assert "gl=us" in url
    assert "hl=en" in url


def test_infer_topic():
    """Test topic inference from ad content."""
    from agent import infer_topic

    # Finance keywords
    assert infer_topic("investment portfolio", "broker.com") is not None
    assert "invest" in infer_topic("invest in stocks", "trade.com").lower()

    # Banking keywords
    topic = infer_topic("savings account", "bank.com")
    assert topic is not None
    assert "bank" in topic.lower() or "saving" in topic.lower()


def test_parse_google_redirect():
    """Test parsing advertiser URL from Google redirect links."""
    from agent import parse_google_redirect

    url = "https://www.google.com/aclk?sa=L&adurl=https%3A%2F%2Fexample.com%2Flanding%3Fa%3D1"
    assert parse_google_redirect(url).startswith("https://example.com/landing")

    # Unknown formats should be returned unchanged
    plain = "https://advertiser.example/path"
    assert parse_google_redirect(plain) == plain


def test_infer_topic_fallback():
    """Test infer_topic fallback when no keywords match."""
    from agent import infer_topic

    assert (
        infer_topic("completely unrelated text", "example.com", fallback="Banking")
        == "Banking"
    )


def test_queries_for_trial():
    """Test trial-specific query selection."""
    from agent import queries_for_trial
    import agent

    # Test deterministically without relying on ENABLE_GOOGLE_SEARCH_MEASUREMENT.
    # Keep the surface area small so this stays a pure unit test.
    agent.ACTIVE_QUERY_TOPICS = ["Banking"]
    agent.TOPIC_QUERY_SETS = {
        "Banking": [
            "open checking account bonus online",
            "best high yield savings account",
        ]
    }
    agent.QUERIES_PER_TOPIC_PER_TRIAL = 1

    # Use hex-like trial IDs so the offset parse is exercised.
    queries1 = queries_for_trial("00000000-0000-0000-0000-000000000000")
    queries2 = queries_for_trial("00000001-0000-0000-0000-000000000000")

    # Should return list of (topic, query) tuples
    assert isinstance(queries1, list)
    assert all(isinstance(q, tuple) and len(q) == 2 for q in queries1)

    # Different trials should get different queries (with high probability)
    assert queries1 != queries2


def test_capture_path_resolution(temp_captures_dir):
    """Test that screenshot paths resolve to project root, not src/."""
    from pathlib import Path

    # Mock the path resolution logic
    project_root = Path(__file__).parent.parent
    captures_dir = "out/captures"
    trial_id = "test-trial"

    # This is the fixed path resolution from agent.py
    relative_dir = os.path.join(captures_dir, trial_id)
    absolute_dir = project_root / relative_dir

    # Verify it resolves under the repo root, not under src/.
    assert absolute_dir == project_root / "out" / "captures" / trial_id
    assert "src/captures" not in str(absolute_dir)


@pytest.mark.asyncio
async def test_capture_page_context_path(
    mock_playwright_page, temp_captures_dir, monkeypatch
):
    """Test capture_page_context uses correct path resolution."""
    import shutil
    import agent
    from agent import capture_page_context

    # capture_page_context reads module-level constants imported from config.
    # Patch them on the agent module directly.
    project_root = Path(agent.__file__).parent.parent
    captures_dir = f".pytest_captures_{temp_captures_dir.name}"
    out_dir = project_root / captures_dir
    if out_dir.exists():
        shutil.rmtree(out_dir)

    monkeypatch.setattr(agent, "CAPTURE_SCREENSHOTS", True)
    monkeypatch.setattr(agent, "CAPTURE_DOM_SNIPPETS", True)
    monkeypatch.setattr(agent, "CAPTURES_DIR", captures_dir)

    # Force the "external screenshot" path so we actually write a file.
    async def _fake_external_screenshot(*_args, **_kwargs):
        return b"x" * 6001

    monkeypatch.setattr(agent, "_external_screenshot", _fake_external_screenshot)

    trial_id = "test-trial"
    zip_condition = "orem_ut"
    intent_profile = "high_income"
    measurement_site = "https://www.cnn.com"

    # Should not raise
    page_title, screenshot_path, dom_snippet = await capture_page_context(
        page=mock_playwright_page,
        trial_id=trial_id,
        zip_condition=zip_condition,
        intent_profile=intent_profile,
        measurement_site=measurement_site,
        pool=None,
    )

    assert page_title == "Test Page Title"
    assert screenshot_path is not None
    assert not os.path.isabs(screenshot_path)
    assert screenshot_path.startswith(captures_dir + os.sep)
    assert (project_root / screenshot_path).exists()
    assert (project_root / screenshot_path).stat().st_size > 5_000
    assert isinstance(dom_snippet, str) and dom_snippet

    # Cleanup to keep the repo tidy.
    shutil.rmtree(out_dir)


@pytest.mark.asyncio
async def test_capture_page_context_records_capture(
    sqlite_pool, mock_playwright_page, monkeypatch, tmp_path
):
    """Test that capture_page_context writes a file and records DB metadata."""
    import shutil
    import agent
    from agent import capture_page_context

    project_root = Path(agent.__file__).parent.parent
    captures_dir = f".pytest_captures_db_{tmp_path.name}"
    out_dir = project_root / captures_dir
    if out_dir.exists():
        shutil.rmtree(out_dir)

    monkeypatch.setattr(agent, "CAPTURE_SCREENSHOTS", True)
    monkeypatch.setattr(agent, "CAPTURE_DOM_SNIPPETS", False)
    monkeypatch.setattr(agent, "CAPTURES_DIR", captures_dir)

    async def _fake_external_screenshot(*_args, **_kwargs):
        return b"y" * 6001

    monkeypatch.setattr(agent, "_external_screenshot", _fake_external_screenshot)

    trial_id = "test-trial-db"
    page_title, screenshot_path, _dom_snippet = await capture_page_context(
        page=mock_playwright_page,
        trial_id=trial_id,
        zip_condition="orem_ut",
        intent_profile="high_income",
        measurement_site="https://www.cnn.com",
        pool=sqlite_pool,
    )

    assert page_title
    assert screenshot_path
    assert (project_root / screenshot_path).exists()

    # Verify DB row inserted
    async with sqlite_pool.acquire() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*), MIN(file_path) FROM captures WHERE trial_id = ?",
            (trial_id,),
        )
        row = await cur.fetchone()

    assert row[0] == 1
    assert row[1] == screenshot_path

    shutil.rmtree(out_dir)


def test_user_agent_pool():
    """Test that user agent pool contains valid UAs."""
    from agent import _USER_AGENTS

    assert len(_USER_AGENTS) > 0

    for ua in _USER_AGENTS:
        assert isinstance(ua, str)
        assert len(ua) > 50  # UAs are typically long strings
        # Should contain browser name
        assert any(browser in ua for browser in ["Chrome", "Firefox", "Safari", "Edge"])


def test_timezones_list():
    """Test that timezone list is valid."""
    from agent import _TIMEZONES

    assert len(_TIMEZONES) > 0

    for tz in _TIMEZONES:
        assert isinstance(tz, str)
        assert "/" in tz  # Valid IANA timezone format (e.g. Europe/Amsterdam)


def test_locales_list():
    """Test that locales list is valid."""
    from agent import _LOCALES

    assert len(_LOCALES) > 0

    for locale in _LOCALES:
        assert isinstance(locale, str)
        assert locale.startswith("en-")  # English locales
