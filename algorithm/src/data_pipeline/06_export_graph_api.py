"""
T2-导出 图 JSON（供后端/前端消费）
用法：
  python algorithm/src/data_pipeline/06_export_graph_api.py \
      --processed algorithm/data/oulad/processed_full_demo \
      --interim   algorithm/data/oulad/interim_demo

产出两个文件（都在 <processed> 目录下）：
  graph_api.json  只含 student / course 两类节点及其之间的边 → 后端 /admin/gnn/graph 直接读
  graph_min.json  5 类节点 + 9 类边的精简版（去掉全部特征，只留 id/type/label）→ 阶段二图模型调试

为什么单独拆一个脚本：
  05_build_graph.py 产出的 graph_nx.json 带全部节点特征，真实数据下 114MB，
  后端每次启动解析会拖慢并占用数百 MB 内存；而它实际只用到 student/course 节点。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

WEEK_RE = re.compile(r"wk(\d+)")


def _load_names(processed: Path, interim: Path) -> dict[str, str]:
    """node_id → 中文 label 的映射（拿不到就用 node_id）"""
    names: dict[str, str] = {}

    # 课程：crs_AAA → 计算机导论
    for f in (interim / "courses.parquet", processed / "courses.parquet"):
        if f.exists():
            c = pd.read_parquet(f)
            if {"code", "name"}.issubset(c.columns):
                names.update({f"crs_{r.code}": str(r.name) for r in c.itertuples()})
            break

    # 学生：stu_123 → 郑静怡（只在演示增强层有）
    f = interim / "student_info.parquet"
    if f.exists():
        si = pd.read_parquet(f).drop_duplicates("stu_id")
        if "student_name" in si.columns:
            names.update(dict(zip(si["stu_id"], si["student_name"].fillna(""))))

    # 知识点：kp_AAA_01 → 变量与数据类型
    for f in (processed / "dim_knowledge_point.parquet", interim / "kp_name_map.parquet"):
        if f.exists():
            kp = pd.read_parquet(f)
            if "kp_id" in kp.columns and "name" in kp.columns:
                names.update(dict(zip(kp["kp_id"].astype(str), kp["name"].astype(str))))
            break
    return names


def export(processed: Path, interim: Path) -> None:
    graph_dir = processed / "graph"
    names = _load_names(processed, interim)

    nodes: list[dict] = []
    for f in sorted(graph_dir.glob("dim_*.parquet")):
        df = pd.read_parquet(f, columns=["node_id", "node_type"])
        for r in df.itertuples():
            nid = str(r.node_id)
            label = names.get(nid)
            if not label and r.node_type == "class_session":
                m = WEEK_RE.search(nid)
                course_label = names.get(f"crs_{nid.split('_')[1]}", nid.split("_")[1])
                label = f"{course_label}·第{int(m.group(1)) + 1}周" if m else nid
            nodes.append({"id": nid, "type": str(r.node_type), "label": label or nid})

    edges: list[dict] = []
    for f in sorted(graph_dir.glob("edge_*.parquet")):
        try:
            sub = pd.read_parquet(f, columns=["src", "dst"])
        except Exception:
            continue
        et = f.stem.replace("edge_", "")
        edges.extend({"source": str(a), "target": str(b), "type": et}
                     for a, b in zip(sub["src"], sub["dst"]))

    # 全量精简版（无特征）
    with open(processed / "graph_min.json", "w", encoding="utf-8") as fp:
        json.dump({"nodes": nodes, "edges": edges}, fp, ensure_ascii=False)

    # 后端接口版：只留 student / course
    keep_types = {"student", "course"}
    keep_ids = {n["id"] for n in nodes if n["type"] in keep_types}
    api_nodes = [n for n in nodes if n["id"] in keep_ids]
    api_edges = [e for e in edges
                 if e["source"] in keep_ids and e["target"] in keep_ids]
    with open(processed / "graph_api.json", "w", encoding="utf-8") as fp:
        json.dump({"nodes": api_nodes, "edges": api_edges}, fp, ensure_ascii=False)

    size_min = (processed / "graph_min.json").stat().st_size / 1e6
    size_api = (processed / "graph_api.json").stat().st_size / 1e6
    print(f"  graph_min.json  nodes={len(nodes):,} edges={len(edges):,} {size_min:.1f}MB")
    print(f"  graph_api.json  nodes={len(api_nodes):,} edges={len(api_edges):,} {size_api:.1f}MB")


def main() -> None:
    ap = argparse.ArgumentParser(description="导出图 JSON（后端接口版 + 精简版）")
    ap.add_argument("--processed", default="algorithm/data/oulad/processed_full_demo",
                    help="图产物目录（含 graph/*.parquet）")
    ap.add_argument("--interim", default="algorithm/data/oulad/interim_demo",
                    help="interim 目录（取中文课程名/学生名）")
    args = ap.parse_args()
    export(Path(args.processed), Path(args.interim))


if __name__ == "__main__":
    main()
