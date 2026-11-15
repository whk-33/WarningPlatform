"""阶段四风险分接口。

契约：`docs/阶段四_风险接口契约.md` §3（5 个接口）。
数据源：`app/data/risk_repo.py`（parquet 优先，mock 兜底）。

与阶段一的 `/api/admin/student/*`、`/api/admin/warn/*` **并行存在**，互不替换（契约 B3）：
前端对比验收后再决定是否切到这套 risk_level 口径。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.config import settings
from app.core.deps import current_user, require_admin
from app.core.response import ApiError, ok
from app.data.risk_repo import get_risk_repo

router = APIRouter(prefix="/api/risk", tags=["risk"])
admin_router = APIRouter(
    prefix="/api/admin/risk", tags=["admin-risk"], dependencies=[Depends(require_admin)]
)


def _guard_owner(user: dict, stu_id: str) -> None:
    """学生端只能看自己：stu_id 与 token 不一致直接 403，不泄露"这个学生存在"这一事实。"""

    if user.get("role") != "student":
        return
    if str(user.get("stu_id") or "") != str(stu_id):
        raise ApiError(403, "只能查看自己的风险数据")


@router.get("/weekly", summary="单周风险（含归因）")
def weekly(
    stu_id: str = Query(..., description="学生 ID，如 stu_100621"),
    course_code: str = Query(..., description="课程代码，如 CCC"),
    semester: str | None = Query(default=None, description="学期，缺省取最新有数据的学期"),
    week: int | None = Query(default=None, ge=0, le=15, description="周次 0-15，缺省取最新一周"),
    user: dict = Depends(current_user),
):
    _guard_owner(user, stu_id)
    return ok(get_risk_repo().weekly(stu_id, course_code, semester, week))


@router.get("/student/{stu_id}", summary="整学期风险曲线（一次返回全部周次）")
def student_curve(
    stu_id: str,
    semester: str | None = Query(default=None),
    user: dict = Depends(current_user),
):
    _guard_owner(user, stu_id)
    return ok(get_risk_repo().student_curve(stu_id, semester))


@router.get("/factors", summary="单周归因 top3（按 contrib 降序）")
def factors(
    stu_id: str = Query(...),
    course_code: str = Query(...),
    semester: str | None = Query(default=None),
    week: int | None = Query(default=None, ge=0, le=15),
    user: dict = Depends(current_user),
):
    _guard_owner(user, stu_id)
    return ok(get_risk_repo().factors(stu_id, course_code, semester, week))


@admin_router.get("/list", summary="风险列表（按 risk_prob 降序，可按等级筛选）")
def risk_list(
    semester: str | None = Query(default=None),
    week: int | None = Query(default=None, ge=0, le=15),
    level: int | None = Query(default=None, ge=0, le=2, description="0 低 / 1 中 / 2 高"),
    page: int = Query(default=1, ge=1),
    size: int | None = Query(default=None, ge=1, le=settings.page_size_max,
                             description="每页条数，契约字段名"),
    page_size: int | None = Query(default=None, ge=1, le=settings.page_size_max,
                                  description="每页条数（兼容 /api/admin/student/list 的命名）"),
):
    # 两个参数名都收，前端不用为新接口改习惯
    per = size or page_size or settings.page_size_default
    return ok(get_risk_repo().admin_list(semester, week, level, page, min(per, settings.page_size_max)))


@admin_router.get("/stat", summary="风险等级分布与预警率")
def risk_stat(
    semester: str | None = Query(default=None),
    week: int | None = Query(default=None, ge=0, le=15),
):
    return ok(get_risk_repo().admin_stat(semester, week))
