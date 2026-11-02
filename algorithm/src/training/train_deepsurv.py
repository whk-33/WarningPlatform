"""M3 DeepSurv 训练：风险发生时间（pred_event_week）。

输入：deepsurv_events（32,593 条 stu×course×semester）+ tft_weekly 的静态特征
评估：test 集 C-index；同时报告"按预测周分箱的实际事件率"作为校准检查。
产物：deepsurv_pred.parquet（stu_id/course_code/semester/risk_score/pred_event_week）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.deepsurv import (  # noqa: E402
    DeepSurv, breslow_baseline, calibrate_threshold, concordance_index, cox_ph_loss,
    predict_event_week,
)
from training.common import (  # noqa: E402
    MODELS_DIR, REPORTS_DIR, SEED, load, resolve_processed, save_json, set_seed, standardizer,
)

STATIC = [
    "gender", "entrance_score", "past_retake_count", "special_flag", "credits", "elective",
    "major_A Level or Equivalent", "major_HE Qualification", "major_Lower Than A Level",
    "major_No Formal quals", "major_Post Graduate Qualification",
]
KEYS = ["stu_id", "course_code", "semester"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", default=None)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--patience", type=int, default=20)
    args = ap.parse_args()

    set_seed(SEED)
    processed = resolve_processed(args.processed)
    events = load(processed, "deepsurv_events")
    weekly = load(processed, "tft_weekly")
    static = (weekly.sort_values(KEYS + ["week"]).groupby(KEYS, as_index=False).head(1)
              [KEYS + STATIC + ["split_group"]])
    df = events.merge(static, on=KEYS, how="left", validate="one_to_one")
    if df["split_group"].isna().any():
        raise ValueError("deepsurv_events 里有样本在 tft_weekly 中找不到静态特征")

    use = [c for c in STATIC if df[c].std() > 1e-8]
    dropped = [c for c in STATIC if c not in use]
    print(f"[数据] 事件样本={len(df)}  学生={df.stu_id.nunique()}  特征={len(use)}  丢弃常数列={dropped}")
    print(f"[标签] 事件率={df.event.mean():.3f}  event_time 分布="
          f"{df[df.event == 1].event_time.value_counts().sort_index().head(8).to_dict()} …")
    print("[切分] " + "  ".join(
        f"{g}:n={len(d)}事件率={d.event.mean():.3f}" for g, d in df.groupby("split_group")))

    split = df["split_group"].to_numpy()
    x = df[use].astype(float).to_numpy()
    time = df["event_time"].to_numpy().astype(np.float32)
    event = df["event"].to_numpy().astype(np.int64)
    mean, std = standardizer(x[split == "train"])
    xz = ((x - mean) / std).astype(np.float32)

    xt = torch.as_tensor(xz)
    tt = torch.as_tensor(time)
    et = torch.as_tensor(event)

    model = DeepSurv(len(use))
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-2)
    tr = np.where(split == "train")[0]
    va = np.where(split == "val")[0]
    best, best_state, bad = -1.0, None, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        perm = np.random.permutation(tr)
        for start in range(0, len(perm), 512):
            b = perm[start:start + 512]
            opt.zero_grad()
            loss = cox_ph_loss(model(xt[b]), tt[b], et[b])
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            h_va = model(xt[va]).numpy()
        c = concordance_index(h_va, time[va], event[va])
        if c > best:
            best, bad = c, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
        if epoch % 20 == 0 or bad == args.patience:
            print(f"  epoch {epoch:03d}  val_C-index={c:.4f}{' *' if bad == 0 else ''}")
        if bad >= args.patience:
            print("  early stop")
            break

    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        h_all = model(xt).numpy()

    results = {}
    for g in ["train", "val", "test"]:
        m = split == g
        results[g] = {"c_index": round(concordance_index(h_all[m], time[m], event[m]), 4), "n": int(m.sum())}
        print(f"[{g}] C-index={results[g]['c_index']:.4f}  n={results[g]['n']}")

    h0 = breslow_baseline(h_all[tr], time[tr], event[tr])
    # 产品口径：预警名单控制在 15% 左右（中位生存时间会让只有 4% 被标出，太保守）
    tau = calibrate_threshold(h_all[tr], h0, target_rate=0.15)
    pred_week = predict_event_week(h_all, h0, threshold=tau)
    te = split == "test"
    obs_rate = float((event[te] == 1).mean())
    pred_rate = float((pred_week[te] >= 0).mean())
    hit = float(((pred_week[te] >= 0) & (event[te] == 1)).sum() / max((pred_week[te] >= 0).sum(), 1))
    recall = float(((pred_week[te] >= 0) & (event[te] == 1)).sum() / max((event[te] == 1).sum(), 1))
    print(f"[校准] 阈值 tau={tau:.3f}  test 实际事件率={obs_rate:.3f}  预测出事件={pred_rate:.3f}  "
          f"精确率={hit:.3f}  召回={recall:.3f}")
    print(f"[基线累积风险] " + "  ".join(f"H0({t})={v:.2f}" for t, v in sorted(h0.items())[:6]))

    out = df[KEYS + ["split_group"]].copy()
    out["risk_score"] = h_all.astype("float32")
    out["pred_event_week"] = pred_week.astype("int8")
    out.to_parquet(processed / "deepsurv_pred.parquet", index=False)
    print(f"[产物] {processed / 'deepsurv_pred.parquet'}  {out.shape}")

    stamp = date.today().strftime("%Y%m%d")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "features": use, "mean": mean, "std": std,
                "h0": h0}, MODELS_DIR / f"model_deepsurv_{stamp}.pt")
    save_json({
        "model": "M3_DeepSurv", "features": use,
        "results": results,
        "survival_threshold": round(tau, 4),
        "test_event_rate": round(obs_rate, 4),
        "test_pred_event_rate": round(pred_rate, 4),
        "test_pred_precision": round(hit, 4),
        "test_pred_recall": round(recall, 4),
        "baseline_hazard": {str(k): round(v, 4) for k, v in h0.items()},
    }, REPORTS_DIR / "deepsurv_metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
