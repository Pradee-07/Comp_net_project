#!/bin/bash
# Parallel sweep runner for real NS-3 gebpdcm.cc (4 schemes x 5 speeds = 20 runs)
# Uses 4 concurrent workers to complete the sweep in ~10 minutes on a 12-core system.
set -e

NS3_DIR="$HOME/ns-allinone-3.42/ns-3.42"
OUT_DIR="$HOME/computer_networks_project/ns3_simulation/data"
mkdir -p "$OUT_DIR"

cd "$NS3_DIR"
echo "=== Building gebpdcm executable ==="
./ns3 build gebpdcm

SCHEMES="baseline gebpdcm gebpdcm_geo gebpdcm_ltpm"
SPEEDS="10 20 30 40 50"
MAX_JOBS=4

echo "=== Launching 20 NS-3 simulation runs (max $MAX_JOBS concurrent) ==="
START_TIME=$(date +%s)

for scheme in $SCHEMES; do
    for speed in $SPEEDS; do
        while [ $(jobs -r -p | wc -l) -ge $MAX_JOBS ]; do
            sleep 2
        done
        echo "[$(date '+%H:%M:%S')] Starting scheme=$scheme speed=${speed} m/s..."
        (
            ./build/scratch/ns3.42-gebpdcm-optimized --scheme="$scheme" --speed="$speed" --packetSize=1024 --simTime=100 > "/tmp/ns3_${scheme}_${speed}.log" 2>&1
            echo "[$(date '+%H:%M:%S')] Finished scheme=$scheme speed=${speed} m/s"
        ) &
    done
done

echo "Waiting for remaining background jobs to finish..."
wait

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
echo "=== All 20 NS-3 runs completed in ${ELAPSED} seconds ==="

echo
echo "=========================================================================="
echo "                   FULL NS-3 SIMULATION RESULTS SUMMARY                   "
echo "=========================================================================="
python3 - << 'PYEOF'
import csv, os

DATA_DIR = os.path.expanduser("~/computer_networks_project/ns3_simulation/data")
schemes = ["baseline", "gebpdcm", "gebpdcm_geo", "gebpdcm_ltpm"]
speeds = [10, 20, 30, 40, 50]

print(f"{'Scheme':<15}{'Speed':>7}{'Delay(ms)':>12}{'Thpt(Mbps)':>12}{'PDR':>9}{'Drop':>9}{'Energy(mJ)':>12}")
print("-" * 76)
for scheme in schemes:
    for speed in speeds:
        path = os.path.join(DATA_DIR, f"ns3_{scheme}_speed{speed}.csv")
        if not os.path.exists(path):
            continue
        m = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                m[row["metric"]] = float(row["value"])
        print(f"{scheme:<15}{speed:>6}m"
              f"{m['delay_ms']:>12.2f}{m['throughput_mbps']:>12.4f}"
              f"{m['pdr']:>9.4f}{m['packet_drop_ratio']:>9.4f}{m['energy_mj']:>12.1f}")
print("-" * 76)
PYEOF
