"""统一响应体与业务异常。

约定：响应体永远是 {code, msg, data}；出错时 HTTP 状态码与 code 保持一致，
方便前端 axios 拦截器用 (res.code !== 200) 与 (err.response.status === 401) 两条路统一处理。
"""

from __future__ import annotations

import math
from typing import Any

from fastapi.responses import JSONResponse


class ApiError(Exception):
    """业务异常。"""

    def __init__(self, code: int = 400, msg: str = "请求有误", data: Any = None):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.data = data


def clean(obj: Any) -> Any:
    """把 NaN / inf / numpy 标量清成 JSON 安全值。

    pandas 里读出来的空值经常是 NaN，直接序列化会变成非法的 JSON（NaN 不是合法 token），
    前端 JSON.parse 会炸。统一在这里兜底成 null。
    """

    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [clean(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if hasattr(obj, "item"):  # numpy 标量（np.int64 / np.float64）
        try:
            return clean(obj.item())
        except Exception:
            return obj
    return obj


def ok(data: Any = None, msg: str = "success") -> dict:
    return {"code": 200, "msg": msg, "data": clean(data)}


def fail(code: int, msg: str, data: Any = None) -> JSONResponse:
    return JSONResponse(status_code=code, content=clean({"code": code, "msg": msg, "data": data}))


def page_payload(items: list, total: int, page_no: int, page_size: int) -> dict:
    return {"list": items, "total": total, "page": page_no, "page_size": page_size}
