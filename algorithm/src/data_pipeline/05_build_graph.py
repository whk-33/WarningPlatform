"""
T2 异质图构建脚本 · 学业预警平台
用法：python algorithm/src/data_pipeline/05_build_graph.py

三种输出格式：
  1) algorithm/data/processed/graph/*.csv      边列表 CSV（后端 ETL 用）
  2) algorithm/data/processed/graph_nx.json    NetworkX node_link（阶段三 GNNExplainer 用）
  3) algorithm/data/processed/hetero_data.pt   PyG HeteroData（阶段二 HAN 用，try-import 保护）

节点：student / course / class_session / knowledge_point / resource
边：enrolled_in / attended / attended_with / scored_on / interacts_with /
    has_kt / prereq_of / supports / course_prereq
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import networkx as nx

INTERIM = Path("algorithm/data/interim")
OUT = Path("algorithm/data/processed")
GRAPH_OUT = OUT / "graph"
GRAPH_OUT.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 节点表构建（5 类，带特征向量化）
# ═══════════════════════════════════════════════════════════════════════════════

def _vectorize_student(info: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """学生节点：gender(one-hot) + major(one-hot) + entrance_score(norm) + past_retake + special_flag"""
    df = info.copy()
    df["node_id"] = df["stu_id"]
    df["node_type"] = "student"
    df = df.drop_duplicates(subset=["node_id"])

    # 特征
    feat_parts = []
    # gender one-hot
    feat_parts.append(pd.get_dummies(df["gender"], dtype=float))
    # major one-hot
    feat_parts.append(pd.get_dummies(df["major"], dtype=float))
    # entrance_score 归一化
    s = df["entrance_score"].astype(float)
    feat_parts.append(((s - s.mean()) / s.std()).to_frame("entrance_score_norm"))
    # past_retake_count
    feat_parts.append(df[["past_retake_count"]].astype(float).reset_index(drop=True))
    # special_flag → int
    feat_parts.append(df[["special_flag"]].astype(int).reset_index(drop=True))

    feat_vec = pd.concat(feat_parts, axis=1).fillna(0).to_numpy(dtype=np.float32)
    return df[["node_id", "node_type"]].reset_index(drop=True), feat_vec


def _vectorize_course(courses: pd.DataFrame,
                      prereq: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    df = courses.copy()
    df["node_id"] = "crs_" + df["code"]
    df["node_type"] = "course"
    df = df.rename(columns={"code": "code_raw"})

    # 特征：credits(标准化) + is_elective(int) + has_prereq(int)
    feat = pd.DataFrame(index=df.index)
    s = df["credits"].astype(float)
    feat["credits_norm"] = (s - s.mean()) / s.std()
    feat["is_elective"] = df["elective"].astype(int)
    feat["has_prereq"] = df["code_raw"].isin(prereq["course_code"]).astype(int)
    return df[["node_id", "node_type", "code_raw"]].reset_index(drop=True), \
           feat.to_numpy(dtype=np.float32)


def _build_class_session_nodes(log: pd.DataFrame,
                                calendar: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """从 platform_log 的 class_session_id 去重，附加日期/周次"""
    cs = log.groupby("class_session_id", observed=True).agg(
        course_code=("course_code", "first"),
        semester=("semester", "first"),
        first_ts=("timestamp", "first"),
    ).reset_index()
    cs["node_id"] = cs["class_session_id"]
    cs["node_type"] = "class_session"
    # 用 first_ts 的 dayofweek 作简化特征
    cs["week"] = cs["first_ts"].dt.dayofweek.astype(int)
    feat = cs[["week"]].astype(float).to_numpy(dtype=np.float32)
    return cs[["node_id", "node_type", "course_code", "semester", "first_ts"]].reset_index(drop=True), feat


def _vectorize_knowledge_point(df_kps: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    df = df_kps.copy()
    df["node_id"] = df["kp_id"]
    df["node_type"] = "knowledge_point"
    feat = df[["difficulty_proxy", "suggested_week"]].astype(float).to_numpy(dtype=np.float32)
    return df[["node_id", "node_type", "course_code"]].reset_index(drop=True), feat


def _build_resource_nodes(log: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    res = log.dropna(subset=["resource_id"])[
        ["resource_id", "resource_type", "course_code"]
    ].drop_duplicates("resource_id").copy()
    res["node_id"] = res["resource_id"]
    res["node_type"] = "resource"
    # 特征：resource_type one-hot
    feat = pd.get_dummies(res["resource_type"], dtype=float).fillna(0).to_numpy(dtype=np.float32)
    return res[["node_id", "node_type", "resource_type", "course_code"]].reset_index(drop=True), feat


def build_all_nodes() -> dict[str, tuple[pd.DataFrame, np.ndarray]]:
    """构建 5 类节点 + 特征，返回 {node_type: (dim_df, feat_matrix)}"""
    info   = pd.read_parquet(INTERIM / "student_info.parquet")
    courses= pd.read_parquet(INTERIM / "courses.parquet")
    prereq = pd.read_parquet(INTERIM / "course_prereq.parquet")
    log    = pd.read_parquet(INTERIM / "platform_log.parquet")
    cal    = pd.read_parquet(INTERIM / "calendar.parquet")
    kps    = pd.read_parquet(OUT / "dim_knowledge_point.parquet")

    return {
        "student":        _vectorize_student(info),
        "course":         _vectorize_course(courses, prereq),
        "class_session":  _build_class_session_nodes(log, cal),
        "knowledge_point":_vectorize_knowledge_point(kps),
        "resource":       _build_resource_nodes(log),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 边表构建（9 类，全部 src/dst 用 node_id）
# ═══════════════════════════════════════════════════════════════════════════════

def _enrolled_in(info: pd.DataFrame,
                 course_dim: pd.DataFrame) -> pd.DataFrame:
    """(student, enrolled_in, course)：按 stu_id + course_code join"""
    # info 里没有 course_code —— 用 registration
    reg = pd.read_parquet(INTERIM / "registration.parquet")
    # reg.course_code → 统一 course 节点 ID
    reg = reg.merge(course_dim[["code_raw", "node_id"]],
                    left_on="course_code", right_on="code_raw", how="left")
    reg = reg.rename(columns={"node_id": "dst", "stu_id": "src"})
    edges = reg[["src", "dst"]].dropna().drop_duplicates()
    edges["edge_type"] = "enrolled_in"
    return edges.reset_index(drop=True)


def _attended(log: pd.DataFrame) -> pd.DataFrame:
    """(student, attended, class_session)"""
    e = log[["stu_id", "class_session_id", "attendance_status", "late_minutes"]].copy()
    e = e.rename(columns={"stu_id": "src", "class_session_id": "dst"})
    e = e.dropna(subset=["src", "dst"]).drop_duplicates(["src", "dst"])
    e["edge_type"] = "attended"
    return e[["src", "dst", "edge_type", "attendance_status", "late_minutes"]].reset_index(drop=True)


def _attended_with(log: pd.DataFrame,
                   max_class_size: int = 40,
                   sample_per_class: int = 8) -> pd.DataFrame:
    """同班同侪：同一 class_session 的学生两两连边
    O(n²) 风险 → 按 class_session 分组，班级 > max_class_size 时做随机采样
    """
    pairs = []
    grouped = log.dropna(subset=["stu_id", "class_session_id"]).groupby(
        "class_session_id", observed=True)

    for cs_id, grp in grouped:
        stus = grp["stu_id"].dropna().unique()
        n = len(stus)
        if n < 2:
            continue
        if n > max_class_size:
            stus = np.random.choice(stus, size=min(sample_per_class, n), replace=False)
            n = len(stus)
        # 两两配对（无向边拆成双向有向）
        for i in range(n):
            for j in range(i + 1, n):
                pairs.append({"src": stus[i], "dst": stus[j], "edge_type": "attended_with"})
                pairs.append({"src": stus[j], "dst": stus[i], "edge_type": "attended_with"})

    return pd.DataFrame(pairs).drop_duplicates().reset_index(drop=True)


def _scored_on(asm_stu: pd.DataFrame,
               quiz: pd.DataFrame,
               kps: pd.DataFrame,
               info: pd.DataFrame) -> pd.DataFrame:
    """(student, scored_on, knowledge_point)
    优先用 quiz_item 逐题数据，考核成绩作辅助
    """
    kp_lookup = kps.set_index("kp_id")["course_code"].to_dict()

    # quiz 有 kp_id → 逐题建边
    quiz_q = quiz[quiz["kp_id"].notna() & quiz["stu_id"].notna()].copy()
    quiz_q["src"] = quiz_q["stu_id"]
    quiz_q["dst"] = quiz_q["kp_id"]
    quiz_q["score_norm"] = quiz_q["score"] / quiz_q["max_score"].clip(lower=1)
    quiz_q["edge_type"] = "scored_on"
    edges = quiz_q[["src", "dst", "edge_type", "score_norm"]].drop_duplicates()

    return edges.reset_index(drop=True)


def _interacts_with(log: pd.DataFrame) -> pd.DataFrame:
    """(student, interacts_with, resource)"""
    e = log[["stu_id", "resource_id", "resource_type"]].copy()
    e = e.rename(columns={"stu_id": "src", "resource_id": "dst"})
    e = e.dropna().drop_duplicates(["src", "dst"])
    e["edge_type"] = "interacts_with"
    return e[["src", "dst", "edge_type"]].reset_index(drop=True)


def _course_has_kt(course_dim: pd.DataFrame,
                   kps: pd.DataFrame,
                   has_kt_parquet: Path) -> pd.DataFrame:
    """(course, has_kt, knowledge_point) 边——从 T4 轻量版产出
    kps.kp_id 本身就是 node_id，不需要额外映射
    """
    if has_kt_parquet.exists():
        e = pd.read_parquet(has_kt_parquet)
        # src 是 course_code → 统一 course 节点 ID
        e = e.merge(course_dim[["code_raw", "node_id"]],
                    left_on="src", right_on="code_raw", how="left")
        e["src"] = e["node_id"]
        # dst 已经是 kp_id，就是 knowledge_point 的 node_id
        e["dst"] = e["dst"]
        e = e.dropna(subset=["src", "dst"])
        e["edge_type"] = "has_kt"
        return e[["src", "dst", "edge_type"]].drop_duplicates().reset_index(drop=True)
    return pd.DataFrame(columns=["src", "dst", "edge_type"])


def _kp_prereq(kps: pd.DataFrame,
               prereq_parquet: Path) -> pd.DataFrame:
    """(kp, prereq_of, kp) 边——从 T4 产出"""
    if prereq_parquet.exists():
        e = pd.read_parquet(prereq_parquet)
        e["edge_type"] = "prereq_of"
        return e[["src", "dst", "edge_type", "source", "strength"]].drop_duplicates().reset_index(drop=True)
    return pd.DataFrame(columns=["src", "dst", "edge_type"])


def _supports(resource_dim: pd.DataFrame,
              kps: pd.DataFrame) -> pd.DataFrame:
    """(resource, supports, kp)：按 course_code 关联
    kp_id 本身就是 node_id
    """
    rows = []
    for course, sub_res in resource_dim.groupby("course_code"):
        sub_kp = kps[kps["course_code"] == course].reset_index(drop=True)
        if sub_kp.empty or sub_res.empty:
            continue
        for _, r in sub_res.iterrows():
            n_tgt = min(3, len(sub_kp))
            target_idx = np.random.choice(len(sub_kp), size=n_tgt, replace=False)
            for idx in target_idx:
                rows.append({"src": r["node_id"],
                             "dst": sub_kp.at[idx, "kp_id"],  # kp_id = node_id
                             "edge_type": "supports"})
    return pd.DataFrame(rows).drop_duplicates().reset_index(drop=True)


def _course_prereq(course_dim: pd.DataFrame) -> pd.DataFrame:
    """(course, prereq, course)：用 course_prereq.parquet 的先修关系"""
    prereq = pd.read_parquet(INTERIM / "course_prereq.parquet")
    m = course_dim.set_index("code_raw")["node_id"].to_dict()
    edges = pd.DataFrame({
        "src": prereq["prereq_course_code"].map(m),
        "dst": prereq["course_code"].map(m),
        "edge_type": "course_prereq",
    }).dropna()
    return edges.reset_index(drop=True)


def build_all_edges(nodes: dict) -> dict[str, pd.DataFrame]:
    info   = pd.read_parquet(INTERIM / "student_info.parquet")
    log    = pd.read_parquet(INTERIM / "platform_log.parquet")
    asm    = pd.read_parquet(INTERIM / "student_assessment.parquet")
    quiz   = pd.read_parquet(INTERIM / "quiz_item.parquet")
    kps    = pd.read_parquet(OUT / "dim_knowledge_point.parquet")
    course_dim    = nodes["course"][0]
    resource_dim  = nodes["resource"][0]

    edges = {}
    edges["enrolled_in"]   = _enrolled_in(info, course_dim)
    edges["attended"]      = _attended(log)
    edges["attended_with"] = _attended_with(log)
    edges["scored_on"]     = _scored_on(asm, quiz, kps, info)
    edges["interacts_with"]= _interacts_with(log)
    edges["has_kt"]        = _course_has_kt(course_dim, kps, OUT / "edge_course_has_kt.parquet")
    edges["prereq_of"]     = _kp_prereq(kps, OUT / "edge_kp_prereq.parquet")
    edges["supports"]      = _supports(resource_dim, kps)
    edges["course_prereq"] = _course_prereq(course_dim)

    for et, df in edges.items():
        print(f"  {et:18s} edges: {len(df):>7,}")
    return edges


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 三种格式输出
# ═══════════════════════════════════════════════════════════════════════════════

def _ensure_node_id_type(nodes, edges):
    """防止 node_id 和 edge src/dst 类型不匹配（str vs object）"""
    for ntype, (dim_df, feat) in nodes.items():
        dim_df["node_id"] = dim_df["node_id"].astype(str)
    for et, df in edges.items():
        if df.empty:
            continue
        df["src"] = df["src"].astype(str)
        df["dst"] = df["dst"].astype(str)


def export_csv(nodes: dict, edges: dict):
    for ntype, (dim_df, _) in nodes.items():
        dim_df.to_parquet(GRAPH_OUT / f"dim_{ntype}.parquet", index=False)
    for et, df in edges.items():
        df.to_parquet(GRAPH_OUT / f"edge_{et}.parquet", index=False)
    print(f"  CSV edge list → {GRAPH_OUT}")


def export_networkx(nodes: dict, edges: dict) -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    # 加节点（node_type 保留在 attr 里用于 validate 时按类型过滤）
    for ntype, (dim_df, feat) in nodes.items():
        for _, r in dim_df.iterrows():
            attrs = {k: v for k, v in r.items()
                     if k != "node_id"
                     and not isinstance(v, (pd.Timestamp, np.ndarray))
                     and pd.notna(v)}
            # 强制加上 node_type（即使 dim_df 里没这列）
            attrs.setdefault("node_type", ntype)
            G.add_node(r["node_id"], **attrs)
    # 加边
    for et, df in edges.items():
        if df.empty:
            continue
        for _, r in df.iterrows():
            G.add_edge(r["src"], r["dst"], edge_type=et)
    # NetworkX 3.x API 改到 nx.json_graph.node_link
    data = nx.node_link_data(G)
    with open(OUT / "graph_nx.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"  NetworkX graph: nodes={G.number_of_nodes():,}, edges={G.number_of_edges():,}")
    return G


def export_pyg(nodes: dict, edges: dict) -> None:
    """PyG HeteroData：try-import 保护，没装 torch 就跳过"""
    try:
        import torch
        from torch_geometric.data import HeteroData
    except ImportError as e:
        print(f"  ⚠️  torch/torch_geometric 未安装，跳过 HeteroData 导出: {e}")
        return

    data = HeteroData()
    node_map: dict[str, dict[str, int]] = {}  # ntype → {node_id → int idx}

    # 节点 → 特征矩阵 + 索引映射
    for ntype, (dim_df, feat) in nodes.items():
        node_map[ntype] = {nid: i for i, nid in enumerate(dim_df["node_id"])}
        data[ntype].x = torch.tensor(feat, dtype=torch.float)
        data[ntype].node_ids = list(dim_df["node_id"])  # 方便调试

    def _edge_index(edge_df, src_type, dst_type):
        if edge_df.empty:
            return None
        src = edge_df["src"].map(node_map[src_type]).to_numpy()
        dst = edge_df["dst"].map(node_map[dst_type]).to_numpy()
        mask = np.isfinite(src) & np.isfinite(dst)
        src, dst = src[mask].astype(np.int64), dst[mask].astype(np.int64)
        return torch.tensor([src, dst], dtype=torch.long)

    # 边类型 → (src_type, edge_key, dst_type) 映射
    triple_map = {
        "enrolled_in":   ("student",        "enrolled_in",    "course"),
        "attended":      ("student",        "attended",       "class_session"),
        "attended_with": ("student",        "attended_with",  "student"),
        "scored_on":     ("student",        "scored_on",      "knowledge_point"),
        "interacts_with":("student",        "interacts_with", "resource"),
        "has_kt":        ("course",         "has_kt",         "knowledge_point"),
        "prereq_of":     ("knowledge_point","prereq_of",      "knowledge_point"),
        "supports":      ("resource",       "supports",       "knowledge_point"),
        "course_prereq": ("course",         "prereq",         "course"),
    }
    for et, df in edges.items():
        src_t, _, dst_t = triple_map[et]
        ei = _edge_index(df, src_t, dst_t)
        if ei is not None and ei.shape[1] > 0:
            data[(src_t, et, dst_t)].edge_index = ei

    torch.save(data, OUT / "hetero_data.pt")
    print(f"  PyG HeteroData saved → {OUT / 'hetero_data.pt'}")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 图校验
# ═══════════════════════════════════════════════════════════════════════════════

def validate(G: nx.MultiDiGraph) -> dict:
    report = {}
    # DAG 校验：知识依赖 + 课程先修 必须无环
    kp_sub   = G.subgraph([n for n, d in G.nodes(data=True) if d["node_type"] == "knowledge_point"])
    course_sub = G.subgraph([n for n, d in G.nodes(data=True) if d["node_type"] == "course"])
    kp_dag = nx.DiGraph(kp_sub)
    course_dag = nx.DiGraph(course_sub)
    report["kp_dag_acyclic"] = nx.is_directed_acyclic_graph(kp_dag)
    report["course_dag_acyclic"] = nx.is_directed_acyclic_graph(course_dag)

    # 悬挂边校验（边两端节点都必须存在）—— 构造时已保证，此处统计
    dangling_edges = 0
    all_nodes = set(G.nodes())
    for u, v in G.edges():
        if u not in all_nodes or v not in all_nodes:
            dangling_edges += 1
    report["dangling_edges"] = dangling_edges

    # 孤立节点
    isolated = [n for n in G.nodes() if G.degree(n) == 0]
    report["isolated_nodes"] = len(isolated)
    report["total_nodes"] = G.number_of_nodes()
    report["total_edges"] = G.number_of_edges()

    for k, v in report.items():
        print(f"  ✅ {k}: {v}")

    assert report["kp_dag_acyclic"], "❌ 知识点依赖子图存在环！"
    assert report["course_dag_acyclic"], "❌ 课程先修子图存在环！"
    assert dangling_edges == 0, f"❌ 存在 {dangling_edges} 条悬挂边！"
    return report


# ═══════════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("── Building nodes ──")
    nodes = build_all_nodes()
    for ntype, (dim_df, feat) in nodes.items():
        print(f"  {ntype:18s} nodes: {len(dim_df):>7,}  feat_dim={feat.shape[1]}")

    print("\n── Building edges ──")
    edges = build_all_edges(nodes)

    print("\n── Type alignment ──")
    _ensure_node_id_type(nodes, edges)

    print("\n── Exporting formats ──")
    export_csv(nodes, edges)
    G = export_networkx(nodes, edges)
    export_pyg(nodes, edges)

    print("\n── Validating ──")
    validate(G)

    print("\n✅ T2 异质图构建完成 → algorithm/data/processed/")


if __name__ == "__main__":
    main()
