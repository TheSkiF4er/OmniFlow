#!/usr/bin/env bash
#
# OmniFlow — Universal Automation & Workflow Engine
# Benchmark Runner Script
#
# This script executes the full benchmark suite for OmniFlow, including:
#  - Engine performance (scheduler, executor, queue)
#  - Workflow throughput and latency
#  - Plugin/connector benchmarks
#  - CPU/memory profiling
#
# All results are saved into: benchmarks/results/<timestamp>/
#

set -e
set -o pipefail

# ------------------------------
# Colors
# ------------------------------
GREEN="\033[0;32m"
BLUE="\033[0;34m"
RED="\033[0;31m"
YELLOW="\033[1;33m"
NC="\033[0m"

# ------------------------------
# Paths
# ------------------------------
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS_DIR="${ROOT_DIR}/benchmarks/results"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
OUTPUT_DIR="${RESULTS_DIR}/${TIMESTAMP}"

mkdir -p "$OUTPUT_DIR"

# ------------------------------
# Checks
# ------------------------------
echo -e "${BLUE}→ Checking environment...${NC}"

if ! command -v docker >/dev/null 2>&1; then
  echo -e "${RED}Docker is required but not installed.${NC}"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo -e "${RED}Docker daemon is not running.${NC}"
  exit 1
fi

echo -e "${GREEN}✓ Environment OK${NC}\n"

# ------------------------------
# Functions
# ------------------------------

log_section() {
  echo -e "\n${YELLOW}========================================${NC}"
  echo -e "${YELLOW}$1${NC}"
  echo -e "${YELLOW}========================================${NC}"
}

run_docker_benchmark() {
  local name="$1"
  local script="$2"

  log_section "Running ${name}"

  docker run --rm \
    -v "${ROOT_DIR}:/omniflow" \
    -w /omniflow/benchmarks \
    --name "omniflow_bench_${name}" \
    python:3.11 \
    bash -c "chmod +x ${script} && ./${script}" \
    | tee "${OUTPUT_DIR}/${name}.log"
}

# ------------------------------
# CPU & Memory Profiling Baseline
# ------------------------------

log_section "Baseline System Metrics"

{
  echo "Timestamp         : ${TIMESTAMP}"
  echo "CPU Model         : $(lscpu | grep 'Model name:' | sed 's/Model name:[ ]*//')"
  echo "Cores             : $(nproc)"
  echo "Memory Total      : $(grep MemTotal /proc/meminfo | awk '{print $2 " kB"}')"
  echo "Docker Version    : $(docker --version)"
} | tee "${OUTPUT_DIR}/baseline_system.txt"

echo -e "${GREEN}✓ Baseline collected${NC}"

# ------------------------------
# Engine Benchmarks
# ------------------------------

log_section "Starting Engine Benchmarks"

if [ -f "${ROOT_DIR}/benchmarks/engine_benchmark.py" ]; then
  run_docker_benchmark "engine_benchmark" "engine_benchmark.py"
else
  echo -e "${RED}Missing: engine_benchmark.py${NC}"
fi

# ------------------------------
# Workflow Throughput Benchmarks
# ------------------------------

log_section "Workflow Throughput Tests"

if [ -f "${ROOT_DIR}/benchmarks/workflow_throughput.py" ]; then
  run_docker_benchmark "workflow_throughput" "workflow_throughput.py"
else
  echo -e "${RED}Missing: workflow_throughput.py${NC}"
fi

# ------------------------------
# Plugin / Connector Tests
# ------------------------------

PLUGIN_BENCH_DIR="${ROOT_DIR}/benchmarks/plugins"

log_section "Plugin / Connector Benchmarks"

if [ -d "$PLUGIN_BENCH_DIR" ]; then
  for script in "$PLUGIN_BENCH_DIR"/*.py; do
    [ -e "$script" ] || continue
    name=$(basename "$script" .py)
    run_docker_benchmark "plugin_${name}" "plugins/${name}.py"
  done
else
  echo -e "${YELLOW}No plugin benchmarking directory found.${NC}"
fi

# ------------------------------
# Final Summary
# ------------------------------

log_section "Benchmark Suite Completed"

echo -e "${GREEN}Results saved to:${NC} ${OUTPUT_DIR}"
echo -e "${BLUE}To view logs:${NC} ls -1 \"${OUTPUT_DIR}\""
echo -e "${GREEN}✓ Done.${NC}"

exit 0
