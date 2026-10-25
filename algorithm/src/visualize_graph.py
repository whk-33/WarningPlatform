"""
图可视化脚本：边/节点类型分布图（中文标签，易读）
输出到 algorithm/data/processed/graph_viz/
"""
import json
import os
import shutil
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # 非交互后端，避免文件被占用报错
import matplotlib.pyplot as plt
import networkx as nx

# 中文字体
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT = Path("algorithm/data/processed/graph_viz")
OUT.mkdir(parents=True, exist_ok=True)


def safe_savefig(fig, path, **kwargs):
    """保存图片，若文件被占用则先存临时文件再替换"""
    try:
        fig.savefig(path, **kwargs)
    except OSError:
        tmp = path.with_suffix(".tmp.png")
        fig.savefig(tmp, **kwargs)
        try:
            os.replace(str(tmp), str(path))
        except OSError:
            shutil.move(str(tmp), str(path))
        print(f"  (文件被占用，已通过临时文件替换 → {path.name})")

# 中文映射
NTYPE_CN = {
    "student": "学生",
    "course": "课程",
    "class_session": "课次",
    "knowledge_point": "知识点",
    "resource": "资源",
}
ETYPE_CN = {
    "enrolled_in": "选课",
    "attended": "出勤",
    "attended_with": "同班同侪",
    "scored_on": "成绩关联",
    "interacts_with": "学习行为",
    "has_kt": "包含知识点",
    "prereq_of": "知识点先修",
    "supports": "支撑",
    "course_prereq": "课程先修",
}
NTYPE_COLOR = {
    "student": "#FF6B6B",
    "course": "#4ECDC4",
    "class_session": "#45B7D1",
    "knowledge_point": "#FFD93D",
    "resource": "#96CEB4",
}

# ═══════════════════════════════════════════════════════════════════════════════
# 1. 载入图
# ═══════════════════════════════════════════════════════════════════════════════
print("Loading graph...")
with open("algorithm/data/processed/graph_nx.json", encoding="utf-8") as f:
    data = json.load(f)
G = nx.node_link_graph(data, edges="links")
G = nx.MultiDiGraph(G)
print(f"  {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 边类型分布 —— 水平条形图（中文标签，按数量降序，每根条末端标数值）
# ═══════════════════════════════════════════════════════════════════════════════
et_counts = Counter(d.get("edge_type") for _, _, d in G.edges(data=True))
# 按数量降序
et_counts = dict(sorted(et_counts.items(), key=lambda x: -x[1]))

cn_labels = [ETYPE_CN.get(t, t) for t in et_counts.keys()]
counts = [int(v) for v in et_counts.values()]

fig2, ax2 = plt.subplots(figsize=(12, 6))
y_pos = range(len(cn_labels))
palette = ["#FF6B6B", "#4ECDC4", "#FFD93D", "#45B7D1", "#96CEB4",
           "#E57373", "#64B5F6", "#FFB74D", "#81C784"]
bars = ax2.barh(y_pos, counts, color=palette[:len(counts)], edgecolor="white")
ax2.set_yticks(list(y_pos))
ax2.set_yticklabels(cn_labels, fontsize=12)
ax2.invert_yaxis()  # 最大的在最上面
ax2.set_xlabel("边的数量（条）", fontsize=12)
ax2.set_title(f"全图边类型分布（共 {G.number_of_edges():,} 条边，9 种类型）",
              fontsize=14, pad=12)
# 在条形末端标注精确数值
for bar, cnt in zip(bars, counts):
    ax2.text(bar.get_width() + max(counts) * 0.01, bar.get_y() + bar.get_height() / 2,
             f"{cnt:,}", va="center", fontsize=10)
ax2.set_xlim(0, max(counts) * 1.12)
plt.tight_layout()
safe_savefig(fig2, OUT / "edge_type_distribution.png", dpi=150, bbox_inches="tight")
print(f"边类型分布图已保存 → {OUT / 'edge_type_distribution.png'}")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 节点类型分布 —— 饼图（中文标签 + 数量标注）
# ═══════════════════════════════════════════════════════════════════════════════
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
labels_cn = [f"{NTYPE_CN.get(l, l)}" for l in nt_counts.keys()]
sizes = list(nt_counts.values())
colors = [NTYPE_COLOR.get(l, "#ccc") for l in nt_counts.keys()]

fig3, ax3 = plt.subplots(figsize=(9, 8))
wedges, texts, autotexts = ax3.pie(
    sizes, labels=labels_cn, colors=colors,
    autopct=lambda pct: f"{pct:.1f}%\n({int(round(pct/100*sum(sizes)))})",
    startangle=140, textprops={"fontsize": 12},
    wedgeprops={"edgecolor": "white", "linewidth": 2})
# 放大百分比文字
for autotext in autotexts:
    autotext.set_fontsize(10)
    autotext.set_fontweight("bold")
ax3.set_title(f"全图节点类型分布（共 {G.number_of_nodes():,} 个节点，5 种类型）",
              fontsize=14, pad=12)
plt.tight_layout()
safe_savefig(fig3, OUT / "node_type_distribution.png", dpi=150, bbox_inches="tight")
print(f"节点类型分布图已保存 → {OUT / 'node_type_distribution.png'}")

print(f"\n✅ 两张分布图已生成 → {OUT}")
print("  1) edge_type_distribution.png  — 边类型分布（水平条形图，中文标签）")
print("  2) node_type_distribution.png  — 节点类型分布（饼图，中文标签+数量）")
print(f"\n  注：原 subgraph_positive_student.png 因过于密集已删除，")
print(f"     请查看 visualize_graph_v2.py 生成的 subgraph_clean.png（分层布局，清晰易读）")
