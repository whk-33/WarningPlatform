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
import os
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import settings
from app.core.response import ApiError

log = logging.getLogger(__name__)

PARQUET_NAME = "risk_scores.parquet"
# 学生姓名/专业/班级的来源文件（风险 parquet 里没有这三列）
META_NAME = "student_meta.json"

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

# 可选字段：有就用，没有就回退代理。`risk_state=1` 表示事件已发生（算法侧口径），
# `risk_score` 是模型侧的连续排序分（避免单课学生只有 0/0.5/1 三档无法排序）。
# 当前导出的 parquet 只有上面 9 列，这两列都还没有，所以不能放进 REQUIRED_COLUMNS，
# 否则读取会直接报字段缺失。
OPTIONAL_COLUMNS = ("risk_state", "risk_score", "risk_event_time")


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
        self._meta: dict[str, dict[str, str | None]] = {}
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
                self._load_meta()
                log.info("风险分数据源=parquet 路径=%s 行数=%d", candidate, len(self._df))
                return
            except Exception as exc:  # 文件损坏/依赖缺失都不该让服务起不来
                log.warning("读取 %s 失败，继续探测下一个：%s", candidate, exc)

        self._load_mock()
        self._load_meta()

    def _load_meta(self) -> None:
        """补学生姓名/专业/班级。

        风险分数据的 9 个冻结字段里没有这三列，只能从外部补，来源按优先级叠加：

        1. 主数据（`students` 表）：学号能对上就用真实姓名——真实 parquet 到位后若与主数据
           同一套学号，这条自动生效，不需要改代码；
        2. `docs/mock_api/student_meta.json`：演示用合成数据，按风险数据的 stu_id 生成。

        两者都缺时字段返回 `null`，接口仍然正常——姓名缺失不该让风险榜 500。
        """

        meta: dict[str, dict[str, str | None]] = {}

        try:
            from app.data.repo import get_repo

            students = get_repo().students
            cols = set(students.columns)
            if "stu_id" in cols or "stu_num" in cols:
                for row in students.itertuples(index=False):
                    info = {
                        "student_name": str(getattr(row, "student_name", "") or "") or None,
                        "major_name": str(getattr(row, "major_name", "") or "") or None,
                        "class_name": str(getattr(row, "class_name", "") or "") or None,
                    }
                    # 风险数据的 stu_id 形如 `stu_11391`（= stu_<stu_num>），而主数据里
                    # stu_num 是 `11391`、student_no 是 `130189`，三套写法混在一起。
                    # 这列全登记上，否则会出现"学生明明在库里、风险榜却补不到姓名"。
                    keys: set[str] = set()
                    for col in ("stu_id", "stu_num", "student_no"):
                        if col not in cols:
                            continue
                        val = getattr(row, col)
                        if val is None or str(val) in ("", "nan", "None"):
                            continue
                        keys.add(str(val))
                        keys.add(f"stu_{val}")
                    for k in keys:
                        meta.setdefault(k, info)
        except Exception as exc:  # 主数据没加载好不影响风险接口
            log.debug("主数据姓名映射不可用：%s", exc)

        path = Path(settings.mock_api_dir) / META_NAME
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                for stu_id, info in (payload.get("students") or {}).items():
                    if not isinstance(info, dict):
                        continue
                    # 主数据优先：真实产物到位后，合成姓名只补主数据里没有的人
                    meta.setdefault(str(stu_id), {
                        "student_name": info.get("student_name"),
                        "major_name": info.get("major_name"),
                        "class_name": info.get("class_name"),
                    })
            except Exception as exc:
                log.warning("读取 %s 失败，风险榜将没有姓名：%s", path, exc)

        self._meta = meta
        if meta:
            log.info("风险学生姓名映射 %d 个键（含 stu_id / stu_num / student_no 别名，来源：主数据 + %s）",
                     len(meta), META_NAME)

    def _student_meta(self, stu_id: str) -> dict[str, str | None]:
        info = self._meta.get(str(stu_id)) or {}
        return {
            "student_name": info.get("student_name"),
            "major_name": info.get("major_name"),
            "class_name": info.get("class_name"),
        }

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"risk_scores 缺少字段 {missing}，请对照契约 §2 核对")
        keep = list(REQUIRED_COLUMNS) + [c for c in OPTIONAL_COLUMNS if c in df.columns]
        out = df[keep].copy()
        out["stu_id"] = out["stu_id"].astype(str)
        out["course_code"] = out["course_code"].astype(str)
        out["semester"] = out["semester"].astype(str)
        out["week"] = out["week"].astype("int64")
        out["risk_prob"] = out["risk_prob"].astype("float64")
        out["risk_level"] = out["risk_level"].astype("int64")
        out["pred_event_week"] = out["pred_event_week"].astype("int64")
        out["risk_level_name"] = out["risk_level_name"].astype(str)
        if "risk_state" in out.columns:
            out["risk_state"] = out["risk_state"].astype("int64")
        if "risk_score" in out.columns:
            out["risk_score"] = out["risk_score"].astype("float64")
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
        """缺省周次。

        优先级：显式传参 → mock 导出周（真实分布随样本走）→ `WP_DEFAULT_WEEK`
        → 第 8 周（期中）。

        **为什么不取最大周**：`risk_scores` 的最后一周年限（实测 4 个学期都是 week=15）
        高危率会比前一周突增 20 个百分点以上（2014J：w14=31.9% → w15=53.3%），
        因为它几乎等价于"期末成绩已定"。把这种周当默认，等于让平台"考完才预警"，
        而且风险榜首页会显示 53% 的学生高危，把老师吓一跳。默认取期中更符合预警语义。
        """

        if week is not None:
            if not 0 <= week <= 15:
                raise ApiError(400, f"week 取值范围 0-15（0 起，前端展示时 +1），收到 {week}")
            return week

        weeks = sorted({int(w) for w in self._df.loc[self._df["semester"] == semester, "week"].unique()})
        if not weeks:
            raise ApiError(404, f"学期 {semester} 没有风险数据")
        if self._default_week is not None and self._default_week in weeks:
            return self._default_week

        override = (os.getenv("WP_DEFAULT_WEEK") or "").strip()
        if override.isdigit() and int(override) in weeks:
            return int(override)

        return 8 if 8 in weeks else weeks[max(0, len(weeks) - 2)]

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

        # 多门课按周取风险最高的那门。同一周内 level 与 prob 单调等价
        # （level = prob 按该周阈值切分，已验证 64 个 (学期,周) 组合复现率 100%），
        # 但阈值逐周变动、跨周不可比，故仍以 level 为第一关键字。
        idx = sub.sort_values(
            ["week", "risk_level", "risk_prob"], ascending=[True, False, False]
        ).groupby("week").head(1).index
        picked = sub.loc[idx].sort_values("week")
        weeks = [self._row_payload(r) for _, r in picked.iterrows()]
        course_count = int(sub["course_code"].nunique())

        return {
            "stu_id": stu_id,
            # 姓名/专业/班级：风险数据本身没有，从主数据或演示元数据补，补不到是 null
            **self._student_meta(stu_id),
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
        raw_rows = int(len(sub))
        # risk_scores 一门课一行，按 level 降序、prob 降序取首行，留下最危险那门。
        # 同一周内 level 与 prob 单调等价（level 就是 prob 按该周阈值切出来的），
        # 但阈值逐周变化、跨周不可比，所以仍以 level 为第一关键字。
        sub = sub.sort_values(
            ["risk_level", "risk_prob", "stu_id"], ascending=[False, False, True]
        ).drop_duplicates(subset=["stu_id"], keep="first")

        if level is not None:
            if level not in (0, 1, 2):
                raise ApiError(400, f"level 只能是 0/1/2，收到 {level}")
            sub = sub[sub["risk_level"] == level]

        total = int(len(sub))
        start = (page - 1) * size
        chunk = sub.iloc[start:start + size]

        items = []
        for _, r in chunk.iterrows():
            payload = self._row_payload(r)
            payload.pop("top_factors")  # 列表不背归因，避免响应膨胀；需要时调 /factors
            payload.update(self._student_meta(payload["stu_id"]))
            items.append(payload)

        return {
            "semester": sem,
            "week": wk,
            "page": page,
            "size": size,
            "total": total,
            # 与 total 同值（全量数据能翻到底）；保留该字段是为了让前端只用一套分页逻辑
            "list_available": total,
            # 去重前的原始记录数（学生-课程对）。total 是学生数，两者不等属正常：
            # 一个学生选了 N 门课就有 N 行，列表只保留风险最高的那门。
            "record_total": raw_rows,
            "items": items,
            "data_source": self.source,
            "sample_based": self._sample_based,
        }

    def admin_stat(self, semester: str | None, week: int | None) -> dict:
        self._ensure()
        sem = self.resolve_semester(semester)
        wk = self.resolve_week(week, sem)

        sub = self._slice(sem, wk)
        record_total = int(len(sub))
        # 必须按学生聚合：risk_scores 一门课一行，直接 value_counts 得到的是
        # 「学生-课程对」的分布，total 会被撑大（2014J 第 8 周 11260 vs 实际 10670 人）。
        # 契约里 risk_level 定义为 max(课程级)，所以这里取 groupby.max。
        student_level = sub.groupby("stu_id")["risk_level"].max()

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
                "record_total": record_total,
            }

        counts = student_level.value_counts().to_dict()
        level_0 = int(counts.get(0, 0))
        level_1 = int(counts.get(1, 0))
        level_2 = int(counts.get(2, 0))
        total = level_0 + level_1 + level_2

        occurred, occurred_source = self._occurred_mask(sub)
        breakdown = self._pool_breakdown(student_level, occurred, total)

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
            # 全量数据下列表也能翻到底，两个数相等；前端统一按 list_available 算分页上限即可
            "list_available": total,
            # 聚合前的「学生-课程对」记录数，用于和 /list 的原始行数对账
            "record_total": record_total,
            # ---- 已发生 / 风险池拆分（算法侧 11-17 口径）----
            **breakdown,
            "occurred_source": occurred_source,
        }

    def _occurred_mask(self, sub: pd.DataFrame) -> tuple[pd.Series, str]:
        """标出「事件已发生」的学生，返回 (按 stu_id 的布尔序列, 判定来源)。

        算法侧口径：`prob = 1.0` 的行 100% 是已发生事件（`risk_event_time <= week`），
        这批人不算模型预测，应从预警率分母里拆出去，单独展示为"已发生"。

        优先用权威字段 `risk_state`（=1 即已发生）；当前导出的 parquet 只有 9 列、
        没有该字段，故回退到 `risk_prob >= 1.0` 代理。代理的可靠性有旁证：
        剔除后风险池 `risk_prob` 最大值恰为 0.9981、池内无一人等于 1.0，
        与算法侧给出的数字完全一致。
        """

        if "risk_state" in sub.columns:
            mask = sub["risk_state"].astype("int64") == 1
            source = "risk_state"
        else:
            mask = sub["risk_prob"] >= 1.0
            source = "prob_proxy"
        # 一个学生有多门课，任一门已发生即算已发生
        return mask.groupby(sub["stu_id"]).any(), source

    def _pool_breakdown(
        self, student_level: pd.Series, occurred: pd.Series, total: int
    ) -> dict:
        """把学生拆成「已发生」与「风险池」两拨，各算预警率。

        算法侧要求的全表口径：
            全表预警率 = 已发生占比 + 风险池预警率 × (1 − 已发生占比)
        即风险池里按 15% 卡阈值，已发生的全部计入。这里把两个分量都透出来，
        前端既能显示总预警率，也能解释"其中多少是已经出事的"。
        """

        occurred = occurred.reindex(student_level.index).fillna(False).astype(bool)
        occurred_count = int(occurred.sum())
        pool_level = student_level[~occurred]

        pool_total = int(len(pool_level))
        pool_alert = int((pool_level > 0).sum())
        # 用未舍入的比率算合成值，否则先舍入再代入会差 0.01pp（例如 w5 的 31.17 vs 31.16）
        occurred_r = occurred_count / total if total else 0.0
        pool_r = pool_alert / pool_total if pool_total else 0.0
        combined = round(occurred_r + pool_r * (1 - occurred_r), 4)
        occurred_rate = round(occurred_r, 4)
        pool_alert_rate = round(pool_r, 4)

        return {
            "occurred_count": occurred_count,
            "occurred_rate": occurred_rate,
            "risk_pool_count": pool_total,
            "pool_alert_count": pool_alert,
            "pool_alert_rate": pool_alert_rate,
            "pool_level_0": int((pool_level == 0).sum()),
            "pool_level_1": int((pool_level == 1).sum()),
            "pool_level_2": int((pool_level == 2).sum()),
            "alert_rate_combined": combined,
        }

    # ---------------------------------------------------------------- 元信息

    def demo_student(self, semester: str | None = None) -> dict | None:
        """挑一个"最适合演示"的学生：曲线周数最多，同周数时风险概率最高。

        **必须限定在默认学期内**：账号绑定的学生如果只有别的学期的数据，
        学生端用默认学期查自己就会 404（`student_curve` 默认取 current_semester）。
        只给账号同步用（`users.sync`），不进任何接口响应。
        """

        if self._df.empty:
            return None

        sem = semester or self.current_semester
        sub = self._df[self._df["semester"] == sem] if sem else self._df
        if sub.empty:
            sub = self._df

        grouped = (
            sub.groupby(["stu_id", "semester"])
            .agg(weeks=("week", "nunique"), peak=("risk_prob", "max"),
                 course_code=("course_code", "first"))
            .reset_index()
        )
        # 单个点的曲线演示不出折线，优先挑曲线完整的
        complete = grouped[grouped["weeks"] >= 2]
        if not complete.empty:
            grouped = complete
        if grouped.empty:
            return None

        top = grouped.sort_values(["weeks", "peak"], ascending=[False, False]).iloc[0]
        return {
            "stu_id": str(top["stu_id"]),
            "semester": str(top["semester"]),
            "course_code": str(top["course_code"]),
            "weeks": int(top["weeks"]),
        }

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
