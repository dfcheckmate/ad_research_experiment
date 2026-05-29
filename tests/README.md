# Test Suite for Ad Research Experiment

This directory contains comprehensive tests for the ad research experiment infrastructure.

## Test Organization

```
tests/
├── conftest.py              # Shared fixtures and configuration
├── test_config.py           # Configuration validation tests
├── test_db.py               # Database layer tests
├── test_agent.py            # Browser agent tests
├── test_analysis.py         # Statistical analysis tests
├── test_proxy_manager.py    # Proxy infrastructure tests
├── test_experiment.py       # Orchestration tests
└── debug/                   # Manual debugging scripts
    ├── debug_agent.py
    └── debug_agent_quick.py
```

## Running Tests

### Install test dependencies
```bash
pip install -r requirements.txt
```

### Run all tests
```bash
pytest
```

### Run specific test file
```bash
pytest tests/test_agent.py
```

### Run tests with coverage
```bash
pytest --cov=src --cov-report=html
```

### Run only fast tests (skip slow integration tests)
```bash
pytest -m "not slow"
```

### Run only database tests
```bash
pytest -m db
```

### Run with verbose output
```bash
pytest -v
```

## Test Categories

### Unit Tests (`-m unit`)
- Individual function testing
- No external dependencies
- Fast execution

### Integration Tests (`-m integration`)
- Component interaction testing
- May require database or file system
- Moderate execution time

### Database Tests (`-m db`)
- SQLite and PostgreSQL operations
- Schema validation
- Data integrity checks

### Browser Tests (`-m browser`)
- Playwright automation (skipped by default)
- Requires browser binary
- Slow execution

## Test Fixtures

### Database Fixtures
- `temp_db_path`: Temporary SQLite database
- `sqlite_pool`: Initialized SQLite connection pool
- `sample_ad_observation`: Sample ad observation data

### Mock Fixtures
- `mock_env`: Mocked environment variables
- `mock_playwright_page`: Mocked Playwright page
- `mock_proxy_config`: Mocked proxy configuration

### Filesystem Fixtures
- `temp_captures_dir`: Temporary captures directory

## Coverage Requirements

Minimum coverage targets:
- **Overall**: 80%
- **Critical modules** (db.py, agent.py, analysis.py): 90%
- **Configuration** (config.py): 95%

## Writing New Tests

### Test Naming Convention
```python
def test_<component>_<behavior>():
    """Test that <component> <expected_behavior>."""
    ...
```

### Use Fixtures for Setup
```python
def test_with_database(sqlite_pool, sample_ad_observation):
    """Test uses database and sample data fixtures."""
    ...
```

### Mark Tests Appropriately
```python
@pytest.mark.db
@pytest.mark.asyncio
async def test_database_operation(sqlite_pool):
    """Database test with async support."""
    ...
```

### Test Both Success and Failure Cases
```python
def test_valid_input():
    """Test with valid input."""
    assert func(valid_input) == expected_output

def test_invalid_input():
    """Test with invalid input."""
    with pytest.raises(ValueError):
        func(invalid_input)
```

## Continuous Integration

Tests run automatically on:
- Pull requests
- Push to main branch
- Nightly builds

Failed tests block merges.

## Known Limitations

1. **Browser tests skipped by default**: Require Playwright browser installation
2. **PostgreSQL tests require running instance**: Use SQLite tests as fallback
3. **Proxy tests may be flaky**: Network-dependent tests have retry logic
4. **Screenshot tests are mocked**: Full integration requires external API

## Debugging Failed Tests

### Get detailed output
```bash
pytest -vv --tb=long
```

### Run single test with print statements
```bash
pytest tests/test_agent.py::test_is_ad_request -s
```

### Drop into debugger on failure
```
pytest --pdb
```

### Generate HTML coverage report
```bash
pytest --cov=src --cov-report=html
open htmlcov/index.html
```

## Contributing

When adding new features:
1. Write tests first (TDD approach preferred)
2. Ensure tests pass locally
3. Maintain >80% coverage
4. Add test markers appropriately
5. Update this README if adding new test categories
