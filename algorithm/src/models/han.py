"""M4 HAN：异质图两级注意力（手写，不依赖 torch_geometric）。

节点：student / course / class_session / knowledge_point / resource（5 类）
关系：9 类原始边 + 反向边（学生要接收课程/课次/知识点/资源的消息，必须有反向关系）
结构：各类型线性投影 → 关系内邻居级 attention → 关系级（语义）attention → LayerNorm + 残差，2 层
采样：固定邻居表（每个关系、每个目标节点 K 个，可重复采样），训练/推理共用，保证可复现
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

KEYS = ["stu_id", "course_code", "semester"]
WEEK_RE = re.compile(r"wk(\d+)")

# (relation, 源类型, 目标类型)。目标类型 = 消息汇聚到的节点类型。
RELATIONS = [
    ("rev_enrolled_in", "course", "student"),
    ("rev_attended", "class_session", "student"),
    ("attended_with", "student", "student"),
    ("rev_scored_on", "knowledge_point", "student"),
    ("rev_interacts_with", "resource", "student"),
    ("enrolled_in", "student", "course"),
    ("rev_has_kt", "knowledge_point", "course"),
    ("has_kt", "course", "knowledge_point"),
    ("prereq_of", "knowledge_point", "knowledge_point"),
    ("rev_prereq_of", "knowledge_point", "knowledge_point"),
    ("rev_supports", "resource", "knowledge_point"),
    ("supports", "resource", "knowledge_point"),
    ("scored_on", "student", "knowledge_point"),
    ("attended", "student", "class_session"),
    ("interacts_with", "student", "resource"),
]

EDGE_FILES = {
    "enrolled_in": ("edge_enrolled_in.parquet", "student", "course"),
    "attended": ("edge_attended.parquet", "student", "class_session"),
    "attended_with": ("edge_attended_with.parquet", "student", "student"),
    "scored_on": ("edge_scored_on.parquet", "student", "knowledge_point"),
    "interacts_with": ("edge_interacts_with.parquet", "student", "resource"),
    "has_kt": ("edge_has_kt.parquet", "course", "knowledge_point"),
    "prereq_of": ("edge_prereq_of.parquet", "knowledge_point", "knowledge_point"),
    "supports": ("edge_supports.parquet", "resource", "knowledge_point"),
    "course_prereq": ("edge_course_prereq.parquet", "course", "course"),
}

STATIC = [
    "gender", "entrance_score", "past_retake_count", "special_flag", "credits", "elective",
    "major_A Level or Equivalent", "major_HE Qualification", "major_Lower Than A Level",
    "major_No Formal quals", "major_Post Graduate Qualification",
]
STUDENT_BEHAV = ["mean_attendance", "mean_quiz", "mean_hw", "mean_absent"]
OBS_HORIZON = 8   # 学生行为特征只汇总到第 8 周（与 M0 基线快照口径一致，避免未来信息泄漏）


def _onehot(values: pd.Series, prefix: str, categories: list[str]) -> np.ndarray:
    return np.c_[tuple((values == c).to_numpy(dtype="float32") for c in categories)]


def build_graph_inputs(processed: Path) -> dict:
    """构造 5 类节点特征、关系边、学生标签与学生级切分。"""
    gdir = processed / "graph"
    weekly = pd.read_parquet(processed / "tft_weekly.parquet")
    kp_dim = pd.read_parquet(processed / "dim_knowledge_point.parquet")

    dim_student = pd.read_parquet(gdir / "dim_student.parquet")
    dim_course = pd.read_parquet(gdir / "dim_course.parquet")
    dim_session = pd.read_parquet(gdir / "dim_class_session.parquet")
    dim_kp = pd.read_parquet(gdir / "dim_knowledge_point.parquet")
    dim_res = pd.read_parquet(gdir / "dim_resource.parquet")

    # ---- student：静态 11 + 跨课程汇总行为 4 ----
    w = weekly.sort_values(KEYS + ["week"])
    w8 = w[w["week"] <= OBS_HORIZON]          # 只用第 8 周之前的数据，避免用到事件发生后的信息
    first = w8.groupby("stu_id", as_index=False).first()
    behav = w8.groupby("stu_id", as_index=False).agg(
        mean_attendance=("attendance_rate", "mean"),
        mean_quiz=("quiz_accuracy_cum", "mean"),
        mean_hw=("hw_score_cum", "mean"),
        mean_absent=("absent_count_cum", "mean"),
    )
    lab = w.groupby("stu_id", as_index=False).agg(is_positive=("is_positive", "max"))
    ev = (w.groupby("stu_id")["risk_event_time"]
          .agg(lambda s: min([int(v) for v in s if v > 0], default=-1))
          .reset_index(name="min_event"))
    stu = (first[["stu_id"] + STATIC + ["split_group"]].merge(behav, on="stu_id")
           .merge(lab, on="stu_id").merge(ev, on="stu_id"))
    stu_ids = dim_student["node_id"].to_numpy()
    stu = stu.set_index("stu_id").reindex(stu_ids)
    stu_x = stu[STATIC + STUDENT_BEHAV]
    stu_x = stu_x.fillna(stu_x.median(numeric_only=True)).to_numpy(dtype="float32")

    # ---- course：课程 one-hot + 学分 + 选修 ----
    codes = sorted(dim_course["node_id"].str.replace("crs_", "", regex=False).unique().tolist())
    dim_course = dim_course.assign(code=dim_course["node_id"].str.replace("crs_", "", regex=False))
    courses = (weekly.drop_duplicates("course_code")[["course_code", "credits", "elective"]]
               .rename(columns={"course_code": "code"}))
    c = dim_course[["node_id", "code"]].merge(courses, on="code", how="left")
    crs_x = np.c_[_onehot(c["code"], "crs", codes),
                  c[["credits", "elective"]].fillna(0).astype("float32").to_numpy()]

    # ---- class_session：周次 + 学期 one-hot + 课程 one-hot ----
    sems = sorted(dim_session["semester"].unique().tolist())
    sess_x = np.c_[
        dim_session["node_id"].str.extract(WEEK_RE)[0].astype("float32").to_numpy()[:, None],
        _onehot(dim_session["semester"], "sem", sems),
        _onehot(dim_session["course_code"], "crs", codes),
    ]

    # ---- knowledge_point：难度 + 建议周 + 课程 one-hot ----
    k = dim_kp[["node_id", "course_code"]].merge(
        kp_dim[["kp_id", "difficulty_proxy", "suggested_week"]],
        left_on="node_id", right_on="kp_id", how="left")
    kp_x = np.c_[k[["difficulty_proxy", "suggested_week"]].fillna(0).astype("float32").to_numpy(),
                 _onehot(k["course_code"], "crs", codes)]

    # ---- resource：资源类型 one-hot + 课程 one-hot ----
    rtypes = sorted(dim_res["resource_type"].unique().tolist())
    res_x = np.c_[_onehot(dim_res["resource_type"], "rt", rtypes),
                  _onehot(dim_res["course_code"], "crs", codes)]

    ids = {
        "student": stu_ids,
        "course": dim_course["node_id"].to_numpy(),
        "class_session": dim_session["node_id"].to_numpy(),
        "knowledge_point": dim_kp["node_id"].to_numpy(),
        "resource": dim_res["node_id"].to_numpy(),
    }
    feats = {"student": stu_x, "course": crs_x, "class_session": sess_x,
             "knowledge_point": kp_x, "resource": res_x}
    index = {t: {v: i for i, v in enumerate(ids[t])} for t in ids}

    return {
        "ids": ids, "index": index, "feats": feats, "gdir": gdir,
        "labels": stu["is_positive"].fillna(0).to_numpy().astype("float32"),
        "event": stu["min_event"].fillna(-1).to_numpy().astype("int64"),
        "split": stu["split_group"].fillna("train").to_numpy(),
        "kp_name": dict(zip(kp_dim["kp_id"], kp_dim["name"])),
    }


def build_relation_tables(inputs: dict, k: int = 5, seed: int = 42) -> dict[str, np.ndarray]:
    """relation → 邻居表 (n_target, k)，元素为源类型中的行号，-1 表示空位。"""
    gdir, index, ids = inputs["gdir"], inputs["index"], inputs["ids"]
    tables = {}
    for name, (fname, src_t, dst_t) in EDGE_FILES.items():
        path = gdir / fname
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        if len(df) == 0:
            continue
        s = df["src"].map(index[src_t])
        d = df["dst"].map(index[dst_t])
        ok = s.notna() & d.notna()
        s, d = s[ok].to_numpy("int64"), d[ok].to_numpy("int64")
        tables[name] = neighbor_table(len(ids[dst_t]), s, d, k, seed)
        tables[f"rev_{name}"] = neighbor_table(len(ids[src_t]), d, s, k, seed + 1)
    return tables


def neighbor_table(n_target: int, src_rows: np.ndarray, dst_rows: np.ndarray, k: int,
                   seed: int = 42) -> np.ndarray:
    """按目标节点分组后随机抽 k 个源节点（可重复），空位填 -1。"""
    rng = np.random.default_rng(seed)
    order = np.argsort(dst_rows, kind="stable")
    d_sorted, s_sorted = dst_rows[order], src_rows[order]
    counts = np.bincount(d_sorted, minlength=n_target)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    table = np.full((n_target, k), -1, dtype=np.int64)
    for j in range(k):
        valid = counts > j
        pos = starts[valid] + rng.integers(0, counts[valid])
        table[valid, j] = s_sorted[pos]
    return table


class HANLayer(nn.Module):
    """一层：关系内邻居 attention → 关系级语义 attention → LayerNorm + 残差。"""

    def __init__(self, relations: list[tuple[str, str, str]], hidden: int):
        super().__init__()
        self.relations = relations
        self.W = nn.ModuleDict({r[0]: nn.Linear(hidden, hidden, bias=False) for r in relations})
        self.att = nn.ModuleDict({r[0]: nn.Linear(2 * hidden, 1, bias=False) for r in relations})
        self.q = nn.Linear(hidden, 1, bias=False)
        self.act = nn.LeakyReLU(0.2)
        self.norm = nn.LayerNorm(hidden)
        self.res = nn.ModuleDict({t: nn.Linear(hidden, hidden, bias=False)
                                  for t in sorted({r[2] for r in relations})})

    def forward(self, h, target_type, target_ids, tables, edge_scale=None):
        x_i = h[target_type][target_ids]
        aggs, used, scales = [], [], []
        for etype, src_t, dst_t in self.relations:
            if dst_t != target_type or etype not in tables:
                continue
            nb = tables[etype][target_ids]
            valid = nb >= 0
            if not valid.any():
                continue
            h_j = h[src_t][nb.clip(0)]
            proj_i = self.W[etype](x_i).unsqueeze(1).expand(-1, nb.size(1), -1)
            score = self.act(self.att[etype](torch.cat([proj_i, h_j], -1)).squeeze(-1))
            score = score.masked_fill(~valid, -1e9)
            alpha = torch.softmax(score, dim=-1)
            if edge_scale is not None and etype in edge_scale:
                alpha = alpha * edge_scale[etype].unsqueeze(0)   # M6 边掩码
            aggs.append((alpha.unsqueeze(-1) * h_j).sum(1))
            used.append(etype)
            scales.append(alpha)
        if not aggs:
            return x_i, None
        stack = torch.stack(aggs, 1)
        beta = torch.softmax(self.q(torch.tanh(stack)).squeeze(-1), dim=1)
        out = self.norm((beta.unsqueeze(-1) * stack).sum(1) + self.res[target_type](x_i))
        return out, {"relations": used, "semantic": beta, "alpha": scales}


class HAN(nn.Module):
    def __init__(self, in_dims: dict[str, int], hidden: int = 64):
        super().__init__()
        self.proj = nn.ModuleDict({t: nn.Linear(d, hidden) for t, d in in_dims.items()})
        self.layer1 = HANLayer(RELATIONS, hidden)
        self.layer2 = HANLayer(RELATIONS, hidden)
        self.head = nn.Sequential(nn.Linear(hidden, 32), nn.ReLU(), nn.Dropout(0.2),
                                  nn.Linear(32, 1))

    def forward(self, feats, stu_ids, tables, neighbor_ids, edge_scale=None, return_attn=False):
        h0 = {t: self.proj[t](feats[t]) for t in feats}
        h1 = dict(h0)
        for t, ids_t in neighbor_ids.items():
            if len(ids_t) == 0:
                continue
            ids_t_t = torch.as_tensor(ids_t)
            out, _ = self.layer1(h0, t, ids_t_t, tables, edge_scale)
            h1[t] = h0[t].index_copy(0, ids_t_t, out)
        stu_t = torch.as_tensor(stu_ids)
        out2, info = self.layer2(h1, "student", stu_t, tables, edge_scale)
        logit = self.head(out2).squeeze(-1)
        return (logit, h1, info) if return_attn else logit


def neighbors_of_students(stu_ids: np.ndarray, tables: dict, ids: dict) -> dict[str, np.ndarray]:
    """batch 学生 1 跳邻居（按类型去重），供第一层传播使用。"""
    out = {t: set() for t in ids}
    for etype, src_t, dst_t in RELATIONS:
        if dst_t != "student" or etype not in tables:
            continue
        nb = tables[etype][stu_ids]
        out[src_t].update(nb[nb >= 0].tolist())
    return {t: np.fromiter(sorted(v), dtype=np.int64) for t, v in out.items()}
