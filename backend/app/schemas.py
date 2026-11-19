"""请求体模型（只约束入参，响应以字典形式返回，字段定义见 docs/接口约定.md）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AdminLoginIn(BaseModel):
    username: str = Field(..., min_length=1, description="管理员账号")
    password: str = Field(..., min_length=1, description="密码")


class StudentLoginIn(BaseModel):
    student_id: str = Field(..., min_length=1, description="学号（student_no，不是 stu_ 内部 ID）")
    password: str = Field(..., min_length=1, description="密码")


class WarnInterveneIn(BaseModel):
    warn_id: str = Field(..., min_length=1, description="预警编号")
    action: str = Field(..., min_length=1, max_length=20, description="处理方式，取值见 GET /api/admin/warn/interventions 返回的 actions")
    note: str | None = Field(default=None, max_length=500, description="处理备注")
