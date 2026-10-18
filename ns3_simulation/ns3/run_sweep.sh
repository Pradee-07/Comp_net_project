#!/bin/bash
# Runs all 4 schemes x 5 speeds through the real NS-3 gebpdcm.cc scenario.
set -e
cd ~/ns-allinone-3.42/ns-3.42
echo "Building gebpdcm..."
./ns3 build gebpdcm > /tmp/gebpdcm_build.log 2>&1 || { cat /tmp/gebpdcm_build.log; exit 1; }
mkdir -p ~/computer_networks_project/ns3_simulation/data

SCHEMES="baseline gebpdcm gebpdcm_geo gebpdcm_ltpm"
SPEEDS="10 20 30 40 50"
TOTAL=20
N=0

echo
echo "############################################################"
echo "#  GEbPDCM + LTPM -- full NS-3 sweep (4 schemes x 5 speeds) #"
echo "############################################################"
echo

for scheme in $SCHEMES; do
    for speed in $SPEEDS; do
        N=$((N+1))
        echo "------------------------------------------------------------"
        echo "  Run $N/$TOTAL  --  scheme=$scheme  speed=${speed}m/s"
        echo "------------------------------------------------------------"
        ./build/scratch/ns3.42-gebpdcm-optimized --scheme=${scheme} --speed=${speed} --packetSize=1024 --simTime=100
    done
done

echo "############################################################"
echo "#  SWEEP COMPLETE -- $TOTAL/$TOTAL runs                     #"
echo "############################################################"
echo

python3 - << 'PYEOF'
import csv, os

DATA_DIR = os.path.expanduser("~/computer_networks_project/ns3_simulation/data")
schemes = ["baseline", "gebpdcm", "gebpdcm_geo", "gebpdcm_ltpm"]
speeds = [10, 20, 30, 40, 50]

print(f"{'Scheme':<14}{'Speed':>7}{'Delay(ms)':>12}{'Thpt(Mbps)':>12}{'PDR':>9}{'Drop':>9}{'Energy(mJ)':>12}")
print("-" * 75)
for scheme in schemes:
    for speed in speeds:
        path = os.path.join(DATA_DIR, f"ns3_{scheme}_speed{speed}.csv")
        if not os.path.exists(path):
            continue
        m = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                m[row["metric"]] = float(row["value"])
        print(f"{scheme:<14}{speed:>6}m"
              f"{m['delay_ms']:>12.2f}{m['throughput_mbps']:>12.4f}"
              f"{m['pdr']:>9.4f}{m['packet_drop_ratio']:>9.4f}{m['energy_mj']:>12.1f}")
print("-" * 75)
print("Next: python3 run_all.py && python3 plot_results.py  (from ns3_simulation/)")
PYEOF
