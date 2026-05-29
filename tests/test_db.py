"""
Tests for database module (db.py).
"""

import pytest


@pytest.mark.asyncio
async def test_sqlite_pool_creation(temp_db_path):
    """Test SQLite pool creation."""
    import db
    
    original_url = db.DB_URL
    db.DB_URL = f"sqlite:///{temp_db_path}"
    
    pool = await db.get_pool()
    assert pool is not None
    await pool.close()
    
    db.DB_URL = original_url


@pytest.mark.asyncio
async def test_init_db_creates_tables(sqlite_pool):
    """Test that init_db creates required tables."""
    import db
    
    # Tables should be created by sqlite_pool fixture
    # Verify by querying sqlite_master
    async with sqlite_pool.acquire() as conn:
        tables = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = [row[0] async for row in tables]
    
    required_tables = ["trials", "ad_observations", "captures"]
    for table in required_tables:
        assert table in table_names, f"Table '{table}' not created"


@pytest.mark.asyncio
async def test_ensure_trial(sqlite_pool):
    """Test trial creation."""
    import db
    
    trial_id = "test-trial-001"
    await db.ensure_trial(sqlite_pool, trial_id)
    
    # Verify trial exists
    async with sqlite_pool.acquire() as conn:
        result = await conn.execute(
            "SELECT trial_id FROM trials WHERE trial_id = ?",
            (trial_id,)
        )
        row = await result.fetchone()
    
    assert row is not None
    assert row[0] == trial_id


@pytest.mark.asyncio
async def test_insert_observations(sqlite_pool, sample_ad_observation):
    """Test inserting ad observations."""
    import db
    
    # Ensure trial exists first
    await db.ensure_trial(sqlite_pool, sample_ad_observation["trial_id"])
    
    # Insert observation
    await db.insert_observations(sqlite_pool, [sample_ad_observation])
    
    # Verify insertion
    async with sqlite_pool.acquire() as conn:
        result = await conn.execute(
            "SELECT COUNT(*) FROM ad_observations WHERE trial_id = ?",
            (sample_ad_observation["trial_id"],)
        )
        count = (await result.fetchone())[0]
    
    assert count == 1


@pytest.mark.asyncio
async def test_insert_multiple_observations(sqlite_pool, sample_ad_observation):
    """Test inserting multiple observations."""
    import db
    
    await db.ensure_trial(sqlite_pool, sample_ad_observation["trial_id"])
    
    # Create 10 observations
    observations = []
    for i in range(10):
        obs = sample_ad_observation.copy()
        obs["ad_url"] = f"https://ad.example.com/ad{i}"
        observations.append(obs)
    
    await db.insert_observations(sqlite_pool, observations)
    
    # Verify count
    async with sqlite_pool.acquire() as conn:
        result = await conn.execute(
            "SELECT COUNT(*) FROM ad_observations WHERE trial_id = ?",
            (sample_ad_observation["trial_id"],)
        )
        count = (await result.fetchone())[0]
    
    assert count == 10


@pytest.mark.asyncio
async def test_insert_capture(sqlite_pool, temp_captures_dir):
    """Test inserting capture metadata."""
    import db
    
    trial_id = "test-trial-001"
    await db.ensure_trial(sqlite_pool, trial_id)
    
    # Create dummy capture file
    capture_path = temp_captures_dir / "test.png"
    capture_path.write_bytes(b"fake image data")
    
    await db.insert_capture(
        pool=sqlite_pool,
        trial_id=trial_id,
        proxy_identity="orem_ut",
        intent_profile="high_income",
        site="cnn_com",
        file_path=str(capture_path),
        file_size_kb=1,
        meta={"source": "test"},
    )
    
    # Verify insertion
    async with sqlite_pool.acquire() as conn:
        result = await conn.execute(
            "SELECT COUNT(*) FROM captures WHERE trial_id = ?",
            (trial_id,)
        )
        count = (await result.fetchone())[0]
    
    assert count == 1


@pytest.mark.asyncio
async def test_observation_schema_fields(sqlite_pool, sample_ad_observation):
    """Test that all expected fields are inserted correctly."""
    import db
    
    await db.ensure_trial(sqlite_pool, sample_ad_observation["trial_id"])
    await db.insert_observations(sqlite_pool, [sample_ad_observation])
    
    # Query and verify fields
    async with sqlite_pool.acquire() as conn:
        result = await conn.execute(
            """SELECT trial_id, agent_id, zip_condition, ad_url, ad_domain, 
                      ad_network, measurement_site, intent_profile
               FROM ad_observations WHERE trial_id = ?""",
            (sample_ad_observation["trial_id"],)
        )
        row = await result.fetchone()
    
    assert row is not None
    assert row[0] == sample_ad_observation["trial_id"]
    assert row[1] == sample_ad_observation["agent_id"]
    assert row[2] == sample_ad_observation["zip_condition"]
    assert row[3] == sample_ad_observation["ad_url"]


@pytest.mark.asyncio
async def test_duplicate_trial_insert(sqlite_pool):
    """Test that inserting duplicate trial is idempotent."""
    import db
    
    trial_id = "test-trial-dup"
    
    # Insert twice
    await db.ensure_trial(sqlite_pool, trial_id)
    await db.ensure_trial(sqlite_pool, trial_id)
    
    # Should only have one entry
    async with sqlite_pool.acquire() as conn:
        result = await conn.execute(
            "SELECT COUNT(*) FROM trials WHERE trial_id = ?",
            (trial_id,)
        )
        count = (await result.fetchone())[0]
    
    assert count == 1


def test_db_url_parsing():
    """Test that DB_URL is parsed correctly."""
    import config
    
    # DB_URL should be set in config (from .env or default)
    assert config.DB_URL is not None
    # Should be either SQLite or PostgreSQL
    assert config.DB_URL.startswith("sqlite") or config.DB_URL.startswith("postgresql")
