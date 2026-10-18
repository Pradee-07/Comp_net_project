"""
migrate.py -- load migration decisions and execution (paper Eq 6-8).

select_target() dispatches by scheme:
  'gebpdcm'      -> nearest free (non-congested) neighbour
  'gebpdcm_geo'  -> geo.select(...), a real GEO fitness search
  'gebpdcm_ltpm' -> geo.select(...) filtered to ltpm.trusted() candidates

geo/ltpm are imported lazily inside select_target so scheme 'gebpdcm' can run
without those modules existing yet (build-order checkpoint, PROJECT_SPEC.md section 8).
"""

import topology


def user_priority(Ns, P, cs):
    """Eq 6: U = Ns + P(CS). We read P(CS) as the predicted load P weighted
    by how far into the congested band the score CS has moved (0 outside
    the congested band, rising to 1 at CS == CONGESTED_LEVEL*2)."""
    import config
    cs_weight = min(max(cs - config.CONGESTED_LEVEL, 0) / config.CONGESTED_LEVEL, 1.0)
    return Ns + P * cs_weight


def is_first_user(U):
    """Eq 7: U >= 1 means this node is the 'first user' -- i.e. highest
    priority for migration this tick."""
    return U >= 1


def should_share(P, optimal=None):
    """Eq 8: node shares (migrates) load when its predicted load P exceeds
    the paper's optimal load O_l."""
    import config
    optimal = config.OPTIMAL_LOAD if optimal is None else optimal
    return P > optimal


def migrate_half(src, dst):
    """Move 50% of src's current load to dst. Mutates both nodes in place."""
    moved = src.load * 0.5
    src.load -= moved
    dst.load += moved
    dst.buffer += moved


def _nearest_free_node(src, candidates):
    free = [c for c in candidates if c.status != "congested"]
    if not free:
        return None
    return min(free, key=lambda c: topology.distance(src, c))


def select_target(src, candidates, scheme, network=None):
    """Pick a migration target for src among candidates, per scheme."""
    if not candidates:
        return None

    if scheme == "gebpdcm":
        return _nearest_free_node(src, candidates)

    if scheme == "gebpdcm_geo":
        import geo
        return geo.select(src, candidates, network)

    if scheme == "gebpdcm_ltpm":
        import geo
        import ltpm
        trusted_candidates = [c for c in candidates if ltpm.trusted(c)]
        if not trusted_candidates:
            return None
        return geo.select(src, trusted_candidates, network)

    raise ValueError(f"unknown scheme: {scheme}")
