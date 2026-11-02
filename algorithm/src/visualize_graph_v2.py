"""
图可视化 v2：精简 + 交互式
  - 方案 A：交互式 HTML（pyvis，浏览器里拖拽/缩放/悬停看详情）
  - 方案 B：精简静态 PNG（matplotlib，分层布局，仅核心边）

运行：python algorithm/src/visualize_graph_v2.py
产出：
  algorithm/data/processed/graph_viz/subgraph_interactive.html
  algorithm/data/processed/graph_viz/subgraph_clean.png
"""
import json
import os
import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # 非交互后端，避免文件被占用报错
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

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
}
NTYPE_COLOR = {
    "student": "#FF6B6B",
    "course": "#4ECDC4",
    "class_session": "#45B7D1",
    "knowledge_point": "#FFD93D",
    "resource": "#96CEB4",
}
ETYPE_EDGE_COLOR = {
    "enrolled_in": "#666666",
    "attended": "#c0392b",
    "attended_with": "#3498db",
    "scored_on": "#27ae60",
}
# 只保留这 4 种有业务意义的核心边
KEEP_ETYPES = {"enrolled_in", "attended", "attended_with", "scored_on"}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 载入图
# ═══════════════════════════════════════════════════════════════════════════════
print("Loading graph...")
with open("algorithm/data/processed/graph_nx.json", encoding="utf-8") as f:
    G = nx.MultiDiGraph(nx.node_link_graph(json.load(f), edges="links"))
print(f"  {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")


def ntype_of(n):
    ntype = G.nodes[n].get("node_type", n.split("_")[0])
    return {"stu": "student", "crs": "course", "cs": "class_session",
            "kp": "knowledge_point", "res": "resource"}.get(ntype, ntype)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 构造精简子图：目标学生 + 课程 + 课次 + 知识点 + 同班同学
#    砍掉 resource 和 interacts_with（噪声边）
# ═══════════════════════════════════════════════════════════════════════════════
target = pd.read_parquet("algorithm/data/interim/final_result.parquet")
positive_stus = target[target["is_positive"] == 1]["stu_id"].unique()
stu_id = str(positive_stus[0])
print(f"\n正例目标学生: {stu_id}")

# 筛选核心边：只留 KEEP_ETYPES
core_edges = [(u, v, k, d) for u, v, k, d in G.edges(keys=True, data=True)
              if d.get("edge_type") in KEEP_ETYPES]

# 目标学生直接关联的节点（每种类型限量）
pick = {"course": [], "class_session": [], "knowledge_point": [], "student": []}
LIMITS = {"course": 3, "class_session": 4, "knowledge_point": 4, "student": 2}
for u, v, k, d in core_edges:
    n1, n2 = (u, v) if u == stu_id else (v, u)
    if n1 != stu_id:
        continue
    t = ntype_of(n2)
    if t in pick and len(pick[t]) < LIMITS[t]:
        pick[t].append(n2)

nodes_keep = {stu_id}
for v in pick.values():
    nodes_keep |= set(v)

# 同班同学的连边
for u, v, k, d in core_edges:
    if u in pick.get("student", []) and v in nodes_keep:
        nodes_keep.add(u)
    if v in pick.get("student", []) and u in nodes_keep:
        nodes_keep.add(v)

G_sub = G.subgraph(nodes_keep).copy()
# 二次过滤边
for u, v, k in list(G_sub.edges(keys=True)):
    etype = G_sub[u][v][k].get("edge_type")
    if etype not in KEEP_ETYPES:
        G_sub.remove_edge(u, v, k)

print(f"  精简子图: {G_sub.number_of_nodes()} nodes, {G_sub.number_of_edges()} edges")


# ═══════════════════════════════════════════════════════════════════════════════
# 标签生成：课次带上课程前缀，避免 wk0 重复
# ═══════════════════════════════════════════════════════════════════════════════
def make_label(n):
    """生成中文标签，课次带课程前缀避免歧义"""
    t = ntype_of(n)
    if n == stu_id:
        return "目标学生"
    if t == "course":
        return n.replace("crs_", "")
    if t == "class_session":
        # cs_AAA_wk0 → AAA·第0周
        parts = n.split("_")
        if len(parts) >= 3:
            return f"{parts[1]}·{parts[-1]}"
        return n.split("_")[-1]
    if t == "knowledge_point":
        # kp_AAA_09 → AAA·知识点09
        parts = n.split("_")
        if len(parts) >= 3:
            return f"{parts[1]}·KP{parts[-1]}"
        return n.split("_", 2)[-1]
    if t == "student":
        # 同班同学：显示后4位ID区分
        return f"同学·{n[-4:]}"
    return n[-6:]


# ═══════════════════════════════════════════════════════════════════════════════
# 方案 A：交互式 HTML（pyvis）
# ═══════════════════════════════════════════════════════════════════════════════
print("\n方案 A：生成交互式 HTML...")
from pyvis.network import Network

# heading 留空，避免 pyvis 重复渲染标题
net = Network(height="680px", width="100%", bgcolor="#ffffff", font_color="#222",
              heading="")

for n in G_sub.nodes():
    t = ntype_of(n)
    color = NTYPE_COLOR.get(t, "#aaa")
    size = 50 if n == stu_id else 28
    label = make_label(n)
    title = (f"<b>{n}</b><br>"
             f"类型: {NTYPE_CN.get(t, t)}<br>"
             f"标签: {label}<br>"
             + ("⚠️ 目标正例学生" if n == stu_id else ""))
    net.add_node(n, label=label, color=color, size=size, title=title,
                 borderWidth=3 if n == stu_id else 1,
                 borderColor="#c0392b" if n == stu_id else "#ffffff",
                 font={"color": "#ffffff", "size": 12, "strokeWidth": 2,
                       "strokeColor": "#222222"})

for u, v, k in G_sub.edges(keys=True):
    etype = G_sub[u][v][k].get("edge_type", "?")
    net.add_edge(u, v, color=ETYPE_EDGE_COLOR.get(etype, "#999"),
                 width=3.5 if etype == "attended" else 1.8,
                 dashes=(8, 5) if etype == "attended_with" else False,
                 title=ETYPE_CN.get(etype, etype))

# 物理参数优化：更大斥力，更长弹簧，节点更分散
net.set_options("""
var options = {
  "physics": {
    "barnesHut": {
      "gravitationalConstant": -8000,
      "centralGravity": 0.3,
      "springLength": 220,
      "springConstant": 0.04,
      "damping": 0.3
    },
    "stabilization": { "iterations": 200 }
  },
  "interaction": { "hover": true, "tooltipDelay": 100, "zoomView": true },
  "edges": { "smooth": { "type": "continuous" }, "arrows": { "enabled": false } },
  "nodes": { "shape": "circle", "font": { "face": "Microsoft YaHei" } }
}
""")

html_path = OUT / "subgraph_interactive.html"
net.write_html(str(html_path), notebook=False)

# 修复 pyvis 标题重复问题：手动在 HTML 顶部插入单一标题
html_text = html_path.read_text(encoding="utf-8")
title_html = (
    '<div style="text-align:center;padding:12px 0 4px;font-family:Microsoft YaHei,sans-serif;">'
    f'<h2 style="margin:0;color:#333;">正例学生 {stu_id} 的局部异质子图</h2>'
    '<p style="margin:4px 0 0;color:#777;font-size:14px;">'
    '可拖拽节点 · 滚轮缩放 · 悬停查看详情 · 颜色含义：红=学生 青=课程 蓝=课次 黄=知识点</p>'
    '</div>\n'
)
# 在 <body> 后插入标题
html_text = html_text.replace("<body>", "<body>\n" + title_html, 1)
html_path.write_text(html_text, encoding="utf-8")

print(f"  ✅ 交互式 HTML → {html_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# 方案 B：精简静态 PNG（分层布局，从左到右）
# ═══════════════════════════════════════════════════════════════════════════════
print("\n方案 B：生成精简静态 PNG（分层布局）...")
fig, ax = plt.subplots(figsize=(16, 10))

# 分层：x 坐标按类型
LAYER_X = {"student": 0.0, "course": 2.5, "class_session": 5.5,
           "knowledge_point": 8.5}
peer_students = [n for n in G_sub.nodes()
                 if ntype_of(n) == "student" and n != stu_id]

pos = {}
# 目标学生居中
pos[stu_id] = (0.0, 0.0)

# 同侪放最右列
if peer_students:
    n = len(peer_students)
    for i, pn in enumerate(peer_students):
        pos[pn] = (12.0, (i - (n - 1) / 2) * 3.0)

# 其他类型按层分布，加大间距避免重叠
for ntype, x in LAYER_X.items():
    if ntype == "student":
        continue
    nodes = [n for n in G_sub.nodes() if ntype_of(n) == ntype]
    n = len(nodes)
    if n == 0:
        continue
    spacing = 2.2 if n <= 3 else 1.8
    for i, node in enumerate(nodes):
        pos[node] = (x, (i - (n - 1) / 2) * spacing)

# 画边
edge_groups = {}
for u, v, k in G_sub.edges(keys=True):
    etype = G_sub[u][v][k].get("edge_type")
    edge_groups.setdefault(etype, []).append((u, v))

for etype, edges in edge_groups.items():
    nx.draw_networkx_edges(G_sub, pos, edgelist=edges,
                           edge_color=ETYPE_EDGE_COLOR.get(etype, "#999"),
                           width=3.5 if etype == "attended" else 2.0,
                           style=("dashed" if etype == "attended_with" else "solid"),
                           alpha=0.85, arrows=False, ax=ax)

# 画节点
for ntype, color in NTYPE_COLOR.items():
    nlist = [n for n in G_sub.nodes() if ntype_of(n) == ntype]
    if not nlist:
        continue
    sizes = [2400 if n == stu_id else 1300 for n in nlist]
    nx.draw_networkx_nodes(G_sub, pos, nodelist=nlist,
                           node_color=color, node_size=sizes,
                           edgecolors="#222222", linewidths=1.5, ax=ax)

# 标签（中文，白色加粗居中）
labels = {n: make_label(n) for n in G_sub.nodes()}
nx.draw_networkx_labels(G_sub, pos, labels, font_size=10,
                        font_color="#ffffff", font_weight="bold",
                        horizontalalignment="center",
                        verticalalignment="center", ax=ax)

# 图例：中文
from matplotlib.lines import Line2D
node_handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor=c,
                       markersize=14, label=f"● {NTYPE_CN.get(t, t)}")
                for t, c in NTYPE_COLOR.items()
                if any(ntype_of(n) == t for n in G_sub.nodes())]
edge_handles = []
for etype in ["attended", "attended_with", "scored_on", "enrolled_in"]:
    if etype in edge_groups:
        edge_handles.append(Line2D(
            [0], [0], color=ETYPE_EDGE_COLOR[etype],
            ls="dashed" if etype == "attended_with" else "solid",
            lw=3.5 if etype == "attended" else 2.0,
            label=ETYPE_CN.get(etype, etype)
        ))
ax.legend(handles=node_handles + edge_handles, loc="lower right",
          fontsize=11, framealpha=0.95, title="图例")

ax.set_title(f"正例学生 {stu_id} · 核心业务子图（分层布局）\n"
             f"节点={G_sub.number_of_nodes()}，边={G_sub.number_of_edges()}  "
             f"（仅保留 4 种核心关系）",
             fontsize=14, pad=12)

# 列标签
col_labels = {0.0: "目标学生", 2.5: "课程", 5.5: "课次",
              8.5: "知识点", 12.0: "同班同侪"}
for x, label in col_labels.items():
    ax.text(x, 5.2, label, ha="center", fontsize=12, color="#555",
            fontweight="bold")

ax.axis("off")
ax.set_xlim(-1.8, 14.5)
ax.set_ylim(-5.5, 6.0)
plt.tight_layout()
safe_savefig(fig, OUT / "subgraph_clean.png", dpi=180, bbox_inches="tight")
print(f"  ✅ 精简静态 PNG → {OUT / 'subgraph_clean.png'}")

print(f"\n🎉 两个版本都在 {OUT}")
print(f"   ① subgraph_interactive.html  ← 浏览器双击打开，可拖拽缩放")
print(f"   ② subgraph_clean.png        ← 静态分层图，中文标签")
