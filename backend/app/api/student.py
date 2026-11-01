"""学生端接口。

所有接口的 stu_id 一律从 token 取（self_student_id 依赖），
前端传什么 student_id 都不影响，杜绝越权查别人数据。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import self_student_id
from app.core.response import ok
from app.data.repo import get_repo
from app.services import build_suggestions

router = APIRouter(prefix="/api/student", tags=["student"])


@router.get("/home/info", summary="个人学业画像")
def home_info(stu_id: str = Depends(self_student_id), semester: str | None = None):
    repo = get_repo()
    sem = repo.resolve_semester(semester)
    row = repo.student_row(stu_id, sem)
    trend, trend_source = repo.weekly_trend(stu_id, sem)
    return ok({
        **row,
        "course_list": repo.course_list(stu_id, sem),
        "attendance_trend": trend,
        "trend_source": trend_source,
    })


@router.get("/home/suggest", summary="学业建议")
def home_suggest(stu_id: str = Depends(self_student_id), semester: str | None = None):
    repo = get_repo()
    row = repo.student_row(stu_id, semester)
    items = build_suggestions(row, repo.course_list(stu_id, semester))
    return ok({"suggest_text": "；".join(items), "items": items})


@router.get("/gnn/graph", summary="个人相关子图")
def gnn_graph(stu_id: str = Depends(self_student_id), semester: str | None = None):
    return ok(get_repo().student_subgraph(stu_id, semester))


@router.get("/warn/list", summary="我的预警记录")
def warn_list(stu_id: str = Depends(self_student_id), semester: str | None = None):
    repo = get_repo()
    df = repo.warnings(semester)
    items = [] if df.empty else df[df["stu_id"] == stu_id].to_dict(orient="records")
    return ok({"list": items, "total": len(items)})
