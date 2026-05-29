"""
Pytest configuration and shared fixtures for ad research experiment tests.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure matplotlib never tries to use a GUI backend in tests.
os.environ.setdefault("MPLBACKEND", "Agg")

# Add src to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


# ── Async fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ── Database fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def temp_db_path():
    """Create temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
async def sqlite_pool(temp_db_path, monkeypatch):
    """Create SQLite connection pool for testing."""
    # Set environment before importing db module
    monkeypatch.setenv("DB_URL", f"sqlite:///{temp_db_path}")

    # Import after setting env
    import importlib
    import db

    importlib.reload(db)

    pool = await db.get_pool()
    await db.init_db(pool)

    yield pool

    await pool.close()


# ── Configuration fixtures ────────────────────────────────────────────────────


@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment variables for testing."""
    test_env = {
        "DB_URL": "sqlite:///test.db",
        "PROXY_MODE": "local",
        "CAPTURES_DIR": "test_captures",
        "CAPTURE_SCREENSHOTS": "0",
        "CAPTURE_DOM_SNIPPETS": "0",
        "ENABLE_GOOGLE_SEARCH_MEASUREMENT": "0",
    }
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)
    return test_env


@pytest.fixture
def temp_captures_dir():
    """Create temporary captures directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# ── Test data fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def sample_ad_observation():
    """Sample ad observation dict for testing."""
    return {
        "trial_id": "test-trial-001",
        "agent_id": "test-agent-001",
        "zip_condition": "orem_ut",
        "ad_url": "https://ad.doubleclick.net/example",
        "ad_domain": "doubleclick.net",
        "ad_network": "google",
        "measurement_site": "https://www.cnn.com",
        "source_type": "network_request",
        "intent_profile": "high_income",
        "query_topic": None,
        "search_query": None,
        "ad_headline": None,
        "ad_description": None,
        "advertiser_name": None,
        "landing_url": None,
        "landing_domain": None,
        "inferred_topic": None,
    }


@pytest.fixture
def sample_trial_data():
    """Sample trial data for testing."""
    return {
        "trial_id": "test-trial-001",
        "n_observations": 42,
        "started_at": "2026-03-30T00:00:00Z",
        "completed_at": "2026-03-30T00:15:00Z",
    }


# ── Mock fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_playwright_page():
    """Mock Playwright page for testing without browser."""
    from unittest.mock import AsyncMock, MagicMock

    page = MagicMock()
    page.goto = AsyncMock(return_value=None)
    page.wait_for_timeout = AsyncMock(return_value=None)
    page.wait_for_function = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(return_value="Mock page content")
    page.screenshot = AsyncMock(return_value=None)
    page.title = AsyncMock(return_value="Test Page Title")
    page.on = MagicMock()
    page.remove_listener = MagicMock()

    return page


@pytest.fixture
def mock_proxy_config():
    """Mock proxy configuration for testing."""
    return {
        "orem_ut": "http://127.0.0.1:8181",
        "boston_ma": "http://127.0.0.1:8182",
        "nyc_ny": "http://127.0.0.1:8183",
    }
