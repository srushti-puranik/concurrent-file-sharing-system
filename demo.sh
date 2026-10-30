#!/bin/bash
# Team 19 scripted demo: run from the project folder
set -e
cd "$(dirname "$0")"
cleanup() { pkill -f src/server.py 2>/dev/null || true; sudo mn -c >/dev/null 2>&1; sudo ovs-vsctl --if-exists del-br s1; }
echo "### 1. Runtime policy"; cleanup; sudo python3 tests/runtime_policy.py
echo "### 2. Hard scenario"; cleanup; sudo python3 tests/hard_scenario.py
echo "### 3. Performance (about 10 minutes)"; cleanup; sudo python3 perf/run_perf.py
python3 perf/make_graphs.py
echo "### Done. Results are in results/"
