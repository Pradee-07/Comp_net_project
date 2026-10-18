"""
simulator.py -- the tick loop (PROJECT_SPEC.md section 7.9).

This is an aggregate, per-node discrete-time simulation (not a packet-level
simulator like NS-3): each of the SIM_TIME ticks represents one second.
Every node accumulates incoming traffic load, and that load is forwarded
one hop at a time towards the base station, along the chain of neighbours
that get it closer to BASE_STATION. A node whose status is 'congested'
drops a fraction of what it is holding instead of forwarding it -- until
GEbPDCM migrates half its load away.

This abstraction is what lets the same Python layer represent all three
proposed schemes without a custom NS-3 module (see PROJECT_SPEC.md section 3
for why: NS-3's AODV does not expose per-node buffer migration).

run(scheme, speed, packet_size_kb) -> dict of the Eq 9-12 metrics + energy.
"""

import random

import config
import ltpm
import metrics
import migrate
import status
import topology


def _move_random_waypoint(node, target, rng, speed, dt=1.0):
    """One second of random-waypoint mobility at `speed` m/s. Returns the
    (possibly new) target the node is walking towards."""
    dx, dy = target[0] - node.x, target[1] - node.y
    dist = (dx ** 2 + dy ** 2) ** 0.5
    if dist < speed * dt or dist == 0:
        node.x, node.y = target
        target = (rng.uniform(0, config.AREA_WIDTH), rng.uniform(0, config.AREA_HEIGHT))
    else:
        node.x += dx / dist * speed * dt
        node.y += dy / dist * speed * dt
    return target


def _closer_neighbour(node, neighbours, base_station):
    """Among node's neighbours, the one that reduces distance-to-BS the most.
    None if no neighbour gets us closer (node forwards directly / is the hop nearest BS)."""
    bs_node = topology.Node(id=-1, x=base_station[0], y=base_station[1])
    my_dist = topology.distance(node, bs_node)
    best, best_dist = None, my_dist
    for nb in neighbours.get(node.id, []):
        d = topology.distance(nb, bs_node)
        if d < best_dist:
            best, best_dist = nb, d
    return best


def run(scheme, speed=config.DEFAULT_SPEED, packet_size_kb=config.DEFAULT_PACKET_SIZE_KB,
        topology_path="data/topology.csv"):
    rng = random.Random(config.RANDOM_SEED)
    # Separate RNG stream for LTPM's trust coin-flip: it must not consume
    # from `rng`, or its extra draws would shift every later mobility/traffic
    # call for the gebpdcm_ltpm scheme only, silently giving that scheme a
    # different offered-traffic sequence than the other three and breaking
    # the fair, same-topology-same-traffic comparison across schemes.
    trust_rng = random.Random(config.RANDOM_SEED + 1)

    nodes = topology.load_topology(topology_path)
    targets = {n.id: (rng.uniform(0, config.AREA_WIDTH), rng.uniform(0, config.AREA_HEIGHT)) for n in nodes}
    packet_size_bits = packet_size_kb * 1024 * 8

    # raw counters accumulated over the whole run, used to compute Eq 9-12
    packets_sent = 0
    packets_received = 0
    total_delay_ms = 0.0
    hops_transmitted = 0
    hops_received = 0
    migrations = 0

    use_migration = scheme != "baseline"

    for t in range(config.SIM_TIME):
        # 1. move nodes (random waypoint at `speed`)
        for n in nodes:
            targets[n.id] = _move_random_waypoint(n, targets[n.id], rng, speed)
        neighbours = topology.build_neighbours(nodes)

        # 2. generate traffic: each node offers a random load in the paper's
        #    data-rate range for this tick (Mbps demand). offered_this_tick is
        #    recorded separately from n.load: it is what step 6 counts as
        #    "packets sent" (so every scheme is compared on the *same* total
        #    traffic), while n.load (mutated by migration below) is what
        #    drives the congestion/drop-rate model -- migration is supposed
        #    to change who experiences congestion, not how much traffic the
        #    network is asked to carry in the first place.
        offered_this_tick = {}
        for n in nodes:
            offered = rng.uniform(config.DATA_RATE_MIN, config.DATA_RATE_MAX)
            offered_this_tick[n.id] = offered
            prev_load = n.load
            n.load += offered
            n.load_history.append(n.load)
            if len(n.load_history) > config.FORECAST_WINDOW:
                n.load_history.pop(0)

            # Record telemetry for Tiny-TTS Transformer
            delta = n.load - prev_load
            n_count = len(neighbours.get(n.id, []))
            prev_drops = getattr(n, "last_drops", 0.0)
            telemetry_frame = [n.load, delta, float(n_count), float(prev_drops), float(speed)]
            n.telemetry_history.append(telemetry_frame)
            if len(n.telemetry_history) > 10:
                n.telemetry_history.pop(0)

        # 3. compute status for every node (status.py)
        for n in nodes:
            cs = status.congestion_score(n, neighbours)
            n.status = status.classify(cs)

        # 4 & 5. decide who needs migration, then migrate
        #    scheme 2/3 -> classify(cs) == 'congested'
        #    scheme 4   -> that, OR ltpm.should_preemptively_migrate() (forecast-based, ahead of congestion)
        if use_migration:
            if scheme == "gebpdcm_ltpm":
                ltpm.batch_evaluate_nodes(nodes)

            for n in nodes:
                needs_migration = n.status == "congested" or (
                    scheme == "gebpdcm_ltpm" and ltpm.should_preemptively_migrate(n)
                )
                if needs_migration:
                    candidates = neighbours.get(n.id, [])
                    target = migrate.select_target(n, candidates, scheme, network=nodes)
                    if target is not None:
                        migrate.migrate_half(n, target)
                        migrations += 1

        # 6. forward each node's current load one hop towards the base station;
        #    log delivery/drop/delay, deduct energy
        for n in nodes:
            # offered_this_tick is a Mbps *rate*; dividing by PACKET_RATE_SCALE
            # (not the full SIM_TIME) converts it to a believable number of
            # packets for this one-second tick -- treating the full 1-second
            # capacity as literally available every tick would put aggregate
            # throughput two orders of magnitude past the paper's own
            # reported range, since 100 nodes offering up to 49 Mbps each
            # would imply a multi-gigabit network.
            packets_this_tick = max(1, int(offered_this_tick[n.id] * 1e6 / packet_size_bits / config.PACKET_RATE_SCALE))
            packets_sent += packets_this_tick
            if n.load <= 0:
                continue

            next_hop = _closer_neighbour(n, neighbours, config.BASE_STATION)

            # Drop rate and queuing delay are continuous functions of how far
            # this node's *current* load sits above SEVERITY_REFERENCE_LOAD
            # (not the paper's O_l -- see config.py), evaluated after this
            # tick's migration (step 4/5) already ran. This is what makes
            # migration quality visible in the
            # aggregate metrics every tick, not just on the rare tick a node
            # is formally classified 'congested' (classify() still gates
            # *whether* a migration triggers; severity drives its payoff).
            severity = max(0.0, n.load / config.SEVERITY_REFERENCE_LOAD - 0.5)
            intrinsic_fail = 0.0 if n.reliable else config.INTRINSIC_FAIL_RATE
            drop_rate = min(0.02 + 0.12 * severity + intrinsic_fail, 0.6)
            queue_delay_ms = severity * 40.0

            delivered = int(packets_this_tick * (1 - drop_rate))
            n.last_drops = float(packets_this_tick - delivered)
            packets_received += delivered
            hops_transmitted += packets_this_tick
            hops_received += delivered

            hop_delay_ms = 1000.0 / max(config.BANDWIDTH, 1e-6) + config.JITTER_MS + queue_delay_ms
            total_delay_ms += delivered * hop_delay_ms

            if scheme == "gebpdcm_ltpm":
                # one trust update per node per tick: did this node forward
                # cleanly this tick, or did it drop above the safe baseline?
                ltpm.update_trust(n, success=(trust_rng.random() > drop_rate))

            n.load *= 0.5 if next_hop is not None else 0.9  # forwarded/absorbed load leaves the node

        # 7. load_history already appended in step 2

    total_time_s = config.SIM_TIME
    result = {
        "scheme": scheme,
        "speed": speed,
        "packet_size_kb": packet_size_kb,
        "delay_ms": metrics.delay(total_delay_ms, packets_received),
        "throughput_mbps": metrics.throughput(packets_received, packet_size_bits, total_time_s),
        "pdr": metrics.pdr(packets_sent, packets_received),
        "packet_drop_ratio": metrics.packet_drop_ratio(packets_sent, packets_received),
        "energy_mj": metrics.energy(hops_transmitted, hops_received),
        "migrations": migrations,
    }
    return result


if __name__ == "__main__":
    # Step-4 checkpoint (PROJECT_SPEC.md section 8): scheme 'gebpdcm' only,
    # default speed/packet size, must produce a finite, plausible result.
    out = run("gebpdcm", speed=10, packet_size_kb=1)
    for k, v in out.items():
        print(f"{k}: {v}")
