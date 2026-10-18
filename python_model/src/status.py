"""
status.py -- one function per congestion equation from the paper (Eq 2-5).

KNOWN PAPER ISSUES (do not silently "fix", see PROJECT_SPEC.md section 1):
  Eq 2  P = Nc + h(sum Dl)   adds a *set of nodes* Nc to a *data load* sum(Dl).
        Dimensionally meaningless as written. We interpret P as a predicted /
        monitored load: P = h * (sum of the node's recent load readings).
  Eq 3  Nd = Dl + Nn(Pmax/Pt) similarly mixes a load with a neighbour count.
        We interpret Nn as neighbour count and (Pmax/Pt) as an interference
        ratio between the node's max possible load and its current load.
These interpretations are ours; the report documents them as findings, not
as corrections to the paper.
"""

import config


def predict_data_load(node):
    """Eq 2 -> P (predicted/monitored load, Mbps).
    Paper form: P = Nc + h(sum Dl). Our interpretation: P = h * sum(recent loads).
    """
    window = node.load_history[-config.FORECAST_WINDOW:] if node.load_history else [node.load]
    return config.MONITOR_COEFF_H * sum(window)


def node_density(node, neighbours):
    """Eq 3 -> Nd (node density / congestion pressure).
    Paper form: Nd = Dl + Nn(Pmax/Pt). Nn = neighbour count, (Pmax/Pt) =
    interference ratio between the area's max supportable load and this
    node's current throughput demand.
    """
    nn = len(neighbours.get(node.id, []))
    # Floor Pt at the paper's own minimum data rate (not 1e-6): a near-idle
    # node would otherwise send Pmax/Pt to the thousands and every node
    # with several neighbours would read as permanently congested.
    pt = max(node.load, config.DATA_RATE_MIN)
    interference_ratio = config.DATA_RATE_MAX / pt
    return node.load + nn * interference_ratio


def node_status(P, Nd):
    """Eq 4 -> Ns (node status). Combines predicted load and density into a
    single normalised figure, scaled against the paper's optimal load O_l
    so Ns lands in a comparable range to the CS boundaries used in Eq 5.
    The 1.6 factor (rather than a plain 2x) is our own calibration so that,
    given this simulator's traffic model, 'congested' captures a believable
    minority of node-ticks instead of a near-zero tail event -- it is not a
    paper value, it is tuned against this implementation's own load profile."""
    return (P + Nd) / (1.6 * config.OPTIMAL_LOAD)


def congestion_score(node, neighbours):
    """Eq 5 -> CS, a float typically in [0, 2].
    CS < 0.5   -> no congestion
    0.5 <= CS < 1 -> at threshold
    CS >= 1    -> congested
    """
    P = predict_data_load(node)
    Nd = node_density(node, neighbours)
    Ns = node_status(P, Nd)
    return Ns


def classify(cs):
    """Map a congestion score to one of 'safe' | 'threshold' | 'congested'."""
    if cs < config.CONGESTION_THRESHOLD:
        return "safe"
    if cs < config.CONGESTED_LEVEL:
        return "threshold"
    return "congested"
