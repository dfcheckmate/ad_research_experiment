# Ad Research Experiment

Playwright-based measurement harness for auditing ad delivery differences across proxy identities (ZIP conditions) and intent profiles, plus an analysis pipeline.

This repository is research tooling (not a product). See [LICENSE](./LICENSE).

## Research Question

Does ad exposure and advertiser/domain composition differ across proxy identities
associated with different geographic or socioeconomic profiles when browsing
behavior is held constant?

This project treats proxy identity (ZIP condition / household identity) as the
experimental treatment and keeps intent-profile browsing scripts fixed within a
trial so differences in observed ads can be attributed to identity-dependent ad
delivery rather than different browsing histories.

## Hypotheses

- `H0`: Ad exposure is independent of proxy identity when browsing behavior is identical.
- `H1`: Ad exposure differs by proxy identity even when browsing behavior is identical.

## Research Design

- Paired execution: all proxy identities run the same intent profile in parallel within a trial.
- Fixed behavior scripts: `high_income`, `low_income`, and `neutral` profiles are held constant across identities.
- Fresh sessions: each agent run starts with a clean browser context.
- Measurement outcome: ad observations captured from ad-network requests (and optionally Google search ad extraction).
- Main analyses: count differences, domain-distribution shifts, and treatment-cell comparisons by intent profile and proxy identity.

## Documentation

Primary documentation lives in Sphinx under `docs/`.

```bash
pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
```

Entry point: `docs/_build/html/index.html`.

## Quickstart

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env

bash quickstart.sh
python src/analysis.py --output results/
```

Default setup is vendor-neutral: `PROXY_MODE=local` (local `mitmdump` + header injection).

## Testing

```bash
./venv/bin/python -m pytest -q
```

Coverage is reported in GitLab CI and via `pytest --cov=src`.

## Safety

- Treat `.env` as secret.
- Do not commit artifacts (`captures/`, `results/`, DB files, net-export logs).

See [docs/ethics.md](./docs/ethics.md).
