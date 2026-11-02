"""M2 TFT 训练：周度风险概率（产品主视图数据源）。

任务：第 t 周预测"该学生-课程在第 t 周之后是否还会出现风险事件"。
- y_t = 0（risk_event_time = -1，全学期无风险）
- y_t = 1（risk_event_time > t）
- 事件已发生的周不参与损失与评估（masked BCE）
切分：split_group（学生级，禁止按行随机切）
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

from models.tft import KNOWN_COLS, OBS_COLS, STATIC_COLS, TFTLite, build_panel  # noqa: E402
from training.common import (  # noqa: E402
    MODELS_DIR, REPORTS_DIR, SEED, cn, evaluate, load, resolve_processed, save_json, set_seed,
    setup_matplotlib, standardizer,
)

CHECK_WEEKS = [4, 8, 12, 14]
# 第 15 周不作为检查点：事件必然 <=15 周，按"未来是否出事"的定义该周没有正例


def masked_eval(y, prob, valid) -> dict:
    return evaluate(y[valid], prob[valid])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", default=None)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--patience", type=int, default=4)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    args = ap.parse_args()

    set_seed(SEED)
    processed = resolve_processed(args.processed)
    weekly = load(processed, "tft_weekly")
    panel = build_panel(weekly)
    meta = panel["meta"]
    split = meta["split_group"].to_numpy()
    T = panel["T"]
    print(f"[数据] 样本={len(meta)}  学生={meta.stu_id.nunique()}  周数={T}  "
          f"切分=" + "  ".join(f"{g}:{int((split == g).sum())}" for g in ["train", "val", "test"]))
    print(f"[标签] 按周有效样本数： " + "  ".join(
        f"w{t}={int(panel['valid'][:, t].sum())}(+{panel['y'][:, t][panel['valid'][:, t]].mean():.2f})"
        for t in CHECK_WEEKS))

    # 只保留非常数列（OULAD 的 video_watch_cum 恒为 0）
    stat_cols, obs_cols, known_cols = list(STATIC_COLS), list(OBS_COLS), list(KNOWN_COLS)
    stat_idx = [i for i, c in enumerate(stat_cols) if panel["x_static"][:, i].std() > 1e-8]
    obs_idx = [i for i, c in enumerate(obs_cols) if panel["x_obs"][:, :, i].std() > 1e-8]
    stat_cols = [stat_cols[i] for i in stat_idx]
    obs_cols = [obs_cols[i] for i in obs_idx]
    dropped = [c for c in STATIC_COLS + OBS_COLS if c not in stat_cols + obs_cols]
    if dropped:
        print(f"[特征] 丢弃常数列 {dropped}")

    xs = panel["x_static"][:, stat_idx]
    xo = panel["x_obs"][:, :, obs_idx]
    xk = panel["x_known"]

    tr, va = (split == "train"), (split == "val")
    sx = standardizer(xs[tr])
    so = standardizer(xo[tr].reshape(-1, len(obs_cols)))
    sk = standardizer(xk[tr].reshape(-1, len(known_cols)))
    xs_z = (xs - sx[0]) / sx[1]
    xo_z = (xo - so[0]) / so[1]
    xk_z = (xk - sk[0]) / sk[1]

    model = TFTLite(len(stat_cols), len(obs_cols), len(known_cols))
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-2)
    loss_fn = nn.BCEWithLogitsLoss(reduction="none")

    xs_t = torch.as_tensor(xs_z)
    xo_t = torch.as_tensor(xo_z)
    xk_t = torch.as_tensor(xk_z)
    y_t = torch.as_tensor(panel["y"])
    valid_t = torch.as_tensor(panel["valid"], dtype=torch.bool)

    def predict(idx: np.ndarray) -> np.ndarray:
        model.eval()
        with torch.no_grad():
            logits, _ = model(xs_t[idx], xo_t[idx], xk_t[idx])
            return torch.sigmoid(logits).numpy()

    tr_idx = np.where(tr)[0]
    best_auc, best_state, bad = -1.0, None, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        perm = np.random.permutation(tr_idx)
        total, seen = 0.0, 0
        for start in range(0, len(perm), args.batch):
            b = perm[start:start + args.batch]
            opt.zero_grad()
            logits, _ = model(xs_t[b], xo_t[b], xk_t[b])
            m = valid_t[b]
            loss = (loss_fn(logits, y_t[b]) * m).sum() / m.sum().clamp(min=1)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += float(loss.detach()) * int(m.sum())
            seen += int(m.sum())
        p_val = predict(np.where(va)[0])
        auc_val = masked_eval(panel["y"][va], p_val, panel["valid"][va])["auc"]
        w8 = evaluate(panel["y"][va][:, 8][panel["valid"][va][:, 8]],
                      p_val[:, 8][panel["valid"][va][:, 8]])["auc"]
        flag = " *" if auc_val > best_auc else ""
        print(f"  epoch {epoch:02d}  loss={total / max(seen,1):.4f}  val_AUC(all)={auc_val:.4f}  "
              f"val_AUC(w8)={w8:.4f}{flag}")
        if auc_val > best_auc:
            best_auc, bad = auc_val, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= args.patience:
                print("  early stop")
                break

    model.load_state_dict(best_state)
    prob = predict(np.arange(len(meta)))
    results = {"per_week": {}, "all_steps": {}}
    for g in ["val", "test"]:
        m = split == g
        results["all_steps"][g] = masked_eval(panel["y"][m], prob[m], panel["valid"][m])
        for t in CHECK_WEEKS:
            results["per_week"][f"w{t}_{g}"] = evaluate(
                panel["y"][m][:, t][panel["valid"][m][:, t]],
                prob[m][:, t][panel["valid"][m][:, t]])
    print("[测试集] " + "  ".join(
        f"w{t}:AUC={results['per_week'][f'w{t}_test']['auc']:.4f}" for t in CHECK_WEEKS))
    print(f"[测试集] 全周 pooled AUC={results['all_steps']['test']['auc']:.4f}  "
          f"PR-AUC={results['all_steps']['test']['pr_auc']:.4f}")

    # ---- 变量选择权重导出（前端"影响因子"条形图的数据源）----
    model.eval()
    with torch.no_grad():
        _, w = model(xs_t, xo_t, xk_t)
    weights = {
        "static": {cn(c): round(float(v), 4) for c, v in zip(
            stat_cols, w["static"].mean(0).numpy())},
        "observed": {cn(c): round(float(v), 4) for c, v in zip(
            obs_cols, w["obs"].mean(dim=(0, 1)).numpy())},
        "known_future": {cn(c): round(float(v), 4) for c, v in zip(
            known_cols, w["known"].mean(dim=(0, 1)).numpy())},
    }
    print("[变量选择/观测] " + "  ".join(
        f"{k}={v:.3f}" for k, v in sorted(weights["observed"].items(), key=lambda x: -x[1])))

    plt = setup_matplotlib()
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    for ax, (title, d) in zip(axes, [
        ("静态变量权重", weights["static"]),
        ("时变观测变量权重", weights["observed"]),
        ("已知未来变量权重", weights["known_future"]),
    ]):
        items = sorted(d.items(), key=lambda x: x[1])
        ax.barh([k for k, _ in items], [v for _, v in items], color="#4C78A8")
        ax.set_title(title)
        ax.set_xlabel("softmax 权重")
        ax.grid(axis="x", alpha=0.3)
    fig.suptitle("TFT 变量选择权重（三组各自归一化）")
    fig.tight_layout()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(REPORTS_DIR / "fig_tft_variable_weights.png")

    stamp = date.today().strftime("%Y%m%d")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(), "T": T,
        "static_cols": stat_cols, "obs_cols": obs_cols, "known_cols": known_cols,
        "static_std": sx, "obs_std": so, "known_std": sk,
    }, MODELS_DIR / f"model_tft_{stamp}.pt")
    save_json({
        "model": "M2_TFT_lite", "processed_dir": str(processed),
        "n_samples": int(len(meta)), "T": T,
        "results": results, "variable_weights": weights,
        "note": "轻量版 TFT：变量选择+LSTM+静态富化+跳连；二元标签用 masked BCE，无分位数头。",
    }, REPORTS_DIR / "tft_metrics.json")
    print(f"[产物] {REPORTS_DIR / 'tft_metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
