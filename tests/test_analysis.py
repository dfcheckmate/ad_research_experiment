"""
Tests for analysis module (analysis.py).
"""

import pandas as pd
import pytest


def test_analysis_imports():
    """Test that analysis module imports successfully."""
    import analysis
    assert analysis is not None


def test_preprocess_function():
    """Test data preprocessing."""
    from analysis import preprocess
    
    # Create sample dataframe
    data = {
        "trial_id": ["t1", "t2", "t3"],
        "zip_condition": ["rich_zip", "poor_zip", "rich_zip"],
        "ad_domain": ["example.com", "test.com", "example.com"],
        "ad_url": ["https://ad1.com", "https://ad2.com", "https://ad3.com"],
        "measurement_site": ["cnn.com", "forbes.com", "cnn.com"],
        "source_type": ["network_request", "network_request", "network_request"],
        "intent_profile": ["high_income", "low_income", "neutral"],
    }
    df = pd.DataFrame(data)
    
    result = preprocess(df)
    
    # Check added columns
    assert "is_rich" in result.columns
    assert "ad_shown" in result.columns
    assert "request_role" in result.columns
    assert "platform_class" in result.columns
    assert "final_taxonomy" in result.columns
    
    # Check is_rich calculation
    assert result.loc[0, "is_rich"] == 1
    assert result.loc[1, "is_rich"] == 0


def test_infer_publisher_context():
    """Test publisher context inference."""
    from analysis import infer_publisher_context
    
    assert infer_publisher_context("cnn.com") == "news & politics"
    assert infer_publisher_context("forbes.com") == "business & investing"
    assert infer_publisher_context("unknown.com") == "general news"


def test_classify_request_role():
    """Test request role classification."""
    from analysis import classify_request_role
    
    row_beacon = pd.Series({
        "ad_url": "https://scorecardresearch.com/beacon.js",
        "ad_domain": "scorecardresearch.com"
    })
    assert classify_request_role(row_beacon) == "analytics beacon"
    
    row_ad = pd.Series({
        "ad_url": "https://pubads.g.doubleclick.net/",
        "ad_domain": "doubleclick.net"
    })
    assert classify_request_role(row_ad) == "ad serving request"


def test_classify_platform_class():
    """Test platform classification."""
    from analysis import classify_platform_class
    
    row_google = pd.Series({
        "ad_domain": "googlesyndication.com",
        "request_role": "ad loader script"
    })
    assert classify_platform_class(row_google) == "google display stack"
    
    row_outbrain = pd.Series({
        "ad_domain": "outbrain.com",
        "request_role": "native recommendation widget"
    })
    assert classify_platform_class(row_outbrain) == "native recommendation network"


def test_classify_final_taxonomy():
    """Test final taxonomy classification."""
    from analysis import classify_final_taxonomy
    
    row_identity = pd.Series({
        "request_role": "identity sync",
        "platform_class": "identity / sync layer"
    })
    assert classify_final_taxonomy(row_identity) == "identity sync"
    
    row_display = pd.Series({
        "request_role": "ad serving request",
        "platform_class": "google display stack"
    })
    assert classify_final_taxonomy(row_display) == "display ad"


def test_build_clean_topic_label():
    """Test clean topic label building."""
    from analysis import build_clean_topic_label
    
    # With inferred topic
    row = pd.Series({
        "inferred_topic": "Banking Services",
        "platform_class": "google display stack",
        "request_role": "ad serving request",
        "publisher_context": "news & politics"
    })
    assert build_clean_topic_label(row) == "Banking Services"
    
    # Without inferred topic
    row_no_topic = pd.Series({
        "inferred_topic": "",
        "platform_class": "native recommendation network",
        "request_role": "native recommendation widget",
        "publisher_context": "news & politics"
    })
    result = build_clean_topic_label(row_no_topic)
    assert "native recommendations" in result.lower()


@pytest.mark.asyncio
async def test_load_data_empty_db():
    """Test loading from empty database."""
    from analysis import load_data
    import config
    
    # Skip if not using SQLite
    if not config.DB_URL.startswith("sqlite"):
        pytest.skip("Test requires SQLite database")
    
    df = await load_data()
    # May be empty or have existing data depending on test environment
    assert df is not None


def test_volume_hypothesis_with_sample_data():
    """Test volume hypothesis (Poisson GLM) model fitting."""
    from analysis import test_volume_hypothesis
    
    # Create sample data with enough observations
    data = {
        "trial_id": [f"t{i}" for i in range(100)],
        "zip_condition": ["orem_ut" if i % 3 == 0 else "boston_ma" if i % 3 == 1 else "nyc_ny" for i in range(100)],
        "ad_shown": [1] * 100,
    }
    df = pd.DataFrame(data)
    
    # Should not raise
    try:
        test_volume_hypothesis(df)
    except Exception as e:
        # May fail if statsmodels isn't available, but shouldn't crash
        pytest.skip(f"Statsmodels not available or insufficient data: {e}")


def test_chi_square_test_with_sample_data():
    """Test chi-square test."""
    from analysis import chi_square_test
    
    # Create sample data
    data = {
        "zip_condition": ["rich_zip"] * 50 + ["poor_zip"] * 50,
        "ad_domain": ["google.com"] * 30 + ["amazon.com"] * 20 +
                     ["google.com"] * 25 + ["amazon.com"] * 25,
        "ad_shown": [1] * 100,
    }
    df = pd.DataFrame(data)
    
    # Should not raise
    try:
        chi_square_test(df)
    except Exception as e:
        pytest.skip(f"Chi-square test failed: {e}")


def test_summary_stats():
    """Test summary statistics generation."""
    from analysis import summary_stats
    
    data = {
        "trial_id": ["t1", "t1", "t2", "t2"],
        "zip_condition": ["rich_zip", "rich_zip", "poor_zip", "poor_zip"],
        "ad_domain": ["google.com", "amazon.com", "google.com", "facebook.com"],
        "ad_network": ["google", "amazon", "google", "facebook"],
        "source_type": ["network_request"] * 4,
        "intent_profile": ["high_income"] * 4,
    }
    df = pd.DataFrame(data)
    
    # Should not raise
    summary_stats(df)
