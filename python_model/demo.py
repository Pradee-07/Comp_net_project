"""
demo.py -- live matplotlib animation for the examiner (PROJECT_SPEC.md section 7.12).

- 100 nodes plotted, base station marked at (500, 450)
- Green = safe, yellow = threshold, red = congested
- An arrow is drawn for one animation frame whenever a load migration happens
- Live counter: packets delivered / dropped, running total
- Runs in well under 60 seconds real time

This reuses the exact same building blocks as simulator.py (topology,
status, migrate, geo, ltpm, metrics) rather than re-implementing the tick
loop's logic -- it only adds the bookkeeping needed to draw each frame.
Defaults to scheme 'gebpdcm_ltpm' since it produces the most migrations
(including preemptive ones), which makes for the most informative demo.
"""

import argparse
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.animation as animation
import matplotlib.pyplot as plt

import config
import ltpm
import metrics
import migrate
import simulator
import status
import topology

STATUS_COLOR = {"safe": "#2ecc71", "threshold": "#f1c40f", "congested": "#e74c3c"}


def build_frames(scheme, speed=config.DEFAULT_SPEED, packet_size_kb=config.DEFAULT_PACKET_SIZE_KB,
                  topology_path="data/topology.csv"):
    """Replays simulator.py's tick loop, recording per-tick state for animation.
    Returns a list of frame dicts: positions, statuses, migration arrows,
    cumulative delivered/dropped counts."""
    rng = random.Random(config.RANDOM_SEED)
    trust_rng = random.Random(config.RANDOM_SEED + 1)

    nodes = topology.load_topology(topology_path)
    targets = {n.id: (rng.uniform(0, config.AREA_WIDTH), rng.uniform(0, config.AREA_HEIGHT)) for n in nodes}
    packet_size_bits = packet_size_kb * 1024 * 8
    use_migration = scheme != "baseline"

    cum_delivered = 0
    cum_dropped = 0
    frames = []

    for t in range(config.SIM_TIME):
        for n in nodes:
            targets[n.id] = simulator._move_random_waypoint(n, targets[n.id], rng, speed)
        neighbours = topology.build_neighbours(nodes)

        offered_this_tick = {}
        for n in nodes:
            offered = rng.uniform(config.DATA_RATE_MIN, config.DATA_RATE_MAX)
            offered_this_tick[n.id] = offered
            n.load += offered
            n.load_history.append(n.load)
            if len(n.load_history) > config.FORECAST_WINDOW:
                n.load_history.pop(0)

        for n in nodes:
            cs = status.congestion_score(n, neighbours)
            n.status = status.classify(cs)

        arrows = []
        if use_migration:
            for n in nodes:
                needs_migration = n.status == "congested" or (
                    scheme == "gebpdcm_ltpm" and ltpm.should_preemptively_migrate(n)
                )
                if needs_migration:
                    candidates = neighbours.get(n.id, [])
                    tgt = migrate.select_target(n, candidates, scheme, network=nodes)
                    if tgt is not None:
                        arrows.append(((n.x, n.y), (tgt.x, tgt.y)))
                        migrate.migrate_half(n, tgt)

        tick_delivered = 0
        tick_dropped = 0
        for n in nodes:
            packets_this_tick = max(1, int(offered_this_tick[n.id] * 1e6 / packet_size_bits / config.PACKET_RATE_SCALE))
            if n.load <= 0:
                tick_dropped += packets_this_tick
                continue
            severity = max(0.0, n.load / config.SEVERITY_REFERENCE_LOAD - 0.5)
            intrinsic_fail = 0.0 if n.reliable else config.INTRINSIC_FAIL_RATE
            drop_rate = min(0.02 + 0.12 * severity + intrinsic_fail, 0.6)
            delivered = int(packets_this_tick * (1 - drop_rate))
            tick_delivered += delivered
            tick_dropped += packets_this_tick - delivered
            if scheme == "gebpdcm_ltpm":
                ltpm.update_trust(n, success=(trust_rng.random() > drop_rate))
            next_hop = simulator._closer_neighbour(n, neighbours, config.BASE_STATION)
            n.load *= 0.5 if next_hop is not None else 0.9

        cum_delivered += tick_delivered
        cum_dropped += tick_dropped

        frames.append({
            "t": t,
            "positions": [(n.x, n.y) for n in nodes],
            "statuses": [n.status for n in nodes],
            "arrows": arrows,
            "delivered": cum_delivered,
            "dropped": cum_dropped,
        })

    return frames


def animate(frames, scheme):
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.set_xlim(0, config.AREA_WIDTH)
    ax.set_ylim(0, config.AREA_HEIGHT)
    ax.set_title(f"GEbPDCM + LTPM live demo -- scheme: {scheme}")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")

    bs_x, bs_y = config.BASE_STATION
    ax.scatter([bs_x], [bs_y], marker="^", s=250, c="blue", label="Base Station", zorder=5)

    scat = ax.scatter([], [], s=60, zorder=4)
    counter_text = ax.text(0.02, 0.98, "", transform=ax.transAxes, va="top", fontsize=11,
                            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))
    arrow_artists = []

    legend_handles = [
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=c, markersize=10, label=lbl)
        for lbl, c in [("Safe", STATUS_COLOR["safe"]), ("Threshold", STATUS_COLOR["threshold"]),
                       ("Congested", STATUS_COLOR["congested"])]
    ]
    legend_handles.append(plt.Line2D([0], [0], marker="^", color="w", markerfacecolor="blue",
                                      markersize=14, label="Base Station"))
    ax.legend(handles=legend_handles, loc="upper right")

    def update(i):
        frame = frames[i]
        xs, ys = zip(*frame["positions"])
        colors = [STATUS_COLOR[s] for s in frame["statuses"]]
        scat.set_offsets(list(zip(xs, ys)))
        scat.set_color(colors)

        for a in arrow_artists:
            a.remove()
        arrow_artists.clear()
        for (sx, sy), (dx, dy) in frame["arrows"]:
            arrow = ax.annotate("", xy=(dx, dy), xytext=(sx, sy),
                                 arrowprops=dict(arrowstyle="->", color="black", lw=1.5, alpha=0.8))
            arrow_artists.append(arrow)

        counter_text.set_text(
            f"t = {frame['t']}s\ndelivered: {frame['delivered']}\ndropped: {frame['dropped']}\n"
            f"migrations this tick: {len(frame['arrows'])}"
        )
        return [scat, counter_text] + arrow_artists

    # SIM_TIME frames within well under 60s real time.
    interval_ms = max(1, int(55000 / len(frames)))
    anim = animation.FuncAnimation(fig, update, frames=len(frames), interval=interval_ms, blit=False, repeat=False)
    return fig, anim


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheme", default="gebpdcm_ltpm", choices=config.SCHEMES)
    parser.add_argument("--speed", type=float, default=config.DEFAULT_SPEED)
    parser.add_argument("--save", default="results/demo.gif",
                         help="save animation to this path (default results/demo.gif; headless-safe)")
    args = parser.parse_args()

    print(f"building frames for scheme={args.scheme} ...")
    frames = build_frames(args.scheme, speed=args.speed)
    fig, anim = animate(frames, args.scheme)

    os.makedirs(os.path.dirname(args.save) or ".", exist_ok=True)
    print(f"saving animation to {args.save} ...")
    anim.save(args.save, writer="pillow", fps=max(1, len(frames) // 55))
    print("done")


if __name__ == "__main__":
    main()
