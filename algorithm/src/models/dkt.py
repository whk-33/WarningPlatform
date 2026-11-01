"""M1 DKT：知识点追踪（手写 GRU 版本，不依赖 torch_geometric / 第三方 DKT 库）。

结构：concat(item_emb(32), resp_emb(32)) → GRU(64) → 与 kp_emb(16) 拼接 → FC → sigmoid
预测：第 t 步的作答正确性由 h_{t-1} 与第 t 步的知识点共同决定（标准 DKT 展开形式）
掌握度：取序列末隐藏态 h_L，对课程内每个知识点输出"下一题答对概率"作为掌握度
"""
from __future__ import annotations

import torch
import torch.nn as nn


class DKT(nn.Module):
    def __init__(self, n_items: int, n_kps: int, emb: int = 32, resp_emb: int = 32,
                 hidden: int = 64, kp_dim: int = 16, dropout: float = 0.2):
        super().__init__()
        # item_idx / kp_idx 从 0 开始，0 同时是 padding，故统一 +1 偏移
        self.item_emb = nn.Embedding(n_items + 1, emb, padding_idx=0)
        self.resp_emb = nn.Embedding(2, resp_emb)
        self.kp_emb = nn.Embedding(n_kps + 1, kp_dim, padding_idx=0)
        self.drop = nn.Dropout(dropout)
        self.gru = nn.GRU(emb + resp_emb, hidden, batch_first=True)
        self.head = nn.Sequential(
            nn.Linear(hidden + kp_dim, 64), nn.ReLU(), nn.Dropout(dropout), nn.Linear(64, 1),
        )

    def encode(self, item, correct):
        """item/correct: (B, T) int64，item 原始 idx（0 为 padding 与真实题号碰撞，用 mask 规避）。"""
        x = torch.cat([self.item_emb(item + 1), self.resp_emb(correct.long())], dim=-1)
        h, _ = self.gru(self.drop(x))
        return h  # (B, T, hidden)

    def predict_step(self, h_prev, kp):
        """用上一步隐藏态 + 当前步知识点预测当前步答对概率。"""
        if h_prev.dim() == 2:  # (B, hidden) + (B, K) → 广播成 (B, K, hidden)
            h_prev = h_prev.unsqueeze(1).expand(-1, kp.size(1), -1)
        return self.head(torch.cat([h_prev, self.kp_emb(kp + 1)], dim=-1)).squeeze(-1)

    def forward(self, item, correct, kp):
        h = self.encode(item, correct)
        # 第 t 步预测用 h_{t-1}；序列首位没有上一步，置零并靠 mask 排除
        h_prev = torch.cat([torch.zeros_like(h[:, :1]), h[:, :-1]], dim=1)
        return self.predict_step(h_prev, kp)

    def mastery(self, item, correct, length, kp_ids):
        """返回 (B, K) 掌握度。kp_ids 是课程内知识点原始 idx。"""
        h = self.encode(item, correct)
        idx = (length - 1).clamp(min=0)
        h_last = h[torch.arange(h.size(0)), idx]          # (B, hidden)
        kp_t = torch.as_tensor(kp_ids, dtype=torch.long, device=h.device)
        kp_t = kp_t.unsqueeze(0).expand(h.size(0), -1)     # (B, K)
        return torch.sigmoid(self.predict_step(h_last, kp_t))
