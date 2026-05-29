"""Additional unit tests to increase coverage of analysis outputs.

These tests focus on deterministic transforms and file outputs, not on
external data sources.
"""

from __future__ import annotations

import random

import pytest
import pandas as pd


def _synthetic_observations(n: int = 200) -> pd.DataFrame:
    rng = random.Random(0)
    identities = ["poor_zip", "rich_zip"]
    sites = ["cnn.com", "forbes.com", "google_search"]
    domains = [
        "doubleclick.net",
        "scorecardresearch.com",
        "outbrain.com",
        "example.com",
    ]

    rows = []
    for i in range(n):
        zip_condition = identities[i % len(identities)]
        measurement_site = rng.choice(sites)
        # Deterministic domain assignment that is balanced across ZIP conditions.
        # Domain changes every pair of rows (poor_zip + rich_zip) so every domain
        # appears under both identities.
        ad_domain = domains[(i // 2) % len(domains)]
        # Make ad_url vary so request_role rules can trigger
        if ad_domain == "scorecardresearch.com":
            ad_url = "https://scorecardresearch.com/beacon.js"
        elif ad_domain == "doubleclick.net":
            ad_url = "https://pubads.g.doubleclick.net/gampad/ads"
        elif ad_domain == "outbrain.com":
            ad_url = "https://widgets.outbrain.com/outbrain.js"
        else:
            ad_url = f"https://{ad_domain}/ad?id={i}"

        # Sprinkle in some inferred topics and google search rows
        source_type = (
            "google_search_ad"
            if measurement_site == "google_search"
            else "network_request"
        )
        inferred_topic = "Banking" if (i % 7 == 0) else ""
        query_topic = "Banking" if source_type == "google_search_ad" else ""
        search_query = (
            "best high yield savings account"
            if source_type == "google_search_ad"
            else ""
        )

        rows.append(
            {
                "trial_id": f"t{i // 5}",
                "zip_condition": zip_condition,
                "ad_domain": ad_domain,
                "ad_url": ad_url,
                "ad_network": "google" if "doubleclick" in ad_domain else "other",
                "measurement_site": measurement_site,
                "source_type": source_type,
                "intent_profile": "neutral" if (i % 3 == 0) else "high_income",
                "inferred_topic": inferred_topic,
                "query_topic": query_topic,
                "search_query": search_query,
            }
        )
    return pd.DataFrame(rows)


def test_output_functions_write_files(tmp_path):
    import analysis

    df = _synthetic_observations(240)
    df = analysis.preprocess(df)

    out = str(tmp_path)

    analysis.google_search_summary(df, out)
    analysis.inferred_topic_summary(df, out)
    analysis.final_taxonomy_summary(df, out)
    analysis.cell_comparison_plot(df, out)
    analysis.domain_breakdown(df, out)

    # per_domain_odds is the most brittle; this dataset is constructed to avoid
    # perfect separation in the logit fit most of the time.
    analysis.per_domain_odds(df, out)

    assert (tmp_path / "final_taxonomy_summary.csv").exists()
    assert (tmp_path / "cell_comparison_taxonomy.png").exists()
    assert (tmp_path / "domain_distribution.png").exists()
    assert (tmp_path / "classified_ads.csv").exists()
    # google_search_ads.csv should be present because we include google_search rows
    assert (tmp_path / "google_search_ads.csv").exists()
    # per-domain odds plot is written only if at least one model fit succeeds
    assert (tmp_path / "per_domain_odds.png").exists()


def test_google_search_summary_empty(tmp_path):
    """google_search_summary should no-op cleanly on datasets without search ads."""
    import analysis

    df = _synthetic_observations(50)
    df["source_type"] = "network_request"
    df = analysis.preprocess(df)

    analysis.google_search_summary(df, str(tmp_path))
    assert not (tmp_path / "google_search_ads.csv").exists()


@pytest.mark.asyncio
async def test_load_data_reads_sqlite(tmp_path, monkeypatch):
    """Exercise analysis.load_data SQLite codepath on a tiny DB."""
    import aiosqlite
    import analysis

    db_path = tmp_path / "analysis_test.db"

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            """
            CREATE TABLE ad_observations (
                trial_id TEXT,
                zip_condition TEXT,
                ad_url TEXT,
                ad_domain TEXT,
                ad_network TEXT,
                measurement_site TEXT,
                source_type TEXT,
                intent_profile TEXT,
                query_topic TEXT,
                search_query TEXT,
                ad_headline TEXT,
                ad_description TEXT,
                advertiser_name TEXT,
                landing_url TEXT,
                landing_domain TEXT,
                inferred_topic TEXT,
                observed_at TEXT
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO ad_observations (
                trial_id, zip_condition, ad_url, ad_domain, ad_network,
                measurement_site, source_type, intent_profile, query_topic, search_query,
                ad_headline, ad_description, advertiser_name, landing_url, landing_domain,
                inferred_topic, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "t1",
                "rich_zip",
                "https://pubads.g.doubleclick.net/gampad/ads",
                "doubleclick.net",
                "google",
                "cnn.com",
                "network_request",
                "neutral",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "2026-04-12T00:00:00Z",
            ),
        )
        await conn.commit()

    monkeypatch.setattr(analysis, "USE_SQLITE", True)
    monkeypatch.setattr(analysis, "SQLITE_PATH", str(db_path))

    df = await analysis.load_data()
    assert len(df) == 1
    assert df.loc[0, "trial_id"] == "t1"
    assert df.loc[0, "zip_condition"] == "rich_zip"
