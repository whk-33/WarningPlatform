"""M6 可解释性：边遮挡归因（delete-one occlusion，GNNExplainer 的确定性简化版）。

为什么不用边掩码学习：
HAN 输出在 logit 空间饱和（高风险学生 logit≈8.9，sigmoid 后 0.9999），
① 概率空间的 (p−p_base)² 在该区间梯度≈0；
② logit 空间的掩码用 sigmoid(p) 初始化后，接近 1 时导数极小。
实测两种写法 25 条边掩码全部收敛到 1，importance 恒为 0，不可用。
改为逐条边删除遮挡：importance = logit(全图) − logit(删掉这条边)，
与"删除 top-3 边"的验证口径完全一致，结果确定、可复现。

产物：
  processed_full_demo/subgraph/<stu_id>.json（前端 pyvis 直接渲染）
  reports/explain_metrics.json（删 top-3 边 vs 随机 3 边 的定量对照）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.han import HAN, build_graph_inputs, build_relation_tables, neighbors_of_students  # noqa: E402
from training.common import REPORTS_DIR, SEED, resolve_interim, resolve_processed, save_json, set_seed  # noqa: E402

STUDENT_REL_CN = {
    "rev_enrolled_in": ("所在课程", "course"),
    "rev_attended": ("上课环节", "class_session"),
    "rev_scored_on": ("考核的知识点", "knowledge_point"),
    "rev_interacts_with": ("使用过的资源", "resource"),
    "attended_with": ("同堂同学", "student"),
}


def load_model_and_graph(processed: Path):
    import glob

    ckpt_path = sorted(glob.glob(str(Path(__file__).resolve().parents[2] / "models" / "model_han_*.pt")))[-1]
    ckpt = torch.load(ckpt_path, weights_only=False)
    inputs = build_graph_inputs(processed)
    tables = build_relation_tables(inputs, k=ckpt["k"])
    feats = {t: torch.as_tensor((x - ckpt["stats"][t][0]) / ckpt["stats"][t][1], dtype=torch.float32)
             for t, x in inputs["feats"].items()}
    model = HAN(ckpt["in_dims"], hidden=ckpt["hidden"])
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, inputs, tables, feats


def node_label(t: str, node_id: str, label_ctx: dict) -> str:
    if t == "course":
        return label_ctx["course_name"].get(node_id, node_id)
    if t == "knowledge_point":
        return label_ctx["kp_name"].get(node_id, node_id)
    if t == "class_session":
        wk = int(str(node_id).split("wk")[-1])
        return f"第{wk + 1}周课次"
    if t == "resource":
        rid = str(node_id).replace("res_", "")
        return "资源（类型缺失）" if rid == "none" else f"资源 {rid}"
    if t == "student":
        return f"同堂同学 {node_id}"
    return node_id


def _sig(x: float) -> float:
    return float(1.0 / (1.0 + np.exp(-x)))


@torch.no_grad()
def logit_of(model, feats, sid, tables_t, nb, scale=None) -> float:
    return float(model(feats, np.array([sid]), tables_t, nb, edge_scale=scale)[0])


def _scale_for(rels: list[str], k: int, removed: list[tuple[str, int]]) -> dict:
    """把指定 (关系, 槽位) 的掩码置 0，其余保持 1。"""
    scale = {r: torch.ones(k) for r in rels}
    for r, slot in removed:
        scale[r][slot] = 0.0
    return scale


def explain(model, inputs, tables, feats, sid: int, top_k: int = 5,
            control_draws: int = 10, seed: int = SEED) -> dict:
    ids = inputs["ids"]
    tables_t = {k: torch.as_tensor(v) for k, v in tables.items()}
    nb = neighbors_of_students(np.array([sid]), tables, ids)
    k = tables["rev_enrolled_in"].shape[1]
    rels = [r for r in STUDENT_REL_CN if r in tables]

    base_logit = logit_of(model, feats, sid, tables_t, nb)
    base_prob = _sig(base_logit)

    # 逐边遮挡，importance = 风险 logit 的下降量（可正可负，负值表示删掉反而更危险）
    # 邻居表是"可重复采样"的，同一邻居可能占多个槽位；删除一条边要把它的所有槽位都置 0，
    # 否则边仍从别的槽位进来，importance 会被系统性低估。
    edges = []
    for r in rels:
        cn_name, tgt = STUDENT_REL_CN[r]
        seen_nbr = set()
        for slot in range(k):
            nbr = int(tables[r][sid, slot])
            if nbr < 0 or nbr in seen_nbr:
                continue
            seen_nbr.add(nbr)
            slots = [(r, s) for s in range(k) if int(tables[r][sid, s]) == nbr]
            after = logit_of(model, feats, sid, tables_t, nb, scale=_scale_for(rels, k, slots))
            edges.append({
                "source": ids["student"][sid],
                "target": ids[tgt][nbr],
                "relation": r,
                "relation_cn": cn_name,
                "target_type": tgt,
                "importance": round(base_logit - after, 4),
                "_slots": slots,
            })
    edges.sort(key=lambda e: -e["importance"])
    top = edges[:top_k]

    # 归一化权重（前端条形图/边颜色用）：除以最大正向重要性，保证 0–1
    max_imp = max((e["importance"] for e in edges), default=0.0)
    for e in edges:
        e["weight"] = round(max(e["importance"], 0.0) / max_imp, 4) if max_imp > 0 else 0.0

    # 删除验证：删掉 top-3 边 → 概率/logit 下降
    removed = [s for e in edges[:3] for s in e["_slots"]]
    after_top3 = logit_of(model, feats, sid, tables_t, nb, scale=_scale_for(rels, k, removed))

    # 随机对照：随机删 3 条有效边（不重复），重复 control_draws 次取均值
    rng = np.random.default_rng(seed + int(sid))
    valid = edges[3:]
    ctrl = []
    for _ in range(control_draws):
        if len(valid) < 3:
            break
        pick = rng.choice(len(valid), size=3, replace=False)
        slots = [s for i in pick for s in valid[i]["_slots"]]
        ctrl.append(base_logit - logit_of(
            model, feats, sid, tables_t, nb, scale=_scale_for(rels, k, slots)))

    for e in edges:
        e.pop("_slots", None)
    return {
        "stu_id": ids["student"][sid],
        "risk_prob": round(base_prob, 4),
        "top_edges": top,
        "prob_after_delete_top3": round(_sig(after_top3), 4),
        "drop": round(base_prob - _sig(after_top3), 4),
        "logit_drop": round(base_logit - after_top3, 4),
        "logit_drop_random3": round(float(np.mean(ctrl)), 4) if ctrl else None,
        "all_edges": edges,
    }


def faithfulness(model, inputs, tables, feats, sids: list[int], control_draws: int = 5) -> dict:
    """批量删除验证：top-3 边 vs 随机 3 边的风险 logit 下降对照。"""
    res = [explain(model, inputs, tables, feats, sid, control_draws=control_draws) for sid in sids]
    l_top = [r["logit_drop"] for r in res]
    l_rnd = [r["logit_drop_random3"] for r in res if r["logit_drop_random3"] is not None]
    p_top = [r["drop"] for r in res]
    return {
        "n_students": len(res),
        "avg_logit_drop_top3": round(float(np.mean(l_top)), 4),
        "avg_logit_drop_random3": round(float(np.mean(l_rnd)), 4) if l_rnd else None,
        "avg_prob_drop_top3": round(float(np.mean(p_top)), 4),
        "median_prob_drop_top3": round(float(np.median(p_top)), 4),
        "n_prob_drop_visible": int(sum(1 for x in p_top if x >= 0.01)),
        "students": [{"stu_id": r["stu_id"], "risk_prob": r["risk_prob"],
                      "logit_drop_top3": r["logit_drop"],
                      "logit_drop_random3": r["logit_drop_random3"]} for r in res],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", default=None)
    ap.add_argument("--interim", default=None)
    ap.add_argument("--top", type=int, default=10, help="解释风险最高的前 N 名学生")
    ap.add_argument("--low", type=int, default=2, help="额外解释低风险对照学生数")
    ap.add_argument("--faithful", type=int, default=100, help="批量删除验证抽取的测试学生数")
    args = ap.parse_args()
    set_seed(SEED)
    processed = resolve_processed(args.processed)
    interim = resolve_interim(args.interim)
    model, inputs, tables, feats = load_model_and_graph(processed)

    scores = pd.read_parquet(processed / "han_student_scores.parquet")
    test = scores[scores.split_group == "test"]
    targets = list(test.nlargest(args.top, "risk_prob").index) + list(
        test.nsmallest(args.low, "risk_prob").index)

    label_ctx = {"kp_name": inputs["kp_name"],
                 "course_name": dict(zip(pd.read_parquet(interim / "courses.parquet")["code"],
                                         pd.read_parquet(interim / "courses.parquet")["name"]))}
    out_dir = processed / "subgraph"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for sid in targets:
        res = explain(model, inputs, tables, feats, sid)
        node_list, seen = [], set()
        for e in res["all_edges"]:
            for node_id in (e["source"], e["target"]):
                if node_id in seen:
                    continue
                seen.add(node_id)
                t = "student" if node_id == res["stu_id"] else e["target_type"]
                node_list.append({"id": node_id, "type": t, "label": node_label(t, node_id, label_ctx)})
        doc = {"stu_id": res["stu_id"], "risk_prob": res["risk_prob"],
               "risk_level": 2 if res["risk_prob"] >= 0.7 else (1 if res["risk_prob"] >= 0.35 else 0),
               "nodes": node_list,
               "edges": [{"source": e["source"], "target": e["target"], "type": e["relation"],
                          "type_cn": e["relation_cn"], "weight": e["weight"],
                          "importance": e["importance"]}
                         for e in res["all_edges"]],
               "top_edges": [{k: v for k, v in e.items() if k != "target_type"} for e in res["top_edges"]],
               "prob_after_delete_top3": res["prob_after_delete_top3"],
               "logit_drop": res["logit_drop"],
               "logit_drop_random3": res["logit_drop_random3"],
               "drop": res["drop"]}
        save_json(doc, out_dir / f"{res['stu_id']}.json")
        summary.append({"stu_id": res["stu_id"], "risk_prob": res["risk_prob"],
                        "drop_when_removing_top3": res["drop"],
                        "logit_drop_when_removing_top3": res["logit_drop"],
                        "logit_drop_random3": res["logit_drop_random3"],
                        "top_edges": [(e["relation_cn"], e["target"], e["importance"])
                                      for e in res["top_edges"]]})
        print(f"[解释] {res['stu_id']}  risk={res['risk_prob']:.4f}  "
              f"删 top3 边后={res['prob_after_delete_top3']:.4f}  logit 下降={res['logit_drop']:+.3f}"
              f"（随机 3 边={res['logit_drop_random3'] or 0:+.3f}）  "
              f"关键边=" + "、".join(f"{e['relation_cn']}→{e['target']}({e['importance']:+.2f})"
                                     for e in res["top_edges"][:3]))

    # 批量删除验证：默认抽 100 名中高风险测试学生，对照随机 3 边
    pool = list(test[test.risk_prob > 0.5].index)
    rng = np.random.default_rng(SEED)
    sids = list(rng.choice(pool, size=min(args.faithful, len(pool)), replace=False)) if pool else []
    faith = faithfulness(model, inputs, tables, feats, sids) if sids else {}

    metrics = {
        "method": "边遮挡归因（delete-one occlusion）",
        "why_not_mask": "HAN 输出 logit 饱和（高风险学生 sigmoid≈0.9999），概率空间与掩码空间的梯度均失效，实测边掩码全部收敛到 1；改用确定性遮挡后 importance 与删除验证口径一致。",
        "n_explained": len(summary),
        "avg_drop_high_risk": round(float(np.mean([s["drop_when_removing_top3"] for s in summary
                                                   if s["risk_prob"] > 0.5])), 4) if summary else None,
        "faithfulness": faith,
        "students": summary,
    }
    save_json(metrics, REPORTS_DIR / "explain_metrics.json")
    print(f"[产物] {out_dir}  {len(summary)} 个学生子图")
    if faith:
        print(f"[验证] {faith['n_students']} 名学生删 top-3 边平均 logit 下降 "
              f"{faith['avg_logit_drop_top3']:+.3f}，随机删 3 边平均 {faith['avg_logit_drop_random3']:+.3f}")
        print(f"[验证] 概率口径平均下降 {faith['avg_prob_drop_top3']:.4f}"
              f"（其中 {faith['n_prob_drop_visible']}/{faith['n_students']} 名学生下降≥0.01）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
