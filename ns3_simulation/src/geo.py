"""
geo.py -- a real Golden Eagle Optimizer (GEO) for picking a migration target.

The paper names GEO in its title but never implements one: no attack
vector, no cruise vector, no iteration loop -- its "optimizer" is a bare
threshold check (see PROJECT_SPEC.md section 1). This module is our
contribution: an actual GEO search loop over the candidate neighbour nodes.

GEO models a hunting eagle's two simultaneous flight components:
  - attack vector : straight line towards the best solution seen so far
                    (exploitation)
  - cruise vector : a component perpendicular to the attack vector, i.e.
                    circling the prey (exploration)
As iterations progress, real eagles commit more to the attack and less to
the cruise -- so p_attack rises 0.5 -> 2.0 and p_cruise falls 1.0 -> 0.5
linearly over the run (PROJECT_SPEC.md section 7.6).

Each "eagle" here is a point in the 2D plane, seeded at a candidate node's
position; it is free to search the continuous space between candidates
but is always scored via the *nearest real candidate node* to its current
position, since the thing we must return is an actual network node.
"""

import math
import random

import config
import topology


def fitness(candidate_node, source_node):
    """F = w_t*throughput_proxy + w_pdr*pdr_proxy - w_delay*delay_proxy
          - w_loss*loss_proxy - w_energy*energy_proxy
    We don't have a full flow simulation to score a hypothetical migration
    target with, so each term is a cheap proxy built from the candidate's
    current state (this is documented here, not hidden):
      throughput_proxy = headroom below SEVERITY_REFERENCE_LOAD, the same
                          load reference simulator.py uses to drive its
                          drop-rate/queuing-delay model -- NOT the paper's
                          O_l (325 Mbps), which real per-node loads never
                          get close to and so cannot discriminate candidates
      pdr_proxy         = 1.0 safe / 0.5 threshold / 0.0 congested
      delay_proxy       = distance to the source, normalised by TX_RANGE
      loss_proxy        = candidate's current load, normalised against the
                          same SEVERITY_REFERENCE_LOAD scale (2x headroom)
      energy_proxy      = fraction of energy already spent
    """
    w = config.FITNESS_WEIGHTS
    ref = config.SEVERITY_REFERENCE_LOAD
    headroom = max(0.0, ref - candidate_node.load) / ref
    pdr_proxy = {"safe": 1.0, "threshold": 0.5, "congested": 0.0}[candidate_node.status]
    delay_proxy = topology.distance(source_node, candidate_node) / config.TX_RANGE
    loss_proxy = min(candidate_node.load / (2 * ref), 1.0)
    energy_proxy = 1.0 - candidate_node.energy / 100.0

    return (
        w["throughput"] * headroom
        + w["pdr"] * pdr_proxy
        - w["delay"] * delay_proxy
        - w["loss"] * loss_proxy
        - w["energy"] * energy_proxy
    )


def _nearest_candidate(x, y, candidates):
    return min(candidates, key=lambda c: math.hypot(c.x - x, c.y - y))


def select(source, candidates, network=None, pop=config.GEO_POPULATION, iters=config.GEO_ITERATIONS):
    """Run a GEO search over `candidates` and return the best node found.
    `network` is accepted for interface symmetry with migrate.select_target
    but unused -- fitness only needs the candidate and the source."""
    if not candidates:
        return None

    rng = random.Random(source.id)  # deterministic per source node, reproducible run-to-run
    step_size = config.TX_RANGE / 10.0  # how far an eagle can move per iteration

    # Population: seed each eagle at a (possibly repeated) candidate's position.
    eagles = [[c.x, c.y] for c in (rng.choices(candidates, k=pop) if len(candidates) < pop
                                    else rng.sample(candidates, pop))]

    best_node = max(candidates, key=lambda c: fitness(c, source))
    best_pos = [best_node.x, best_node.y]

    for it in range(iters):
        p_attack = 0.5 + (2.0 - 0.5) * it / max(iters - 1, 1)   # 0.5 -> 2.0
        p_cruise = 1.0 - (1.0 - 0.5) * it / max(iters - 1, 1)   # 1.0 -> 0.5

        for eagle in eagles:
            # Attack vector: straight towards the best position found so far.
            attack = [best_pos[0] - eagle[0], best_pos[1] - eagle[1]]
            attack_len = math.hypot(*attack) or 1e-9
            attack_unit = [attack[0] / attack_len, attack[1] / attack_len]

            # Cruise vector: perpendicular to the attack vector (circling motion).
            cruise_unit = [-attack_unit[1], attack_unit[0]]
            if rng.random() < 0.5:
                cruise_unit = [-cruise_unit[0], -cruise_unit[1]]

            move_x = (p_attack * attack_unit[0] + p_cruise * cruise_unit[0]) * step_size
            move_y = (p_attack * attack_unit[1] + p_cruise * cruise_unit[1]) * step_size
            eagle[0] = min(max(eagle[0] + move_x, 0.0), config.AREA_WIDTH)
            eagle[1] = min(max(eagle[1] + move_y, 0.0), config.AREA_HEIGHT)

            nearest = _nearest_candidate(eagle[0], eagle[1], candidates)
            if fitness(nearest, source) > fitness(best_node, source):
                best_node, best_pos = nearest, [nearest.x, nearest.y]

    return best_node
