"""
04_knowledge_map.py  —— T4 完整版：知识点体系 + DKT 逐题答题序列
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

输入：algorithm/data/interim/*.parquet
输出：
  ① dim_knowledge_point.parquet    知识点节点（50 个 + 1 个 kp_unknown 兜底）
  ② edge_course_has_kt.parquet     课程 → 知识点（has_kt 边）
  ③ edge_kp_prereq.parquet         知识点内部依赖链（rule，按编号顺序）
  ④ item_kp_map.parquet           题库 → 知识点映射（含连续编码 item_idx）
  ⑤ item_idx_map.parquet          item_id → 连续 item_idx（DKT embedding 用）
  ⑥ dkt_sequences.parquet         逐题答题序列（split_group 复用 T3）
  ⑦ 依赖链 DAG 校验日志

DKT 序列格式（每行 = 学生×课×学期）：
  stu_id, course_code, semester, split_group,
  item_idx_seq:      list[int]   # 连续编码后的 item_idx，时间有序
  is_correct_seq:    list[int]   # 0/1，和 item_idx_seq 对齐
  kp_idx_seq:        list[int]   # 对应知识点编码（-1 表示 unknown）
  week_seq:          list[int]   # 每题发生在第几周
  sequence_len:       int
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

INTERIM = Path("algorithm/data/interim")
OUT = Path("algorithm/data/processed")
OUT.mkdir(parents=True, exist_ok=True)

# ── 先确认 quiz_item 长啥样 ────────────────────────────────────────────────
quiz = pd.read_parquet(INTERIM / "quiz_item.parquet")
lens = quiz.groupby(["stu_id", "course_code", "semester"]).size()
print(f"quiz_item: {len(quiz):,} 行 · "
      f"stu×course×semester={lens.shape[0]:,}")
print(f"序列长度  median={lens.median():.0f}  "
      f"p90={lens.quantile(0.9):.0f}  p95={lens.quantile(0.95):.0f}  max={lens.max()}")
print(f"kp_label_missing={quiz['kp_label_missing'].sum()} "
      f"({quiz['kp_label_missing'].mean():.1%})")

# 序列截断上限：p95 = 160 道题，超长截断，不足补零（后续 DKT padding）
MAX_SEQ_LEN = int(lens.quantile(0.95))
print(f"→ 截断上限 MAX_SEQ_LEN = {MAX_SEQ_LEN}")


# ═════════════════════════════════════════════════════════════════════════════
# Part 1：知识点节点 + 课程 has_kt + kp 依赖链（保留轻量版逻辑）
# ═════════════════════════════════════════════════════════════════════════════
print("\n▶ Part 1：知识点节点 + 边 ...")

# 1a. 所有课程的知识点（每课 10 个）
courses = pd.read_parquet(INTERIM / "courses.parquet")["code"].unique()
all_kps = []
for code in courses:
    for i in range(1, 11):
        all_kps.append({"kp_id": f"kp_{code}_{i:02d}", "course_code": code})

df_kps = pd.DataFrame(all_kps)
df_kps["difficulty_proxy"] = 0.5
df_kps["suggested_week"] = df_kps.index % 16
df_kps["name"] = df_kps["kp_id"]

# 加 kp_unknown 兜底节点（放所有缺失标签的题目）
UNKNOWN_KP = "kp_unknown"
df_kps = pd.concat([
    df_kps,
    pd.DataFrame([{"kp_id": UNKNOWN_KP, "course_code": "unknown",
                    "difficulty_proxy": 0.5, "suggested_week": 0,
                    "name": UNKNOWN_KP}]),
], ignore_index=True)

# 加连续 kp_idx（DKT embedding 层需要 0..N-1）
df_kps = df_kps.reset_index(drop=True).reset_index().rename(
    columns={"index": "kp_idx"})
df_kps[["kp_idx", "kp_id", "course_code",
        "difficulty_proxy", "suggested_week", "name"]].to_parquet(
    OUT / "dim_knowledge_point.parquet", index=False)
print(f"  dim_knowledge_point: {len(df_kps)} 节点 (含 1 unknown)")

# kp_id → kp_idx 映射字典
kp_id_to_idx = dict(zip(df_kps["kp_id"], df_kps["kp_idx"]))
unknown_idx = kp_id_to_idx[UNKNOWN_KP]

# 1b. course → kp (has_kt) 边
has_kt = df_kps[df_kps["kp_id"] != UNKNOWN_KP][["course_code", "kp_id"]].copy()
has_kt.columns = ["src", "dst"]
has_kt["src_type"] = "course"
has_kt["dst_type"] = "knowledge_point"
has_kt.to_parquet(OUT / "edge_course_has_kt.parquet", index=False)
print(f"  has_kt edges: {len(has_kt)}")

# 1c. kp 依赖链（rule：同课编号小 → 大）
rows = []
for course, sub in df_kps[df_kps["kp_id"] != UNKNOWN_KP].groupby("course_code"):
    sub = sub.sort_values("kp_idx").reset_index(drop=True)
    for i in range(len(sub) - 1):
        rows.append({
            "src": sub.at[i, "kp_id"],
            "dst": sub.at[i + 1, "kp_id"],
            "src_type": "knowledge_point",
            "dst_type": "knowledge_point",
            "source": "rule",
            "strength": 0.7,
        })
kp_dep = pd.DataFrame(rows)
kp_dep.to_parquet(OUT / "edge_kp_prereq.parquet", index=False)
print(f"  kp_prereq edges: {len(kp_dep)}")


# ═════════════════════════════════════════════════════════════════════════════
# Part 2：依赖链 DAG 校验（轻量版缺的）
# ═════════════════════════════════════════════════════════════════════════════
print("\n▶ Part 2：DAG 校验 ...")
import networkx as nx

G_kp = nx.DiGraph()
G_kp.add_nodes_from(df_kps["kp_id"])
G_kp.add_edges_from(zip(kp_dep["src"], kp_dep["dst"]))
# 加课程先修边（先修课的所有 kp → 后修课的所有 kp）
course_prereq = pd.read_parquet(INTERIM / "course_prereq.parquet")
for _, row in course_prereq.iterrows():
    pre_kps = df_kps[df_kps["course_code"] == row["prereq_course_code"]]["kp_id"]
    post_kps = df_kps[df_kps["course_code"] == row["course_code"]]["kp_id"]
    for p in pre_kps:
        for q in post_kps:
            G_kp.add_edge(p, q)
print(f"  kp DAG: {G_kp.number_of_nodes()} nodes, {G_kp.number_of_edges()} edges")
if nx.is_directed_acyclic_graph(G_kp):
    print("  ✅ kp DAG 无环")
else:
    cycles = list(nx.simple_cycles(G_kp))
    print(f"  ❌ 发现环: {cycles[:3]}")
# 拓扑序长度验证
topo = list(nx.topological_sort(G_kp))
assert len(topo) == G_kp.number_of_nodes(), "拓扑序应覆盖全部节点"


# ═════════════════════════════════════════════════════════════════════════════
# Part 3：item_kp_map（题库 → 知识点映射 + 连续编码）
# ═════════════════════════════════════════════════════════════════════════════
print("\n▶ Part 3：item_kp_map ...")

# 3a. 所有 item 去重（item_id 在 quiz_item 里唯一）
items = (
    quiz[["item_id", "course_code", "kp_id", "kp_label_missing"]]
    .drop_duplicates("item_id")
    .reset_index(drop=True)
)
# 缺失标签 → 统一归到 kp_unknown
items["kp_id"] = items["kp_id"].fillna(UNKNOWN_KP)
items["kp_idx"] = items["kp_id"].map(kp_id_to_idx)

# 3b. 连续 item_idx（DKT embedding 层需要 0..N-1）
items = items.reset_index(drop=True).reset_index().rename(
    columns={"index": "item_idx"})
items[["item_idx", "item_id", "course_code", "kp_id", "kp_idx",
        "kp_label_missing"]].to_parquet(OUT / "item_kp_map.parquet", index=False)
items[["item_idx", "item_id"]].to_parquet(OUT / "item_idx_map.parquet", index=False)

print(f"  item_kp_map: {len(items)} items  "
      f"({len(items[items.kp_label_missing])} 缺失标签 → kp_unknown)")
print(f"  item_idx 范围: 0..{items['item_idx'].max()}")

# 3c. 简单 sanity：每个 kp 至少挂了 1 道题
kp_coverage = items[items.kp_id != UNKNOWN_KP].groupby("course_code")["kp_idx"].nunique()
for code in courses:
    n_covered = kp_coverage.get(code, 0)
    print(f"    {code}: {n_covered}/{df_kps[df_kps.course_code==code].shape[0]-1} kp 有题")


# ═════════════════════════════════════════════════════════════════════════════
# Part 4：DKT 逐题答题序列（核心交付物）
# ═════════════════════════════════════════════════════════════════════════════
print("\n▶ Part 4：DKT 序列 ...")

# 4a. 合并 item_idx 到 quiz
q_full = quiz.merge(
    items[["item_id", "item_idx", "kp_idx"]],
    on="item_id", how="left",
)
# 缺失 kp_idx → -1（unknown）
q_full["kp_idx"] = q_full["kp_idx"].fillna(-1).astype(int)

# is_correct：score == max_score → 1，否则 0（mock 是二值的 score∈{0,1}）
q_full["is_correct"] = (q_full["score"] == q_full["max_score"]).astype(int)

# 4b. 按 (stu_id, course_code, semester) 排序 week，构造序列
q_full = q_full.sort_values(
    ["stu_id", "course_code", "semester", "week", "item_idx"]
).reset_index(drop=True)

# 4c. 复用 T3 split_group（按 stu_id 哈希，同一学生不跨组）
tft = pd.read_parquet(OUT / "tft_weekly.parquet")
stu_to_split = dict(zip(
    tft[["stu_id", "split_group"]].drop_duplicates()["stu_id"],
    tft[["stu_id", "split_group"]].drop_duplicates()["split_group"],
))

# 4d. 聚合序列
def pad_or_trim(seq, max_len):
    """截断到 max_len，不足补 0"""
    arr = list(seq)
    if len(arr) > max_len:
        arr = arr[-max_len:]  # 保留最近的题（更反映当前能力）
    arr = arr + [0] * (max_len - len(arr))
    return arr

rows_seq = []
for (stu, crs, sem), grp in q_full.groupby(
    ["stu_id", "course_code", "semester"], sort=False
):
    n = len(grp)
    rows_seq.append({
        "stu_id": stu,
        "course_code": crs,
        "semester": sem,
        "split_group": stu_to_split.get(stu, "train"),
        "item_idx_seq": pad_or_trim(grp["item_idx"].tolist(), MAX_SEQ_LEN),
        "is_correct_seq": pad_or_trim(grp["is_correct"].tolist(), MAX_SEQ_LEN),
        "kp_idx_seq": pad_or_trim(grp["kp_idx"].tolist(), MAX_SEQ_LEN),
        "week_seq": pad_or_trim(grp["week"].tolist(), MAX_SEQ_LEN),
        "sequence_len": min(n, MAX_SEQ_LEN),
        "sequence_len_raw": n,
    })

df_seq = pd.DataFrame(rows_seq)
df_seq.to_parquet(OUT / "dkt_sequences.parquet", index=False)

print(f"  DKT 序列: {len(df_seq):,} 行  (stu×course×semester)")
print(f"  MAX_SEQ_LEN={MAX_SEQ_LEN}")
print(f"  split 分布: {df_seq['split_group'].value_counts().to_dict()}")
print(f"  sequence_len_raw  max={df_seq['sequence_len_raw'].max()}  "
      f"截断后 max={df_seq['sequence_len'].max()}")

# 4e. 校验：序列内 week 单调不减
viol = 0
for _, r in df_seq.iterrows():
    wk = [w for w in r["week_seq"] if w != 0]
    if wk != sorted(wk):
        viol += 1
print(f"  week 单调校验: {'✅' if viol == 0 else f'❌ {viol} 处'}")

# 4f. 校验：split_group 与 T3 完全一致
assert set(df_seq["stu_id"]) <= set(stu_to_split.keys()), \
    "DKT 序列里有 T3 没覆盖的学生"
print(f"  split_group 复用 T3 ✅  "
      f"({df_seq['stu_id'].nunique()} 学生都在 T3 里)")

print("\n🎉 T4 完整版全部完成")
print(f"   {OUT / 'dim_knowledge_point.parquet'}")
print(f"   {OUT / 'item_kp_map.parquet'}")
print(f"   {OUT / 'item_idx_map.parquet'}")
print(f"   {OUT / 'dkt_sequences.parquet'}")
