"""
图可视化 v2：精简 + 交互式
  - 方案 A：交互式 HTML（pyvis，浏览器里拖拽/缩放/悬停看详情，最清晰）
  - 方案 B：精简静态 PNG（matplotlib，砍掉噪声边，只保留 12-15 节点 + 4 种核心边）

运行：python src/visualize_graph_v2.py
产出：algorithm/data/processed/graph_viz/subgraph_interactive.html + subgraph_clean.png
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT = Path("algorithm/data/processed/graph_viz")
OUT.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. 载入图
# ═══════════════════════════════════════════════════════════════════════════════
print("Loading graph...")
with open("algorithm/data/processed/graph_nx.json", encoding="utf-8") as f:
    G = nx.MultiDiGraph(nx.node_link_graph(json.load(f), edges="links"))
print(f"  {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

NTYPE_COLOR = {
    "student":         "#FF6B6B",
    "course":          "#4ECDC4",
    "class_session":   "#45B7D1",
    "knowledge_point": "#FFD93D",
    "resource":        "#96CEB4",
}
# 只保留这 4 种有业务意义的核心边
KEEP_ETYPES = {"enrolled_in", "attended", "attended_with", "scored_on"}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 构造精简子图：目标学生 + 3 课 + 4 课次 + 4 知识点 + 2 同班同学 = 14 节点
#    砍掉 resource 和 interacts_with（噪声边，不影响理解业务结构）
# ═══════════════════════════════════════════════════════════════════════════════
target = pd.read_parquet("algorithm/data/interim/final_result.parquet")
positive_stus = target[target["is_positive"] == 1]["stu_id"].unique()
stu_id = str(positive_stus[0])
print(f"\n正例目标学生: {stu_id}")

def ntype_of(n):
    ntype = G.nodes[n].get("node_type", n.split("_")[0])
    return {"stu": "student", "crs": "course", "cs": "class_session",
            "kp": "knowledge_point", "res": "resource"}.get(ntype, ntype)

# 筛选核心边：只留 KEEP_ETYPES
core_edges = [(u, v, k, d) for u, v, k, d in G.edges(keys=True, data=True)
              if d.get("edge_type") in KEEP_ETYPES]

# 目标学生直接关联的节点（每种类型限 3-4 个）
pick = {"course": [], "class_session": [], "knowledge_point": [], "student": []}
for u, v, k, d in core_edges:
    n1, n2 = (u, v) if u == stu_id else (v, u)
    if n1 != stu_id:
        continue
    t = ntype_of(n2)
    if t in pick and len(pick[t]) < {"course": 3, "class_session": 4,
                                     "knowledge_point": 4, "student": 2}[t]:
        pick[t].append(n2)

nodes_keep = {stu_id}
for v in pick.values():
    nodes_keep |= set(v)

# 同班同学的直接连边也加进来（attended_with → peer 之间的边）
for u, v, k, d in core_edges:
    if u in pick.get("student", []) and v in nodes_keep:
        nodes_keep.add(u)
    if v in pick.get("student", []) and u in nodes_keep:
        nodes_keep.add(v)

G_sub = G.subgraph(nodes_keep).copy()
# 再二次过滤边：只保留 KEEP_ETYPES
for u, v, k in list(G_sub.edges(keys=True)):
    etype = G_sub[u][v][k].get("edge_type")
    if etype not in KEEP_ETYPES:
        G_sub.remove_edge(u, v, k)

print(f"  精简子图: {G_sub.number_of_nodes()} nodes, {G_sub.number_of_edges()} edges")


# ═══════════════════════════════════════════════════════════════════════════════
# 方案 A：交互式 HTML（pyvis）
# ═══════════════════════════════════════════════════════════════════════════════
print("\n方案 A：生成交互式 HTML...")
from pyvis.network import Network

net = Network(height="720px", width="100%", bgcolor="#ffffff", font_color="#222",
              heading=f"正例学生 {stu_id} 的局部异质子图（可拖拽/缩放/悬停）")

# 加节点
for n in G_sub.nodes():
    t = ntype_of(n)
    color = NTYPE_COLOR.get(t, "#aaa")
    size = 45 if n == stu_id else 25
    label = stu_id[-6:] if n == stu_id else (
        n.replace("crs_", "") if t == "course"
        else n.split("_")[-1] if t == "class_session"
        else n.split("_", 2)[-1] if t == "knowledge_point"
        else n[-6:]
    )
    title = (f"<b>{n}</b><br>"
             f"类型: {t}<br>"
             + ("⚠️ 目标正例学生" if n == stu_id else ""))
    net.add_node(n, label=label, color=color, size=size, title=title,
                 borderWidth=3 if n == stu_id else 1,
                 borderColor="#c0392b" if n == stu_id else "#ffffff",
                 font={"color": "#ffffff", "size": 11, "strokeWidth": 2,
                       "strokeColor": "#222222"})

# 加边，按 edge_type 染色
ETYPE_EDGE_COLOR = {
    "enrolled_in":   "#666666",
    "attended":      "#c0392b",   # 出勤：红色粗线
    "attended_with": "#3498db",   # 同班同侪：蓝色虚线
    "scored_on":     "#27ae60",   # 成绩关联：绿色
}
ETYPE_LABEL = {
    "enrolled_in": "选课",
    "attended": "出勤",
    "attended_with": "同班同侪",
    "scored_on": "成绩",
}
for u, v, k in G_sub.edges(keys=True):
    etype = G_sub[u][v][k].get("edge_type", "?")
    net.add_edge(u, v, color=ETYPE_EDGE_COLOR.get(etype, "#999"),
                 width=3 if etype == "attended" else 1.5,
                 dashes=(6, 4) if etype == "attended_with" else False,
                 title=ETYPE_LABEL.get(etype, etype))

# 物理参数：让节点分布均匀
net.set_options("""
var options = {
  "physics": { "barnesHut": { "gravitationalConstant": -3000, "centralGravity": 0.8, "springLength": 150 } },
  "interaction": { "hover": true, "tooltipDelay": 100 },
  "edges": { "smooth": { "type": "continuous" }, "arrows": { "enabled": false } },
  "nodes": { "shape": "circle" }
}
""")

html_path = OUT / "subgraph_interactive.html"
net.write_html(str(html_path), notebook=False)
print(f"  ✅ 交互式 HTML → {html_path}")
print(f"     用浏览器（Chrome/Edge）双击打开即可，可拖拽/缩放/悬停看节点详情")


# ═══════════════════════════════════════════════════════════════════════════════
# 方案 B：精简静态 PNG（matplotlib + kamada_kawai_layout，均匀分布）
# ═══════════════════════════════════════════════════════════════════════════════
print("\n方案 B：生成精简静态 PNG...")
fig, ax = plt.subplots(figsize=(12, 10))

pos = nx.kamada_kawai_layout(G_sub)
pos[stu_id] = (0.0, 0.0)

# 按 edge_type 分组画
edge_groups: dict[str, list] = {}
for u, v, k in G_sub.edges(keys=True):
    etype = G_sub[u][v][k].get("edge_type")
    edge_groups.setdefault(etype, []).append((u, v))

for etype, edges in edge_groups.items():
    nx.draw_networkx_edges(G_sub, pos, edgelist=edges,
                           edge_color=ETYPE_EDGE_COLOR.get(etype, "#999"),
                           width=3.0 if etype == "attended" else 1.8,
                           style=("dashed" if etype == "attended_with" else "solid"),
                           alpha=0.85, arrows=False, ax=ax)

# 节点（放大，让白色字能完整显示在球里）
for ntype, color in NTYPE_COLOR.items():
    nlist = [n for n in G_sub.nodes() if ntype_of(n) == ntype]
    if not nlist:
        continue
    sizes = [2200 if n == stu_id else 1000 for n in nlist]
    nx.draw_networkx_nodes(G_sub, pos, nodelist=nlist,
                           node_color=color, node_size=sizes,
                           edgecolors="#222222", linewidths=1.2, ax=ax)

# 标签：白色字居中在球里
labels = {}
for n in G_sub.nodes():
    if n == stu_id:
        labels[n] = stu_id[-6:]     # 去掉 ★ 让文字更短，目标用红色边框已突出
    elif ntype_of(n) == "course":
        labels[n] = n.replace("crs_", "")
    elif ntype_of(n) == "class_session":
        labels[n] = n.split("_")[-1]
    elif ntype_of(n) == "knowledge_point":
        labels[n] = n.split("_", 2)[-1]
    else:
        labels[n] = n[-6:]
nx.draw_networkx_labels(G_sub, pos, labels, font_size=11,
                        font_color="#ffffff", font_weight="bold",
                        horizontalalignment="center",
                        verticalalignment="center", ax=ax)

# 图例
from matplotlib.lines import Line2D
node_handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor=c,
                       markersize=14, label=f"● {t}")
                for t, c in NTYPE_COLOR.items()
                if any(ntype_of(n) == t for n in G_sub.nodes())]
edge_handles = []
for etype in ["attended", "attended_with", "scored_on", "enrolled_in"]:
    if etype in edge_groups:
        edge_handles.append(Line2D(
            [0], [0], color=ETYPE_EDGE_COLOR[etype],
            ls="dashed" if etype == "attended_with" else "solid",
            lw=3 if etype == "attended" else 1.8,
            label=f"{ETYPE_LABEL.get(etype, etype)}"
        ))
ax.legend(handles=node_handles + edge_handles, loc="lower right",
          fontsize=9, framealpha=0.92)

ax.set_title(f"正例学生 {stu_id} · 核心业务子图\n"
             f"节点={G_sub.number_of_nodes()}, 边={G_sub.number_of_edges()}  "
             f"(仅保留 4 种核心边，砍掉噪声资源/交互边)",
             fontsize=13, pad=10)
ax.axis("off")
plt.tight_layout()
plt.savefig(OUT / "subgraph_clean.png", dpi=180, bbox_inches="tight")
print(f"  ✅ 精简静态 PNG → {OUT / 'subgraph_clean.png'}")

print(f"\n🎉 两个版本都在 {OUT}")
print(f"   ① subgraph_interactive.html  ← 浏览器双击打开，可拖拽缩放（最清晰）")
print(f"   ② subgraph_clean.png        ← 静态图，节点/边已精简")
