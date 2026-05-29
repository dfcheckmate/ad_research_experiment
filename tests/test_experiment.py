"""
Tests for experiment orchestrator (experiment.py).
"""

import pytest


def test_experiment_imports():
    """Test that experiment module imports successfully."""
    import experiment
    assert experiment is not None


def test_global_semaphore_exists():
    """Test that browser semaphore is defined."""
    import experiment
    
    # Before main() is called, should be None
    assert experiment._browser_sem is None or hasattr(experiment._browser_sem, "_value")


@pytest.mark.asyncio
async def test_run_trial_inserts_observations(sqlite_pool, mock_proxy_config, monkeypatch):
    """run_trial should persist observations returned by agents."""
    import experiment
    import asyncio

    monkeypatch.setattr(experiment, "PROXIES", mock_proxy_config)
    monkeypatch.setattr(experiment, "ACTIVE_INTENT_PROFILES", ["high_income"])

    async def mock_run_agent(trial_id, label, intent_profile, *_args, **_kwargs):
        return [
            {
                "trial_id": trial_id,
                "agent_id": f"agent-{label}",
                "zip_condition": label,
                "ad_url": "https://ad.example.test/banner",
                "ad_domain": "ad.example.test",
                "ad_network": "test",
                "measurement_site": "https://publisher.example.test",
                "source_type": "network_request",
                "intent_profile": intent_profile,
            }
        ]

    monkeypatch.setattr(experiment, "run_agent", mock_run_agent)
    experiment._browser_sem = asyncio.Semaphore(3)

    trial_id = "test-trial-001"

    obs_count = await experiment.run_trial(sqlite_pool, trial_id)

    assert obs_count == len(mock_proxy_config)


@pytest.mark.asyncio
async def test_run_trial_rejects_zero_observations(
    sqlite_pool, mock_proxy_config, monkeypatch
):
    """run_trial should fail when agents complete without captured ads."""
    import experiment
    import asyncio

    monkeypatch.setattr(experiment, "PROXIES", mock_proxy_config)
    monkeypatch.setattr(experiment, "ACTIVE_INTENT_PROFILES", ["high_income"])

    async def mock_run_agent(*_args, **_kwargs):
        return []

    monkeypatch.setattr(experiment, "run_agent", mock_run_agent)
    experiment._browser_sem = asyncio.Semaphore(3)

    with pytest.raises(RuntimeError, match="no observations"):
        await experiment.run_trial(sqlite_pool, "empty-trial")


@pytest.mark.asyncio
async def test_worker_records_failed_trial(monkeypatch):
    """Failed trials should still advance progress accounting."""
    import experiment
    import asyncio

    async def mock_run_trial(*_args, **_kwargs):
        raise RuntimeError("no observations")

    monkeypatch.setattr(experiment, "run_trial", mock_run_trial)

    queue = asyncio.Queue()
    await queue.put("failed-trial")
    results = []

    task = asyncio.create_task(experiment.worker(queue, None, results))
    await queue.join()
    task.cancel()

    assert results == [0]


def test_concurrency_defaults():
    """Test that concurrency parameters have sensible defaults."""
    import config
    
    assert config.CONCURRENCY > 0
    assert config.CONCURRENCY <= 10, "Concurrency too high for memory safety"


def test_n_trials_positive():
    """Test that N_TRIALS is positive."""
    import config
    
    assert config.N_TRIALS > 0


def test_max_browsers_calculation():
    """Test max browsers calculation logic."""
    import config
    
    # From experiment.py logic
    n_proxies = len(config.PROXIES)
    concurrency = config.CONCURRENCY
    
    default_max = min(concurrency * n_proxies, 6)
    
    assert default_max >= concurrency
    assert default_max <= 12, "Max browsers too high for typical system"
