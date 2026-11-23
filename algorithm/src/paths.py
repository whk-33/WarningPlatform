"""算法侧统一路径解析：环境变量优先，其次多候选目录探测。

背景：数据目录在不同机器上布局不同（`data/oulad/processed_full_demo`、
`data/processed/processed_full_demo`、`data/processed` …），早期脚本各自硬编码
旧路径，换机器 / 换数据包必挂。所有算法侧脚本与测试统一走本模块。

规则：
1. 设置了 WP_PROCESSED / WP_INTERIM / WP_MODELS 时优先使用；
   但若该目录缺少哨兵文件，**直接报错**而不是静默回退，避免跑错数据。
2. 未设置环境变量时，按候选列表顺序取第一个含哨兵文件的目录。
3. 全部候选都不命中时报错，并列出已尝试的目录。
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALGO_ROOT = PROJECT_ROOT / "algorithm"
DATA_ROOT = ALGO_ROOT / "data"


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else None


def _processed_candidates() -> list[Path]:
    return [
        DATA_ROOT / "oulad" / "processed_full_demo",
        DATA_ROOT / "processed" / "processed_full_demo",
        DATA_ROOT / "processed_full_demo",
        DATA_ROOT / "processed",
        DATA_ROOT / "oulad" / "processed_demo",
        DATA_ROOT / "oulad" / "processed",
    ]


def _interim_candidates() -> list[Path]:
    return [
        DATA_ROOT / "oulad" / "interim_demo",
        DATA_ROOT / "interim" / "interim_demo",
        DATA_ROOT / "interim_demo",
        DATA_ROOT / "interim",
        DATA_ROOT / "oulad" / "interim",
    ]


def _resolve(env_name: str, candidates: list[Path], require: str, what: str) -> Path:
    env = _env_path(env_name)
    if env is not None:
        if (env / require).exists():
            return env
        raise FileNotFoundError(
            f"{env_name}={env} 无效：缺少 {require}。"
            f"请指向包含 {require} 的目录，或取消该环境变量走自动探测。"
        )
    for cand in candidates:
        if (cand / require).exists():
            return cand
    tried = "\n  ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        f"找不到{what}（缺少 {require}）。已尝试：\n  {tried}\n"
        f"可用 {env_name} 显式指定目录。"
    )


def resolve_processed(require: str = "tft_weekly.parquet") -> Path:
    """打分产物目录。默认哨兵文件 tft_weekly.parquet。"""
    return _resolve("WP_PROCESSED", _processed_candidates(), require, "打分产物目录")


def resolve_interim(require: str = "student_info.parquet") -> Path:
    """清洗层 interim 目录。默认哨兵文件 student_info.parquet。"""
    return _resolve("WP_INTERIM", _interim_candidates(), require, "interim 数据目录")


def resolve_models() -> Path:
    """模型权重目录（*.pt）。不校验内容，由调用方按文件名报告缺失。"""
    return _env_path("WP_MODELS") or (ALGO_ROOT / "models")


def describe_models_hint(models_dir: Path, pattern: str) -> str:
    """模型缺失时的统一提示，避免各脚本各写一套。"""
    return (
        f"缺少模型权重 {models_dir / pattern}。\n"
        f"  · 权重文件不入 git（.gitignore 的 *.pt），需从发布包解压到 {models_dir}\n"
        f"  · 或用 WP_MODELS 指向权重所在目录\n"
        f"  · 仅需离线产物（risk_scores.parquet）时，不必运行推理桥"
    )
