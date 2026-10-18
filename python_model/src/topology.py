"""
topology.py -- node positions, neighbour graph, and per-node state.

The seeded random topology is the default path, not a fallback: it lets the
Python control layer run standalone before NS-3 has produced anything
(build-order checkpoint, PROJECT_SPEC.md section 8) and it's also the
documented pure-Python mode (PROJECT_SPEC.md section 10) if NS-3 is ever
abandoned. Once NS-3 exports data/topology.csv (non-empty), that real
100-node layout is used instead so the Python layer runs on the *same*
topology as the baseline scheme.
"""

import csv
import math
import os
import random
from dataclasses import dataclass, field

import config


@dataclass
class Node:
    id: int
    x: float
    y: float
    buffer: float = 0.0        # current queued load waiting to be forwarded, Mbps
    load: float = 0.0          # current data load, Mbps (this is the paper's Dl)
    energy: float = 100.0      # remaining energy, mJ (starting budget, arbitrary but shared across schemes)
    trust: float = config.TRUST_INIT
    load_history: list = field(default_factory=list)  # recent load readings, most recent last
    telemetry_history: list = field(default_factory=list)  # recent multivariate (load, delta, n_count, drops, speed) frames
    status: str = "safe"       # 'safe' | 'threshold' | 'congested', set each tick by status.py
    reliable: bool = True      # False = a persistently malicious/failing node (LTPM's trust target, see ltpm.py)


def load_topology(path="data/topology.csv"):
    """Generate a seeded random topology of NUM_NODES nodes over
    AREA_WIDTH x AREA_HEIGHT by default. If NS-3 has exported a non-empty
    data/topology.csv, load that real 100-node layout instead so the Python
    layer runs on the same topology as the NS-3 baseline. A missing CSV
    (the expected state before step 7 wires NS-3 in) is not an error.

    Either way, a fixed ~UNRELIABLE_FRACTION of nodes are marked unreliable
    (deterministically, from the same seed): these represent the
    malicious/failing nodes LTPM's trust scoring exists to route around
    (PROJECT_SPEC.md section 1.2). NS-3 has no notion of node reliability,
    so this is synthesised in Python regardless of where positions came from."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        nodes = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                nodes.append(Node(id=int(row["node_id"]), x=float(row["x"]), y=float(row["y"])))
        if not nodes:
            nodes = None
    else:
        nodes = None

    if nodes is None:
        rng = random.Random(config.RANDOM_SEED)
        nodes = [
            Node(id=i, x=rng.uniform(0, config.AREA_WIDTH), y=rng.uniform(0, config.AREA_HEIGHT))
            for i in range(config.NUM_NODES)
        ]

    reliability_rng = random.Random(config.RANDOM_SEED + 100)
    for n in nodes:
        n.reliable = reliability_rng.random() >= config.UNRELIABLE_FRACTION

    return nodes


def distance(a: Node, b: Node) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def build_neighbours(nodes, tx_range=config.TX_RANGE):
    """Return {node_id: [neighbour_node, ...]} for every pair within tx_range."""
    neighbours = {n.id: [] for n in nodes}
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            if distance(a, b) <= tx_range:
                neighbours[a.id].append(b)
                neighbours[b.id].append(a)
    return neighbours
