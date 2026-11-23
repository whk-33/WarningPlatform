"""推理桥（契约 B6）：`risk_scores` 查不到时，按需调用算法侧 `30_online_risk.py`。

## 为什么走子进程而不是 import

算法脚本顶部就 `import torch`，并且 `sys.path` 会插入 `algorithm/src`。
在 FastAPI 主进程里这么干有三个问题：

1. torch 常驻 300MB+，而后端 99% 的请求根本不碰模型；
2. torch 的 DLL 加载顺序对 numpy/MKL 敏感，和 uvicorn reload 一起用容易拖挂进程；
3. `sys.path` 被污染后，`paths`、`training` 这些模块名会盖住后端自己的模块。

子进程退出即释放，脚本自己注入 `sys.path`，与后端工作目录无关。

## 降级原则（比功能本身更重要）

**桥不可用 = 保持接桥前的行为（404），而不是 500。**

以下任何一种情况都返回 `None`，由 `RiskRepo` 回落成 404：

- 未启用（`WP_BRIDGE=0`）或脚本不存在；
- 子进程超时（默认 60s）；
- 非 0 退出码（典型：机器上没有 torch）；
- stdout 不是合法 JSON，或返回体里带 `error`。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `WP_BRIDGE` | `auto` | `1`/`0` 强制开关；`auto` = 脚本存在即启用 |
| `WP_BRIDGE_PYTHON` | 当前解释器 | 跑桥的 python，必须装了 torch |
| `WP_BRIDGE_SCRIPT` | `<repo>/algorithm/src/serving/30_online_risk.py` | 桥脚本路径 |
| `WP_BRIDGE_TIMEOUT` | `60` | 单次调用超时（秒） |
| `WP_BRIDGE_TTL` | `300` | 结果缓存（含失败）的秒数，`0` 表示不缓存 |

`WP_PROCESSED` / `WP_MODELS` / `WP_INTERIM` 会自动透传给子进程——桥脚本正是靠它们找数据和模型。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT

log = logging.getLogger(__name__)

DEFAULT_SCRIPT = PROJECT_ROOT / "algorithm" / "src" / "serving" / "30_online_risk.py"
MODES = ("weekly", "student", "subgraph")

# 缓存里用一个不可伪造的哨兵区分「没查过」和「查过、结果是失败(None)」
_MISS = object()
_CACHE: dict[tuple, tuple[float, Any]] = {}
_LOCK = threading.Lock()

# 最近一次失败的原因。验收方提过一个矛盾点：桥挂了到底回 404 还是 503。
# 我们保持 404（前端空态不用改），但用这个字段把「基础设施坏了」和
# 「这个学生确实没有数据」区分开——排障时不必翻日志。
_LAST_ERROR: dict[str, Any] = {}


# ------------------------------------------------------------------ 配置

def script_path() -> Path:
    raw = (os.getenv("WP_BRIDGE_SCRIPT") or "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_SCRIPT


def python_exe() -> str:
    """跑桥的解释器。默认用后端自己的解释器（同一个 venv 里装了 torch 就能直接用）。"""

    return (os.getenv("WP_BRIDGE_PYTHON") or "").strip() or sys.executable


def timeout_seconds() -> float:
    raw = (os.getenv("WP_BRIDGE_TIMEOUT") or "").strip()
    try:
        return float(raw) if raw else 60.0
    except ValueError:
        return 60.0


def ttl_seconds() -> float:
    raw = (os.getenv("WP_BRIDGE_TTL") or "").strip()
    try:
        return float(raw) if raw else 300.0
    except ValueError:
        return 300.0


def enabled() -> bool:
    """`auto` 模式下只看脚本在不在——本机没装 torch 时不该让配置看起来「已启用」。"""

    flag = (os.getenv("WP_BRIDGE") or "auto").strip().lower()
    if flag in ("0", "false", "off", "no"):
        return False
    if flag in ("1", "true", "on", "yes"):
        return True
    return script_path().is_file()


def describe() -> dict:
    """给 `/api/health` 和排障用：桥到底能不能用、为什么不能用。"""

    script = script_path()
    is_on = enabled()
    reason = ""
    if not is_on:
        flag = (os.getenv("WP_BRIDGE") or "auto").strip().lower()
        reason = "disabled_by_env" if flag in ("0", "false", "off", "no") else "script_not_found"
    return {
        "enabled": is_on,
        "script": str(script),
        "script_exists": script.is_file(),
        "python": python_exe(),
        "timeout_s": timeout_seconds(),
        "reason": reason,
        "last_error": last_error(),
    }


def last_error() -> dict:
    """最近一次桥调用失败的原因（成功即清空）。空 dict 表示本次进程内还没失败过。"""

    with _LOCK:
        return dict(_LAST_ERROR)


def _note_error(reason: str, detail: str = "") -> None:
    with _LOCK:
        _LAST_ERROR.clear()
        _LAST_ERROR.update(
            {"reason": reason, "detail": (detail or "")[:300], "at": time.time()}
        )


def _clear_error() -> None:
    with _LOCK:
        _LAST_ERROR.clear()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


# ------------------------------------------------------------------ 缓存

def _cache_get(key: tuple):
    ttl = ttl_seconds()
    if ttl <= 0:
        return _MISS
    with _LOCK:
        hit = _CACHE.get(key)
    if not hit:
        return _MISS
    expires_at, payload = hit
    if expires_at < time.monotonic():
        with _LOCK:
            _CACHE.pop(key, None)
        return _MISS
    return payload


def _cache_set(key: tuple, payload: Any) -> None:
    ttl = ttl_seconds()
    if ttl <= 0:
        return
    with _LOCK:
        _CACHE[key] = (time.monotonic() + ttl, payload)


# ------------------------------------------------------------------ 调用

def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    # 子进程 print(json.dumps(..., ensure_ascii=False))，不强制 utf-8 在 Windows 上会变成 cp936
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    return env


def _parse_stdout(stdout: str) -> dict | None:
    """桥可能在被 import 时就打了 warning，所以取最后一行非空输出。"""

    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except (ValueError, TypeError):
            continue
        return payload if isinstance(payload, dict) else None
    return None


def run(mode: str, **params: str | int) -> dict | None:
    """调一次桥，返回 JSON dict；任何失败都返回 None（调用方回落 404）。"""

    if mode not in MODES:
        raise ValueError(f"未知的桥模式 {mode}，可选 {MODES}")
    if not enabled():
        return None

    key = (mode, tuple(sorted((k, str(v)) for k, v in params.items())))
    cached = _cache_get(key)
    if cached is not _MISS:
        return cached

    script = script_path()
    cmd = [python_exe(), str(script), mode]
    for k, v in params.items():
        cmd.extend([f"--{k}", str(v)])

    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds(),
            cwd=str(script.parent),      # 桥自己注入 sys.path，这里只是给相对路径兜底
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        log.warning("推理桥超时（%.0fs）：%s", timeout_seconds(), " ".join(cmd[-8:]))
        _note_error("timeout", f"{timeout_seconds():.0f}s")
        _cache_set(key, None)
        return None
    except OSError as exc:
        log.warning("推理桥无法启动：%s（%s）", exc, python_exe())
        _note_error("spawn_failed", f"{exc} ({python_exe()})")
        _cache_set(key, None)
        return None

    elapsed = time.perf_counter() - started
    payload = _parse_stdout(proc.stdout)

    if proc.returncode != 0 or (payload and payload.get("error")):
        err = (payload or {}).get("error") or (proc.stderr or "").strip().splitlines()[-1:]
        log.warning("推理桥失败（exit=%s，%.1fs）：%s", proc.returncode, elapsed, err)
        _note_error("non_zero_exit" if proc.returncode else "error_payload", str(err))
        _cache_set(key, None)
        return None

    if payload is None:
        log.warning("推理桥输出不是 JSON（%.1fs）：%r", elapsed, (proc.stdout or "")[:200])
        _note_error("bad_json", (proc.stdout or "")[:200])
        _cache_set(key, None)
        return None

    log.info("推理桥 %s 完成（%.1fs）", mode, elapsed)
    _clear_error()
    _cache_set(key, payload)
    return payload


# ------------------------------------------------------------------ 便捷封装

def weekly(stu_id: str, course_code: str, semester: str, week: int) -> dict | None:
    return run("weekly", stu_id=stu_id, course_code=course_code, semester=semester, week=week)


def student_weeks(stu_id: str, semester: str) -> list[dict] | None:
    """学生 16 周曲线。桥返回 `{"weeks": [...]}`，这里只摘数组。"""

    payload = run("student", stu_id=stu_id, semester=semester)
    if not payload:
        return None
    weeks = payload.get("weeks")
    if not isinstance(weeks, list) or not weeks:
        return None
    return [w for w in weeks if isinstance(w, dict) and w.get("week") is not None]


def subgraph(stu_id: str, semester: str, week: int = 8) -> dict | None:
    return run("subgraph", stu_id=stu_id, semester=semester, week=week)
