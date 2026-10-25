"""
图可视化脚本：画一个正例学生的局部子图 + 边/节点类型统计
输出到 algorithm/data/processed/graph_viz/
"""
import json
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

# 中文字体
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT = Path("algorithm/data/processed/graph_viz")
OUT.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. 载入图
# ═══════════════════════════════════════════════════════════════════════════════
print("Loading graph...")
with open("algorithm/data/processed/graph_nx.json", encoding="utf-8") as f:
    data = json.load(f)
G = nx.node_link_graph(data, edges="links")
G = nx.MultiDiGraph(G)
print(f"  {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

NTYPE_COLOR = {
    "student":         "#FF6B6B",
    "course":          "#4ECDC4",
    "class_session":   "#45B7D1",
    "knowledge_point": "#FFD93D",
    "resource":        "#96CEB4",
}
ETYPE_STYLE = {
    # etype: (linestyle, width)   —— matplotlib 只认这几种
    "enrolled_in":       ("solid",   1.5),
    "attended":          ("solid",   2.5),   # 关键：粗线
    "attended_with":     ("dashed",  1.2),   # 虚线：同班同侪
    "scored_on":         ("solid",   2.0),
    "interacts_with":    ("solid",   1.0),
    "has_kt":            ("solid",   1.5),
    "prereq_of":         ("solid",   1.5),
    "supports":          ("solid",   1.0),
    "course_prereq":     ("solid",   2.0),
}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 选正例学生，构造紧凑局部子图
#    只保留：目标学生 + 目标学生直接连的课/课次/知识点 + 最多 3 个同班同学
#    约 30-50 节点，清晰可辨
# ═══════════════════════════════════════════════════════════════════════════════
target = pd.read_parquet("algorithm/data/interim/final_result.parquet")
positive_stus = target[target["is_positive"] == 1]["stu_id"].unique()
stu_id = str(positive_stus[0])
print(f"\n正例目标学生: {stu_id}")

# 直接邻居（课/课次/知识点/资源）— 限制每种最多 8 个
neighbors = {"student": set(), "course": set(), "class_session": set(),
             "knowledge_point": set(), "resource": set()}

# MultiDiGraph 需要 (u, v, key) → 简化：只取 v
for u, v, k, d in G.edges(keys=True, data=True):
    if u == stu_id:
        nid = v
    elif v == stu_id:
        nid = u
    else:
        continue
    ntype = G.nodes[nid].get("node_type", nid.split("_")[0])
    ntype = {
        "stu": "student", "crs": "course", "cs": "class_session",
        "kp": "knowledge_point", "res": "resource",
    }.get(ntype, ntype)
    if ntype in neighbors and len(neighbors[ntype]) < 8:
        neighbors[ntype].add(nid)

nodes_keep = {stu_id}
for s in neighbors.values():
    nodes_keep |= s

# 加 3 个同班同侪（attended_with 邻居里挑）
peer_pool = set()
for u, v, k, d in G.edges(keys=True, data=True):
    if d.get("edge_type") == "attended_with":
        if u == stu_id: peer_pool.add(v)
        if v == stu_id: peer_pool.add(u)
peer_pool = list(peer_pool)[:3]
nodes_keep |= set(peer_pool)

G_sub = G.subgraph(nodes_keep).copy()
# 加 node_type 兜底
for n in G_sub.nodes():
    if "node_type" not in G_sub.nodes[n]:
        prefix = n.split("_")[0]
        G_sub.nodes[n]["node_type"] = {
            "stu": "student", "crs": "course", "cs": "class_session",
            "kp": "knowledge_point", "res": "resource",
        }.get(prefix, "unknown")

node_types = {n: G_sub.nodes[n]["node_type"] for n in G_sub.nodes()}
print(f"  局部子图: {G_sub.number_of_nodes()} nodes, {G_sub.number_of_edges()} edges")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 画局部子图
# ═══════════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(16, 12))

pos = nx.spring_layout(G_sub, seed=42, k=1.6 / (G_sub.number_of_nodes() ** 0.5))
pos[stu_id] = (0.0, 0.0)  # 目标学生居中

# 边：按类型分组，只画 edge_type 明确的
drawn_etypes = set()
for u, v, k, d in G_sub.edges(keys=True, data=True):
    etype = d.get("edge_type")
    if not etype or etype not in ETYPE_STYLE:
        continue
    ls, lw = ETYPE_STYLE[etype]
    nx.draw_networkx_edges(G_sub, pos, edgelist=[(u, v)],
                           style=ls, width=lw, edge_color="#888888",
                           alpha=0.65, arrows=False, ax=ax)
    drawn_etypes.add(etype)

# 节点：按类型分组
for ntype, color in NTYPE_COLOR.items():
    nlist = [n for n, t in node_types.items() if t == ntype]
    if not nlist:
        continue
    sz = 1500 if ntype == "student" else 600
    nx.draw_networkx_nodes(G_sub, pos, nodelist=nlist,
                           node_color=color, node_size=sz,
                           edgecolors="white", linewidths=1.5, ax=ax)

# 节点标签（简化显示）
labels = {}
for n in G_sub.nodes():
    if n == stu_id:
        labels[n] = f"★{stu_id[-6:]}"   # 目标学生加 ★
    elif node_types.get(n) == "student":
        labels[n] = n[-6:]               # 同班同学只显示后 6 位
    elif node_types.get(n) == "course":
        labels[n] = n.replace("crs_", "")
    elif node_types.get(n) == "class_session":
        labels[n] = n.split("_")[-1]    # 只显示 wk 号
    elif node_types.get(n) == "knowledge_point":
        labels[n] = n.split("_", 2)[-1]  # 只显示编号
    else:
        labels[n] = n[-10:]
nx.draw_networkx_labels(G_sub, pos, labels, font_size=8, ax=ax)

# 图例
from matplotlib.lines import Line2D
node_handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor=c,
                       markersize=12, label=f"● {t}")
                for t, c in NTYPE_COLOR.items()
                if any(v == t for v in node_types.values())]
edge_handles = [Line2D([0], [0], color="#888888", ls=ls, lw=lw, label=f"— {t}")
                for t in sorted(drawn_etypes)
                for ls, lw in [ETYPE_STYLE[t]]]

ax.legend(handles=node_handles + edge_handles, loc="lower right",
          fontsize=8, ncol=1, framealpha=0.92)

ax.set_title(f"正例学生 {stu_id} 的局部异质子图\n"
             f"节点={G_sub.number_of_nodes()}, 边={G_sub.number_of_edges()}  "
             f"(仅直接关联 + 3 个同班同侪)",
             fontsize=13, pad=10)
ax.axis("off")
plt.tight_layout()
plt.savefig(OUT / "subgraph_positive_student.png", dpi=150, bbox_inches="tight")
print(f"\n子图已保存 → {OUT / 'subgraph_positive_student.png'}")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 边类型柱状图
# ═══════════════════════════════════════════════════════════════════════════════
fig2, ax2 = plt.subplots(figsize=(12, 6))
et_counts = Counter(d.get("edge_type") for _, _, d in G.edges(data=True))
et_counts = dict(sorted(et_counts.items(), key=lambda x: -x[1]))
types = list(et_counts.keys())
counts = list(et_counts.values())
palette = ["#FF6B6B", "#4ECDC4", "#FFD93D", "#45B7D1", "#96CEB4",
           "#E57373", "#64B5F6", "#FFB74D", "#81C784"]
bars = ax2.bar(types, counts, color=palette[:len(types)], edgecolor="white")
ax2.bar_label(bars, fmt="{:,}", fontsize=9)
ax2.set_title(f"全图 {G.number_of_edges():,} 条边 · 9 种类型分布", fontsize=14, pad=10)
ax2.set_ylabel("边数")
ax2.tick_params(axis="x", rotation=25)
plt.tight_layout()
plt.savefig(OUT / "edge_type_distribution.png", dpi=150, bbox_inches="tight")
print(f"边类型柱状图已保存 → {OUT / 'edge_type_distribution.png'}")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 节点类型饼图
# ═══════════════════════════════════════════════════════════════════════════════
fig3, ax3 = plt.subplots(figsize=(8, 8))
nt_counts = Counter(
    G.nodes[n].get("node_type", n.split("_")[0])
    for n in G.nodes()
)
# 兜底前缀映射
for k in list(nt_counts.keys()):
    prefix = k.split("_")[0]
    mapped = {"stu": "student", "crs": "course", "cs": "class_session",
              "kp": "knowledge_point", "res": "resource"}.get(prefix, k)
    if mapped != k:
        nt_counts[mapped] = nt_counts.get(mapped, 0) + nt_counts.pop(k)

nt_counts = dict(sorted(nt_counts.items(), key=lambda x: -x[1]))
labels = list(nt_counts.keys())
sizes = list(nt_counts.values())
colors = [NTYPE_COLOR.get(l, "#ccc") for l in labels]
wedges, texts, autotexts = ax3.pie(
    sizes, labels=labels, colors=colors, autopct="%1.1f%%",
    startangle=140, textprops={"fontsize": 11},
    wedgeprops={"edgecolor": "white", "linewidth": 1.5})
ax3.set_title(f"全图 {G.number_of_nodes():,} 个节点 · 5 种类型分布", fontsize=14, pad=12)
plt.tight_layout()
plt.savefig(OUT / "node_type_distribution.png", dpi=150, bbox_inches="tight")
print(f"节点类型饼图已保存 → {OUT / 'node_type_distribution.png'}")

print(f"\n✅ 三张图已生成 → {OUT}")
print("  1) subgraph_positive_student.png  — 正例学生的局部子图（★=目标，可看清出勤/同班同侪/成绩边）")
print("  2) edge_type_distribution.png     — 9 类边的数量柱状图")
print("  3) node_type_distribution.png     — 5 类节点的占比饼图")
