"""
阶段一全链路验收测试 · pytest
覆盖 T1 清洗 + T2 异质图 + T3 时序特征 + T4 DKT 序列
运行：python -m pytest tests/ -v
"""
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pytest

INTERIM = Path("algorithm/data/interim")
RAW = Path("algorithm/data/raw")
PROCESSED = Path("algorithm/data/processed")

# ═════════════════════════════════════════════════════════════════════════════
# T1 清洗层（原有 10 条，这里新增 2 条）
# ═════════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def tables():
    return {p.stem: pd.read_parquet(p)
            for p in INTERIM.glob("*.parquet")}


def test_t1_time_unified(tables):
    """清洗后 timestamp 列都是 datetime（不是 string/Unix ms）"""
    log = tables["platform_log"]
    assert pd.api.types.is_datetime64_any_dtype(log["timestamp"]), \
        "platform_log.timestamp 不是 datetime"


def test_t1_late_truncated(tables):
    """迟到分钟已截断到合理范围"""
    log = tables["platform_log"]
    assert log["late_minutes"].max() <= 60, \
        f"late_minutes 最大 {log['late_minutes'].max()} > 60"


# ═════════════════════════════════════════════════════════════════════════════
# T2 异质图
# ═════════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def graph_files():
    gdir = PROCESSED / "graph"
    dims = {p.stem.replace("dim_", ""): pd.read_parquet(p)
            for p in gdir.glob("dim_*.parquet")}
    edges = {p.stem.replace("edge_", ""): pd.read_parquet(p)
             for p in gdir.glob("edge_*.parquet")}
    return dims, edges


def test_t2_node_types_complete(graph_files):
    """5 类节点都存在"""
    dims, _ = graph_files
    for nt in ["student", "course", "class_session",
               "knowledge_point", "resource"]:
        assert nt in dims, f"缺少 dim_{nt}.parquet"
        assert len(dims[nt]) > 0, f"dim_{nt} 为空"


def test_t2_edge_types_complete(graph_files):
    """核心 4 类业务边存在"""
    _, edges = graph_files
    for et in ["enrolled_in", "attended", "attended_with", "scored_on"]:
        assert et in edges, f"缺少 edge_{et}.parquet"


def test_t2_kp_dag_acyclic(graph_files):
    """知识点依赖链 + 课程先修链合起来是 DAG"""
    _, edges = graph_files
    G = nx.DiGraph()
    # kp 节点列名是 node_id
    kp_df = graph_files[0].get("knowledge_point")
    if kp_df is None:
        pytest.skip("没有 knowledge_point 节点表")
    kp_col = "node_id"
    kp_nodes = kp_df[kp_col].tolist()
    G.add_nodes_from(kp_nodes)

    # 直接用 graph pipeline 的 edge_prereq_of（src→dst）
    if "prereq_of" in edges:
        G.add_edges_from(zip(edges["prereq_of"]["src"],
                              edges["prereq_of"]["dst"]))

    # course_prereq 边扩展：先修课的 kp → 后修课的 kp
    # src/dst 是图 node_id 格式如 crs_AAA，需要映射回 course_code=AAA
    if "course_prereq" in edges:
        crs_prereq = edges["course_prereq"]
        for _, r in crs_prereq.iterrows():
            # crs_AAA → AAA
            pre_code = r["src"].replace("crs_", "")
            post_code = r["dst"].replace("crs_", "")
            pre_kps = kp_df[kp_df.course_code == pre_code][kp_col]
            post_kps = kp_df[kp_df.course_code == post_code][kp_col]
            for p in pre_kps:
                for q in post_kps:
                    G.add_edge(p, q)

    assert G.number_of_nodes() > 0, "kp DAG 节点为空"
    assert nx.is_directed_acyclic_graph(G), \
        f"kp DAG 发现环: {list(nx.simple_cycles(G))[:2]}"


def test_t2_no_dangling_edges(graph_files):
    """图无悬挂边（所有边的 src/dst 都在节点表里）"""
    dims, edges = graph_files
    all_nodes = set()
    for df in dims.values():
        id_col = [c for c in df.columns
                  if c.endswith("_id") or c in ("kp_id", "resource_id",
                                                  "class_session_id", "code")]
        if id_col:
            all_nodes.update(df[id_col[0]].tolist())
    bad = []
    for et, df in edges.items():
        for col in ["src", "dst"]:
            if col in df.columns:
                miss = set(df[col]) - all_nodes
                if miss:
                    bad.append(f"{et}.{col} 有 {len(miss)} 个悬挂节点")
    assert not bad, "\n".join(bad)


def test_t2_nx_json_exists():
    """NetworkX node_link 序列化存在"""
    assert (PROCESSED / "graph_nx.json").exists(), "graph_nx.json 不存在"


# ═════════════════════════════════════════════════════════════════════════════
# T3 时序特征
# ═════════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def tft_df():
    return pd.read_parquet(PROCESSED / "tft_weekly.parquet")


def test_t3_row_count(tft_df, tables):
    """行数 = 学生×课×学期 × 16 周"""
    base = tables["final_result"].groupby(
        ["stu_id", "course_code", "semester"]).ngroups
    expected = base * 16
    assert len(tft_df) == expected, \
        f"TFT 行数 {len(tft_df)} ≠ 预期 {expected} ({base} × 16)"


def test_t3_each_student_has_all_16_weeks(tft_df):
    """每个学生×课×学期的 week 覆盖 0..15"""
    wk_count = tft_df.groupby(
        ["stu_id", "course_code", "semester"])["week"].nunique()
    missing = wk_count[wk_count != 16]
    assert missing.empty, \
        f"{len(missing)} 个 (stu,course,semester) 组合缺周"


def test_t3_split_no_leak(tft_df):
    """同一 stu_id 只在一个 split_group 里"""
    stu_split = tft_df.groupby("stu_id")["split_group"].nunique()
    multi = stu_split[stu_split > 1]
    assert multi.empty, \
        f"{len(multi)} 个学生跨了 split_group（泄漏！）"


def test_t3_positive_ratio_matches_final(tft_df, tables):
    """正例比例和 final_result 一致"""
    tft_pos = tft_df.groupby(
        ["stu_id", "course_code", "semester"])["is_positive"].max().mean()
    final_pos = tables["final_result"]["is_positive"].mean()
    assert abs(tft_pos - final_pos) < 0.01, \
        f"TFT 正例比 {tft_pos:.3f} ≠ final_result {final_pos:.3f}"


def test_t3_count_cum_monotonic(tft_df):
    """计数型累计特征单调不减（防泄漏）"""
    for col in ["ppt_clicks_cum", "hw_days_late_cum", "absent_count_cum"]:
        if col not in tft_df.columns:
            continue
        df_sorted = tft_df.sort_values(
            ["stu_id", "course_code", "semester", "week"])
        diffs = df_sorted.groupby(
            ["stu_id", "course_code", "semester"], group_keys=False
        )[col].diff().fillna(0)
        viol = (diffs < -1e-6).sum()
        assert viol == 0, f"{col} 有 {viol} 处下降"


def test_t3_known_future_present(tft_df):
    """三类 known_future 特征存在"""
    for col in ["week_norm", "is_late_half", "weeks_to_final"]:
        assert col in tft_df.columns, f"缺少 known_future: {col}"


def test_t3_deepsurv_exists():
    """DeepSurv 事件表存在"""
    surv = PROCESSED / "deepsurv_events.parquet"
    assert surv.exists(), "deepsurv_events.parquet 不存在"
    df = pd.read_parquet(surv)
    for col in ["start_time", "event_time", "event"]:
        assert col in df.columns, f"DeepSurv 缺 {col}"


# ═════════════════════════════════════════════════════════════════════════════
# T4 DKT 序列 + 知识点
# ═════════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def dkt_df():
    return pd.read_parquet(PROCESSED / "dkt_sequences.parquet")


@pytest.fixture(scope="module")
def kp_df():
    return pd.read_parquet(PROCESSED / "dim_knowledge_point.parquet")


@pytest.fixture(scope="module")
def item_map():
    return pd.read_parquet(PROCESSED / "item_kp_map.parquet")


def test_t4_kp_node_count(kp_df):
    """知识点节点 = 50 + 1 kp_unknown"""
    assert len(kp_df) == 51, f"kp 节点数 {len(kp_df)} ≠ 51"
    assert "kp_unknown" in kp_df["kp_id"].values, "缺 kp_unknown 兜底"


def test_t4_item_idx_continuous(item_map):
    """item_idx 是 0..N-1 连续整数（DKT embedding 层要求）"""
    idx = item_map["item_idx"].values
    expected = np.arange(len(idx))
    assert np.array_equal(np.sort(idx), expected), \
        "item_idx 不是 0..N-1 连续"


def test_t4_dkt_week_monotonic(dkt_df):
    """每个序列内 week 单调不减（时间有序）"""
    viol = 0
    for _, r in dkt_df.iterrows():
        wk = [w for w in r["week_seq"] if w != 0]
        if wk != sorted(wk):
            viol += 1
    assert viol == 0, f"{viol} 个序列 week 不单调"


def test_t4_dkt_split_matches_t3(dkt_df, tft_df):
    """DKT 的 split_group 和 T3 完全一致"""
    stu_split_tft = dict(zip(
        tft_df[["stu_id", "split_group"]].drop_duplicates()["stu_id"],
        tft_df[["stu_id", "split_group"]].drop_duplicates()["split_group"],
    ))
    stu_split_dkt = dict(zip(
        dkt_df[["stu_id", "split_group"]].drop_duplicates()["stu_id"],
        dkt_df[["stu_id", "split_group"]].drop_duplicates()["split_group"],
    ))
    mismatches = {s: (stu_split_tft[s], stu_split_dkt[s])
                  for s in stu_split_dkt
                  if s in stu_split_tft
                  and stu_split_tft[s] != stu_split_dkt[s]}
    assert not mismatches, f"split_group 不一致: {list(mismatches.items())[:3]}"


def test_t4_dkt_seq_len_positive(dkt_df):
    """所有序列长度 > 0（mock 保证每个学生都有小测）"""
    assert (dkt_df["sequence_len"] > 0).all(), "有学生 sequence_len=0"


def test_t4_dkt_item_in_range(dkt_df, item_map):
    """item_idx 都在 item_map 范围内"""
    max_idx = item_map["item_idx"].max()
    for _, r in dkt_df.iterrows():
        for idx in r["item_idx_seq"]:
            if idx != 0:  # padding 是 0
                assert idx <= max_idx, f"item_idx={idx} > max={max_idx}"
