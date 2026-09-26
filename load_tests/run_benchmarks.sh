#!/usr/bin/env bash
set -euo pipefail

# Slice 9: runs four benchmark tiers -- an unsaturated baseline (15 users, at
# this architecture's DB-connection-pool ceiling) plus CLAUDE.md's literal
# example tiers (100/500/1000 concurrent users) -- against a live app
# instance started WITHOUT --reload. docker-compose.yml's dev command uses
# `uvicorn --reload` (file-watching overhead not representative of the
# Dockerfile's plain production CMD); this script starts its own detached
# instance without it instead of just documenting the discrepancy as a
# caveat.
#
# Usage: ./load_tests/run_benchmarks.sh
# Prerequisite: ./venv/bin/pip install -e ".[load-test]"
#
# See docs/plans/slice-09-load-chaos-testing.md for the full protocol and
# README.md's "Load Testing" section for the actual recorded results.

cd "$(dirname "$0")/.."

HOST="http://localhost:8000"
DURATION="60s"
RESULTS_DIR="load_tests/results"
CONTAINER_NAME="ride_matching_benchmark_app"

mkdir -p "$RESULTS_DIR"

echo "=== Preparing benchmark target ==="
docker compose up -d postgres redis
docker compose stop app 2>/dev/null || true
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

docker compose run --rm app alembic upgrade head

docker compose run --rm -d -p 8000:8000 --name "$CONTAINER_NAME" app \
    uvicorn app.main:app --host 0.0.0.0 --port 8000

echo "Waiting for $HOST/health ..."
for _ in $(seq 1 30); do
    if curl -sf "$HOST/health" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl -sf "$HOST/health"
echo

run_tier() {
    local tier_name="$1"
    local users="$2"
    local spawn_rate="$3"

    echo
    echo "=== Tier: $tier_name ($users users, spawn rate $spawn_rate/s, ${DURATION}) ==="
    curl -s "$HOST/stats" -o "$RESULTS_DIR/${tier_name}_stats_before.json"

    ./venv/bin/locust -f load_tests/locustfile.py --host "$HOST" \
        --users "$users" --spawn-rate "$spawn_rate" --run-time "$DURATION" \
        --headless --csv "$RESULTS_DIR/${tier_name}" --csv-full-history \
        --only-summary || true

    curl -s "$HOST/stats" -o "$RESULTS_DIR/${tier_name}_stats_after.json"
    echo "--- stats after ($tier_name) ---"
    cat "$RESULTS_DIR/${tier_name}_stats_after.json"
    echo
}

run_tier "01_baseline_15users" 15 5
run_tier "02_100users" 100 20
run_tier "03_500users" 500 50
run_tier "04_1000users" 1000 100

echo
echo "=== Done. Results in $RESULTS_DIR ==="
docker stop "$CONTAINER_NAME" > /dev/null
