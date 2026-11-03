"""阶段三质量门禁：缺失率 + PSI + 标签缺失。

运行：py -3 algorithm/src/serving/32_quality_checks.py `
        --baseline algorithm/data/oulad/processed_full_demo/tft_weekly.parquet `
        --new algorithm/data/oulad/processed_new/tft_weekly.parquet
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

THRESHOLD_MISS = 0.05
THRESHOLD_PSI = 0.25
THRESHOLD_LABEL = 0.01


def missing_rate(df: pd.DataFrame, cols: list[str] | None = None) -> dict[str, float]:
    cols = cols or [c for c in df.columns if df[c].dtype in ("float32", "float64", "float", "int32", "int64")]
    return {c: round(float(df[c].isna().mean()), 4) for c in cols}


def psi(p: np.ndarray, q: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index。

    阈值经验：<0.1 稳定 / 0.1-0.25 关注 / >0.25 漂移严重。
    p=新数据分桶占比，q=基线分桶占比。用 q 的分位数做分桶边界，保证基线桶平衡。
    """
    p = np.asarray(p, dtype=float)
    q = np.asarray(q, dtype=float)
    mask = np.isfinite(p) & np.isfinite(q)
    p, q = p[mask], q[mask]
    if len(p) < 100 or len(q) < 100:
        return 0.0
    edges = np.quantile(q, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    p_counts, _ = np.histogram(p, bins=edges)
    q_counts, _ = np.histogram(q, bins=edges)
    p_ratio = p_counts / max(p_counts.sum(), 1)
    q_ratio = q_counts / max(q_counts.sum(), 1)
    p_ratio = np.clip(p_ratio, 1e-6, 1)
    q_ratio = np.clip(q_ratio, 1e-6, 1)
    return float(np.sum((p_ratio - q_ratio) * np.log(p_ratio / q_ratio)))


def check_dataset(new: pd.DataFrame, baseline: pd.DataFrame,
                  cols: list[str] | None = None) -> tuple[bool, dict]:
    """对数值列同时做缺失率与 PSI 检查。返回 (是否通过, 详细结果 dict)。"""
    cols = cols or [c for c in new.columns if new[c].dtype in ("float32", "float64", "float", "int32", "int64")]
    ms = missing_rate(new, cols)
    psis: dict[str, float] = {}
    for c in cols:
        if c not in baseline.columns:
            continue
        q = baseline[c].dropna().to_numpy()
        p = new[c].dropna().to_numpy()
        psis[c] = psi(p, q)
    miss_fail = {c: v for c, v in ms.items() if v > THRESHOLD_MISS}
    psi_fail = {c: v for c, v in psis.items() if v > THRESHOLD_PSI}
    passed = not miss_fail and not psi_fail
    return passed, {
        "missing_rate": ms,
        "psi": psis,
        "missing_fail": miss_fail,
        "psi_fail": psi_fail,
        "threshold_miss": THRESHOLD_MISS,
        "threshold_psi": THRESHOLD_PSI,
        "passed": passed,
    }


def check_labels(new: pd.DataFrame) -> tuple[bool, dict]:
    if "risk_event_time" not in new.columns:
        return True, {}
    miss = float(new["risk_event_time"].isna().mean())
    passed = miss <= THRESHOLD_LABEL
    return passed, {
        "label_missing_rate": round(miss, 4),
        "threshold_label": THRESHOLD_LABEL,
        "passed": passed,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True, help="基线 parquet（用于 PSI 分桶边界）")
    ap.add_argument("--new", required=True, help="新数据 parquet（周更产物）")
    args = ap.parse_args()

    b = pd.read_parquet(args.baseline)
    n = pd.read_parquet(args.new)

    ok_d, r_d = check_dataset(n, b)
    ok_l, r_l = check_labels(n)
    all_ok = ok_d and ok_l

    print(f"[缺失率] {'FAIL' if r_d['missing_fail'] else 'OK'}  "
          f"超标列: {list(r_d['missing_fail'].keys())}")
    print(f"[PSI]    {'FAIL' if r_d['psi_fail'] else 'OK'}  "
          f"超标列: {list(r_d['psi_fail'].keys())}")
    if not r_l:
        print("[标签]   跳过（new 无 risk_event_time 列）")
    else:
        print(f"[标签]   {'FAIL' if not ok_l else 'OK'}  "
              f"缺失率: {r_l['label_missing_rate']:.4f}")

    if not all_ok:
        print("[结论] 门禁未通过，阻断打分。")
        return 1
    print("[结论] 门禁通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
