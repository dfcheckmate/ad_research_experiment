"""
Tests for configuration module (config.py).
"""

import os
import importlib


def test_config_imports():
    """Test that config module imports successfully."""
    import config

    assert config is not None


def test_proxy_mode_values(mock_env):
    """Test that PROXY_MODE accepts valid values."""
    import importlib
    import config

    valid_modes = ["residential", "local", "socks5", "upstream", "upstream_mitm"]

    for mode in valid_modes:
        os.environ["PROXY_MODE"] = mode
        importlib.reload(config)
        assert config.PROXY_MODE == mode


def test_proxy_map_structure(mock_env):
    """Test that PROXIES dict has correct structure."""
    import config

    importlib.reload(config)

    assert isinstance(config.PROXIES, dict)
    assert len(config.PROXIES) > 0

    for label, url in config.PROXIES.items():
        assert isinstance(label, str)
        assert isinstance(url, str)
        assert url.startswith(("http://", "socks5://"))


def test_intent_profiles_structure():
    """Test that INTENT_PROFILES is properly structured."""
    import config

    assert isinstance(config.INTENT_PROFILES, dict)
    assert len(config.INTENT_PROFILES) > 0

    for profile_name, urls in config.INTENT_PROFILES.items():
        assert isinstance(profile_name, str)
        assert isinstance(urls, list)
        assert len(urls) > 0
        for url in urls:
            assert isinstance(url, str)
            assert url.startswith("http")


def test_active_intent_profiles():
    """Test that ACTIVE_INTENT_PROFILES is subset of INTENT_PROFILES."""
    import config

    assert isinstance(config.ACTIVE_INTENT_PROFILES, list)
    assert len(config.ACTIVE_INTENT_PROFILES) > 0

    for profile in config.ACTIVE_INTENT_PROFILES:
        assert profile in config.INTENT_PROFILES, (
            f"Active profile '{profile}' not found in INTENT_PROFILES"
        )


def test_ad_sites_list():
    """Test that AD_SITES contains valid URLs."""
    import config

    assert isinstance(config.AD_SITES, list)
    assert len(config.AD_SITES) > 0

    for site in config.AD_SITES:
        assert isinstance(site, str)
        assert site.startswith("https://"), f"AD_SITES must use HTTPS: {site}"


def test_ad_network_patterns():
    """Test that AD_NETWORK_PATTERNS contains valid domain patterns."""
    import config

    assert isinstance(config.AD_NETWORK_PATTERNS, list)
    assert len(config.AD_NETWORK_PATTERNS) > 0

    # Check for common ad networks
    patterns_str = " ".join(config.AD_NETWORK_PATTERNS)
    assert "doubleclick" in patterns_str or "googlesyndication" in patterns_str
    assert "amazon" in patterns_str or "adsystem" in patterns_str


def test_experiment_parameters():
    """Test that experiment parameters are sensible."""
    import config

    assert config.N_TRIALS > 0
    assert config.DWELL_TIME_MS > 0
    assert config.AD_DWELL_MS > 0
    assert config.CONCURRENCY > 0
    assert isinstance(config.HEADLESS, bool)


def test_captures_dir_path():
    """Test that CAPTURES_DIR path is relative (not absolute)."""
    import config

    assert not os.path.isabs(config.CAPTURES_DIR), (
        "CAPTURES_DIR should be relative to project root"
    )


def test_sqlite_path_not_under_src():
    """SQLite output should not default into the source directory."""
    import config

    if config.DB_URL.startswith("sqlite"):
        sqlite_path = config.DB_URL.removeprefix("sqlite:///")
        assert not sqlite_path.startswith("src" + os.sep)


def test_proxy_identity_meta():
    """Test that PROXY_IDENTITY_META contains expected fields."""
    import config

    assert isinstance(config.PROXY_IDENTITY_META, dict)

    for identity, meta in config.PROXY_IDENTITY_META.items():
        assert isinstance(meta, dict)
        # Check for expected metadata fields
        if meta:  # Some identities may have empty meta
            expected_fields = {"city", "state", "asn", "isp"}
            assert expected_fields.intersection(meta.keys()), (
                f"Identity '{identity}' meta missing expected fields"
            )


def test_topic_query_sets_structure():
    """Test that TOPIC_QUERY_SETS is properly structured."""
    import config

    assert isinstance(config.TOPIC_QUERY_SETS, dict)

    for topic, queries in config.TOPIC_QUERY_SETS.items():
        assert isinstance(topic, str)
        assert isinstance(queries, list)
        assert len(queries) > 0
        for query in queries:
            assert isinstance(query, str)
            assert len(query) > 0


def test_active_query_topics():
    """Test that ACTIVE_QUERY_TOPICS is subset of TOPIC_QUERY_SETS."""
    import config

    if config.ENABLE_GOOGLE_SEARCH_MEASUREMENT:
        assert isinstance(config.ACTIVE_QUERY_TOPICS, list)
        for topic in config.ACTIVE_QUERY_TOPICS:
            assert topic in config.TOPIC_QUERY_SETS, (
                f"Active topic '{topic}' not found in TOPIC_QUERY_SETS"
            )
