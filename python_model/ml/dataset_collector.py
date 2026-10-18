"""
dataset_collector.py -- Harvests multivariate temporal traces from simulator runs.
Outputs data/manet_traces.npy of shape (N_runs * N_nodes, T_ticks, 5)
Features:
  0: load (Mbps)
  1: delta_load (load_t - load_{t-1})
  2: neighbor_count (count within TX_RANGE)
  3: packet_drops (dropped packets this tick)
  4: speed (m/s)
"""

import os
import sys
import numpy as np
import random

# Add python_model/src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import config
import topology
import status
import migrate
import metrics
import simulator

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))


def collect_run_traces(speed, seed, sim_time=config.SIM_TIME):
    """
    Runs a 100-node simulation with the given speed and seed,
    capturing per-node multivariate telemetry at every 1-second tick.
    Returns: numpy array of shape (NUM_NODES, sim_time, 5)
    """
    rng = random.Random(seed)
    nodes = topology.load_topology(os.path.join(DATA_DIR, "topology.csv"))
    targets = {n.id: (rng.uniform(0, config.AREA_WIDTH), rng.uniform(0, config.AREA_HEIGHT)) for n in nodes}
    packet_size_bits = config.DEFAULT_PACKET_SIZE_KB * 1024 * 8

    # Buffer for telemetry: (NUM_NODES, sim_time, 5)
    telemetry = np.zeros((config.NUM_NODES, sim_time, 5), dtype=np.float32)
    prev_loads = np.zeros(config.NUM_NODES, dtype=np.float32)

    for t in range(sim_time):
        # 1. Mobility
        for n in nodes:
            targets[n.id] = simulator._move_random_waypoint(n, targets[n.id], rng, speed)
        neighbours = topology.build_neighbours(nodes)

        # 2. Offered traffic
        offered_this_tick = {}
        for n in nodes:
            offered = rng.uniform(config.DATA_RATE_MIN, config.DATA_RATE_MAX)
            offered_this_tick[n.id] = offered
            n.load += offered

        # 3. Congestion status
        for n in nodes:
            cs = status.congestion_score(n, neighbours)
            n.status = status.classify(cs)

        # 4. Forwarding, drop computation, and telemetry recording
        for n in nodes:
            packets_this_tick = max(1, int(offered_this_tick[n.id] * 1e6 / packet_size_bits / config.PACKET_RATE_SCALE))
            next_hop = simulator._closer_neighbour(n, neighbours, config.BASE_STATION)

            severity = max(0.0, n.load / config.SEVERITY_REFERENCE_LOAD - 0.5)
            intrinsic_fail = 0.0 if n.reliable else config.INTRINSIC_FAIL_RATE
            drop_rate = min(0.02 + 0.12 * severity + intrinsic_fail, 0.6)
            delivered = int(packets_this_tick * (1 - drop_rate))
            dropped_packets = packets_this_tick - delivered

            # Telemetry record for node n at tick t
            delta = n.load - prev_loads[n.id]
            n_count = len(neighbours.get(n.id, []))
            telemetry[n.id, t, 0] = n.load
            telemetry[n.id, t, 1] = delta
            telemetry[n.id, t, 2] = float(n_count)
            telemetry[n.id, t, 3] = float(dropped_packets)
            telemetry[n.id, t, 4] = float(speed)

            prev_loads[n.id] = n.load
            n.load *= 0.5 if next_hop is not None else 0.9

    return telemetry


def harvest_all_datasets():
    os.makedirs(DATA_DIR, exist_ok=True)
    all_runs = []
    speeds = [10, 20, 30, 40, 50]

    print("==============================================================")
    print("  HARVESTING MANET MULTIVARIATE TELEMETRY DATASET")
    print("==============================================================")

    for idx, sp in enumerate(speeds):
        # 2 independent simulation runs per speed with distinct seeds
        for run_id, seed_offset in enumerate([42, 1042]):
            seed = config.RANDOM_SEED + seed_offset + idx * 7
            print(f"  Harvesting speed={sp} m/s [Run {run_id+1}/2, seed={seed}]...")
            run_data = collect_run_traces(speed=sp, seed=seed, sim_time=100)
            all_runs.append(run_data)

    # Combine into single array: (Total_Nodes_x_Runs, Ticks, Features)
    combined = np.concatenate(all_runs, axis=0) # shape: (10 runs * 100 nodes = 1000 nodes, 100 ticks, 5 features)
    out_path = os.path.join(DATA_DIR, "manet_traces.npy")
    np.save(out_path, combined)
    print(f"\n  ✓ Successfully saved {combined.shape} frames to:")
    print(f"    {out_path}")
    print("==============================================================")
    return out_path


if __name__ == "__main__":
    harvest_all_datasets()
