"""FastAPI 入口。

启动：cd backend && uvicorn app.main:app --reload --port 8000
文档：http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, student
from app.config import settings
from app.core.response import ApiError, fail, ok
from app.data import users
from app.data.repo import get_repo

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="大学生学业关联分析与预警平台 · 后端 API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:3000", "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ApiError)
async def _handle_api_error(request: Request, exc: ApiError):
    return fail(exc.code, exc.msg, exc.data)


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(request: Request, exc: RequestValidationError):
    errors = [
        {"field": ".".join(str(x) for x in err.get("loc", [])[1:]), "msg": err.get("msg", "")}
        for err in exc.errors()
    ]
    first = errors[0] if errors else {"field": "", "msg": ""}
    return fail(422, f"参数校验失败：{first['field']} {first['msg']}".strip(), errors)


@app.exception_handler(Exception)
async def _handle_unexpected(request: Request, exc: Exception):
    logging.exception("未捕获异常: %s", exc)
    return fail(500, "服务器内部错误，请查看后端日志")


@app.on_event("startup")
def _startup() -> None:
    repo = get_repo()
    users.init_db()
    created = users.sync(
        repo.student_accounts(),
        settings.demo_admin_account,
        settings.demo_password,
        settings.demo_student_accounts,
    )
    logging.getLogger("uvicorn").info(
        "数据源=%s 学期=%s 学生=%d 账号同步新增=%d",
        repo.source, repo.current_semester, len(repo.students), created,
    )


app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(student.router)


@app.get("/api/health", tags=["meta"], summary="健康检查与数据源信息")
def health():
    repo = get_repo()
    return ok({
        "status": "ok",
        "version": settings.version,
        "data_source": repo.source,           # parquet = 真实算法产物；demo = 内置演示数据
        "current_semester": repo.current_semester,
        "semesters": repo.semesters,
        "students": int(len(repo.students)),
        "courses": int(len(repo.courses)),
        "accounts": int(users.count()),
        "behaviour_metrics_is_proxy": "platform_log" not in repo.raw,
    })


@app.get("/api/meta/semesters", tags=["meta"], summary="学期列表（前端下拉框用）")
def semesters():
    repo = get_repo()
    return ok({"current": repo.current_semester, "list": repo.semesters})


@app.get("/", include_in_schema=False)
def root():
    return ok({"service": settings.app_name, "docs": "/docs", "health": "/api/health"})
