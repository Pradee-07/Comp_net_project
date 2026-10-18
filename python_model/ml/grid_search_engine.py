"""
grid_search_engine.py -- Automated Hyperparameter Grid Search for Tiny-TTS.
Uses 3-fold Walk-Forward expanding temporal validation to prevent lookahead bias.
Evaluates NMSE, Risk F1-score, and False Alarm Rate (FAR).
Saves best configuration to python_model/results/optimal_hyperparameters.json.
"""

import os
import sys
import json
import itertools
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import TensorDataset, DataLoader

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from python_model.ml.model import TinyTTS

DATA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "manet_traces.npy"))
RESULTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results"))


def extract_windows(traces, target_start, target_end, seq_len=10, risk_threshold=90.0):
    """
    Extracts sliding temporal windows from traces (shape: N, T, F).
    Targets lie at time step t in [target_start, target_end).
    The input window for target t is traces[:, t - seq_len : t, :].
    """
    N, T, F = traces.shape
    start = max(seq_len, target_start)
    X_list, y_load_list, y_risk_list = [], [], []

    for t in range(start, target_end):
        window = traces[:, t - seq_len : t, :] # (N, seq_len, F)
        target_load = traces[:, t, 0]          # (N,)
        target_risk = (target_load >= risk_threshold).astype(np.float32)

        X_list.append(window)
        y_load_list.append(target_load)
        y_risk_list.append(target_risk)

    X = np.concatenate(X_list, axis=0)
    y_load = np.concatenate(y_load_list, axis=0)
    y_risk = np.concatenate(y_risk_list, axis=0)
    return X, y_load, y_risk


def evaluate_model(model, val_loader, feat_mean, feat_std, load_mean, load_std):
    model.eval()
    all_preds_load = []
    all_trues_load = []
    all_preds_risk = []
    all_trues_risk = []

    with torch.no_grad():
        for bx, by_load, by_risk in val_loader:
            # Normalize bx
            bx_norm = (bx - feat_mean) / (feat_std + 1e-6)
            pred_load_norm, pred_risk = model(bx_norm, normalize_inputs=False)
            pred_load = pred_load_norm * (load_std + 1e-6) + load_mean

            all_preds_load.append(pred_load.cpu().numpy())
            all_trues_load.append(by_load.numpy())
            all_preds_risk.append(pred_risk.cpu().numpy())
            all_trues_risk.append(by_risk.numpy())

    preds_load = np.concatenate(all_preds_load)
    trues_load = np.concatenate(all_trues_load)
    preds_risk = np.concatenate(all_preds_risk)
    trues_risk = np.concatenate(all_trues_risk)

    # Metrics
    mse = np.mean((preds_load - trues_load) ** 2)
    var = np.var(trues_load) + 1e-6
    nmse = float(mse / var)

    # Risk metrics with 0.5 threshold
    risk_binary = (preds_risk >= 0.5).astype(np.float32)
    tp = np.sum((risk_binary == 1.0) & (trues_risk == 1.0))
    fp = np.sum((risk_binary == 1.0) & (trues_risk == 0.0))
    fn = np.sum((risk_binary == 0.0) & (trues_risk == 1.0))
    tn = np.sum((risk_binary == 0.0) & (trues_risk == 0.0))

    precision = float(tp / (tp + fp + 1e-6))
    recall = float(tp / (tp + fn + 1e-6))
    f1 = float(2 * precision * recall / (precision + recall + 1e-6))
    far = float(fp / (fp + tn + 1e-6)) # False Alarm Rate

    return {"nmse": nmse, "f1": f1, "precision": precision, "recall": recall, "far": far}


def train_fold(config_dict, train_loader, val_loader, feat_mean, feat_std, load_mean, load_std, epochs=5):
    torch.manual_seed(42)
    model = TinyTTS(
        input_dim=5,
        d_model=config_dict["d_model"],
        num_heads=config_dict["num_heads"],
        d_ff=config_dict["d_ff"],
        num_layers=config_dict["num_layers"],
        dropout=config_dict["dropout"],
    )

    optimizer = torch.optim.AdamW(model.parameters(), lr=config_dict["lr"], weight_decay=1e-4)
    loss_mse = nn.MSELoss()
    loss_bce = nn.BCELoss()

    feat_mean_t = torch.tensor(feat_mean, dtype=torch.float32)
    feat_std_t = torch.tensor(feat_std, dtype=torch.float32)
    load_mean_t = torch.tensor(load_mean, dtype=torch.float32)
    load_std_t = torch.tensor(load_std, dtype=torch.float32)

    for epoch in range(epochs):
        model.train()
        for bx, by_load, by_risk in train_loader:
            bx_norm = (bx - feat_mean_t) / (feat_std_t + 1e-6)
            by_load_norm = (by_load - load_mean_t) / (load_std_t + 1e-6)

            pred_load_norm, pred_risk = model(bx_norm, normalize_inputs=False)
            l_load = loss_mse(pred_load_norm, by_load_norm)
            l_risk = loss_bce(pred_risk, by_risk)
            loss = l_load + 2.0 * l_risk

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

    metrics = evaluate_model(model, val_loader, feat_mean_t, feat_std_t, load_mean_t, load_std_t)
    return metrics


def run_grid_search():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"Harvested traces not found at {DATA_PATH}. Run dataset_collector.py first.")

    print(f"Loading harvested traces from: {DATA_PATH} ...")
    traces = np.load(DATA_PATH) # (1000, 100, 5)

    # 3-Fold Walk-Forward Temporal Splits
    # Fold 1: train [0, 60), val [60, 75)
    # Fold 2: train [0, 75), val [75, 90)
    # Fold 3: train [0, 90), val [90, 100)
    folds = [
        {"train": (0, 60), "val": (60, 75)},
        {"train": (0, 75), "val": (75, 90)},
        {"train": (0, 90), "val": (90, 100)},
    ]

    # Hyperparameter search space
    param_grid = {
        "d_model": [16, 32],
        "num_heads": [2, 4],
        "d_ff": [32, 64],
        "num_layers": [1],
        "dropout": [0.0],
        "lr": [1e-3, 3e-3],
    }

    keys, values = zip(*param_grid.items())
    candidate_configs = [dict(zip(keys, v)) for v in itertools.product(*values)]
    # Filter valid combinations: d_model must be divisible by num_heads
    candidate_configs = [c for c in candidate_configs if c["d_model"] % c["num_heads"] == 0]

    print(f"\n==============================================================")
    print(f"  TINY-TTS WALK-FORWARD GRID SEARCH ({len(candidate_configs)} candidate architectures)")
    print(f"==============================================================")

    best_score = -float("inf")
    best_config = None
    best_metrics = None
    all_results = []

    print("Pre-computing temporal fold datasets...")
    prepared_folds = []
    for fold in folds:
        X_tr, y_tr_load, y_tr_risk = extract_windows(traces, fold["train"][0], fold["train"][1])
        X_va, y_va_load, y_va_risk = extract_windows(traces, fold["val"][0], fold["val"][1])

        feat_mean = X_tr.mean(axis=(0, 1))
        feat_std = X_tr.std(axis=(0, 1)) + 1e-6
        load_mean = y_tr_load.mean()
        load_std = y_tr_load.std() + 1e-6

        tr_ds = TensorDataset(torch.from_numpy(X_tr), torch.from_numpy(y_tr_load), torch.from_numpy(y_tr_risk))
        va_ds = TensorDataset(torch.from_numpy(X_va), torch.from_numpy(y_va_load), torch.from_numpy(y_va_risk))

        tr_loader = DataLoader(tr_ds, batch_size=256, shuffle=True)
        va_loader = DataLoader(va_ds, batch_size=512, shuffle=False)

        prepared_folds.append({
            "tr_loader": tr_loader,
            "va_loader": va_loader,
            "feat_mean": feat_mean,
            "feat_std": feat_std,
            "load_mean": load_mean,
            "load_std": load_std,
        })
    print(f"  ✓ {len(prepared_folds)} folds prepared successfully.")

    for idx, cfg in enumerate(candidate_configs):
        print(f"\n[Config {idx+1}/{len(candidate_configs)}] d_model={cfg['d_model']}, heads={cfg['num_heads']}, d_ff={cfg['d_ff']}, lr={cfg['lr']}")

        fold_nmse, fold_f1, fold_far = [], [], []

        for p_fold in prepared_folds:
            res = train_fold(
                cfg,
                p_fold["tr_loader"],
                p_fold["va_loader"],
                p_fold["feat_mean"],
                p_fold["feat_std"],
                p_fold["load_mean"],
                p_fold["load_std"],
                epochs=4,
            )
            fold_nmse.append(res["nmse"])
            fold_f1.append(res["f1"])
            fold_far.append(res["far"])

        avg_nmse = float(np.mean(fold_nmse))
        avg_f1 = float(np.mean(fold_f1))
        avg_far = float(np.mean(fold_far))

        # Composite score: reward high F1, penalize NMSE and False Alarm Rate
        # Composite Score = F1 - 0.5 * NMSE - 1.0 * FAR
        composite = avg_f1 - 0.5 * avg_nmse - 1.0 * avg_far

        print(f"  -> Fold Avg: NMSE={avg_nmse:.4f}, Risk F1={avg_f1:.4f}, FAR={avg_far:.4f} | Composite Score: {composite:.4f}")

        entry = {
            "config": cfg,
            "nmse": avg_nmse,
            "f1": avg_f1,
            "far": avg_far,
            "composite": composite,
        }
        all_results.append(entry)

        if composite > best_score:
            best_score = composite
            best_config = cfg
            best_metrics = entry

    print(f"\n==============================================================")
    print(f"  OPTIMAL CONFIGURATION DISCOVERED:")
    print(f"  d_model:     {best_config['d_model']}")
    print(f"  num_heads:   {best_config['num_heads']} (d_k = {best_config['d_model'] // best_config['num_heads']})")
    print(f"  d_ff:        {best_config['d_ff']}")
    print(f"  learning_rate: {best_config['lr']}")
    print(f"  NMSE:        {best_metrics['nmse']:.4f}")
    print(f"  Risk F1:     {best_metrics['f1']:.4f}")
    print(f"  FAR:         {best_metrics['far']:.4f}")
    print(f"  Composite:   {best_metrics['composite']:.4f}")
    print(f"==============================================================")

    out_file = os.path.join(RESULTS_DIR, "optimal_hyperparameters.json")
    with open(out_file, "w") as f:
        json.dump({"optimal": best_config, "metrics": best_metrics, "all_results": all_results}, f, indent=2)
    print(f"  ✓ Saved to: {out_file}\n")


if __name__ == "__main__":
    run_grid_search()
