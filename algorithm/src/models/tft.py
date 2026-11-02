"""M2 TFT（轻量自研版）：变量选择 + LSTM + 静态富化 + 跳连。

与论文版 TFT 的差异（写在报告里）：
1. 不实现 LSTM 编码器-解码器：任务是"每周判一次未来风险"，单向 LSTM 足够，且能逐周输出；
2. 不做分位数头：标签是二元的（未来是否出事），分位数头需要连续目标，会变成无意义的回归；
   因此用 masked BCE，输出 0–1 风险概率，与 `risk_scores.parquet` 契约一致；
3. 保留核心：static / observed / known-future 三组变量选择权重，逐周导出给前端"影响因子"。

数据面板：每个 (stu_id, course_code, semester) 一条 16 步序列（week 0–15）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

STATIC_COLS = [
    "gender", "entrance_score", "past_retake_count", "special_flag", "credits", "elective",
    "major_A Level or Equivalent", "major_HE Qualification", "major_Lower Than A Level",
    "major_No Formal quals", "major_Post Graduate Qualification",
]
OBS_COLS = [
    "attendance_rate", "ppt_clicks_cum", "video_watch_cum", "absent_count_cum",
    "quiz_accuracy_cum", "hw_score_cum", "hw_days_late_cum",
]
KNOWN_COLS = ["week_norm", "is_late_half", "weeks_to_final"]
KEYS = ["stu_id", "course_code", "semester"]


def build_panel(weekly: pd.DataFrame) -> dict:
    """把长表转成 (样本, 周, 特征) 面板。要求每个样本恰好覆盖 week 0..15 且连续。"""
    w = weekly.sort_values(KEYS + ["week"]).reset_index(drop=True)
    T = int(w["week"].max()) + 1
    n = len(w) // T
    if len(w) % T != 0:
        raise ValueError(f"tft_weekly 行数 {len(w)} 不能被周数 {T} 整除")
    weeks = w["week"].to_numpy().reshape(n, T)
    if not np.all(weeks == np.arange(T)[None, :]):
        raise ValueError("存在缺周的样本，请先检查 03_weekly_features.py 的补全逻辑")

    def stack(cols):
        return np.stack([w[c].to_numpy(dtype=np.float64).reshape(n, T) for c in cols], axis=-1)

    x_obs = stack(OBS_COLS)                       # (n, T, K)
    x_known = stack(KNOWN_COLS)                   # (n, T, 3)
    x_static = stack(STATIC_COLS)[:, 0, :]        # (n, S)，静态特征取第 0 周

    meta = w.groupby(KEYS, sort=True).head(1)[KEYS + ["risk_event_time", "is_positive", "split_group"]]
    meta = meta.reset_index(drop=True)
    event = meta["risk_event_time"].to_numpy().astype(int)[:, None]
    t = np.arange(T)[None, :]
    # 第 t 周标签：之后还会不会出事；事件已发生的周不参与训练/评估
    y = (event > t).astype(np.float32)
    valid = (event == -1) | (event > t)

    return {
        "x_static": x_static.astype(np.float32),
        "x_obs": x_obs.astype(np.float32),
        "x_known": x_known.astype(np.float32),
        "y": y, "valid": valid, "meta": meta, "T": T,
    }


class TFTLite(nn.Module):
    def __init__(self, n_static: int, n_obs: int, n_known: int, hidden: int = 64,
                 dropout: float = 0.1):
        super().__init__()
        # 变量选择：一部线性层出每个变量的权重，再 softmax（TFT 的选择网络简化版）
        self.w_static = nn.Linear(n_static, n_static)
        self.w_obs = nn.Linear(n_obs, n_obs)
        self.w_known = nn.Linear(n_known, n_known)
        self.static_ctx = nn.Linear(n_static, hidden)
        self.lstm = nn.LSTM(3 + n_obs, hidden, batch_first=True)
        self.head = nn.Sequential(nn.ReLU(), nn.Dropout(dropout), nn.Linear(hidden, 1))
        self.skip = nn.Linear(1, 1)

    def forward(self, xs, xo, xk):
        """xs (B,S)  xo (B,T,K)  xk (B,T,3) → (logits (B,T), weights dict)"""
        ws = torch.softmax(self.w_static(xs), dim=-1)                    # (B,S)
        s_sel = (ws * xs).sum(-1, keepdim=True)                          # (B,1)
        wo = torch.softmax(self.w_obs(xo), dim=-1)                       # (B,T,K)
        o_sel = (wo * xo).sum(-1, keepdim=True)                          # (B,T,1)
        wk = torch.softmax(self.w_known(xk), dim=-1)
        k_sel = (wk * xk).sum(-1, keepdim=True)

        seq = torch.cat([s_sel.unsqueeze(1).expand(-1, xo.size(1), -1), o_sel, k_sel, xo], dim=-1)
        h0 = torch.tanh(self.static_ctx(xs)).unsqueeze(0)
        c0 = torch.zeros_like(h0)
        out, _ = self.lstm(seq, (h0, c0))
        logits = self.head(out).squeeze(-1) + self.skip(s_sel)           # 静态跳连
        return logits, {"static": ws, "obs": wo, "known": wk}
