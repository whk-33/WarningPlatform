"""M0 基线：第 t 周快照 + 静态特征 → 逻辑回归 / MLP。

任务定义（与 TFT 完全一致，保证可比）：
    在第 t 周，预测"该学生-课程在第 t 周之后是否还会出现风险事件"。
    y_t = 0 若 risk_event_time == -1（全学期无风险）
    y_t = 1 若 risk_event_time > t（后面还会出事）
    事件已发生（0 < risk_event_time <= t）的样本剔除 —— 这不是预测题。

用法：
    py -3 algorithm/src/training/train_baseline.py --week 8
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import baseline as B  # noqa: E402
from training.common import (  # noqa: E402
    MODELS_DIR, REPORTS_DIR, SEED, cn, evaluate, load, resolve_processed, save_json, set_seed,
    setup_matplotlib, standardizer,
)

STATIC_NUM = [
    "gender", "entrance_score", "past_retake_count", "special_flag", "credits", "elective",
    "major_A Level or Equivalent", "major_HE Qualification", "major_Lower Than A Level",
    "major_No Formal quals", "major_Post Graduate Qualification",
]
OBS = [
    "attendance_rate", "ppt_clicks_cum", "video_watch_cum", "absent_count_cum",
    "quiz_accuracy_cum", "hw_score_cum", "hw_days_late_cum",
]


def build_snapshot(weekly: pd.DataFrame, week: int) -> pd.DataFrame:
    snap = weekly[weekly["week"] == week].copy()
    snap["y"] = np.where(
        snap["risk_event_time"] < 0, 0,
        np.where(snap["risk_event_time"] > week, 1, -1),
    )
    return snap[snap["y"] >= 0].reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", default=None, help="数据目录，默认 processed_full_demo")
    ap.add_argument("--week", type=int, default=8, help="快照周（0 起）")
    ap.add_argument("--epochs", type=int, default=300)
    args = ap.parse_args()

    set_seed(SEED)
    processed = resolve_processed(args.processed)
    weekly = load(processed, "tft_weekly")
    print(f"[数据] {processed.name}  tft_weekly={weekly.shape}  学生={weekly.stu_id.nunique()}  "
          f"正例率={weekly.drop_duplicates(['stu_id','course_code','semester']).is_positive.mean():.3f}")

    snap = build_snapshot(weekly, args.week)
    print(f"[快照] week={args.week}  样本={len(snap)}  正例率={snap.y.mean():.3f}  "
          f"（已剔除事件已发生的样本）")
    print("[切分] " + "  ".join(
        f"{g}: n={len(d)} pos={d.y.mean():.3f}"
        for g, d in snap.groupby("split_group")
    ))

    feats = STATIC_NUM + OBS
    x_all = snap[feats].astype(float).to_numpy()
    y_all = snap["y"].to_numpy().astype(int)
    split = snap["split_group"].to_numpy()

    # 常数列（OULAD 的 video_watch_cum 恒为 0）直接删掉，否则标准化后是噪声
    keep = [i for i in range(len(feats)) if np.nanstd(x_all[:, i]) > 1e-8]
    dropped = [feats[i] for i in range(len(feats)) if i not in keep]
    if dropped:
        print(f"[特征] 丢弃常数列 {dropped}")
    feats = [feats[i] for i in keep]
    x_all = x_all[:, keep]

    tr, va, te = (split == "train"), (split == "val"), (split == "test")
    mean, std = standardizer(x_all[tr])
    xz = (x_all - mean) / std

    import torch

    stamp = date.today().strftime("%Y%m%d")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    results, preds, fitted = {}, {}, {}
    for name, build in [
        ("logistic_regression", lambda: B.LogisticRegression(len(feats))),
        ("mlp", lambda: B.MLP(len(feats))),
    ]:
        model = build()
        B.fit(model, xz[tr], y_all[tr], epochs=args.epochs, seed=SEED)
        prob = B.predict(model, xz)
        preds[name], fitted[name] = prob, model
        results[name] = {g: evaluate(y_all[m], prob[m]) for g, m in
                         [("train", tr), ("val", va), ("test", te)]}
        print(f"[{name}] test AUC={results[name]['test']['auc']:.4f} "
              f"PR-AUC={results[name]['test']['pr_auc']:.4f} "
              f"Recall@10%={results[name]['test']['recall_at_10pct']:.4f} "
              f"Brier={results[name]['test']['brier']:.4f}")
        torch.save({"state_dict": model.state_dict(), "features": feats,
                    "mean": mean, "std": std, "week": args.week},
                   MODELS_DIR / f"model_{name}_{stamp}.pt")

    # 特征重要性：LR 用标准化系数（标准化后系数绝对值可直接比较）；
    # MLP 用验证集置换重要性（逐列打乱后 AUC 下降量），避免"权重不可解释"的指责。
    lr_w = np.abs(fitted["logistic_regression"].linear.weight.detach().numpy().ravel())
    base_auc = results["mlp"]["val"]["auc"]
    rng = np.random.default_rng(SEED)
    xv = xz[va].copy()
    perm_auc = []
    for i in range(len(feats)):
        saved = xv[:, i].copy()
        rng.shuffle(xv[:, i])
        perm_auc.append(float(base_auc - evaluate(y_all[va], B.predict(fitted["mlp"], xv))["auc"]))
        xv[:, i] = saved

    importance = {
        "logistic_regression_abs_coef": {cn(f): round(float(w), 4) for f, w in zip(feats, lr_w)},
        "mlp_val_permutation_auc_drop": {cn(f): round(v, 4) for f, v in zip(feats, perm_auc)},
    }
    top = sorted(zip(feats, lr_w), key=lambda t: -t[1])[:10]
    print("[最重要特征/LR] " + "  ".join(f"{cn(f)}={w:.3f}" for f, w in top))

    plt = setup_matplotlib()
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
    for ax, (title, values) in zip(axes, [
        ("逻辑回归：标准化系数绝对值", lr_w),
        ("MLP：验证集置换后 AUC 下降", np.array(perm_auc)),
    ]):
        order = np.argsort(values)[-10:]
        ax.barh([cn(feats[i]) for i in order], values[order], color="#4C78A8")
        ax.set_title(title)
        ax.set_xlabel("重要性")
        ax.grid(axis="x", alpha=0.3)
    fig.suptitle(f"M0 基线特征重要性（第 {args.week} 周快照，n={len(snap)}）")
    fig.tight_layout()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fig_path = REPORTS_DIR / "fig_baseline_importance.png"
    fig.savefig(fig_path)
    print(f"[产物] {fig_path}")

    report = {
        "model": "M0_baseline",
        "week": args.week,
        "processed_dir": str(processed),
        "n_samples": int(len(snap)),
        "positive_rate": round(float(snap.y.mean()), 4),
        "features": feats,
        "dropped_constant_features": dropped,
        "results": results,
        "importance": importance,
    }
    save_json(report, REPORTS_DIR / "baseline_metrics.json")
    print(f"[产物] {REPORTS_DIR / 'baseline_metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
