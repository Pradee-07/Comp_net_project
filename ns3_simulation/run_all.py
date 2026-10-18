"""
run_all.py -- runs 4 schemes x 5 speeds (+ packet sizes if time allows) and
writes results/all_results.csv (PROJECT_SPEC.md section 7.10).

All 4 schemes in the main speed sweep are now sourced from real NS-3
packet-level runs (ns3/gebpdcm.cc), not the Python simulator. GEbPDCM's
threshold+migration, GEO's attack/cruise search, and LTPM's trust/forecast
logic all run inside the actual ns-3.42 simulation now: real per-node WiFi
MAC queue occupancy drives Eq2-5 classification, migration is a real
bounded-duration Ipv4 static-route override that reroutes actual packets,
and LTPM's trust EWMA is fed by real Ipv4L3Protocol Tx/Drop trace counts
instead of a synthetic coin flip. See ns3/gebpdcm.cc's header for the full
design (real load signal, real migration mechanism, real trust signal).

src/simulator.py, geo.py, ltpm.py, migrate.py, status.py remain in the repo
as the original design-reference implementation -- the paper's equations
worked out and validated in Python first, before being ported to NS-3 -- but
they no longer produce the numbers in results/speed_sweep.csv.

This build's WiFi PHY was initially delivering 0 packets in every scenario,
including its own unmodified reference example (examples/routing/
manet-routing-compare.cc) -- traced with a debug-profile NS_LOG rebuild to
every HR/DSSS frame being logged "Unsupported modulation received ...
consider as noise" and discarded at receive time. Switching DataMode/
ControlMode/NonUnicastMode to DsssRate2Mbps (plain DSSS, not HR/DSSS) fixed
real packet delivery; see ns3/gebpdcm.cc's header for the full account.

Real finding worth documenting honestly (see report Discussion): on this
real, shared-medium 2Mbps DSSS channel, GEbPDCM's migration consistently
scores *worse* than plain baseline (no congestion control at all) across
every speed -- rerouting via a neighbour adds a hop across the same
contended channel rather than relieving it, and the frequent route
overrides add AODV/static-route churn. GEO and LTPM's smarter target
selection claw back a little of that gap versus plain nearest-free
migration but do not close it. This is reported as found, not hidden or
re-tuned away -- the paper's own claimed GEO/primal-dual framing has the
same kind of gap (see report Section 7).
"""

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import config

RESULTS_DIR = "results"
DATA_DIR = "data"


def load_ns3_csv(scheme, speed):
    path = os.path.join(DATA_DIR, f"ns3_{scheme}_speed{speed}.csv")
    metrics = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            metrics[row["metric"]] = float(row["value"])
    return {
        "scheme": scheme,
        "speed": speed,
        "packet_size_kb": config.DEFAULT_PACKET_SIZE_KB,
        "delay_ms": metrics["delay_ms"],
        "throughput_mbps": metrics["throughput_mbps"],
        "pdr": metrics["pdr"],
        "packet_drop_ratio": metrics["packet_drop_ratio"],
        "energy_mj": metrics["energy_mj"],
    }


def _progress(label, done, total):
    # Prints at 25/50/75/100% only -- a handful of clean lines rather than a
    # \r-updating line, so this looks right whether stdout is an interactive
    # terminal or piped/redirected somewhere that doesn't honour carriage returns.
    step = max(total // 4, 1)
    if done == total or done % step == 0:
        pct = done * 100 // total
        print(f"  {label:<28} {pct:>3}%  [{done:>2}/{total}]")


def run_speed_sweep():
    """4 schemes x 5 speeds, default packet size (PROJECT_SPEC.md section 5,
    priority sweep) -- read from ns3/gebpdcm.cc's real sweep output
    (data/ns3_<scheme>_speed<N>.csv, produced by ns3/run_sweep.sh)."""
    rows = []
    combos = [(s, sp) for s in config.SCHEMES for sp in config.SPEED_SWEEP]
    for i, (scheme, speed) in enumerate(combos, 1):
        _progress("Loading NS-3 speed sweep", i, len(combos))
        rows.append(load_ns3_csv(scheme, speed))
    print()
    return rows


def run_packet_size_sweep():
    """4 schemes x 5 packet sizes, default speed (PROJECT_SPEC.md section 5,
    cut first if short on time). NOT ported to NS-3 (only the priority speed
    sweep was, per the spec's own cut order) -- still sourced from the
    Python reference simulator, kept separate and clearly labelled."""
    import simulator  # imported here, not at module level, so this stays
                       # the only place the Python simulator is still used

    rows = []
    combos = [(s, kb) for s in config.SCHEMES for kb in config.PACKET_SIZE_SWEEP]
    for i, (scheme, packet_size_kb) in enumerate(combos, 1):
        _progress("Packet-size sweep (Python)", i, len(combos))
        result = simulator.run(scheme, speed=config.DEFAULT_SPEED, packet_size_kb=packet_size_kb)
        result.pop("migrations", None)  # not present in the NS3-sourced speed_sweep rows; keep columns aligned
        rows.append(result)
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
    print("  GEbPDCM + LTPM -- v1 (real NS-3) results build")
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
