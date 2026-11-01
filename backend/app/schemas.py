"""请求体模型（只约束入参，响应以字典形式返回，字段定义见 docs/接口约定.md）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AdminLoginIn(BaseModel):
    username: str = Field(..., min_length=1, description="管理员账号")
    password: str = Field(..., min_length=1, description="密码")


class StudentLoginIn(BaseModel):
    student_id: str = Field(..., min_length=1, description="学号（student_no，不是 stu_ 内部 ID）")
    password: str = Field(..., min_length=1, description="密码")
