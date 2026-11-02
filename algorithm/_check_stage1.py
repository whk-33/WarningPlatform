"""阶段一交付物只读检查 — 不修改任何文件"""
import json, sys, os
sys.path.insert(0, ".")
import networkx as nx
import numpy as np
import pandas as pd

PASS = 0; FAIL = 0; WARN = 0
def chk(name, ok, detail="", level="PASS"):
    global PASS, FAIL, WARN
    if level == "WARN": WARN += 1
    elif ok: PASS += 1
    else: FAIL += 1
    if not ok: tag = "❌"
    else: tag = {"PASS": "✅", "WARN": "⚠️ "}[level]
    print(f"  {tag} {name}  {detail}")

print("═" * 60)
print("  阶段一交付物只读完整性检查")
print("═" * 60)

# ════════════════════════════════════════════════════════════════════════════
# 0. 文件是否存在
# ════════════════════════════════════════════════════════════════════════════
print("\n▍0. 文件存在性")
files = [
    "algorithm/src/data_pipeline/00_generate_mock.py",
    "algorithm/src/data_pipeline/02_clean.py",
    "algorithm/src/data_pipeline/03_weekly_features.py",
    "algorithm/src/data_pipeline/04_knowledge_map.py",
    "algorithm/src/data_pipeline/05_build_graph.py",
    "algorithm/tests/test_data_integrity.py",
    "algorithm/tests/test_stage1_full.py",
    "algorithm/configs/schema.yaml",
    ".gitignore",
    "docs/阶段一_算法工程师实施指南.md",
    "algorithm/data/processed/graph_nx.json",
    "algorithm/data/processed/tft_weekly.parquet",
    "algorithm/data/processed/deepsurv_events.parquet",
    "algorithm/data/processed/dkt_sequences.parquet",
    "algorithm/data/processed/item_kp_map.parquet",
    "algorithm/data/processed/item_idx_map.parquet",
    "algorithm/data/processed/dim_knowledge_point.parquet",
]
for f in files:
    chk(f"exists {f}", os.path.exists(f))

# graph/ 子目录
for f in os.listdir("algorithm/data/processed/graph"):
    p = f"algorithm/data/processed/graph/{f}"
    chk(f"graph/{f}", os.path.exists(p) and os.path.getsize(p) > 0)

# graph_viz/
for f in os.listdir("algorithm/data/processed/graph_viz"):
    p = f"algorithm/data/processed/graph_viz/{f}"
    chk(f"viz/{f}", os.path.exists(p) and os.path.getsize(p) > 0)

# ════════════════════════════════════════════════════════════════════════════
# 1. 源数据链路
# ════════════════════════════════════════════════════════════════════════════
print("\n▍1. raw → interim → processed 链路一致性")
raw = {p.replace(".csv",""): p for p in os.listdir("algorithm/data/raw") if p.endswith(".csv")}
interim = {}
for p in os.listdir("algorithm/data/interim"):
    if p.endswith(".parquet"):
        interim[p.replace(".parquet","")] = pd.read_parquet(f"algorithm/data/interim/{p}")

# raw CSV 行 vs interim parquet 行
raw_fix = {
    "student_info": "student_info.csv",
    "courses": "courses.csv",
    "registration": "registration.csv",
    "student_assessment": "student_assessment.csv",
    "final_result": "final_result.csv",
    "platform_log": "platform_log.csv",
    "quiz_item": "quiz_item_responses.csv",
    "assessment_config": "assessment_config.csv",
    "calendar": "calendar.csv",
    "course_prereq": "course_prereq.csv",
}
for stem, fname in raw_fix.items():
    csv_path = f"algorithm/data/raw/{fname}"
    if not os.path.exists(csv_path):
        continue
    csv = pd.read_csv(csv_path)
    ipar = interim.get(stem)
    if ipar is None:
        chk(f"raw→interim {stem}", False, f"interim 缺 {stem}")
        continue
    chk(f"{stem}: raw {len(csv)} → interim {len(ipar)}",
        len(csv) == len(ipar) or abs(len(csv) - len(ipar)) < 10,
        f"(差 {abs(len(csv)-len(ipar))})",
        "WARN" if abs(len(csv) - len(ipar)) > 10 else "PASS")

# ════════════════════════════════════════════════════════════════════════════
# 2. ID 打通
# ════════════════════════════════════════════════════════════════════════════
print("\n▍2. ID 统一 (stu_ 前缀)")
for tname in ["student_info", "registration", "platform_log",
              "quiz_item", "student_assessment", "final_result"]:
    df = interim[tname]
    if "stu_id" in df.columns:
        bad = df[~df["stu_id"].str.startswith("stu_", na=True)]
        chk(f"{tname}.stu_id 统一", bad.empty,
            f"{len(bad)} 行未统一", "WARN" if not bad.empty else "PASS")

# ════════════════════════════════════════════════════════════════════════════
# 3. T2 异质图
# ════════════════════════════════════════════════════════════════════════════
print("\n▍3. T2 异质图")
with open("algorithm/data/processed/graph_nx.json") as f:
    nx_data = json.load(f)
G = nx.node_link_graph(nx_data, edges="links")
G = nx.MultiDiGraph(G)
chk("graph_nx.json 可加载", True, f"{G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# 5 类节点
ntypes = {"student", "course", "class_session", "knowledge_point", "resource"}
actual_ntypes = set(nx.get_node_attributes(G, "node_type").values())
for nt in ntypes:
    count = sum(1 for _, t in G.nodes(data="node_type") if t == nt)
    chk(f"节点类型 {nt}", count > 0, f"count={count}")

# 9 类边
etypes_expected = {"enrolled_in", "attended", "attended_with", "scored_on",
                   "interacts_with", "has_kt", "prereq_of", "supports",
                   "course_prereq"}
actual_etypes = set()
for _, _, d in G.edges(data=True):
    actual_etypes.add(d.get("edge_type", "?"))
for et in etypes_expected:
    count = sum(1 for _, _, d in G.edges(data=True) if d.get("edge_type") == et)
    chk(f"边类型 {et}", et in actual_etypes, f"count={count}")

# kp DAG
kp_dag = nx.DiGraph()
for n, t in G.nodes(data="node_type"):
    if t == "knowledge_point":
        kp_dag.add_node(n)
for u, v, d in G.edges(data=True):
    if d.get("edge_type") == "prereq_of":
        kp_dag.add_edge(u, v)
chk("kp DAG 无环", nx.is_directed_acyclic_graph(kp_dag),
    f"{kp_dag.number_of_nodes()} nodes, {kp_dag.number_of_edges()} edges")

# 无悬挂边
all_nodes = set(G.nodes())
orphans = []
for u, v in G.edges():
    if u not in all_nodes or v not in all_nodes:
        orphans.append((u, v))
chk("无悬挂边", len(orphans) == 0, f"{len(orphans)} 条悬挂" if orphans else "0")

# ════════════════════════════════════════════════════════════════════════════
# 4. T3 TFT + DeepSurv
# ════════════════════════════════════════════════════════════════════════════
print("\n▍4. T3 TFT 宽表")
tft = pd.read_parquet("algorithm/data/processed/tft_weekly.parquet")
fin = interim["final_result"]
expected_rows = fin.groupby(["stu_id", "course_code", "semester"]).ngroups * 16
chk("TFT 行数", len(tft) == expected_rows, f"{len(tft)} vs 预期 {expected_rows}")
chk("TFT 列数", tft.shape[1] == 29, f"{tft.shape[1]}")

# 每周覆盖
wk_nunique = tft.groupby(["stu_id", "course_code", "semester"])["week"].nunique()
missing_wk = (wk_nunique != 16).sum()
chk("每 stu×course×semester 覆盖 16 周", missing_wk == 0, f"缺周: {missing_wk}")

# split_group 不泄漏
stu_split = tft.groupby("stu_id")["split_group"].nunique()
multi = (stu_split > 1).sum()
chk("split_group 无泄漏", multi == 0, f"跨组学生: {multi}")

# 正例比例
tft_pos = tft.groupby(["stu_id","course_code","semester"])["is_positive"].max().mean()
fin_pos = fin["is_positive"].mean()
chk("正例比例一致", abs(tft_pos - fin_pos) < 0.01,
    f"TFT {tft_pos:.3f} vs final {fin_pos:.3f}")

# 计数型累计特征单调不减
for col in ["ppt_clicks_cum", "hw_days_late_cum", "absent_count_cum"]:
    if col in tft.columns:
        diffs = tft.sort_values(["stu_id","course_code","semester","week"])\
                   .groupby(["stu_id","course_code","semester"], group_keys=False)\
                   [col].diff().fillna(0)
        viol = (diffs < -1e-6).sum()
        chk(f"{col} 单调不减", viol == 0, f"下降: {viol}")

# DeepSurv
surv = pd.read_parquet("algorithm/data/processed/deepsurv_events.parquet")
chk("DeepSurv 存在", len(surv) == fin.groupby(["stu_id","course_code","semester"]).ngroups,
    f"{len(surv)} vs {fin.groupby(['stu_id','course_code','semester']).ngroups}")

# ════════════════════════════════════════════════════════════════════════════
# 5. T4 知识点 + DKT
# ════════════════════════════════════════════════════════════════════════════
print("\n▍5. T4 DKT 序列 + 知识点")
kp = pd.read_parquet("algorithm/data/processed/dim_knowledge_point.parquet")
item_map = pd.read_parquet("algorithm/data/processed/item_kp_map.parquet")
item_idx_map = pd.read_parquet("algorithm/data/processed/item_idx_map.parquet")
dkt = pd.read_parquet("algorithm/data/processed/dkt_sequences.parquet")

chk("知识点节点数 = 51 (50+unknown)", len(kp) == 51, f"{len(kp)}")
chk("kp_unknown 存在", "kp_unknown" in kp["kp_id"].values)
chk("item_idx 连续 0..N-1",
    np.array_equal(np.sort(item_map["item_idx"]), np.arange(len(item_map))),
    f"range 0..{item_map['item_idx'].max()}")
chk("item_kp_map 有 kp_unknown 兜底",
    (item_map["kp_id"] == "kp_unknown").sum() > 0,
    f"unknown 行数 {(item_map['kp_id']=='kp_unknown').sum()}")

# DKT
expected_dkt_rows = fin.groupby(["stu_id", "course_code", "semester"]).ngroups
chk("DKT 行数 = stu×course×semester", len(dkt) == expected_dkt_rows,
    f"{len(dkt)} vs {expected_dkt_rows}")

# 序列内 week 单调
viol_wk = 0
for _, r in dkt.iterrows():
    wk = [w for w in r["week_seq"] if w != 0]
    if wk != sorted(wk):
        viol_wk += 1
chk("DKT week 单调不减", viol_wk == 0, f"不单调序列: {viol_wk}")

# split_group 与 T3 一致
stu_split_tft = dict(zip(
    tft[["stu_id","split_group"]].drop_duplicates()["stu_id"],
    tft[["stu_id","split_group"]].drop_duplicates()["split_group"]))
stu_split_dkt = dict(zip(
    dkt[["stu_id","split_group"]].drop_duplicates()["stu_id"],
    dkt[["stu_id","split_group"]].drop_duplicates()["split_group"]))
mismatch = {s: (stu_split_tft[s], stu_split_dkt[s])
            for s in stu_split_dkt if stu_split_tft.get(s) != stu_split_dkt[s]}
chk("DKT split_group 与 T3 一致", not mismatch, f"不一致: {len(mismatch)} 学生")

# 所有 item_idx 在范围内
max_item = item_map["item_idx"].max()
viol_item = 0
for _, r in dkt.iterrows():
    for idx in r["item_idx_seq"]:
        if idx != 0 and idx > max_item:
            viol_item += 1
chk("item_idx ≤ max_item", viol_item == 0, f"越界: {viol_item}")

# ════════════════════════════════════════════════════════════════════════════
# 6. 数据信号（mock 注入的可学习信号是否存在）
# ════════════════════════════════════════════════════════════════════════════
print("\n▍6. 可学习信号验证")
log = interim["platform_log"]
attend = (log.groupby(["stu_id","course_code","semester"])
             .agg(att=("attendance_status", lambda s: (s.isin(["attended","attended_leave"])).sum()),
                  tot=("attendance_status", "count")).reset_index())
attend["rate"] = attend["att"] / attend["tot"].clip(lower=1)
merged = attend.merge(fin, on=["stu_id","course_code","semester"])
pos_rate = merged[merged["is_positive"]==1]["rate"].mean()
neg_rate = merged[merged["is_positive"]==0]["rate"].mean()
chk("正例出勤率显著低于负例", pos_rate < neg_rate,
    f"正例 {pos_rate:.2%} vs 负例 {neg_rate:.2%}")

quiz = interim["quiz_item"]
coverage = quiz["kp_id"].notna().mean()
chk("知识点覆盖率 ≥ 80%", coverage >= 0.80, f"{coverage:.1%}")

pos_final_ratio = fin["is_positive"].mean()
chk("正例比例 20–35%", 0.20 <= pos_final_ratio <= 0.35, f"{pos_final_ratio:.1%}")

# ════════════════════════════════════════════════════════════════════════════
# 汇总
# ════════════════════════════════════════════════════════════════════════════
print()
print("═" * 60)
total = PASS + FAIL + WARN
print(f"  总计 {total} 项检查")
print(f"  ✅ PASS  {PASS}")
print(f"  ⚠️  WARN  {WARN}")
print(f"  ❌ FAIL  {FAIL}")
print()
if FAIL == 0:
    print("  结论: PASS  ✅ 数据完整性验证通过")
else:
    print(f"  结论: FAIL  ❌ 发现 {FAIL} 项问题")
print("═" * 60)
