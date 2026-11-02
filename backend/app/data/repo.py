"""数据仓库层：把 Parquet / 演示数据整理成接口要的形状。

一次加载，进程内缓存。数据量在 3 万学生 / 百万行级别时内存可承受；
真要上 PostgreSQL，只需替换本文件的方法实现，API 层不动。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import settings
from app.core.response import ApiError, page_payload
from app.data import loader

STATUS_TO_LEVEL = {
    "优秀": 0, "良好": 0, "合格": 0,
    "不合格": 1, "缺考": 1,
    "退学": 2, "休学": 2, "学业警示": 2,
}
LEVEL_TEXT = {0: "正常", 1: "警惕", 2: "高危"}


def level_text(level: int) -> str:
    return LEVEL_TEXT.get(int(level), "正常")


def make_warn_id(stu_id: str, course_code: str, semester: str, week: int) -> str:
    """确定性预警编号：同一 (学生, 课程, 学期, 周) 永远得到同一个 ID，重复跑批不会重复入库。"""

    key = f"{stu_id}|{course_code}|{semester}|{week}"
    return "W" + hashlib.md5(key.encode("utf-8")).hexdigest()[:9].upper()


def semester_start(semester: str) -> date:
    """学期起始日。'2024F' -> 2024-09-02，'2025S' -> 2025-02-24（与 schema.yaml 一致）。"""

    text = str(semester)
    year = int(text[:4])
    return date(year, 9, 2) if text[-1].upper() == "F" else date(year, 2, 24)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


class Repo:
    def __init__(self) -> None:
        self.source, self.raw = loader.load_all()
        self._risk_cache: dict[str, pd.DataFrame] = {}
        self._warn_cache: dict[str, pd.DataFrame] = {}
        self._graph_raw: dict[str, Any] | None = None
        self._graph_loaded = False

        self.students = self._build_students()
        self.courses = self._build_courses()
        self.final = self._build_final()

        sems = sorted({str(s) for s in self.final["semester"].dropna().unique()})
        self.semesters = sems
        self.current_semester = sems[-1] if sems else "2025S"

        # 对外的 student_id 用学号（student_no），stu_id 只在内部/算法侧使用
        self._no_of = {str(k): str(v) for k, v in self.students["student_no"].items()}
        self._stu_of = {v: k for k, v in self._no_of.items()}

    # ------------------------------------------------------------------ 构建

    def _build_students(self) -> pd.DataFrame:
        si = self.raw["student_info"].copy()
        if "stu_id" not in si.columns:
            if "stu_num" not in si.columns:
                raise ApiError(500, "student_info 缺少 stu_id / stu_num，无法定位学生主键")
            si["stu_id"] = "stu_" + si["stu_num"].astype(str)
        si["stu_id"] = si["stu_id"].astype(str)
        si = si.drop_duplicates("stu_id").set_index("stu_id", drop=False)

        tail = si["stu_id"].str.replace("stu_", "", regex=False).str[-4:]
        si["student_name"] = si["student_name"].fillna("学生" + tail) if "student_name" in si else "学生" + tail
        si["student_no"] = si["student_no"].fillna(si["stu_id"].str.replace("stu_", "", regex=False)) if "student_no" in si else si["stu_id"].str.replace("stu_", "", regex=False)
        if "major_name" not in si.columns:
            si["major_name"] = si["major"] if "major" in si.columns else "未分专业"
        si["major_name"] = si["major_name"].fillna("未分专业")
        si["class_name"] = si["class_name"].fillna("未分班") if "class_name" in si.columns else "未分班"
        si["grade"] = si["grade"].fillna("—") if "grade" in si.columns else "—"
        if "major" not in si.columns:
            si["major"] = si["major_name"]
        return si

    def _build_courses(self) -> pd.DataFrame:
        c = self.raw["courses"].copy()
        if "code" not in c.columns and "course_code" in c.columns:
            c = c.rename(columns={"course_code": "code"})
        c["code"] = c["code"].astype(str)
        if "name" not in c.columns:
            c["name"] = c["code"]
        if "credits" in c.columns:
            c["credits"] = pd.to_numeric(c["credits"], errors="coerce").fillna(1).clip(lower=1)
        else:
            c["credits"] = 1
        return c.drop_duplicates("code").set_index("code", drop=False)

    def _build_final(self) -> pd.DataFrame:
        fr = self.raw["final_result"].copy()
        if "stu_id" not in fr.columns and "stu_num" in fr.columns:
            fr["stu_id"] = "stu_" + fr["stu_num"].astype(str)
        fr["stu_id"] = fr["stu_id"].astype(str)
        fr["course_code"] = fr["course_code"].astype(str)
        fr["semester"] = (fr["semester"].astype(str) if "semester" in fr.columns else "2024F")
        fr["final_score"] = pd.to_numeric(fr.get("final_score"), errors="coerce")

        if "status" not in fr.columns:
            fr["status"] = fr["risk_level"].map(LEVEL_TEXT).fillna("合格") if "risk_level" in fr.columns else "合格"
        fr["status"] = fr["status"].astype(str)

        if "risk_level" in fr.columns:
            fr["risk_level"] = pd.to_numeric(fr["risk_level"], errors="coerce")
        else:
            fr["risk_level"] = fr["status"].map(STATUS_TO_LEVEL)
        fr["risk_level"] = fr["risk_level"].fillna(fr["status"].map(STATUS_TO_LEVEL)).fillna(0).astype(int)

        if "risk_event_time" in fr.columns:
            fr["risk_event_time"] = pd.to_numeric(fr["risk_event_time"], errors="coerce")
        else:
            fr["risk_event_time"] = -1
        fr["risk_event_time"] = fr["risk_event_time"].fillna(-1).astype(int)
        return fr

    # ------------------------------------------------------------------ 学期

    def resolve_semester(self, semester: str | None) -> str:
        if not semester:
            return self.current_semester
        if semester not in self.semesters:
            raise ApiError(400, f"学期不存在：{semester}")
        return semester

    def resolve_stu_id(self, key: str) -> str:
        """接受学号或 stu_id，统一换成内部主键。"""

        text = str(key).strip()
        if text in self.students.index:
            return text
        if text in self._stu_of:
            return self._stu_of[text]
        if text.startswith("stu_"):
            raise ApiError(404, f"学生不存在：{text}")
        raise ApiError(404, f"学生不存在：{text}")

    # ------------------------------------------------------------------ 风险聚合

    def risk_table(self, semester: str | None = None) -> pd.DataFrame:
        """学生级风险表（当学期）。

        判定口径（与前端约定一致）：
          risk_level        = max(课程级风险)，保召回
          risk_score        = 学分加权风险分，仅用于排序
          high_course_count = 高危课程数，用于优先级筛选
          avg_score         = 剔除退学、缺考按 0、仅当学期
        """

        sem = self.resolve_semester(semester)
        if sem in self._risk_cache:
            return self._risk_cache[sem]

        sub = self.final[self.final["semester"] == sem].copy()
        if sub.empty:
            empty = pd.DataFrame(
                columns=["risk_level", "risk_score", "course_count", "high_course_count",
                         "avg_score", "fail_count", "attendance_rate", "quiz_accuracy"]
            )
            empty.index.name = "stu_id"
            self._risk_cache[sem] = empty
            return empty

        sub["credits"] = sub["course_code"].map(self.courses["credits"]).fillna(1).clip(lower=1)
        sub["w"] = sub["risk_level"] * sub["credits"]

        agg = sub.groupby("stu_id").agg(
            risk_level=("risk_level", "max"),
            weighted=("w", "sum"),
            credit_sum=("credits", "sum"),
            course_count=("risk_level", "size"),
            high_course_count=("risk_level", lambda s: int((s >= 2).sum())),
        )
        # 归一到 0~1：risk_level 上限是 2，除以 2 后可直接当"风险强度"展示与排序
        agg["risk_score"] = (agg["weighted"] / agg["credit_sum"].replace(0, 1) / 2).round(4)

        valid = sub[sub["status"] != "退学"]
        agg["avg_score"] = (
            valid.assign(s=valid["final_score"].fillna(0)).groupby("stu_id")["s"].mean().round(2)
        )
        fail = sub[sub["status"].isin(["不合格", "缺考"])].groupby("stu_id").size()
        agg["fail_count"] = fail.reindex(agg.index).fillna(0).astype(int)

        att, quiz = self._behaviour(sem, agg.index)
        agg["attendance_rate"] = att
        agg["quiz_accuracy"] = quiz

        self._risk_cache[sem] = agg
        return agg

    def _behaviour(self, sem: str, index: pd.Index) -> tuple[pd.Series, pd.Series]:
        """出勤率 / 小测正确率。

        有 platform_log / quiz_item 就用真实聚合；没有则用期末成绩做代理，
        返回的字段在 /health 与文档里都标注为 proxy，避免被当成真实行为数据。
        """

        att = pd.Series(dtype="float64")
        quiz = pd.Series(dtype="float64")

        pl = self.raw.get("platform_log")
        if pl is not None and {"attendance_status", "semester", "stu_id"} <= set(pl.columns):
            p = pl[pl["semester"].astype(str) == sem].copy()
            p["ok"] = p["attendance_status"].isin(["attended", "attended_leave"])
            att = p.groupby("stu_id")["ok"].mean()

        qi = self.raw.get("quiz_item")
        if qi is not None and {"score", "max_score", "semester", "stu_id"} <= set(qi.columns):
            q = qi[qi["semester"].astype(str) == sem]
            g = q.groupby("stu_id").agg(s=("score", "sum"), m=("max_score", "sum"))
            quiz = g["s"] / g["m"].replace(0, 1)

        base = self.final[self.final["semester"] == sem].groupby("stu_id")["final_score"].mean()
        base = base.reindex(index).fillna(60.0)
        att = att.reindex(index).fillna((0.30 + 0.62 * base / 100).clip(0.05, 0.99))
        quiz = quiz.reindex(index).fillna((0.25 + 0.65 * base / 100).clip(0.05, 0.99))
        return att.round(4), quiz.round(4)

    # ------------------------------------------------------------------ 学生

    def student_row(self, stu_id: str, semester: str | None = None) -> dict:
        sem = self.resolve_semester(semester)
        if stu_id not in self.students.index:
            raise ApiError(404, f"学生不存在：{stu_id}")

        s = self.students.loc[stu_id]
        risk = self.risk_table(sem)
        if stu_id in risk.index:
            r = risk.loc[stu_id]
            level = int(r["risk_level"])
            payload = {
                "avg_score": _num(r["avg_score"]),
                "risk_level": level,
                "risk_level_text": level_text(level),
                "risk_score": _num(r["risk_score"]),
                "course_count": int(r["course_count"]),
                "high_course_count": int(r["high_course_count"]),
                "fail_count": int(r["fail_count"]),
                "metrics": {
                    "attendance_rate": _num(r["attendance_rate"]),
                    "quiz_accuracy": _num(r["quiz_accuracy"]),
                    "attendance_threshold": settings.attendance_threshold,
                    "quiz_threshold": settings.quiz_threshold,
                },
            }
        else:
            payload = {
                "avg_score": None, "risk_level": 0, "risk_level_text": level_text(0),
                "risk_score": 0.0, "course_count": 0, "high_course_count": 0, "fail_count": 0,
                "metrics": {"attendance_rate": None, "quiz_accuracy": None,
                            "attendance_threshold": settings.attendance_threshold,
                            "quiz_threshold": settings.quiz_threshold},
            }

        return {
            "student_id": str(s["student_no"]),
            "stu_id": str(s["stu_id"]),
            "student_no": str(s["student_no"]),
            "name": str(s["student_name"]),
            "major": str(s["major_name"]),
            "class_name": str(s["class_name"]),
            "grade": str(s["grade"]),
            "semester": sem,
            **payload,
        }

    def course_list(self, stu_id: str, semester: str | None = None) -> list[dict]:
        sem = self.resolve_semester(semester)
        sub = self.final[(self.final["stu_id"] == stu_id) & (self.final["semester"] == sem)]
        rows = []
        for r in sub.itertuples(index=False):
            level = int(r.risk_level)
            rows.append({
                "course_code": str(r.course_code),
                "course_name": str(self.courses["name"].get(r.course_code, r.course_code)),
                "credit": int(self.courses["credits"].get(r.course_code, 1)),
                "score": _num(r.final_score),
                "status": "预警" if level >= settings.warn_min_level else "正常",
                "risk_level": level,
                "risk_level_text": level_text(level),
                "semester": sem,
            })
        rows.sort(key=lambda x: (-x["risk_level"], x["score"] if x["score"] is not None else 0))
        return rows

    def search_students(
        self,
        keyword: str | None,
        semester: str | None,
        risk_level: int | None,
        page_no: int,
        page_size: int,
    ) -> dict:
        sem = self.resolve_semester(semester)
        risk = self.risk_table(sem)

        df = self.students[self.students.index.isin(risk.index)].copy()
        df = df.join(risk[["risk_level", "risk_score", "avg_score", "course_count",
                           "high_course_count", "fail_count"]], how="left")

        if keyword:
            kw = str(keyword).strip()
            mask = (
                df["student_no"].astype(str).str.contains(kw, na=False)
                | df["student_name"].astype(str).str.contains(kw, na=False)
                | df["stu_id"].astype(str).str.contains(kw, na=False)
            )
            df = df[mask]
        if risk_level is not None:
            df = df[df["risk_level"] == int(risk_level)]

        df = df.sort_values(["risk_level", "risk_score", "avg_score"],
                            ascending=[False, False, True], na_position="last")

        total = int(len(df))
        start = (page_no - 1) * page_size
        items = []
        for stu_id, r in df.iloc[start:start + page_size].iterrows():
            level = int(r["risk_level"]) if pd.notna(r["risk_level"]) else 0
            items.append({
                "student_id": str(r["student_no"]),
                "stu_id": str(stu_id),
                "student_no": str(r["student_no"]),
                "name": str(r["student_name"]),
                "major": str(r["major_name"]),
                "class_name": str(r["class_name"]),
                "avg_score": _num(r["avg_score"]),
                "risk_level": level,
                "risk_level_text": level_text(level),
                "risk_score": _num(r["risk_score"]),
                "course_count": int(r["course_count"]) if pd.notna(r["course_count"]) else 0,
                "high_course_count": int(r["high_course_count"]) if pd.notna(r["high_course_count"]) else 0,
            })
        return page_payload(items, total, page_no, page_size)

    def stat(self, semester: str | None = None) -> dict:
        sem = self.resolve_semester(semester)
        risk = self.risk_table(sem)
        total = int(len(risk))
        high = int((risk["risk_level"] >= 2).sum())
        mid = int((risk["risk_level"] == 1).sum())
        normal = total - high - mid
        return {
            "semester": sem,
            "total_student": total,
            "warn_count": high + mid,
            "high_count": high,
            "mid_count": mid,
            "normal_count": normal,
            "risk_distribute": {"高危": high, "警惕": mid, "正常": normal},
            "warn_rate": round((high + mid) / total, 4) if total else 0.0,
        }

    # ------------------------------------------------------------------ 预警

    def warnings(self, semester: str | None = None) -> pd.DataFrame:
        sem = self.resolve_semester(semester)
        if sem in self._warn_cache:
            return self._warn_cache[sem]

        sub = self.final[
            (self.final["semester"] == sem) & (self.final["risk_level"] >= settings.warn_min_level)
        ]
        risk = self.risk_table(sem)
        start = semester_start(sem)

        rows = []
        for r in sub.itertuples(index=False):
            week = int(r.risk_event_time) if r.risk_event_time >= 0 else 15
            warn_date = start + timedelta(days=7 * week)
            s = self.students.loc[r.stu_id] if r.stu_id in self.students.index else None
            att = float(risk.loc[r.stu_id, "attendance_rate"]) if r.stu_id in risk.index else 0.5
            quiz = float(risk.loc[r.stu_id, "quiz_accuracy"]) if r.stu_id in risk.index else 0.5
            level = int(r.risk_level)
            rows.append({
                "warn_id": make_warn_id(r.stu_id, r.course_code, sem, week),
                "student_id": str(s["student_no"]) if s is not None else str(r.stu_id),
                "stu_id": str(r.stu_id),
                "student_no": str(s["student_no"]) if s is not None else "",
                "name": str(s["student_name"]) if s is not None else "",
                "course_code": str(r.course_code),
                "course_name": str(self.courses["name"].get(r.course_code, r.course_code)),
                "semester": sem,
                "semester_week": week,
                "warn_time": f"{warn_date.isoformat()} 09:00",
                "risk_level": level,
                "risk_level_text": level_text(level),
                "warn_reason": self._warn_reason(
                    course=self.courses["name"].get(r.course_code, r.course_code),
                    status=str(r.status),
                    score=_num(r.final_score),
                    attendance=att,
                    quiz=quiz,
                    level=level,
                ),
                "final_score": _num(r.final_score),
                "status": str(r.status),
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values(["risk_level", "warn_time"], ascending=[False, False])
        self._warn_cache[sem] = df
        return df

    def _warn_reason(self, course: str, status: str, score: float | None,
                     attendance: float, quiz: float, level: int) -> str:
        """规则生成的预警原因。模型上线后换成 GNNExplainer 摘要，字段不变。"""

        parts: list[str] = []
        if status == "退学":
            parts.append(f"《{course}》已退课")
        elif score is not None:
            parts.append(f"《{course}》期末 {score:.0f} 分（{status}）")
        else:
            parts.append(f"《{course}》{status}")

        if attendance < settings.attendance_threshold:
            parts.append(f"当学期出勤率 {attendance:.0%}，低于 {settings.attendance_threshold:.0%} 阈值")
        if quiz < settings.quiz_threshold:
            parts.append(f"小测正确率 {quiz:.0%}，低于 {settings.quiz_threshold:.0%} 阈值")
        if level >= 2 and len(parts) == 1:
            parts.append("该课程已触发高风险判定")
        return "；".join(parts)

    def warning_list(
        self,
        start_time: str | None,
        end_time: str | None,
        risk_level: int | None,
        page_no: int,
        page_size: int,
        semester: str | None = None,
    ) -> dict:
        df = self.warnings(semester)
        if df.empty:
            return page_payload([], 0, page_no, page_size)

        if start_time:
            df = df[df["warn_time"].str[:10] >= str(start_time)[:10]]
        if end_time:
            df = df[df["warn_time"].str[:10] <= str(end_time)[:10]]
        if risk_level is not None:
            df = df[df["risk_level"] == int(risk_level)]

        total = int(len(df))
        start = (page_no - 1) * page_size
        items = df.iloc[start:start + page_size].to_dict(orient="records")
        return page_payload(items, total, page_no, page_size)

    def warning_by_id(self, warn_id: str) -> dict:
        for sem in self.semesters:
            df = self.warnings(sem)
            if df.empty:
                continue
            hit = df[df["warn_id"] == warn_id]
            if not hit.empty:
                return hit.iloc[0].to_dict()
        raise ApiError(404, f"预警记录不存在：{warn_id}")

    def weekly_trend(self, stu_id: str, semester: str) -> tuple[list[dict], str]:
        """周度出勤 / 小测曲线。有真实行为日志就聚合，否则按当前值做估算曲线并标注来源。"""

        sem = self.resolve_semester(semester)
        pl = self.raw.get("platform_log")
        if pl is not None and {"attendance_status", "week", "semester", "stu_id"} <= set(pl.columns):
            p = pl[(pl["stu_id"] == stu_id) & (pl["semester"].astype(str) == sem)].copy()
            if not p.empty:
                p["ok"] = p["attendance_status"].isin(["attended", "attended_leave"])
                g = p.groupby("week")["ok"].mean().sort_index()
                return ([{"week": int(w), "attendance_rate": round(float(v), 4)} for w, v in g.items()],
                        "platform_log")

        risk = self.risk_table(sem)
        current = float(risk.loc[stu_id, "attendance_rate"]) if stu_id in risk.index else 0.6
        weeks = 8
        start = min(0.88, current + 0.32)
        step = (start - current) / max(weeks - 1, 1)
        trend = [{"week": w, "attendance_rate": round(_clamp(start - step * w, 0.02, 0.99), 4)}
                 for w in range(weeks)]
        return trend, "estimated"

    # ------------------------------------------------------------------ 图

    def _load_graph_json(self) -> dict[str, Any] | None:
        if self._graph_loaded:
            return self._graph_raw
        self._graph_loaded = True
        for p in settings.graph_candidates:
            if p.exists():
                try:
                    self._graph_raw = json.loads(Path(p).read_text(encoding="utf-8"))
                    break
                except Exception:
                    continue
        return self._graph_raw

    def _course_label(self, code: str) -> str:
        return str(self.courses["name"].get(code, code))

    def graph(
        self,
        limit_students: int,
        course_code: str | None = None,
        semester: str | None = None,
    ) -> dict:
        """全局学生-课程图。默认只取风险最高的 N 个学生，避免一次吐 10 万条边把前端拖死。"""

        sem = self.resolve_semester(semester)
        limit_students = max(1, min(int(limit_students), 200))
        risk = self.risk_table(sem)

        pool = risk
        if course_code:
            enrolled = set(
                self.final[
                    (self.final["semester"] == sem) & (self.final["course_code"] == course_code)
                ]["stu_id"]
            )
            pool = pool[pool.index.isin(enrolled)]

        top = pool.sort_values(["risk_level", "risk_score"], ascending=[False, False]).head(limit_students)
        keep_students = set(top.index)

        raw = self._load_graph_json()
        if raw and raw.get("nodes"):
            nodes = []
            for n in raw["nodes"]:
                nid = str(n.get("id"))
                ntype = n.get("node_type") or n.get("type")
                if ntype == "student" and nid not in keep_students:
                    continue
                if ntype not in ("student", "course"):
                    continue
                nodes.append({"id": nid, "type": ntype})
            edges = []
            for e in raw.get("edges", []):
                src, dst = str(e.get("source") or e.get("src")), str(e.get("target") or e.get("dst"))
                edges.append({"source": src, "target": dst, "type": e.get("edge_type") or e.get("type")})
        else:
            sub = self.final[(self.final["semester"] == sem) & (self.final["stu_id"].isin(keep_students))]
            nodes, edges, seen = [], [], set()
            for r in sub.itertuples(index=False):
                sid, cid = str(r.stu_id), f"crs_{r.course_code}"
                if sid not in seen:
                    nodes.append({"id": sid, "type": "student"})
                    seen.add(sid)
                if cid not in seen:
                    nodes.append({"id": cid, "type": "course"})
                    seen.add(cid)
                edges.append({"source": sid, "target": cid, "type": "enrolled_in"})

        for n in nodes:
            if n["type"] == "student":
                s = self.students.loc[n["id"]] if n["id"] in self.students.index else None
                r = risk.loc[n["id"]] if n["id"] in risk.index else None
                level = int(r["risk_level"]) if r is not None else 0
                n.update({
                    "label": str(s["student_name"]) if s is not None else n["id"],
                    "student_no": str(s["student_no"]) if s is not None else "",
                    "risk_level": level,
                    "risk_level_text": level_text(level),
                    "risk_score": _num(r["risk_score"]) if r is not None else 0.0,
                })
            else:
                code = n["id"].replace("crs_", "")
                n.update({
                    "label": self._course_label(code),
                    "course_code": code,
                    "credit": int(self.courses["credits"].get(code, 1)),
                })

        keep_nodes = {n["id"] for n in nodes}
        edges = [e for e in edges if e["source"] in keep_nodes and e["target"] in keep_nodes]

        # 对外节点 id 统一用学号，前端点节点即可直接查详情
        id_map = {node_id: self._no_of.get(node_id, node_id) for node_id in keep_nodes}
        for n in nodes:
            n["id"] = id_map.get(n["id"], n["id"])
        for e in edges:
            e["source"] = id_map.get(e["source"], e["source"])
            e["target"] = id_map.get(e["target"], e["target"])

        return {
            "nodes": nodes,
            "edges": edges,
            "meta": {
                "semester": sem,
                "student_count": sum(1 for n in nodes if n["type"] == "student"),
                "node_count": len(nodes),
                "edge_count": len(edges),
                "truncated": bool(len(pool) > limit_students),
                "limit_students": limit_students,
            },
        }

    def student_subgraph(self, stu_id: str, semester: str | None = None) -> dict:
        """个人子图：学生 + 当学期所选课程（1 跳），节点数 <= 10。"""

        sem = self.resolve_semester(semester)
        row = self.student_row(stu_id, sem)
        nodes = [{
            "id": row["student_id"],
            "type": "student",
            "label": row["name"],
            "risk_level": row["risk_level"],
            "risk_level_text": row["risk_level_text"],
            "risk_score": row["risk_score"],
        }]
        edges = []
        for c in self.course_list(stu_id, sem):
            cid = f"crs_{c['course_code']}"
            nodes.append({
                "id": cid,
                "type": "course",
                "label": c["course_name"],
                "course_code": c["course_code"],
                "credit": c["credit"],
                "risk_level": c["risk_level"],
                "risk_level_text": c["risk_level_text"],
                "score": c["score"],
            })
            edges.append({"source": row["student_id"], "target": cid,
                          "type": "enrolled_in", "weight": 1.0})

        return {
            "nodes": nodes,
            "edges": edges,
            "meta": {"semester": sem, "node_count": len(nodes), "edge_count": len(edges)},
        }

    # ------------------------------------------------------------------ 账号

    def student_accounts(self) -> list[dict]:
        """给演示账号用的学生列表：优先高危学生，保证登录后能看到有内容的页面。"""

        risk = self.risk_table(self.current_semester)
        ordered = risk.sort_values(["risk_level", "risk_score"], ascending=[False, False]).index
        out: list[dict] = []
        for stu_id in ordered:
            if stu_id not in self.students.index:
                continue
            s = self.students.loc[stu_id]
            out.append({
                "stu_id": str(stu_id),
                "student_no": str(s["student_no"]),
                "student_name": str(s["student_name"]),
            })
        return out


def _num(value: Any) -> float | None:
    """pandas 数值 -> float，NaN 变 None。"""

    if value is None or pd.isna(value):
        return None
    return float(value)


@lru_cache(maxsize=1)
def get_repo() -> Repo:
    return Repo()
