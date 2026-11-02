"""阶段二离线打分：产出后端唯一数据源 `risk_scores.parquet`。

输入：tft_weekly.parquet（周度面板）、model_tft_<日期>.pt、deepsurv_pred.parquet
输出：processed/risk_scores.parquet，字段严格按 docs/数据字段约定.md §3.2：
  stu_id / course_code / semester / week(0 起) / risk_prob / risk_level /
  risk_level_name / pred_event_week / top_factors(JSON 字符串)

口径说明（写进报告与后端对接文档）：
1. risk_prob 是 TFT 在"第 t 周"给出的"之后还会不会出事"概率；
2. 当真实风险事件已经发生（0 <= risk_event_time <= week）时，该周 risk_prob 置 1.0、
   risk_level 置 2（含义是"已出事"，不是模型预测值），前端应展示为"已发生"而不是"风险 100%"；
3. risk_level 的阈值在 test 切分第 8 周（产品主视图）上按预警率校准到 ≈15%（高 5% / 中 10%），
   并报告全表逐周的实际预警率；
4. pred_event_week 来自 DeepSurv，-1 表示本学期无风险；
5. top_factors 用 TFT 每一步的观测变量选择权重 × 该变量的标准化偏离，取前 3，中文名与图表共用 FEATURE_CN。

运行：py -3 algorithm/src/serving/20_score_offline.py
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.tft import KNOWN_COLS, OBS_COLS, STATIC_COLS, TFTLite, build_panel  # noqa: E402
from training.common import (  # noqa: E402
    MODELS_DIR, REPORTS_DIR, SEED, cn, resolve_processed, save_json, set_seed,
)

LEVEL_CN = {0: "低", 1: "中", 2: "高"}


def load_tft() -> tuple[TFTLite, dict, str]:
    paths = sorted(glob.glob(str(MODELS_DIR / "model_tft_*.pt")))
    if not paths:
        raise FileNotFoundError("缺少模型 model_tft_*.pt，先跑 train_tft.py")
    ckpt = torch.load(paths[-1], weights_only=False)
    model = TFTLite(len(ckpt["static_cols"]), len(ckpt["obs_cols"]), len(ckpt["known_cols"]))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt, Path(paths[-1]).name


def calibrate_by_week(prob: np.ndarray, y: np.ndarray, valid: np.ndarray, cal_split: np.ndarray,
                      test_split: np.ndarray, high_rate: float, mid_rate: float) -> tuple[dict, dict]:
    """逐周阈值校准：在 val（从未参与训练/汇报）上取分数分位，使当周预警率 ≈ high+mid。

    逐周（而非全局）的原因：风险概率随周次自然升高，全局阈值会让第 14 周预警率冲到 38%，
    失去"每周约 15% 学生需要关注"的产品含义。某周有效样本不足 200 时沿用上一周阈值。
    阈值来自 val，`test_alert_rate` 是 test 上的实际表现，避免"自己校自己"。
    """
    T = prob.shape[1]
    th, info, prev = {}, {}, None
    for t in range(T):
        m = cal_split & valid[:, t]
        if int(m.sum()) >= 200:
            p_high = float(np.quantile(prob[m, t], 1 - high_rate))
            p_mid = float(np.quantile(prob[m, t], 1 - high_rate - mid_rate))
            pred = np.where(prob[m, t] >= p_high, 2, np.where(prob[m, t] >= p_mid, 1, 0))
            mt = test_split & valid[:, t]
            pt = np.where(prob[mt, t] >= p_high, 2, np.where(prob[mt, t] >= p_mid, 1, 0))
            pos = y[mt, t] == 1
            info[t] = {
                "n_val": int(m.sum()),
                "n_test": int(mt.sum()),
                "threshold_high": round(p_high, 4),
                "threshold_mid": round(p_mid, 4),
                "val_alert_rate": round(float((pred > 0).mean()), 4),
                "test_alert_rate": round(float((pt > 0).mean()), 4),
                "test_precision_high": round(float(y[mt, t][pt == 2].mean()), 4) if (pt == 2).any() else None,
                "test_recall_high": round(float((pt[pos] >= 2).mean()), 4) if pos.any() else None,
            }
            prev = (p_high, p_mid)
        th[t] = prev if prev else (0.95, 0.75)
    return th, info


def build_top_factors(z_obs: np.ndarray, weights: np.ndarray, obs_raw: np.ndarray,
                     obs_cols: list[str], sign: np.ndarray, k: int = 3) -> list[str]:
    """TFT 变量权重 × 方向修正后的偏离 → 归一化贡献度，取前 k 个，输出 JSON 字符串。

    方向修正（sign）来自训练集第 8 周上"特征 → 未来风险"的相关性符号：
    出勤率越高越安全（sign=-1），缺勤越多越危险（sign=+1）。
    只用 |z| 会把"出勤率 100%"当成高风险因素，加上方向后只保留真正把风险推高的特征。

    z_obs    (n, T, K) 标准化后的观测特征
    weights  (n, T, K) 模型每一步的观测变量选择权重（softmax）
    obs_raw  (n, T, K) 原始观测值（展示用）
    """
    push = np.clip(z_obs * weights * sign, 0, None)   # 只保留"推高风险"的方向
    total = push.sum(-1, keepdims=True)
    fallback = np.abs(z_obs) * weights                        # 全部起保护作用时退化为强度排序
    fb_total = fallback.sum(-1, keepdims=True)
    safe = np.where(total > 1e-12, total, np.where(fb_total > 1e-12, fb_total, 1.0))
    contrib = np.where(total > 1e-12, push, fallback) / safe
    order = np.argsort(-contrib, axis=-1)[:, :, :k]
    idx = order.reshape(-1, k)
    c = np.take_along_axis(contrib, order, axis=2).reshape(-1, k)
    v = np.take_along_axis(obs_raw, order, axis=2).reshape(-1, k)
    names = np.asarray([cn(col) for col in obs_cols])[idx]
    return [
        "[" + ",".join(
            f'{{"factor":"{names[i, j]}","value":{v[i, j]:.2f},"contrib":{c[i, j]:.3f}}}'
            for j in range(k)) + "]"
        for i in range(names.shape[0])
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", default=None)
    ap.add_argument("--high", type=float, default=0.05, help="高风险目标占比")
    ap.add_argument("--mid", type=float, default=0.10, help="中风险目标占比")
    args = ap.parse_args()
    set_seed(SEED)
    processed = resolve_processed(args.processed)

    model, ckpt, ckpt_name = load_tft()
    weekly = pd.read_parquet(processed / "tft_weekly.parquet")
    panel = build_panel(weekly)
    T = panel["T"]
    n = len(panel["meta"])
    print(f"[数据] 样本={n:,}  周数={T}  行数={n * T:,}  模型={ckpt_name}")

    # 只保留训练时用到的非常数列（与 train_tft.py 一致）
    stat_cols, obs_cols, known_cols = ckpt["static_cols"], ckpt["obs_cols"], ckpt["known_cols"]
    si = [STATIC_COLS.index(c) for c in stat_cols]
    oi = [OBS_COLS.index(c) for c in obs_cols]
    ki = [KNOWN_COLS.index(c) for c in known_cols]
    xs = panel["x_static"][:, si]
    xo = panel["x_obs"][:, :, oi]
    xk = panel["x_known"][:, :, ki]

    (ms, ss), (mo, so), (mk, sk) = ckpt["static_std"], ckpt["obs_std"], ckpt["known_std"]
    xs_z = (xs - ms) / ss
    xo_z = (xo - mo) / so
    xk_z = (xk - mk) / sk

    with torch.no_grad():
        logits, w = model(torch.as_tensor(xs_z), torch.as_tensor(xo_z), torch.as_tensor(xk_z))
        prob = torch.sigmoid(logits).numpy().astype(np.float32)
        wo = w["obs"].numpy()

    meta = panel["meta"]
    event = meta["risk_event_time"].to_numpy().astype(int)[:, None]
    weeks = np.arange(T)[None, :]
    occurred = (event >= 0) & (event <= weeks)     # 该周风险事件已发生
    prob = np.where(occurred, 1.0, prob).astype(np.float32)

    # ---- 逐周阈值校准（val 定阈值，test 报表现）----
    split = meta["split_group"].to_numpy()
    th, calib = calibrate_by_week(prob, panel["y"].astype(int), panel["valid"],
                                  split == "val", split == "test", args.high, args.mid)
    print(f"[校准] w8 阈值 高={th[8][0]:.4f} 中={th[8][1]:.4f}  "
          f"val 预警率={calib[8]['val_alert_rate']:.1%}  test 预警率={calib[8]['test_alert_rate']:.1%}")

    # ---- 拼输出表（逐周套用当周阈值）----
    out = pd.DataFrame({
        "stu_id": np.repeat(meta["stu_id"].to_numpy(), T),
        "course_code": np.repeat(meta["course_code"].to_numpy(), T),
        "semester": np.repeat(meta["semester"].to_numpy(), T),
        "week": np.tile(np.arange(T), n).astype("int8"),
        "risk_prob": prob.reshape(-1),
    })
    pf = prob.reshape(-1)
    w_idx = out["week"].to_numpy()
    p_high_vec = np.array([th[t][0] for t in range(T)])[w_idx]
    p_mid_vec = np.array([th[t][1] for t in range(T)])[w_idx]
    level = np.where(pf >= p_high_vec, 2, np.where(pf >= p_mid_vec, 1, 0)).astype("int8")
    level[occurred.reshape(-1)] = 2             # 已发生 → 高
    out["risk_level"] = level
    out["risk_level_name"] = out["risk_level"].map(LEVEL_CN).astype("string")

    surv = pd.read_parquet(processed / "deepsurv_pred.parquet")[
        ["stu_id", "course_code", "semester", "pred_event_week"]]
    out = out.merge(surv, on=["stu_id", "course_code", "semester"], how="left")
    out["pred_event_week"] = out["pred_event_week"].fillna(-1).astype("int8")

    # 影响因子的方向：训练集第 8 周上"观测特征 ↔ 未来风险"的相关性符号
    tr8 = (split == "train") & panel["valid"][:, 8]
    y8 = panel["y"][tr8, 8]
    sign = np.array([
        np.sign(np.corrcoef(np.stack([xo_z[tr8, 8, k], y8]))[0, 1]) or 1.0
        for k in range(len(obs_cols))
    ], dtype=np.float32)
    print("[因子方向] " + "  ".join(
        f"{cn(c)}={'推高' if s > 0 else '降低'}" for c, s in zip(obs_cols, sign)))

    out["top_factors"] = build_top_factors(xo_z, wo, xo, obs_cols, sign)
    out = out[["stu_id", "course_code", "semester", "week", "risk_prob", "risk_level",
               "risk_level_name", "pred_event_week", "top_factors"]]

    # ---- 校验：契约字段 / 取值范围 ----
    checks = {
        "week_range_ok": bool(out["week"].between(0, T - 1).all()),
        "prob_range_ok": bool(out["risk_prob"].between(0, 1).all()),
        "prob_nan": int(out["risk_prob"].isna().sum()),
        "pred_event_week_min": int(out["pred_event_week"].min()),
        "pred_event_week_max": int(out["pred_event_week"].max()),
        "rows": int(len(out)),
        "students": int(out["stu_id"].nunique()),
    }
    by_week = (out.groupby("week")["risk_level"].apply(lambda s: round(float((s > 0).mean()), 4))
               .to_dict())
    valid_flat = panel["valid"].reshape(-1)
    alert_valid = {
        int(t): round(float((level[valid_flat & (out["week"].to_numpy() == t)] > 0).mean()), 4)
        for t in range(T) if int((valid_flat & (out["week"].to_numpy() == t)).sum()) > 0
    }
    test_flat = np.repeat(split == "test", T)
    alert_test = {
        int(t): round(float((level[test_flat & valid_flat & (out["week"].to_numpy() == t)] > 0).mean()), 4)
        for t in range(T) if int((test_flat & valid_flat & (out["week"].to_numpy() == t)).sum()) > 0
    }

    path = processed / "risk_scores.parquet"
    out.to_parquet(path, index=False)
    print(f"[产物] {path}  {len(out):,} 行  {path.stat().st_size / 1e6:.1f} MB")
    print("[校验] " + "  ".join(f"{k}={v}" for k, v in checks.items()))
    print("[逐周预警率] " + "  ".join(f"w{k}:{v:.1%}" for k, v in by_week.items()))
    print("[逐周预警率/仅有效样本] " + "  ".join(f"w{k}:{v:.1%}" for k, v in alert_valid.items()))
    print("[逐周预警率/test有效样本] " + "  ".join(f"w{k}:{v:.1%}" for k, v in alert_test.items()))

    save_json({
        "product": "risk_scores.parquet",
        "generated_at": date.today().isoformat(),
        "model_file": ckpt_name,
        "n_rows": int(len(out)),
        "n_students": int(out["stu_id"].nunique()),
        "weeks": int(T),
        "calibration_by_week": {str(k): v for k, v in calib.items()},
        "thresholds_by_week": {str(k): [round(v[0], 4), round(v[1], 4)] for k, v in th.items()},
        "alert_rate_by_week": by_week,
        "alert_rate_valid_by_week": alert_valid,
        "alert_rate_test_by_week": alert_test,
        "checks": checks,
        "notes": [
            "risk_prob 为 TFT 在当周给出的'之后是否还会出事'概率；",
            "风险事件已发生的周（0 <= risk_event_time <= week）置 risk_prob=1.0、risk_level=2，语义是'已发生'；",
            "risk_level 阈值逐周在 val 有效样本上校准（目标 15% = 高 5% + 中 10%），test 上的实际预警率见 alert_rate_test_by_week；",
            "第 15 周没有有效样本（事件必然已发生/无风险），沿用第 14 周阈值；",
            "pred_event_week 来自 M3 DeepSurv，-1 表示本学期无风险；",
        ],
    }, REPORTS_DIR / "serving_metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
