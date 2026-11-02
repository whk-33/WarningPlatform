"""登录 / 登出。

登录失败统一返回 401，不区分"账号不存在"和"密码错误"，避免账号枚举。
登出是空操作：JWT 无状态，服务端不维护会话，前端清掉 token 即可。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import current_user
from app.core.response import ApiError, ok
from app.core.security import create_token
from app.data import users
from app.schemas import AdminLoginIn, StudentLoginIn

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login/admin", summary="管理员登录")
def login_admin(body: AdminLoginIn):
    row = users.authenticate(body.username.strip(), body.password, "admin")
    if row is None:
        raise ApiError(401, "账号或密码错误")
    token = create_token(sub=str(row["account"]), role="admin", extra={"name": row["display_name"]})
    return ok({
        "token": token,
        "username": row["account"],
        "name": row["display_name"],
        "role": "admin",
    })


@router.post("/login/student", summary="学生登录")
def login_student(body: StudentLoginIn):
    row = users.authenticate(body.student_id.strip(), body.password, "student")
    if row is None:
        raise ApiError(401, "学号或密码错误")
    token = create_token(
        sub=str(row["stu_id"]),
        role="student",
        extra={"stu_id": row["stu_id"], "name": row["display_name"]},
    )
    return ok({
        "token": token,
        "student_id": row["account"],
        "student_no": row["account"],
        "stu_id": row["stu_id"],
        "name": row["display_name"],
        "role": "student",
    })


@router.post("/logout", summary="退出登录")
def logout(user: dict = Depends(current_user)):
    return ok(None, msg="已退出")
