"""M3 DeepSurv：Cox 比例风险（2 层 MLP 输出风险分 h(x)）。

标签：event=1 表示观察到风险事件（首次退学/不合格），event_time 为周（0–15）；
event=0 为删失（到学期末都没出事）。
损失：Cox 部分似然（Breslow 处理并列时间，时间只有 16 个取值，按时间集合精确计算即可）。
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn


class DeepSurv(nn.Module):
    def __init__(self, n_features: int, hidden: int = 32, dropout: float = 0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_features, hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden, hidden // 2), nn.ReLU(),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)  # 风险分 h(x)，越大风险越高


def cox_ph_loss(h: torch.Tensor, time: torch.Tensor, event: torch.Tensor) -> torch.Tensor:
    """Breslow 并列处理：同一时间点的样本共享同一个风险集。"""
    h = h - h.max().detach()  # 数值稳定
    loss, n_event = 0.0, 0
    for t in torch.unique(time):
        risk_set = time >= t          # 风险集：时间不早于 t（含并列）
        log_denom = torch.logsumexp(h[risk_set], dim=0)
        mask = (time == t) & (event == 1)
        loss = loss + (log_denom - h[mask]).sum()
        n_event += int(mask.sum())
    return loss / max(n_event, 1)


def concordance_index(h: np.ndarray, time: np.ndarray, event: np.ndarray) -> float:
    """C-index：对所有"时间可比且较早者发生事件"的样本对，判断风险分排序是否正确。"""
    order = np.argsort(time)
    h, time, event = h[order], time[order], event[order]
    num, den = 0.0, 0.0
    for i in range(len(h)):
        if event[i] != 1:
            continue
        later = time[i + 1:] > time[i]
        if later.sum() == 0:
            continue
        comparable = h[i + 1:][later]
        den += len(comparable)
        num += float((comparable < h[i]).sum()) + 0.5 * float((comparable == h[i]).sum())
    return float(num / den) if den else float("nan")


def breslow_baseline(h: np.ndarray, time: np.ndarray, event: np.ndarray) -> dict[int, float]:
    """估计基线累积风险 H0(t)，用于把风险分换算成预测事件周。"""
    exp_h = np.exp(np.clip(h, -20, 20))
    h0, acc = {}, 0.0
    for t in sorted(set(time.tolist())):
        at_risk = time >= t
        d = int(((time == t) & (event == 1)).sum())
        denom = exp_h[at_risk].sum()
        acc += d / max(denom, 1e-12)
        h0[int(t)] = acc
    return h0


def survival_curves(h: np.ndarray, h0: dict[int, float], horizon: int = 15) -> tuple[np.ndarray, np.ndarray]:
    """S_i(t) = exp(-H0(t)·exp(h_i))，返回 (时间点数组, 生存概率矩阵)。"""
    times = np.array([t for t in sorted(h0) if 0 <= t <= horizon])
    exp_h = np.exp(np.clip(h, -20, 20))
    s = np.exp(-np.outer(exp_h, [h0[int(t)] for t in times]))
    return times, s


def predict_event_week(h: np.ndarray, h0: dict[int, float], horizon: int = 15,
                       threshold: float = 0.5) -> np.ndarray:
    """首次 S(t) 低于 threshold 的周视为预测事件周；否则 -1（学期内无明显风险）。

    threshold=0.5 即"中位生存时间"（标准口径）；产品线可调高（预警率 10–15%）后
    用 `calibrate_threshold` 反推阈值。
    """
    times, s = survival_curves(h, h0, horizon)
    out = np.full(len(h), -1, dtype=np.int64)
    for i in range(len(h)):
        hit = np.where(s[i] < threshold)[0]
        if hit.size:
            out[i] = int(times[hit[0]])
    return out


def calibrate_threshold(h_calib: np.ndarray, h0: dict[int, float], target_rate: float = 0.15,
                        horizon: int = 15) -> float:
    """在训练集上取"最低生存概率"的分位数作为阈值，使被标为有风险的比例≈target_rate。"""
    _, s = survival_curves(h_calib, h0, horizon)
    mins = s.min(axis=1)
    return float(np.quantile(mins, target_rate))
