"""M1 DKT 训练 + 知识点掌握度导出。

任务：下一题答对预测（masked BCE）。评估只看 val/test 上的下一题 AUC。
产物：
    algorithm/models/model_dkt_<日期>.pt
    algorithm/data/oulad/processed_full_demo/kp_mastery.parquet
    algorithm/reports/dkt_metrics.json

⚠️ 限制（必须写进报告）：OULAD 没有真实题目标注，kp_idx 由考核截止日期伪标签推导，
AUC 只证明训练/评估流程正确，不能作为教学质量结论。
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

from models.dkt import DKT  # noqa: E402
from training.common import (  # noqa: E402
    MODELS_DIR, REPORTS_DIR, SEED, evaluate, load, resolve_processed, save_json, set_seed,
)

MAX_SEQ_LEN = 12


def to_matrix(series, maxlen: int) -> np.ndarray:
    out = np.zeros((len(series), maxlen), dtype=np.int64)
    for i, seq in enumerate(series):
        s = np.asarray(seq, dtype=np.int64)[:maxlen]
        out[i, :len(s)] = s
    return out


def step_mask(lengths: np.ndarray, maxlen: int) -> np.ndarray:
    """第 t 步可预测 ⇔ 1<=t<L（用 h_{t-1} 预测第 t 步）。"""
    t = np.arange(maxlen)[None, :]
    return (t >= 1) & (t < lengths[:, None])


def masked_preds(model, item, correct, kp, lengths) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    with torch.no_grad():
        logits = model(torch.as_tensor(item), torch.as_tensor(correct), torch.as_tensor(kp))
        prob = torch.sigmoid(logits).numpy()
    m = step_mask(lengths, item.shape[1])
    return prob[m], correct[m]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", default=None)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--patience", type=int, default=5)
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()

    set_seed(SEED)
    processed = resolve_processed(args.processed)
    seq = load(processed, "dkt_sequences")
    kp_dim = load(processed, "dim_knowledge_point").sort_values("kp_idx")

    item = to_matrix(seq["item_idx_seq"], MAX_SEQ_LEN)
    correct = to_matrix(seq["is_correct_seq"], MAX_SEQ_LEN)
    kp = to_matrix(seq["kp_idx_seq"], MAX_SEQ_LEN)
    lengths = seq["sequence_len"].to_numpy().astype(np.int64).clip(1, MAX_SEQ_LEN)
    split = seq["split_group"].to_numpy()

    n_items = int(item.max()) + 1
    n_kps = int(kp_dim["kp_idx"].max()) + 1
    print(f"[数据] 序列={len(seq)}  学生={seq.stu_id.nunique()}  题目={n_items}  知识点={n_kps}  "
          f"切分={ {g: int((split == g).sum()) for g in ['train','val','test']} }")
    step_valid = np.arange(MAX_SEQ_LEN)[None, :] < lengths[:, None]
    print(f"[信息] 序列长度 均值={lengths.mean():.2f}  中位={np.median(lengths):.0f}  "
          f"答对率={correct[step_valid].mean():.3f}")

    tr = np.where(split == "train")[0]
    va = np.where(split == "val")[0]
    te = np.where(split == "test")[0]

    model = DKT(n_items, n_kps)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-2)
    loss_fn = nn.BCEWithLogitsLoss(reduction="none")
    mask_np = step_mask(lengths, MAX_SEQ_LEN)

    best_auc, best_state, bad = -1.0, None, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        perm = np.random.permutation(tr)
        total, seen = 0.0, 0
        for start in range(0, len(perm), args.batch):
            b = perm[start:start + args.batch]
            opt.zero_grad()
            logits = model(torch.as_tensor(item[b]), torch.as_tensor(correct[b]),
                           torch.as_tensor(kp[b]))
            m = torch.as_tensor(mask_np[b], dtype=torch.bool)
            loss = (loss_fn(logits, torch.as_tensor(correct[b]).float()) * m).sum() / m.sum().clamp(min=1)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += float(loss) * int(m.sum())
            seen += int(m.sum())
        p_val, y_val = masked_preds(model, item[va], correct[va], kp[va], lengths[va])
        auc = evaluate(y_val, p_val)["auc"]
        flag = " *" if auc > best_auc else ""
        print(f"  epoch {epoch:02d}  train_loss={total / max(seen,1):.4f}  val_AUC={auc:.4f}{flag}")
        if auc > best_auc:
            best_auc, bad = auc, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= args.patience:
                print(f"  early stop（val AUC 连续 {args.patience} 轮未提升）")
                break

    model.load_state_dict(best_state)
    metrics = {}
    for name, idxs in [("val", va), ("test", te)]:
        p, y = masked_preds(model, item[idxs], correct[idxs], kp[idxs], lengths[idxs])
        metrics[name] = evaluate(y, p)
        print(f"[{name}] AUC={metrics[name]['auc']:.4f}  PR-AUC={metrics[name]['pr_auc']:.4f}  "
              f"Brier={metrics[name]['brier']:.4f}  n={metrics[name]['n']}")

    # ---- 知识点掌握度导出 ----
    kp_by_course = {c: d["kp_idx"].to_numpy() for c, d in kp_dim.groupby("course_code")}
    rows = []
    model.eval()
    with torch.no_grad():
        for course, sub in seq.groupby("course_code"):
            kp_ids = kp_by_course.get(course)
            if kp_ids is None:
                continue
            b_idx = sub.index.to_numpy()
            m = model.mastery(torch.as_tensor(item[b_idx]), torch.as_tensor(correct[b_idx]),
                              torch.as_tensor(lengths[b_idx]), kp_ids).numpy()
            base = sub[["stu_id", "course_code", "semester"]].reset_index(drop=True)
            for j, k in enumerate(kp_ids):
                rows.append(base.assign(kp_idx=int(k), mastery=m[:, j].astype("float32")))
    mastery = (pd.concat(rows, ignore_index=True)
               .merge(kp_dim[["kp_idx", "kp_id", "name"]], on="kp_idx", how="left"))
    mastery["mastery"] = mastery["mastery"].astype("float32")
    out = processed / "kp_mastery.parquet"
    mastery.to_parquet(out, index=False)
    print(f"[产物] {out}  {mastery.shape}  掌握度均值={mastery.mastery.mean():.3f}")

    stamp = date.today().strftime("%Y%m%d")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "n_items": n_items, "n_kps": n_kps,
                "max_seq_len": MAX_SEQ_LEN}, MODELS_DIR / f"model_dkt_{stamp}.pt")
    save_json({
        "model": "M1_DKT", "processed_dir": str(processed),
        "n_sequences": int(len(seq)), "n_items": n_items, "n_kps": n_kps,
        "split": {g: int((split == g).sum()) for g in ["train", "val", "test"]},
        "results": metrics,
        "caveat": "OULAD 无真实题目/知识点标注，kp_idx 为按考核截止日期的伪标签；"
                  "AUC 只证明流程正确，不能作为教学质量结论。",
    }, REPORTS_DIR / "dkt_metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
