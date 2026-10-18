"""
ltpm.py -- Lightweight Trust and Predictive Module (our novelty, PROJECT_SPEC.md section 1.2).

Two parts:
  1. Trust scoring: only migrate load onto nodes that have proven reliable,
     so a malicious/failing node can't absorb traffic (update_trust, trusted).
  2. Predictive forecasting: forecast a node's load a few steps ahead and
     migrate *before* it congests, instead of reacting after
     (forecast_load, should_preemptively_migrate).

Deliberately plain arithmetic -- no LSTM/ARIMA/sklearn/trained model. The
module is called *Lightweight*; a linear EWMA and a linear extrapolation are
the correct, defensible choice under exam questioning, not a shortcut.
"""

import config


def update_trust(node, success: bool):
    """EWMA trust update.
    success -> T = a*T + (1-a)   (trust drifts up towards 1.0)
    failure -> T = a*T           (trust drifts down towards 0.0)
    """
    a = config.TRUST_ALPHA
    if success:
        node.trust = a * node.trust + (1 - a)
    else:
        node.trust = a * node.trust
    node.trust = min(max(node.trust, 0.0), 1.0)


def trusted(node) -> bool:
    """A node is an eligible migration target only if its trust score
    hasn't dropped below TRUST_MIN."""
    return node.trust >= config.TRUST_MIN


def forecast_load(node, window=config.FORECAST_WINDOW) -> float:
    """Linear extrapolation one step ahead over the node's last `window`
    load readings (simple least-squares slope). Falls back to the last
    known value if there isn't enough history yet to fit a line."""
    history = node.load_history[-window:]
    if len(history) < 2:
        return history[-1] if history else node.load

    n = len(history)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(history) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, history))
    den = sum((x - mean_x) ** 2 for x in xs) or 1e-9
    slope = num / den
    intercept = mean_y - slope * mean_x

    next_x = n  # one step past the last reading
    return slope * next_x + intercept


def should_preemptively_migrate(node) -> bool:
    """True when the *forecast* load crosses into risky territory even
    though the node's *current* load hasn't, i.e. migrate ahead of the
    congestion instead of reacting to it.

    Uses SEVERITY_REFERENCE_LOAD, not the paper's O_l (325 Mbps): per-node
    loads in this simulator run in the tens-to-low-hundreds, so comparing a
    forecast against O_l would (and originally did) never trigger -- the
    forecast needs to be checked against the same reference the drop-rate
    model in simulator.py actually reacts to."""
    ref = config.SEVERITY_REFERENCE_LOAD
    current_severity = max(0.0, node.load / ref - 0.5)  # matches simulator.py's severity formula
    forecast_severity = max(0.0, forecast_load(node) / ref - 0.5)
    RISK_BAR = 0.5  # matches the point simulator.py's drop rate starts climbing meaningfully
    return current_severity < RISK_BAR and forecast_severity >= RISK_BAR
