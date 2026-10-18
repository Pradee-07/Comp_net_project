"""
train_production.py -- Trains production-grade Tiny-TTS model using optimal hyperparameters
and exports weights for both Python (torch model) and NS-3 (zero-dependency C++ header).
"""

import os
import sys
import json
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import TensorDataset, DataLoader

# Ensure python_model root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from python_model.ml.model import TinyTTS
from python_model.ml.grid_search_engine import extract_windows, evaluate_model
from python_model.ml.export_cpp import export_to_header

DATA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "manet_traces.npy"))
RESULTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "results"))
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
NS3_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ns3_simulation", "ns3"))


def train_production_model(epochs=20):
    print("==============================================================")
    print("  TRAINING PRODUCTION TINY-TTS EDGE TRANSFORMER")
    print("==============================================================")

    # 1. Load optimal config if exists, else defaults
    cfg_path = os.path.join(RESULTS_DIR, "optimal_hyperparameters.json")
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            data = json.load(f)
            config = data["optimal"]
            print(f"  Loaded optimal hyperparameters from: {cfg_path}")
    else:
        config = {
            "d_model": 32,
            "num_heads": 4,
            "d_ff": 64,
            "num_layers": 1,
            "dropout": 0.0,
            "lr": 0.002,
        }
        print("  Using default tuned hyperparameters.")

    print(f"  Architecture: d_model={config['d_model']}, heads={config['num_heads']}, d_ff={config['d_ff']}, lr={config['lr']}")

    # 2. Load dataset
    traces = np.load(DATA_PATH) # (1000, 100, 5)
    # Split: Train [0, 85), Test [85, 100)
    X_train, y_train_load, y_train_risk = extract_windows(traces, target_start=0, target_end=85)
    X_test, y_test_load, y_test_risk = extract_windows(traces, target_start=85, target_end=100)

    print(f"  Train samples: {len(X_train):,}, Test samples: {len(X_test):,}")

    # Normalization statistics
    feat_mean = X_train.mean(axis=(0, 1))
    feat_std = X_train.std(axis=(0, 1)) + 1e-6
    load_mean = y_train_load.mean()
    load_std = y_train_load.std() + 1e-6

    # DataLoaders
    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train_load), torch.from_numpy(y_train_risk))
    test_ds = TensorDataset(torch.from_numpy(X_test), torch.from_numpy(y_test_load), torch.from_numpy(y_test_risk))

    train_loader = DataLoader(train_ds, batch_size=256, shuffle=True)
    test_loader = DataLoader(test_ds, batch_size=512, shuffle=False)

    # Instantiate Model
    torch.manual_seed(42)
    model = TinyTTS(
        input_dim=5,
        d_model=config["d_model"],
        num_heads=config["num_heads"],
        d_ff=config["d_ff"],
        num_layers=config.get("num_layers", 1),
        dropout=config.get("dropout", 0.0),
    )
    model.set_normalization(feat_mean, feat_std, load_mean, load_std)

    optimizer = torch.optim.AdamW(model.parameters(), lr=config["lr"], weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)
    loss_mse = nn.MSELoss()
    loss_bce = nn.BCELoss()

    feat_mean_t = torch.tensor(feat_mean, dtype=torch.float32)
    feat_std_t = torch.tensor(feat_std, dtype=torch.float32)
    load_mean_t = torch.tensor(load_mean, dtype=torch.float32)
    load_std_t = torch.tensor(load_std, dtype=torch.float32)

    print(f"\n  Starting {epochs} training epochs...")
    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        batches = 0
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

            total_loss += loss.item()
            batches += 1

        scheduler.step()
        if epoch % 5 == 0 or epoch == epochs:
            avg_loss = total_loss / batches
            eval_metrics = evaluate_model(model, test_loader, feat_mean_t, feat_std_t, load_mean_t, load_std_t)
            print(f"  [Epoch {epoch:2d}/{epochs}] Train Loss: {avg_loss:.4f} | Test NMSE: {eval_metrics['nmse']:.4f}, Risk F1: {eval_metrics['f1']:.4f}, FAR: {eval_metrics['far']:.4f}")

    # Final Evaluation
    final_metrics = evaluate_model(model, test_loader, feat_mean_t, feat_std_t, load_mean_t, load_std_t)
    print("\n==============================================================")
    print("  FINAL PRODUCTION MODEL BENCHMARK:")
    print(f"  Test NMSE:        {final_metrics['nmse']:.4f}")
    print(f"  Test Risk F1:     {final_metrics['f1']:.4f}")
    print(f"  Test Precision:   {final_metrics['precision']:.4f}")
    print(f"  Test Recall:      {final_metrics['recall']:.4f}")
    print(f"  False Alarm Rate: {final_metrics['far']:.4f}")
    print("==============================================================")

    # 3. Save PyTorch Model Checkpoint
    model_path = os.path.join(DATA_DIR, "tiny_tts.pt")
    torch.save(model.state_dict(), model_path)
    print(f"\n  ✓ Saved PyTorch model checkpoint to:\n    {model_path}")

    # Save metrics JSON
    eval_path = os.path.join(RESULTS_DIR, "tiny_tts_eval.json")
    with open(eval_path, "w") as f:
        json.dump(final_metrics, f, indent=2)

    # 4. Export zero-dependency C++ header for NS-3
    ns3_header_path = os.path.join(NS3_DIR, "tiny_tts_weights.h")
    export_to_header(model, ns3_header_path)

    # Also export local copy for python_model
    py_header_path = os.path.join(DATA_DIR, "tiny_tts_weights.h")
    export_to_header(model, py_header_path)

    print("\n  ✓ Production training and dual-target weight export complete.")
    return model, final_metrics


if __name__ == "__main__":
    train_production_model()
