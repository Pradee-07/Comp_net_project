"""
model.py -- Tiny-TTS (Tiny Time-Series Transformer) architecture for MANET edge forecasting.
Designed for ultra-low latency (<1 ms inference), minimal memory footprint (<10 KB INT8 / <40 KB FP32),
and seamless zero-dependency C++ export for NS-3 simulation.
"""

import math
import torch
import torch.nn as nn
import torch.nn.functional as F


class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 50):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2, dtype=torch.float32) * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0)) # shape (1, max_len, d_model)

    def forward(self, x):
        # x shape: (batch_size, seq_len, d_model)
        seq_len = x.size(1)
        return x + self.pe[:, :seq_len, :]


class TinyAttention(nn.Module):
    """
    Explicit Multi-Head Self-Attention for transparent C++ export.
    """
    def __init__(self, d_model: int = 32, num_heads: int = 4, dropout: float = 0.0):
        super().__init__()
        assert d_model % num_heads == 0, "d_model must be divisible by num_heads"
        self.d_model = d_model
        self.num_heads = num_heads
        self.d_k = d_model // num_heads

        self.q_proj = nn.Linear(d_model, d_model)
        self.k_proj = nn.Linear(d_model, d_model)
        self.v_proj = nn.Linear(d_model, d_model)
        self.out_proj = nn.Linear(d_model, d_model)
        self.dropout = nn.Dropout(dropout) if dropout > 0 else nn.Identity()

    def forward(self, x):
        # x: (B, T, D)
        B, T, D = x.shape
        q = self.q_proj(x).view(B, T, self.num_heads, self.d_k).transpose(1, 2) # (B, H, T, d_k)
        k = self.k_proj(x).view(B, T, self.num_heads, self.d_k).transpose(1, 2) # (B, H, T, d_k)
        v = self.v_proj(x).view(B, T, self.num_heads, self.d_k).transpose(1, 2) # (B, H, T, d_k)

        scores = torch.matmul(q, k.transpose(-2, -1)) / math.sqrt(self.d_k) # (B, H, T, T)
        attn = F.softmax(scores, dim=-1)
        attn = self.dropout(attn)

        context = torch.matmul(attn, v) # (B, H, T, d_k)
        context = context.transpose(1, 2).contiguous().view(B, T, D) # (B, T, D)
        return self.out_proj(context)


class TinyEncoderBlock(nn.Module):
    def __init__(self, d_model: int = 32, num_heads: int = 4, d_ff: int = 64, dropout: float = 0.0):
        super().__init__()
        self.attn = TinyAttention(d_model, num_heads, dropout)
        self.ln1 = nn.LayerNorm(d_model)
        self.ffn = nn.Sequential(
            nn.Linear(d_model, d_ff),
            nn.GELU(),
            nn.Dropout(dropout) if dropout > 0 else nn.Identity(),
            nn.Linear(d_ff, d_model),
            nn.Dropout(dropout) if dropout > 0 else nn.Identity(),
        )
        self.ln2 = nn.LayerNorm(d_model)

    def forward(self, x):
        # Post-LN Transformer block
        x = self.ln1(x + self.attn(x))
        x = self.ln2(x + self.ffn(x))
        return x


class TinyTTS(nn.Module):
    """
    Edge-AI Multivariate Time-Series Transformer for MANET LTPM.
    Inputs:
      x: (B, seq_len=10, input_dim=5)
    Outputs:
      load_pred: (B,) predicted next-tick load (continuous)
      risk_pred: (B,) predicted next-tick risk probability [0, 1]
    """
    def __init__(
        self,
        input_dim: int = 5,
        d_model: int = 32,
        num_heads: int = 4,
        d_ff: int = 64,
        num_layers: int = 1,
        seq_len: int = 10,
        dropout: float = 0.0,
    ):
        super().__init__()
        self.input_dim = input_dim
        self.d_model = d_model
        self.num_heads = num_heads
        self.d_ff = d_ff
        self.num_layers = num_layers
        self.seq_len = seq_len

        # Feature normalization buffers
        self.register_buffer("feat_mean", torch.zeros(input_dim))
        self.register_buffer("feat_std", torch.ones(input_dim))
        self.register_buffer("load_mean", torch.zeros(1))
        self.register_buffer("load_std", torch.ones(1))

        # Input projection & positional encoding
        self.input_proj = nn.Linear(input_dim, d_model)
        self.pos_enc = PositionalEncoding(d_model, max_len=seq_len + 10)

        # Transformer blocks
        self.layers = nn.ModuleList([
            TinyEncoderBlock(d_model, num_heads, d_ff, dropout) for _ in range(num_layers)
        ])

        # Dual prediction heads (pooling last token)
        self.head_load = nn.Linear(d_model, 1)
        self.head_risk = nn.Linear(d_model, 1)

    def set_normalization(self, feat_mean, feat_std, load_mean, load_std):
        self.feat_mean.copy_(torch.as_tensor(feat_mean, dtype=torch.float32))
        self.feat_std.copy_(torch.as_tensor(feat_std, dtype=torch.float32))
        self.load_mean.copy_(torch.as_tensor(load_mean, dtype=torch.float32))
        self.load_std.copy_(torch.as_tensor(load_std, dtype=torch.float32))

    def forward(self, x, normalize_inputs: bool = False):
        """
        x: (B, seq_len, input_dim)
        Returns: (pred_load, pred_risk)
        """
        if normalize_inputs:
            x = (x - self.feat_mean) / (self.feat_std + 1e-6)

        B, T, _ = x.shape
        h = self.input_proj(x)
        h = self.pos_enc(h)

        for layer in self.layers:
            h = layer(h)

        # Representation of latest time-step (last token)
        h_last = h[:, -1, :] # (B, d_model)

        # Predict normalized load and risk logit
        norm_load_pred = self.head_load(h_last).squeeze(-1) # (B,)
        risk_logit = self.head_risk(h_last).squeeze(-1)      # (B,)

        if normalize_inputs:
            load_pred = norm_load_pred * (self.load_std + 1e-6) + self.load_mean
        else:
            load_pred = norm_load_pred

        risk_pred = torch.sigmoid(risk_logit)
        return load_pred, risk_pred
