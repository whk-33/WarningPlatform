"""M4 HAN 训练：学生级风险分类 + 注意力导出。

标签：学生是"任意一门课为风险正例"即判为正（node 级任务，与课程级标签不同，报告需注明）。
切分：学生级 split_group（不能按边切分，否则同一学生会同时出现在训练/评估集）。
产物：model_han_<日期>.pt、han_student_scores.parquet、han_metrics.json
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

from models.han import (  # noqa: E402
    HAN, build_graph_inputs, build_relation_tables, neighbors_of_students,
)
from training.common import (  # noqa: E402
    MODELS_DIR, REPORTS_DIR, SEED, evaluate, resolve_processed, save_json, set_seed,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", default=None)
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--neighbors", type=int, default=5)
    ap.add_argument("--hidden", type=int, default=64)
    args = ap.parse_args()

    set_seed(SEED)
    processed = resolve_processed(args.processed)
    inputs = build_graph_inputs(processed)
    tables = build_relation_tables(inputs, k=args.neighbors)
    print(f"[图] 节点=" + "  ".join(f"{t}:{len(ids)}" for t, ids in inputs["ids"].items()))
    print(f"[图] 关系=" + "  ".join(f"{r}:{t.shape[0]}×{t.shape[1]}" for r, t in sorted(tables.items())))
    print(f"[标签] 学生={len(inputs['labels'])}  正例率={inputs['labels'].mean():.3f}  "
          f"切分={ {g: int((inputs['split'] == g).sum()) for g in ['train','val','test']} }")

    # 节点特征标准化（无监督统计量，不涉及标签）
    feats = {}
    stats = {}
    for t, x in inputs["feats"].items():
        m, s = x.mean(0), np.where(x.std(0) < 1e-8, 1.0, x.std(0))
        feats[t] = torch.as_tensor((x - m) / s, dtype=torch.float32)
        stats[t] = (m, s)
    tables_t = {k: torch.as_tensor(v) for k, v in tables.items()}

    model = HAN({t: x.shape[1] for t, x in inputs["feats"].items()}, hidden=args.hidden)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-2)
    loss_fn = nn.BCEWithLogitsLoss()
    y_all = torch.as_tensor(inputs["labels"])
    split = inputs["split"]

    @torch.no_grad()
    def predict(idx: np.ndarray) -> np.ndarray:
        model.eval()
        out = []
        for s in range(0, len(idx), 2048):
            b = idx[s:s + 2048]
            nb = neighbors_of_students(b, tables, inputs["ids"])
            logit = model(feats, b, tables_t, nb)
            out.append(torch.sigmoid(logit).numpy())
        return np.concatenate(out)

    tr = np.where(split == "train")[0]
    va = np.where(split == "val")[0]
    te = np.where(split == "test")[0]
    best_auc, best_state, bad = -1.0, None, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        perm = np.random.permutation(tr)
        total = 0.0
        for s in range(0, len(perm), args.batch):
            b = perm[s:s + args.batch]
            nb = neighbors_of_students(b, tables, inputs["ids"])
            opt.zero_grad()
            logit = model(feats, b, tables_t, nb)
            loss = loss_fn(logit, y_all[b])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += float(loss.detach()) * len(b)
        p_va = predict(va)
        auc = evaluate(inputs["labels"][va], p_va)["auc"]
        flag = " *" if auc > best_auc else ""
        print(f"  epoch {epoch:02d}  loss={total / len(perm):.4f}  val_AUC={auc:.4f}{flag}")
        if auc > best_auc:
            best_auc, bad = auc, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= 3:
                print("  early stop")
                break

    model.load_state_dict(best_state)
    results = {}
    for g, idx in [("val", va), ("test", te)]:
        p = predict(idx)
        results[g] = evaluate(inputs["labels"][idx], p)
        print(f"[{g}] AUC={results[g]['auc']:.4f}  PR-AUC={results[g]['pr_auc']:.4f}  "
              f"Recall@10%={results[g]['recall_at_10pct']:.4f}")

    prob = predict(np.arange(len(inputs["labels"])))
    # 与 M0 基线可比的子集：剔除"第 8 周前就已出事"的学生（基线在第 8 周快照里也剔除了他们）
    ev = inputs["event"]
    comp = (ev == -1) | (ev > 8)
    idx_c = te[comp[te]]
    results["test_comparable_to_baseline"] = evaluate(inputs["labels"][idx_c], prob[idx_c])
    print(f"[test·可比子集] AUC={results['test_comparable_to_baseline']['auc']:.4f}  "
          f"n={results['test_comparable_to_baseline']['n']}  "
          f"正例率={results['test_comparable_to_baseline']['pos_rate']:.3f}")

    scores = pd.DataFrame({"stu_id": inputs["ids"]["student"], "risk_prob": prob.astype("float32"),
                           "is_positive": inputs["labels"].astype("int8"), "min_event": ev,
                           "split_group": split})
    scores.to_parquet(processed / "han_student_scores.parquet", index=False)
    print(f"[产物] {processed / 'han_student_scores.parquet'}  {scores.shape}")

    stamp = date.today().strftime("%Y%m%d")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "hidden": args.hidden,
                "in_dims": {t: x.shape[1] for t, x in inputs["feats"].items()},
                "k": args.neighbors, "stats": stats}, MODELS_DIR / f"model_han_{stamp}.pt")
    save_json({
        "model": "M4_HAN", "processed_dir": str(processed),
        "node_counts": {t: int(len(v)) for t, v in inputs["ids"].items()},
        "relations": {r: [int(t.shape[0]), args.neighbors] for r, t in tables.items()},
        "hidden": args.hidden, "neighbors_per_relation": args.neighbors,
        "results": results,
        "note": "学生级标签=任意课程为风险正例；学生行为特征只汇总 week<=8 的观测，避免未来泄漏；"
                "图边覆盖整学期（interacts_with/scored_on 无时间戳，无法按周过滤），"
                "因此 HAN 的 AUC 应读作图关联强度，`test_comparable_to_baseline` 才是与基线的对照；"
                "course_prereq 边为空，实际使用 8 类原始边 + 反向边。",
    }, REPORTS_DIR / "han_metrics.json")
    print(f"[产物] {REPORTS_DIR / 'han_metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
