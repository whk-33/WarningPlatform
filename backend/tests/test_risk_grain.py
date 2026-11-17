"""风险数据的粒度与口径测试。

背景：`risk_scores.parquet` 是「学生 × 课程 × 周」粒度——一个学生选两门课就有两行。
契约里 `risk_level` 定义为学生级 `max(课程级)`，所以统计和列表都必须先按学生聚合，
否则 `total` 会被撑大成「学生-课程对」的数（真实数据 2014J 第 8 周：11260 vs 10670）。

这些测试用合成数据，不依赖 mock 数据包也不必本机有 parquet。
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.data.risk_repo import RiskRepo


def _repo(rows: list[dict]) -> RiskRepo:
    """造一个只含指定行的 RiskRepo，跳过真实数据加载。"""

    repo = RiskRepo.__new__(RiskRepo)
    repo.source = "unit"
    repo.path = None
    df = pd.DataFrame(rows)
    base = ["stu_id", "course_code", "semester", "week", "risk_prob",
            "risk_level", "pred_event_week", "risk_level_name", "top_factors"]
    # 可选列（risk_state / risk_score）有就保留，用于验证优先走权威字段
    extra = [c for c in ("risk_state", "risk_score") if c in df.columns]
    repo._df = df[base + extra]
    repo._sample_based = False
    repo._expected_total = None
    repo._default_week = None
    repo._mock_stat = None
    repo._meta = {}
    return repo


# 学生 A 两门课（一门高危一门正常），B 单门高危，C 单门警惕，D 单门正常
ROWS = [
    {"stu_id": "A", "course_code": "CCC", "semester": "2014J", "week": 8,
     "risk_prob": 0.10, "risk_level": 0, "pred_event_week": -1,
     "risk_level_name": "正常", "top_factors": "[]"},
    # 关键：prob 低但 level 高。真实数据里 prob 与 level 并不单调，
    # 排序若以 prob 为第一关键字，会把这个学生的高危课丢掉。
    {"stu_id": "A", "course_code": "DDD", "semester": "2014J", "week": 8,
     "risk_prob": 0.25, "risk_level": 2, "pred_event_week": 8,
     "risk_level_name": "高危", "top_factors": "[]"},
    {"stu_id": "B", "course_code": "CCC", "semester": "2014J", "week": 8,
     "risk_prob": 0.90, "risk_level": 2, "pred_event_week": 8,
     "risk_level_name": "高危", "top_factors": "[]"},
    {"stu_id": "C", "course_code": "CCC", "semester": "2014J", "week": 8,
     "risk_prob": 0.50, "risk_level": 1, "pred_event_week": 9,
     "risk_level_name": "警惕", "top_factors": "[]"},
    {"stu_id": "D", "course_code": "CCC", "semester": "2014J", "week": 8,
     "risk_prob": 0.70, "risk_level": 0, "pred_event_week": -1,
     "risk_level_name": "正常", "top_factors": "[]"},
]


def test_stat_counts_students_not_course_rows():
    stat = _repo(ROWS).admin_stat("2014J", 8)

    assert stat["total"] == 4, "total 必须是学生数，不是学生-课程对"
    assert stat["record_total"] == 5, "原始行数要单独透出，不能丢"
    # A 的两门课取 max：0 和 2 → 2
    assert stat["level_2"] == 2
    assert stat["level_1"] == 1
    assert stat["level_0"] == 1
    assert stat["total"] == stat["level_0"] + stat["level_1"] + stat["level_2"]
    assert stat["alert_rate"] == pytest.approx(3 / 4, abs=1e-4)


def test_list_emits_one_row_per_student():
    data = _repo(ROWS).admin_list("2014J", 8, None, 1, 100)

    assert data["total"] == 4
    ids = [it["stu_id"] for it in data["items"]]
    assert len(ids) == len(set(ids)) == 4


def test_list_keeps_the_highest_level_course():
    """A 有两门课，列表里应保留 level=2 那门（DDD），而不是 prob 更高的 CCC。"""

    data = _repo(ROWS).admin_list("2014J", 8, None, 1, 100)
    a = next(it for it in data["items"] if it["stu_id"] == "A")

    assert a["course_code"] == "DDD"
    assert a["risk_level"] == 2


def test_list_sorted_by_level_before_prob():
    """prob 与 level 非单调，排序必须 level 优先，否则筛高危会混进低 level。"""

    levels = [it["risk_level"] for it in _repo(ROWS).admin_list("2014J", 8, None, 1, 100)["items"]]
    assert levels == sorted(levels, reverse=True)


def test_list_level_filter_matches_stat():
    """列表按 level 筛出的条数要和统计里的分档人数一致——这是最容易对不上的地方。"""

    repo = _repo(ROWS)
    stat = repo.admin_stat("2014J", 8)
    for level, key in ((2, "level_2"), (1, "level_1"), (0, "level_0")):
        got = repo.admin_list("2014J", 8, level, 1, 100)["total"]
        assert got == stat[key], f"level={level} 列表 {got} 条，统计 {stat[key]} 人"


# ---------------------------------------------------------------- 已发生 / 风险池
#
# 算法侧 11-17 口径：prob=1.0 的行是「事件已发生」，不算模型预测，
# 要从预警率分母拆出去。全表预警率 = 已发生占比 + 风险池预警率 ×(1−已发生占比)。

POOL_ROWS = [
    # A 已发生（prob 打满 1.0）
    {"stu_id": "A", "course_code": "CCC", "semester": "2014J", "week": 8,
     "risk_prob": 1.0, "risk_level": 2, "pred_event_week": 8,
     "risk_level_name": "高危", "top_factors": "[]"},
    # B 风险池内被预警
    {"stu_id": "B", "course_code": "CCC", "semester": "2014J", "week": 8,
     "risk_prob": 0.90, "risk_level": 2, "pred_event_week": 12,
     "risk_level_name": "高危", "top_factors": "[]"},
    # C 风险池内正常
    {"stu_id": "C", "course_code": "CCC", "semester": "2014J", "week": 8,
     "risk_prob": 0.30, "risk_level": 0, "pred_event_week": -1,
     "risk_level_name": "正常", "top_factors": "[]"},
]


def test_stat_splits_occurred_from_risk_pool():
    stat = _repo(POOL_ROWS).admin_stat("2014J", 8)

    assert stat["total"] == 3
    assert stat["occurred_count"] == 1
    assert stat["risk_pool_count"] == 2
    assert stat["pool_alert_count"] == 1
    assert stat["occurred_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert stat["pool_alert_rate"] == pytest.approx(1 / 2, abs=1e-4)


def test_alert_rate_equals_occurred_plus_pool_formula():
    """全表预警率必须能被「已发生 + 风险池」两个分量还原，否则前端没法解释。"""

    stat = _repo(POOL_ROWS).admin_stat("2014J", 8)

    assert stat["alert_rate"] == pytest.approx(stat["alert_rate_combined"], abs=1e-4)
    assert stat["pool_level_2"] == 1
    assert stat["pool_level_0"] == 1


def test_occurred_uses_risk_state_when_column_present():
    """有权威字段 risk_state 时必须用它，而不是 prob>=1.0 代理。

    构造一组两种判定会给出**不同结果**的数据：
      A: prob=1.0 但 risk_state=0 —— 代理会误判成已发生
      B: prob=0.3 但 risk_state=1 —— 代理会漏判
      C: 都正常
    用 risk_state → 已发生={B}，风险池={A,C}，池内 A 是 level 2 所以预警 1 人
    用 prob 代理 → 已发生={A}，风险池={B,C}，池内全是 level 0 所以预警 0 人
    """

    rows = [
        {"stu_id": "A", "course_code": "CCC", "semester": "2014J", "week": 8,
         "risk_prob": 1.0, "risk_level": 2, "pred_event_week": 8,
         "risk_level_name": "高危", "top_factors": "[]", "risk_state": 0},
        {"stu_id": "B", "course_code": "CCC", "semester": "2014J", "week": 8,
         "risk_prob": 0.30, "risk_level": 0, "pred_event_week": -1,
         "risk_level_name": "正常", "top_factors": "[]", "risk_state": 1},
        {"stu_id": "C", "course_code": "CCC", "semester": "2014J", "week": 8,
         "risk_prob": 0.20, "risk_level": 0, "pred_event_week": -1,
         "risk_level_name": "正常", "top_factors": "[]", "risk_state": 0},
    ]
    stat = _repo(rows).admin_stat("2014J", 8)

    assert stat["occurred_source"] == "risk_state"
    assert stat["occurred_count"] == 1
    assert stat["risk_pool_count"] == 2
    # A 留在风险池里，且是 level 2 —— 代理口径下这个数会是 0
    assert stat["pool_alert_count"] == 1


def test_occurred_falls_back_to_prob_proxy_without_risk_state():
    stat = _repo(POOL_ROWS).admin_stat("2014J", 8)
    assert stat["occurred_source"] == "prob_proxy"
