"""管理员端接口。全部需要 admin 角色。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.config import settings
from app.core.deps import require_admin
from app.core.response import ok
from app.data.repo import get_repo
from app.data.risk_repo import get_risk_repo
from app.services import build_report

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _paging(page: int, page_size: int) -> tuple[int, int]:
    page_no = max(1, int(page))
    size = min(max(1, int(page_size)), settings.page_size_max)
    return page_no, size


@router.get("/home/stat", summary="首页统计卡片 + 风险分布")
def home_stat(
    semester: str | None = Query(default=None, description="学期，缺省取最新学期"),
    week: int | None = Query(
        default=None,
        ge=1, le=16,
        description="教学周，缺省取默认周（期中第 8 周）。与 /api/admin/risk/stat 完全同源",
    ),
):
    """首页卡片。

    v1.3 起**不再**使用规则式（期末结算）口径，改为直接复用模型风险分，
    与 `/api/admin/risk/stat` 同一个数据源、同一个周次、同一个学生级聚合，
    两个接口的数字必然一致。

    历史口径（规则式整学期结算）保留在 `get_repo().stat()`，仅供需要「期末回顾」
    视图的场景调用，任何首页/看板都不应再展示它——它没有周维度，
    本质是"考完之后回头看"，预警率 51% 也不具备可干预性。
    """

    stat = get_risk_repo().admin_stat(semester, week)
    total = int(stat["total"])
    high = int(stat["level_2"])
    mid = int(stat["level_1"])
    normal = int(stat["level_0"])
    return ok({
        # ---- 以下字段名与旧版完全一致，前端无需改动 ----
        "semester": stat["semester"],
        "total_student": total,
        "warn_count": high + mid,
        "high_count": high,
        "mid_count": mid,
        "normal_count": normal,
        "risk_distribute": {"高危": high, "警惕": mid, "正常": normal},
        "warn_rate": stat["alert_rate"],
        # ---- 已发生 / 风险池拆分（算法侧 11-17 口径）----
        # 全表预警率 = 已发生占比 + 风险池预警率 ×(1−已发生占比)。
        # 只看 warn_rate 会把"已经出事的"和"模型预测会出事的"混成一个数，
        # 老师看到 34.6% 会以为模型误报率高，其实是 24.7% 已经发生了。
        "occurred_count": stat.get("occurred_count"),
        "occurred_rate": stat.get("occurred_rate"),
        "risk_pool_count": stat.get("risk_pool_count"),
        "pool_alert_count": stat.get("pool_alert_count"),
        "pool_alert_rate": stat.get("pool_alert_rate"),
        "occurred_source": stat.get("occurred_source"),
        # ---- 新增：把口径本身透出去，避免"两个接口数字不同"的猜测 ----
        "week": stat["week"],
        "caliber": f"model_week{stat['week']}",
        "caliber_text": f"模型风险分 · 第{stat['week']}周 · 学生级 max(课程级)",
        "data_source": stat["data_source"],
        "sample_based": stat["sample_based"],
        "record_total": stat.get("record_total"),
        "deprecated_fields": [],
    })


@router.get("/student/list", summary="学生列表（分页 + 搜索 + 风险筛选）")
def student_list(
    keyword: str | None = Query(default=None, description="学号 / 姓名 / stu_id 模糊搜索"),
    semester: str | None = None,
    risk_level: int | None = Query(default=None, ge=0, le=2, description="0 正常 / 1 警惕 / 2 高危"),
    page: int = 1,
    page_size: int = settings.page_size_default,
):
    page_no, size = _paging(page, page_size)
    return ok(get_repo().search_students(keyword, semester, risk_level, page_no, size))


@router.get("/student/detail", summary="学生详情 + 当学期课程成绩")
def student_detail(
    student_id: str = Query(..., description="学号或 stu_id"),
    semester: str | None = None,
):
    repo = get_repo()
    stu_id = repo.resolve_stu_id(student_id)
    row = repo.student_row(stu_id, semester)
    return ok({**row, "course_list": repo.course_list(stu_id, semester)})


@router.get("/gnn/graph", summary="全局学生-课程异质图（默认 top50 高风险学生）")
def gnn_graph(
    limit_students: int = Query(default=settings.graph_default_students, ge=1, le=200),
    course_code: str | None = Query(default=None, description="只看某门课的学生"),
    semester: str | None = None,
):
    return ok(get_repo().graph(limit_students, course_code, semester))


@router.get("/warn/list", summary="预警列表（时间范围筛选）")
def warn_list(
    start_time: str | None = Query(default=None, description="YYYY-MM-DD"),
    end_time: str | None = Query(default=None, description="YYYY-MM-DD"),
    risk_level: int | None = Query(default=None, ge=1, le=2),
    page: int = 1,
    page_size: int = settings.page_size_default,
    semester: str | None = None,
):
    page_no, size = _paging(page, page_size)
    repo = get_repo()
    data = repo.warning_list(start_time, end_time, risk_level, page_no, size, semester)
    # 这张表是规则式（期末结算）口径，且单位是「学生-课程」记录，
    # 与首页/risk 接口的模型口径、学生级人数不是同一个数。
    # 不标注清楚，前端会拿 total 去对首页卡片，然后误报 bug。
    data.update({
        "caliber": "rule_settlement",
        "caliber_text": "规则式 · 整学期结算 · 学生-课程级记录",
        "record_unit": "student_course",
        "semester": repo.resolve_semester(semester),
        "comparable_with_home_stat": False,
    })
    return ok(data)


@router.get("/warn/report", summary="预警分析报告")
def warn_report(warn_id: str = Query(..., description="预警编号")):
    repo = get_repo()
    warn = repo.warning_by_id(warn_id)
    return ok(build_report(repo, warn))
