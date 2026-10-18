"""
plot_results.py -- five graphs (each with the 4 scheme curves vs node speed)
plus results/comparison_table.csv against the paper's Table 9 baselines
(PROJECT_SPEC.md section 7.11).
"""

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config

RESULTS_DIR = "results"

METRICS = [
    ("delay_ms", "Delay (ms)", "delay_vs_speed.png"),
    ("throughput_mbps", "Throughput (Mbps)", "throughput_vs_speed.png"),
    ("pdr", "Packet Delivery Ratio", "pdr_vs_speed.png"),
    ("packet_drop_ratio", "Packet Drop Ratio", "drop_ratio_vs_speed.png"),
    ("energy_mj", "Energy Consumption (mJ)", "energy_vs_speed.png"),
]

SCHEME_LABELS = {
    "baseline": "Baseline (AODV)",
    "gebpdcm": "GEbPDCM",
    "gebpdcm_geo": "GEbPDCM + GEO",
    "gebpdcm_ltpm": "GEbPDCM + GEO + LTPM (ours)",
}


def load_speed_sweep(path):
    """Return {scheme: {speed: {metric: value}}}, speed sweep rows only."""
    data = {s: {} for s in config.SCHEMES}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            scheme = row["scheme"]
            speed = int(float(row["speed"]))
            data[scheme][speed] = {k: float(row[k]) for k in row if k not in ("scheme", "speed", "packet_size_kb")}
    return data


def plot_metric(data, metric, ylabel, filename):
    plt.figure(figsize=(7, 5))
    for scheme in config.SCHEMES:
        speeds = sorted(data[scheme].keys())
        values = [data[scheme][s][metric] for s in speeds]
        plt.plot(speeds, values, marker="o", label=SCHEME_LABELS[scheme])
    plt.xlabel("Node speed (m/s)")
    plt.ylabel(ylabel)
    plt.title(f"{ylabel} vs Node Speed")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    out_path = os.path.join(RESULTS_DIR, filename)
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"  ✓ {out_path}")


# Paper's Table 9 -- ten prior methods' reported delay/throughput/PDR, for
# side-by-side comparison against our four schemes. Values transcribed from
# the paper; not something we can regenerate, only report alongside ours.
PAPER_TABLE_9 = [
    ("Edge computing", 78.2, 62.1, 0.94),
    ("Latency Control", 71.5, 65.8, 0.95),
    ("Congestion controlling protocol", 69.3, 68.2, 0.955),
    ("Resource allocation", 66.1, 71.4, 0.96),
    ("MEC", 62.8, 74.9, 0.965),
    ("DRL", 58.4, 79.3, 0.97),
    ("ACFP", 54.9, 82.7, 0.975),
    ("UA with SG", 51.2, 86.1, 0.978),
    ("Game theory", 47.6, 89.4, 0.982),
    ("6TSCHD", 44.3, 91.8, 0.985),
    ("Proposed (paper's GEbPDCM)", 65.1, 93.7, 0.988),
]


def write_comparison_table(data):
    """Our 4 schemes (averaged over the speed sweep) vs the paper's Table 9."""
    out_path = os.path.join(RESULTS_DIR, "comparison_table.csv")
    our_rows = []
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["method", "delay_ms", "throughput_mbps", "pdr"])
        for scheme in config.SCHEMES:
            speeds = data[scheme]
            n = len(speeds)
            avg_delay = sum(v["delay_ms"] for v in speeds.values()) / n
            avg_throughput = sum(v["throughput_mbps"] for v in speeds.values()) / n
            avg_pdr = sum(v["pdr"] for v in speeds.values()) / n
            row = [SCHEME_LABELS[scheme], round(avg_delay, 2), round(avg_throughput, 2), round(avg_pdr, 4)]
            writer.writerow(row)
            our_rows.append(row)
        for name, delay, throughput, pdr in PAPER_TABLE_9:
            writer.writerow([f"[paper] {name}", delay, throughput, pdr])
    print(f"  ✓ {out_path}")
    return our_rows


def print_summary_table(rows):
    print()
    print("-" * 62)
    print(f"  {'Scheme':<28}{'Delay(ms)':>10}{'Thpt(Mbps)':>12}{'PDR':>9}")
    print("-" * 62)
    for method, delay, throughput, pdr in rows:
        print(f"  {method:<28}{delay:>10.2f}{throughput:>12.2f}{pdr:>9.4f}")
    print("-" * 62)
    print("  (full 4-vs-paper's-Table-9 comparison in comparison_table.csv)")
    print()


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("=" * 50)
    print("  Generating graphs + comparison table")
    print("=" * 50)
    data = load_speed_sweep(os.path.join(RESULTS_DIR, "speed_sweep.csv"))
    for metric, ylabel, filename in METRICS:
        plot_metric(data, metric, ylabel, filename)
    our_rows = write_comparison_table(data)
    print_summary_table(our_rows)


if __name__ == "__main__":
    main()
