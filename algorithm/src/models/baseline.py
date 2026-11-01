"""M0 基线模型：标准化后的逻辑回归 + 两层 MLP（纯 torch，不依赖 sklearn）。"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn


class LogisticRegression(nn.Module):
    def __init__(self, n_features: int):
        super().__init__()
        self.linear = nn.Linear(n_features, 1)

    def forward(self, x):
        return self.linear(x).squeeze(-1)


class MLP(nn.Module):
    def __init__(self, n_features: int, hidden: int = 32, dropout: float = 0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_features, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


def fit(
    model: nn.Module,
    x: np.ndarray,
    y: np.ndarray,
    *,
    epochs: int = 300,
    lr: float = 0.05,
    weight_decay: float = 1e-3,
    batch_size: int = 512,
    seed: int = 42,
) -> nn.Module:
    """全批量/小批量混合训练；样本量 3 万级时全批量 300 轮足够收敛且可复现。"""
    torch.manual_seed(seed)
    x_t = torch.tensor(x, dtype=torch.float32)
    y_t = torch.tensor(y, dtype=torch.float32)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    loss_fn = nn.BCEWithLogitsLoss()
    n = len(x_t)
    model.train()
    for _ in range(epochs):
        perm = torch.randperm(n)
        for start in range(0, n, batch_size):
            idx = perm[start:start + batch_size]
            opt.zero_grad()
            loss = loss_fn(model(x_t[idx]), y_t[idx])
            loss.backward()
            opt.step()
    return model


@torch.no_grad()
def predict(model: nn.Module, x: np.ndarray, batch_size: int = 4096) -> np.ndarray:
    model.eval()
    out = []
    for start in range(0, len(x), batch_size):
        xb = torch.tensor(x[start:start + batch_size], dtype=torch.float32)
        out.append(torch.sigmoid(model(xb)).numpy())
    return np.concatenate(out) if out else np.zeros(0)
