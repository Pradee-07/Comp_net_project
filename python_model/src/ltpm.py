"""
ltpm.py -- Lightweight Trust and Predictive Module.
Integrated with the Tiny-TTS (Tiny Time-Series Transformer) Edge-AI model for
accurate, multivariate congestion forecasting and low false-alarm preemptive migration.
Maintains fallback to linear regression if model weights or PyTorch are absent.
"""

import os
import json
import config

_TINY_TTS_MODEL = None
_MODEL_ATTEMPTED = False


def _get_tiny_tts_model():
    """
    Lazy loads the trained TinyTTS PyTorch model once on demand.
    """
    global _TINY_TTS_MODEL, _MODEL_ATTEMPTED
    if _TINY_TTS_MODEL is None and not _MODEL_ATTEMPTED:
        _MODEL_ATTEMPTED = True
        model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "tiny_tts.pt"))
        cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results", "optimal_hyperparameters.json"))

        if os.path.exists(model_path):
            try:
                import torch
                from python_model.ml.model import TinyTTS

                d_model = 32
                num_heads = 2
                d_ff = 64
                if os.path.exists(cfg_path):
                    with open(cfg_path) as f:
                        cfg = json.load(f)["optimal"]
                        d_model = cfg["d_model"]
                        num_heads = cfg["num_heads"]
                        d_ff = cfg["d_ff"]

                model = TinyTTS(
                    input_dim=5,
                    d_model=d_model,
                    num_heads=num_heads,
                    d_ff=d_ff,
                    num_layers=1,
                    seq_len=10,
                )
                model.load_state_dict(torch.load(model_path, weights_only=True, map_location="cpu"))
                model.eval()
                _TINY_TTS_MODEL = model
            except Exception as e:
                _TINY_TTS_MODEL = None
    return _TINY_TTS_MODEL


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


def batch_evaluate_nodes(nodes):
    """
    Evaluates all nodes in the network in a single batched PyTorch forward pass.
    Caches node._pred_load and node._pred_risk on each node.
    """
    model = _get_tiny_tts_model()
    if model is None:
        return

    try:
        import torch
        batch = []
        valid_nodes = []
        for n in nodes:
            if hasattr(n, "telemetry_history") and len(n.telemetry_history) >= 2:
                frames = list(n.telemetry_history)
                while len(frames) < 10:
                    frames.insert(0, frames[0])
                batch.append(frames[-10:])
                valid_nodes.append(n)
            else:
                n._pred_load = n.load
                n._pred_risk = 0.0

        if batch:
            x = torch.tensor(batch, dtype=torch.float32)
            with torch.no_grad():
                loads, risks = model(x, normalize_inputs=True)
                loads = loads.cpu().numpy()
                risks = risks.cpu().numpy()
                for i, n in enumerate(valid_nodes):
                    n._pred_load = float(loads[i])
                    n._pred_risk = float(risks[i])
    except Exception:
        pass


def forecast_load(node, window=config.FORECAST_WINDOW) -> float:
    """
    One step ahead load forecasting.
    Uses cached Tiny-TTS prediction if available, otherwise falls back
    to simple least-squares slope.
    """
    if hasattr(node, "_pred_load"):
        return node._pred_load

    # Baseline linear least-squares extrapolation
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
    """
    Evaluates whether the node should preemptively migrate load ahead of impending congestion.
    Uses Tiny-TTS Transformer risk head and load forecast to avoid false alarms.
    """
    ref = config.SEVERITY_REFERENCE_LOAD
    current_severity = max(0.0, node.load / ref - 0.5)
    RISK_BAR = 0.5

    # If current load is already above risk bar, reactive migration will trigger anyway
    if current_severity >= RISK_BAR:
        return False

    # Check cached Tiny-TTS forward pass
    if hasattr(node, "_pred_risk") and hasattr(node, "_pred_load"):
        forecast_severity = max(0.0, node._pred_load / ref - 0.5)
        return (node._pred_risk >= 0.5) or (forecast_severity >= RISK_BAR)

    # Fallback to linear least-squares slope
    forecast_severity = max(0.0, forecast_load(node) / ref - 0.5)
    return current_severity < RISK_BAR and forecast_severity >= RISK_BAR
