"""风险分数据源（阶段四）。

对应 `docs/阶段四_风险接口契约.md`：唯一真实数据源是
`algorithm/data/oulad/processed_full_demo/risk_scores.parquet`（9 字段冻结）。

该 parquet 被 `.gitignore` 忽略，克隆仓库后通常不存在，因此这里做了两级兜底：

    1. parquet  —— 完整数据，季度/周次/学生全量
    2. mock     —— docs/mock_api/*.json（算法侧从真实 parquet 导出的样本）

对外结构完全一致，接口层只依赖本类的 5 个方法，数据到位后不用改一行业务代码。
`source` 会通过 /api/health 与每个响应的 `data_source` 字段透出，避免把样本当全量。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import settings
from app.core.response import ApiError

log = logging.getLogger(__name__)

PARQUET_NAME = "risk_scores.parquet"

# 冻结字段（契约 §2）；mock 与 parquet 都要保证这 9 列存在，缺列直接报错而不是静默跑出坏数据
REQUIRED_COLUMNS = (
    "stu_id",
    "course_code",
    "semester",
    "week",
    "risk_prob",
    "risk_level",
    "risk_level_name",
    "pred_event_week",
    "top_factors",
)

LEVEL_NAMES = {0: "低", 1: "中", 2: "高"}


def _parse_factors(raw: Any) -> list[dict]:
    """top_factors 在 parquet 里是 JSON 字符串（B5），在这里统一解析成数组。

    解析失败不抛错，返回空数组 —— 归因缺失不应该让整个风险接口 500。
    """

    if isinstance(raw, list):
        return raw
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    text = str(raw).strip()
    if not text or text in ("[]", "null", "None", "nan"):
        return []
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []


class RiskRepo:
    """risk_scores 的读取门面。进程内单例，数据量 50 万行，整表驻留内存。"""

    def __init__(self) -> None:
        self.source = "none"          # parquet / mock / none
        self.path: Path | None = None
        self._df: pd.DataFrame = pd.DataFrame(columns=REQUIRED_COLUMNS)
        self._sample_based = False
        self._expected_total: int | None = None
        self._default_week: int | None = None
        self._mock_stat: dict[str, Any] | None = None
        self._load()

    # ---------------------------------------------------------------- 加载

    def _load(self) -> None:
        for root in settings.processed_candidates:
            candidate = Path(root) / PARQUET_NAME
            if not candidate.exists():
                continue
            try:
                df = pd.read_parquet(candidate)
                self._df = self._normalize(df)
                self.source = "parquet"
                self.path = candidate
                log.info("风险分数据源=parquet 路径=%s 行数=%d", candidate, len(self._df))
                return
            except Exception as exc:  # 文件损坏/依赖缺失都不该让服务起不来
                log.warning("读取 %s 失败，继续探测下一个：%s", candidate, exc)

        self._load_mock()

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"risk_scores 缺少字段 {missing}，请对照契约 §2 核对")
        out = df[list(REQUIRED_COLUMNS)].copy()
        out["stu_id"] = out["stu_id"].astype(str)
        out["course_code"] = out["course_code"].astype(str)
        out["semester"] = out["semester"].astype(str)
        out["week"] = out["week"].astype("int64")
        out["risk_prob"] = out["risk_prob"].astype("float64")
        out["risk_level"] = out["risk_level"].astype("int64")
        out["pred_event_week"] = out["pred_event_week"].astype("int64")
        out["risk_level_name"] = out["risk_level_name"].astype(str)
        # top_factors 保持原始字符串，取行时再解析：50 万行全量 json.loads 会让启动慢十几秒
        return out

    def _load_mock(self) -> None:
        base = Path(settings.mock_api_dir)
        needed = {name: base / f"{name}.json" for name in
                  ("admin_list", "student", "weekly", "factors", "admin_stat")}
        if not all(p.exists() for p in needed.values()):
            log.warning("既没有 risk_scores.parquet，也没有完整的 mock 数据包（%s）", base)
            return

        rows: list[dict] = []

        # 管理员列表：20 条样本，覆盖 20 个学生 / 一周
        admin = json.loads(needed["admin_list"].read_text(encoding="utf-8"))
        for item in admin.get("items", []):
            rows.append({**item})

        # 单学生全学期曲线：stu_100621 / CCC / 16 周，带完整归因
        student = json.loads(needed["student"].read_text(encoding="utf-8"))
        for item in student.get("weeks", []):
            rows.append({**item})

        if not rows:
            log.warning("mock 数据包为空，风险接口将返回 503")
            return

        df = pd.DataFrame(rows)
        # 同一 (学生,课程,学期,周) 以曲线那份为准（它字段更全，含 top_factors）
        df = df.drop_duplicates(subset=["stu_id", "course_code", "semester", "week"], keep="last")
        for col, default in (("pred_event_week", -1), ("top_factors", "[]"),
                             ("risk_level_name", ""), ("risk_prob", 0.0), ("risk_level", 0)):
            if col not in df.columns:
                df[col] = default
        self._df = self._normalize(df)

        # admin_stat 是从真实 parquet 导出的全量分布，只在 mock 下作为"应有规模"参考透出
        stat = json.loads(needed["admin_stat"].read_text(encoding="utf-8"))
        self._sample_based = True
        self._expected_total = int(stat.get("total") or 0)
        # mock 的样本都按 admin_stat 那一周导出，默认周次跟着它走，否则默认取到 week15 只剩 1 条
        self._default_week = int(stat["week"]) if stat.get("week") is not None else None
        self._mock_stat = stat
        self.source = "mock"
        self.path = base
        log.info("风险分数据源=mock 路径=%s 样本行数=%d（真实全量 %s 行，parquet 缺失时使用）",
                 base, len(self._df), self._expected_total or "未知")

    # ---------------------------------------------------------------- 查询辅助

    def _ensure(self) -> None:
        if self._df.empty:
            raise ApiError(503, "风险数据未就绪：既没有 risk_scores.parquet，也没有 mock 数据包")

    @property
    def semesters(self) -> list[str]:
        if self._df.empty:
            return []
        # 2013B/2013J/2014B/2014J 按 (年份, 学期字母) 排，J 晚于 B
        return sorted({str(s) for s in self._df["semester"].unique()})

    @property
    def current_semester(self) -> str | None:
        sems = self.semesters
        return sems[-1] if sems else None

    def resolve_semester(self, semester: str | None) -> str:
        self._ensure()
        if not semester:
            return self.current_semester  # type: ignore[return-value]
        if semester not in self.semesters:
            raise ApiError(400, f"学期不存在：{semester}，可选 {self.semesters}")
        return semester

    def _slice(self, semester: str, week: int | None = None) -> pd.DataFrame:
        sub = self._df[self._df["semester"] == semester]
        if sub.empty:
            raise ApiError(404, f"学期 {semester} 没有风险数据")
        if week is not None:
            sub = sub[sub["week"] == week]
            if sub.empty:
                raise ApiError(404, f"{semester} 第 {week} 周没有风险数据")
        return sub

    def resolve_week(self, week: int | None, semester: str) -> int:
        """缺省周次：mock 跟导出周一致，parquet 取该学期已产出的最大周（最新跑批）。"""

        if week is not None:
            if not 0 <= week <= 15:
                raise ApiError(400, f"week 取值范围 0-15（0 起，前端展示时 +1），收到 {week}")
            return week
        weeks = self._df.loc[self._df["semester"] == semester, "week"]
        if weeks.empty:
            raise ApiError(404, f"学期 {semester} 没有风险数据")
        if self._default_week is not None and bool((weeks == self._default_week).any()):
            return self._default_week
        return int(weeks.max())

    def _row_payload(self, row: pd.Series) -> dict:
        level = int(row["risk_level"])
        name = str(row["risk_level_name"]) or LEVEL_NAMES.get(level, "")
        return {
            "stu_id": str(row["stu_id"]),
            "course_code": str(row["course_code"]),
            "semester": str(row["semester"]),
            "week": int(row["week"]),
            "risk_prob": round(float(row["risk_prob"]), 4),
            "risk_level": level,
            "risk_level_name": name,
            "pred_event_week": int(row["pred_event_week"]),
            "top_factors": _parse_factors(row["top_factors"]),
        }

    # ---------------------------------------------------------------- 5 个接口

    def weekly(self, stu_id: str, course_code: str, semester: str | None, week: int | None) -> dict:
        self._ensure()
        sem = self.resolve_semester(semester)
        wk = self.resolve_week(week, sem)

        sub = self._slice(sem, wk)
        hit = sub[(sub["stu_id"] == stu_id) & (sub["course_code"] == course_code)]
        if hit.empty:
            raise ApiError(404, f"{stu_id} 在 {sem} 第 {wk} 周没有《{course_code}》的风险记录")
        return {**self._row_payload(hit.iloc[0]), "data_source": self.source}

    def student_curve(self, stu_id: str, semester: str | None) -> dict:
        """一次返回整学期曲线（契约 F1：不要让前端循环 16 次）。

        多门课按周取风险最高的那门，与推理桥 predict_student 同口径。
        """

        self._ensure()
        sem = self.resolve_semester(semester)

        sub = self._df[(self._df["semester"] == sem) & (self._df["stu_id"] == stu_id)]
        if sub.empty:
            raise ApiError(404, f"{sem} 没有 {stu_id} 的风险记录")

        idx = sub.groupby("week")["risk_prob"].idxmax()
        picked = sub.loc[idx].sort_values("week")
        weeks = [self._row_payload(r) for _, r in picked.iterrows()]
        course_count = int(sub["course_code"].nunique())

        return {
            "stu_id": stu_id,
            "semester": sem,
            "course_count": course_count,
            "week_count": len(weeks),
            "weeks": weeks,
            # 曲线不完整（mock 中非样本学生只有导出那一周），前端据此提示"数据不足"
            "partial": len(weeks) < 2,
            "data_source": self.source,
            "sample_based": self._sample_based,
        }

    def factors(self, stu_id: str, course_code: str, semester: str | None, week: int | None) -> dict:
        payload = self.weekly(stu_id, course_code, semester, week)
        items = payload["top_factors"]
        # 契约：最多 3 个、按 contrib 降序；算法侧不保证顺序与个数
        items = sorted(items, key=lambda x: -float(x.get("contrib") or 0))[:3]
        return {
            "stu_id": payload["stu_id"],
            "course_code": payload["course_code"],
            "semester": payload["semester"],
            "week": payload["week"],
            "risk_level": payload["risk_level"],
            "risk_prob": payload["risk_prob"],
            "top_factors": items,
            "data_source": self.source,
        }

    def admin_list(
        self,
        semester: str | None,
        week: int | None,
        level: int | None,
        page: int,
        size: int,
    ) -> dict:
        self._ensure()
        sem = self.resolve_semester(semester)
        wk = self.resolve_week(week, sem)

        sub = self._slice(sem, wk)
        if level is not None:
            if level not in (0, 1, 2):
                raise ApiError(400, f"level 只能是 0/1/2，收到 {level}")
            sub = sub[sub["risk_level"] == level]

        sub = sub.sort_values(["risk_prob", "stu_id"], ascending=[False, True])
        total = int(len(sub))
        start = (page - 1) * size
        chunk = sub.iloc[start:start + size]

        items = []
        for _, r in chunk.iterrows():
            payload = self._row_payload(r)
            payload.pop("top_factors")  # 列表不背归因，避免响应膨胀；需要时调 /factors
            items.append(payload)

        return {
            "semester": sem,
            "week": wk,
            "page": page,
            "size": size,
            "total": total,
            "items": items,
            "data_source": self.source,
            "sample_based": self._sample_based,
        }

    def admin_stat(self, semester: str | None, week: int | None) -> dict:
        self._ensure()
        sem = self.resolve_semester(semester)
        wk = self.resolve_week(week, sem)

        sub = self._slice(sem, wk)

        # mock 的 items 只导出了 top20 高危，直接按样本统计会得到 alert_rate=100%，
        # 前端联调时会误以为全校都是高危。这里用算法侧导出的真实分布，
        # 并用 list_available 说明列表实际只能取到多少条。
        if self._mock_stat and str(self._mock_stat.get("semester")) == sem \
                and int(self._mock_stat.get("week", -1)) == wk:
            ms = self._mock_stat
            return {
                "semester": sem,
                "week": wk,
                "total": int(ms["total"]),
                "level_0": int(ms["level_0"]),
                "level_1": int(ms["level_1"]),
                "level_2": int(ms["level_2"]),
                "alert_rate": float(ms["alert_rate"]),
                "data_source": self.source,
                "sample_based": True,
                "list_available": int(len(sub)),
            }

        counts = sub["risk_level"].value_counts().to_dict()
        level_0 = int(counts.get(0, 0))
        level_1 = int(counts.get(1, 0))
        level_2 = int(counts.get(2, 0))
        total = level_0 + level_1 + level_2

        return {
            "semester": sem,
            "week": wk,
            "total": total,
            "level_0": level_0,
            "level_1": level_1,
            "level_2": level_2,
            "alert_rate": round((level_1 + level_2) / total, 4) if total else 0.0,
            "data_source": self.source,
            "sample_based": self._sample_based,
        }

    # ---------------------------------------------------------------- 元信息

    def info(self) -> dict:
        return {
            "source": self.source,
            "path": str(self.path) if self.path else None,
            "rows": int(len(self._df)),
            "semesters": self.semesters,
            "sample_based": self._sample_based,
        }


_repo: RiskRepo | None = None


def get_risk_repo() -> RiskRepo:
    global _repo
    if _repo is None:
        _repo = RiskRepo()
    return _repo
