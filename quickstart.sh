#!/usr/bin/env bash
# quickstart.sh — one-script setup and run for the ad-targeting experiment
# Usage:
#   bash quickstart.sh              # 10 trial smoke-test
#   bash quickstart.sh --full       # 200 trial full run
#   bash quickstart.sh --analyse    # re-run analysis on existing data only

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/venv"
PYTHON="$VENV/bin/python"
EXP_DIR="$SCRIPT_DIR"

# Set PYTHONPATH to include repository root for src.* imports
export PYTHONPATH="$EXP_DIR${PYTHONPATH:+:$PYTHONPATH}"

cd "$EXP_DIR"

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[quickstart]${NC} $*"; }
warn()  { echo -e "${YELLOW}[quickstart]${NC} $*"; }
die()   { echo -e "${RED}[quickstart]${NC} $*"; exit 1; }

# ── Parse args ────────────────────────────────────────────────────────────────
TRIALS=10
CONCURRENCY=2
ANALYSE_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --full)      TRIALS=200; CONCURRENCY=4 ;;
    --analyse)   ANALYSE_ONLY=true ;;
    --trials=*)  TRIALS="${arg#*=}" ;;
    --help|-h)
      echo "Usage: bash quickstart.sh [--full] [--analyse] [--trials=N]"
      exit 0 ;;
  esac
done

# ── Step 0: Check Python venv ─────────────────────────────────────────────────
info "Checking Python environment …"
[[ -x "$PYTHON" ]] || die "venv not found at $VENV. Run: python -m venv $VENV && $VENV/bin/pip install -r requirements.txt"
"$PYTHON" -c "import playwright, aiosqlite, mitmproxy" 2>/dev/null \
  || die "Missing packages. Run: $VENV/bin/pip install -r requirements.txt && $VENV/bin/python -m playwright install chromium"

# ── Step 1: Check / install Playwright browsers ───────────────────────────────
info "Checking Playwright Chromium …"
"$PYTHON" -m playwright install chromium --dry-run 2>/dev/null \
  && "$PYTHON" -m playwright install chromium \
  || true

# ── Step 2: Verify mitmdump ───────────────────────────────────────────────────
info "Checking mitmdump …"
MITMDUMP="$VENV/bin/mitmdump"
[[ -x "$MITMDUMP" ]] || die "mitmdump not found. Run: $VENV/bin/pip install mitmproxy"
info "mitmdump → $MITMDUMP"

# ── Step 3: Show active config ────────────────────────────────────────────────
info "Active configuration:"
"$PYTHON" - <<'PYEOF'
from src.config import DB_URL, PROXY_MODE, PROXIES, PROXY_POOR_PORT, PROXY_RICH_PORT
print(f"  DB_URL      : {DB_URL}")
print(f"  PROXY_MODE  : {PROXY_MODE}")
if PROXY_MODE in ("local", "upstream_mitm"):
    print(f"  poor_zip    : http://127.0.0.1:{PROXY_POOR_PORT}  (mitmdump)")
    print(f"  rich_zip    : http://127.0.0.1:{PROXY_RICH_PORT}  (mitmdump)")
else:
    for k, v in PROXIES.items():
        print(f"  {k:10s}: {v}")
PYEOF

# ── Step 4: Analyse-only shortcut ────────────────────────────────────────────
if $ANALYSE_ONLY; then
  info "Running analysis on existing data …"
  mkdir -p out/results
  "$PYTHON" src/analysis.py --output out/results/
  info "Done. Results in $EXP_DIR/out/results/"
  exit 0
fi

# ── Step 5: Run experiment ────────────────────────────────────────────────────
info "Starting experiment: $TRIALS trials, concurrency=$CONCURRENCY …"
info "(proxies are started automatically in 'local' mode)"
echo ""
"$PYTHON" src/experiment.py --trials "$TRIALS" --concurrency "$CONCURRENCY"
echo ""

# ── Step 6: Quick DB sanity check ─────────────────────────────────────────────
info "Database row counts:"
"$PYTHON" - <<'PYEOF'
import asyncio
from src.config import DB_URL

USE_SQLITE = DB_URL.startswith("sqlite")

async def check():
  if USE_SQLITE:
    import aiosqlite
    path = DB_URL.removeprefix("sqlite:///")
    async with aiosqlite.connect(path) as db:
      async with db.execute("SELECT COUNT(*) FROM trials") as c:
        trials = (await c.fetchone())[0]
      async with db.execute("SELECT COUNT(*) FROM ad_observations") as c:
        obs = (await c.fetchone())[0]
      async with db.execute(
        "SELECT zip_condition, COUNT(*) FROM ad_observations GROUP BY zip_condition"
      ) as c:
        breakdown = await c.fetchall()
  else:
    import asyncpg
    conn = await asyncpg.connect(DB_URL)
    trials = await conn.fetchval("SELECT COUNT(*) FROM trials")
    obs = await conn.fetchval("SELECT COUNT(*) FROM ad_observations")
    breakdown = await conn.fetch(
      "SELECT zip_condition, COUNT(*) FROM ad_observations GROUP BY zip_condition ORDER BY zip_condition"
    )
    breakdown = [(r["zip_condition"], r["count"]) for r in breakdown]
    await conn.close()
    print(f"  trials          : {trials}")
    print(f"  total ad obs    : {obs}")
    for row in breakdown:
        print(f"  {row[0]:12s}: {row[1]} obs")

asyncio.run(check())
PYEOF

# ── Step 7: Run analysis ──────────────────────────────────────────────────────
echo ""
info "Running causal analysis …"
mkdir -p out/results
"$PYTHON" src/analysis.py --output out/results/

echo ""
info "═══════════════════════════════════════════════════"
info "  Done."
info "  Dataset  : $EXP_DIR/out/results/observations.csv"
info "  Plots    : $EXP_DIR/out/results/*.png"
info "  Database : $(cd $EXP_DIR && "$PYTHON" -c "from src.config import DB_URL; print(DB_URL)")"
info "═══════════════════════════════════════════════════"
