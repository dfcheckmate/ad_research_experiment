# Ad Delivery Measurement Platform

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
![Tests](badges/tests.svg)
![Coverage](badges/coverage.svg)
![Academia](badges/academia.svg)

**Educational research project exploring distributed measurement infrastructure for algorithmic auditing.**

This is a personal project developed independently to explore causal inference methodology, distributed browser automation, and measurement system design. Not affiliated with or endorsed by my employer.

---

## Overview

This project implements a distributed measurement platform for auditing ad delivery systems. It measures whether ad delivery algorithms show different results to users based on demographic proxies (ZIP code / household identity) while holding browsing behavior constant.

**Research Question:** If two users browse identically but have different demographic proxies, do they receive different ad deliveries?

**Primary Use Case:** Educational exploration of:
- Causal inference experimental design
- Distributed browser automation at scale
- Memory-safe concurrency patterns
- Anti-detection engineering for measurement integrity
- Statistical analysis pipelines

**Note:** This is research-grade code intended for learning and experimentation. It is tested and documented, but designed for measurement workflows rather than production ad-serving environments.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Controller (MCP Server)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ Task Queue  │  │   Agent     │  │   Results   │              │
│  │             │  │  Scheduler  │  │   Collector │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└─────────────────────────────────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Proxy Identity │  │  Proxy Identity │  │  Proxy Identity │
│  (ZIP A)        │  │  (ZIP B)        │  │  (ZIP C)        │
│  ┌───────────┐  │  │  ┌───────────┐  │  │  ┌───────────┐  │
│  │  Browser  │  │  │  │  Browser  │  │  │  │  Browser  │  │
│  │  Session  │  │  │  │  Session  │  │  │  │  Session  │  │
│  └───────────┘  │  │  └───────────┘  │  │  └───────────┘  │
└─────────────────┘  └─────────────────┘  └─────────────────┘
          │                    │                    │
          └────────────────────┼────────────────────┘
                               ▼
                    ┌─────────────────────┐
                    │   Measurement DB    │
                    │   (SQLite/Postgres) │
                    └─────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Analysis Pipeline │
                    │   (Statistical)     │
                    └─────────────────────┘
```

---

## Technical Challenges

| Challenge | Solution | Engineering Significance |
|-----------|----------|-------------------------|
| **Causal Inference** | Paired trial design (identical behavior, varied identity) | Isolates identity as the only variable for valid causal claims |
| **Bot Detection** | Fingerprint randomization (UA, viewport, navigator properties) | Maintains measurement integrity by avoiding detection systems |
| **Memory Safety** | Semaphore-based concurrency control | Prevents host exhaustion during parallel browser execution |
| **Identity Isolation** | Proxy rotation with clean sessions | Ensures no cross-contamination between experimental trials |
| **Reproducibility** | Containerized runtime (Docker) | Enables reproducible results across different environments |

---

## Quickstart

## Quickstart

### Prerequisites
- Python 3.10+
- Docker (optional, for reproducible runs)
- Residential proxies (optional, for identity variation)

### Run Locally
```bash
# Clone and setup
git clone https://github.com/dfcheckmate/ad_research_experiment.git
cd ad-research-experiment
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Configure (copy example and edit)
cp .env.example .env

# Run small experiment
bash quickstart.sh
python src/analysis.py --output out/results/
```

### Run with Docker (Reproducible)
```bash
# Build image
docker build -t ad-research-experiment:latest .

# Run experiment
mkdir -p out
docker run --rm \
  --shm-size=1gb \
  -v "$PWD/out:/out" \
  --env-file .env \
  ad-research-experiment:latest \
  src/experiment.py --trials 10 --concurrency 2
```

---

## Sample Results

After running 200 trials across 3 proxy identities:

| Metric | ZIP A | ZIP B | ZIP C | p-value |
|--------|-------|-------|-------|---------|
| Unique Ad Domains | 145 | 132 | 151 | 0.023* |
| Ad Network Diversity | 0.73 | 0.68 | 0.71 | 0.041* |
| Avg Ads per Session | 23.4 | 22.8 | 24.1 | 0.312 |

*Statistically significant at α=0.05

---

## Project Structure

```
ad-research-experiment/
├── src/
│   ├── agent.py           # Browser automation with anti-detection
│   ├── experiment.py      # Trial orchestration (concurrency control)
│   ├── analysis.py        # Statistical analysis pipeline
│   ├── config.py          # Configuration management
│   ├── db.py              # Database layer (SQLite/Postgres)
│   ├── proxy_manager.py   # Proxy rotation and identity isolation
│   └── literature.py      # Literature review integration
├── tests/
│   ├── test_agent.py      # Browser automation tests
│   ├── test_experiment.py # Concurrency and isolation tests
│   └── test_analysis.py   # Statistical validation tests
├── scripts/
│   ├── enqueue_experiment.py  # Task queue management
│   └── cleanup.py             # Artifact cleanup
├── docs/                  # Sphinx documentation
├── docker-compose.yml     # Multi-service orchestration
├── Dockerfile            # Reproducible runtime
└── README.md             # This file
```

---

## Testing

```bash
# Run test suite
pytest -q

# With coverage
pytest --cov=src --cov-report=html

# Run inside Docker (isolated environment)
docker run --rm ad-research-experiment:latest pytest -q
```

---

## Ethics & Safety

This tool is designed for **research purposes only**. Key principles:

- No personal data collection (only ad metadata)
- Respectful request rates (rate limiting built-in)
- Compliant with CFAA (authorized access only)

---

## License

CC BY-NC 4.0 License — see [`LICENSE`](./LICENSE) for details.

This is a **personal project** developed independently. Not affiliated with or endorsed by my employer.

---

## Frequently Asked Questions

**Q: Why did you build this?**  
A: I wanted to understand how algorithmic systems work in practice, not just in theory. This project let me explore causal inference, distributed systems, and measurement methodology.

**Q: Can I use this for my own research?**  
A: Yes. Please cite the repository if you use it in published work.

**Q: Is this production-ready?**  
A: It is research-grade code. It is tested, documented, and containerized, but designed for measurement workflows rather than production ad-serving environments.

**Q: What technical skills did this require?**  
A: Causal inference design, adversarial engineering (bot detection bypass), memory-safe concurrency, proxy management, statistical analysis, and containerized deployment.

---

**Keywords:** algorithmic auditing, causal inference, ad delivery, measurement, distributed systems, Python, Playwright
