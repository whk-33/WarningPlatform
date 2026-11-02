"""鉴权依赖：从 Authorization 头解析 token，并做角色校验。"""

from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Depends, Header

from app.core.response import ApiError
from app.core.security import decode_token


def current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """解析 Bearer token，返回 {sub, role, stu_id?}。"""

    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "未登录或缺少 token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return decode_token(token)
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "token 已过期，请重新登录")
    except jwt.PyJWTError:
        raise ApiError(401, "token 无效")


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise ApiError(403, "需要管理员权限")
    return user


def require_student(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "student":
        raise ApiError(403, "需要学生权限")
    return user


def self_student_id(user: dict = Depends(require_student)) -> str:
    """学生端只允许访问自己：stu_id 一律从 token 取，绝不接受前端传参。"""

    stu_id = user.get("stu_id")
    if not stu_id:
        raise ApiError(401, "token 缺少学生身份")
    return str(stu_id)
