"""
run_all.py -- runs 4 schemes x 5 speeds (+ packet sizes) and writes
results/all_results.csv (PROJECT_SPEC.md section 7.10).

THIS IS v2 (the pure-Python version): all 4 schemes -- baseline, gebpdcm,
gebpdcm_geo, gebpdcm_ltpm -- are computed by src/simulator.py's tick loop, an
aggregate, per-node discrete-time model (not a packet-level simulator). NS-3
is used only to export the real 100-node topology (data/topology.csv);
nothing else here talks to NS-3.

See ~/computer_networks_project/ns3_simulation for the sibling version where the same
GEbPDCM/GEO/LTPM logic runs inside a real NS-3 packet-level simulation
instead, and the project's Lab Report PDF for why the two versions'
results disagree despite implementing the same algorithm.
"""

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import config
import simulator

RESULTS_DIR = "results"


def _progress(label, done, total):
    # Prints at 25/50/75/100% only -- a handful of clean lines rather than a
    # \r-updating line, so this looks right whether stdout is an interactive
    # terminal or piped/redirected somewhere that doesn't honour carriage returns.
    step = max(total // 4, 1)
    if done == total or done % step == 0:
        pct = done * 100 // total
        print(f"  {label:<28} {pct:>3}%  [{done:>2}/{total}]")


def run_speed_sweep():
    """4 schemes x 5 speeds, default packet size (PROJECT_SPEC.md section 5, priority sweep)."""
    rows = []
    combos = [(s, sp) for s in config.SCHEMES for sp in config.SPEED_SWEEP]
    for i, (scheme, speed) in enumerate(combos, 1):
        _progress("Speed sweep", i, len(combos))
        rows.append(simulator.run(scheme, speed=speed, packet_size_kb=config.DEFAULT_PACKET_SIZE_KB))
    print()
    return rows


def run_packet_size_sweep():
    """4 schemes x 5 packet sizes, default speed (PROJECT_SPEC.md section 5, cut first if short on time)."""
    rows = []
    combos = [(s, kb) for s in config.SCHEMES for kb in config.PACKET_SIZE_SWEEP]
    for i, (scheme, packet_size_kb) in enumerate(combos, 1):
        _progress("Packet-size sweep", i, len(combos))
        rows.append(simulator.run(scheme, speed=config.DEFAULT_SPEED, packet_size_kb=packet_size_kb))
    print()
    return rows


def write_csv(rows, path):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  ✓ {path}  ({len(rows)} rows)")


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    print("=" * 50)
    print("  GEbPDCM + LTPM -- v2 (Python model) results build")
    print("=" * 50)

    speed_rows = run_speed_sweep()
    packet_rows = run_packet_size_sweep()
    all_rows = speed_rows + packet_rows

    print()
    write_csv(speed_rows, os.path.join(RESULTS_DIR, "speed_sweep.csv"))
    write_csv(packet_rows, os.path.join(RESULTS_DIR, "packet_size_sweep.csv"))
    write_csv(all_rows, os.path.join(RESULTS_DIR, "all_results.csv"))
    print("=" * 50)
    print("  Next: python3 plot_results.py")
    print("=" * 50)


if __name__ == "__main__":
    main()
