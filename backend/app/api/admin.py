"""管理员端接口。全部需要 admin 角色。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.config import settings
from app.core.deps import require_admin
from app.core.response import ApiError, ok
from app.data import interventions
from app.data.repo import get_repo
from app.schemas import WarnInterveneIn
from app.services import build_report

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _paging(page: int, page_size: int) -> tuple[int, int]:
    page_no = max(1, int(page))
    size = min(max(1, int(page_size)), settings.page_size_max)
    return page_no, size


@router.get("/home/stat", summary="首页统计卡片 + 风险分布")
def home_stat(semester: str | None = Query(default=None, description="学期，缺省取最新学期")):
    return ok(get_repo().stat(semester))


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
    data = get_repo().warning_list(start_time, end_time, risk_level, page_no, size, semester)

    # 批量标注处理状态，避免前端逐条查干预记录
    latest = interventions.latest_map()
    for item in data["list"]:
        hit = latest.get(item["warn_id"])
        item["intervened"] = hit is not None
        item["last_intervention_action"] = hit["action"] if hit else ""
        item["last_intervention_time"] = hit["created_at"] if hit else ""
    return ok(data)


@router.get("/warn/report", summary="预警分析报告")
def warn_report(warn_id: str = Query(..., description="预警编号")):
    repo = get_repo()
    warn = repo.warning_by_id(warn_id)
    data = build_report(repo, warn)
    data["interventions"] = interventions.list_by_warn(warn_id)
    data["intervention_actions"] = list(interventions.ACTIONS)
    return ok(data)


@router.get("/warn/interventions", summary="预警干预记录")
def warn_interventions(warn_id: str = Query(..., description="预警编号")):
    return ok({
        "list": interventions.list_by_warn(warn_id),
        "actions": list(interventions.ACTIONS),
    })


@router.post("/warn/intervene", summary="登记预警干预记录")
def warn_intervene(payload: WarnInterveneIn, user: dict = Depends(require_admin)):
    if payload.action not in interventions.ACTIONS:
        raise ApiError(400, f"处理方式不支持：{payload.action}")
    # 预警编号不存在直接 404，避免脏数据入库
    get_repo().warning_by_id(payload.warn_id)
    item = interventions.add(
        payload.warn_id,
        payload.action,
        (payload.note or "").strip(),
        str(user.get("sub") or ""),
    )
    return ok(item, msg="干预记录已保存")
