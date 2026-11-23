"""阶段四补丁：给已有 risk_scores.parquet 补 risk_state / risk_score，并修正期末周阈值。

为什么需要：本机 torch 被 Windows Smart App Control 拦截，无法重跑 20_score_offline.py。
本脚本不加载模型，只用「已有概率 + tft_weekly 的 risk_event_time」做等价补齐。
重跑 20 之后本脚本不再需要（20 已直接输出 11 列且期末周沿用上一周阈值）。

做的事：
1. 从 tft_weekly.parquet 取 risk_event_time，判定"已发生"（0 <= event <= week）；
2. risk_state：0=模型预测，1=已发生；
3. risk_score：未发生行 = logit(risk_prob)，已发生行 = NaN（未饱和排序分）；
4. 期末周（week=T-1）原阈值取自"永不事件"池、无判别意义 → 改用 T-2 周阈值重算 level；
5. 原子替换写回 parquet，并打印逐周预警率（全表 / 风险池）。

用法：
    py -3 algorithm/src/serving/41_enrich_risk_scores.py [--processed <dir>]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROCESSED = PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_full_demo"
REPORT = PROJECT_ROOT / "algorithm" / "reports" / "serving_metrics.json"
LEVEL_CN = {0: "低", 1: "中", 2: "高"}


def logit(p: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    p = np.clip(p.astype(np.float64), eps, 1.0 - eps)
    return np.log(p / (1.0 - p)).astype(np.float32)


def load_thresholds(T: int) -> list[tuple[float, float]]:
    """读取逐周阈值 (high, mid)；期末周强制沿用 T-2 周。"""
    if not REPORT.exists():
        raise FileNotFoundError(f"缺少 {REPORT}，无法取得逐周阈值，请先跑 20_score_offline.py")
    th_map = json.loads(REPORT.read_text(encoding="utf-8"))["thresholds_by_week"]
    th: list[tuple[float, float]] = []
    for t in range(T):
        key = str(t) if str(t) in th_map else str(T - 2)
        high, mid = th_map[key]
        th.append((float(high), float(mid)))
    th[T - 1] = th[T - 2]
    return th


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", default=str(DEFAULT_PROCESSED))
    args = ap.parse_args()
    processed = Path(args.processed)
    rs_path = processed / "risk_scores.parquet"

    df = pd.read_parquet(rs_path)
    tw = pd.read_parquet(
        processed / "tft_weekly.parquet",
        columns=["stu_id", "course_code", "semester", "week", "risk_event_time"],
    )
    T = int(df["week"].max()) + 1
    before = df.groupby("week")["risk_level"].apply(lambda s: float((s > 0).mean())).to_dict()

    keys = ["stu_id", "course_code", "semester", "week"]
    merged = df.merge(tw, on=keys, how="left", validate="one_to_one", suffixes=("", "_tw"))
    if len(merged) != len(df) or merged["risk_event_time"].isna().any():
        raise ValueError("tft_weekly 与 risk_scores 行不匹配，请确认两表同源")
    week = merged["week"].to_numpy()
    event = merged["risk_event_time"].to_numpy().astype(int)
    occurred = (event >= 0) & (event <= week)
    valid = (event == -1) | (event > week)          # 风险池：尚未出事

    th = load_thresholds(T)
    p_high = np.array([th[t][0] for t in range(T)])[week]
    p_mid = np.array([th[t][1] for t in range(T)])[week]
    prob = merged["risk_prob"].to_numpy()
    level = np.where(prob >= p_high, 2, np.where(prob >= p_mid, 1, 0)).astype("int8")
    level[occurred] = 2                              # 已发生 → 高（展示"已发生"）

    df["risk_state"] = occurred.astype("int8")
    df["risk_score"] = np.where(occurred, np.nan, logit(prob)).astype(np.float32)
    df["risk_level"] = level
    df["risk_level_name"] = pd.Series(level, index=df.index).map(LEVEL_CN).astype("string")
    df = df[[
        "stu_id", "course_code", "semester", "week", "risk_prob", "risk_level",
        "risk_level_name", "pred_event_week", "top_factors", "risk_state", "risk_score",
    ]]

    tmp = rs_path.with_name("risk_scores.tmp.parquet")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, rs_path)

    after = df.groupby("week")["risk_level"].apply(lambda s: float((s > 0).mean())).to_dict()
    print(f"[产物] {rs_path}  {len(df):,} 行 × {df.shape[1]} 列  {rs_path.stat().st_size / 1e6:.1f} MB")
    print(f"[risk_state] 预测={int((df.risk_state == 0).sum()):,}  已发生={int((df.risk_state == 1).sum()):,}")
    print(f"[risk_score] 非空={int(df.risk_score.notna().sum()):,}  空值=已发生行")
    print(f"[期末周] 沿用 w{T - 2} 阈值 高={th[T - 1][0]:.4f} 中={th[T - 1][1]:.4f}")
    for t in (8, T - 2, T - 1):
        pool = valid & (week == t)
        rows_t = week == t
        print(
            f"[w{t}] 全表预警 {before[t]:.2%} → {after[t]:.2%}  "
            f"风险池预警={float((level[pool] > 0).mean()):.2%}  "
            f"已发生={float((occurred & rows_t).sum() / rows_t.sum()):.2%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
