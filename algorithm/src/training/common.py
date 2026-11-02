"""阶段二公共工具：路径解析、随机种子、指标计算、产物落盘。

约定（与 docs/数据字段约定.md 一致）：
- 数据目录默认 algorithm/data/oulad/processed_full_demo，可用 WP_PROCESSED 覆盖；
- 切分一律用 tft_weekly/dkt_sequences 里的 split_group（学生级），禁止随机切行；
- 指标只用 numpy 实现，不引入 scikit-learn（本机 Python 3.13 装不上预编译包）。
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROCESSED = PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_full_demo"
DEFAULT_INTERIM = PROJECT_ROOT / "algorithm" / "data" / "oulad" / "interim_demo"
REPORTS_DIR = PROJECT_ROOT / "algorithm" / "reports"
MODELS_DIR = PROJECT_ROOT / "algorithm" / "models"
SUBGRAPH_DIR = PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_full_demo" / "subgraph"


def resolve_processed(arg: str | None = None) -> Path:
    """训练脚本的 --processed 参数优先级：命令行 > WP_PROCESSED > 默认全量目录。"""
    if arg:
        return Path(arg).resolve()
    env = os.environ.get("WP_PROCESSED")
    if env:
        return Path(env).resolve()
    return DEFAULT_PROCESSED


def resolve_interim(arg: str | None = None) -> Path:
    if arg:
        return Path(arg).resolve()
    env = os.environ.get("WP_INTERIM")
    if env:
        return Path(env).resolve()
    return DEFAULT_INTERIM


def load(processed: Path, name: str) -> pd.DataFrame:
    path = Path(processed) / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"缺少数据表 {path}，先跑 10_adapt_oulad.py + 05_build_graph.py")
    return pd.read_parquet(path)


def set_seed(seed: int = SEED) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
    except ImportError:  # 基线脚本不依赖 torch
        pass


def _as_1d(y, s) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(y).astype(int).ravel()
    s = np.asarray(s, dtype=float).ravel()
    ok = np.isfinite(s)
    return y[ok], s[ok]


def roc_auc(y, score) -> float:
    """Mann-Whitney 形式的 AUC，带并列秩处理（与 sklearn 一致）。"""
    y, score = _as_1d(y, score)
    n_pos = int(y.sum())
    n_neg = int(len(y) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = pd.Series(score).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def average_precision(y, score) -> float:
    """PR-AUC（sklearn 口径的 AP = Σ precision_i / n_pos，按分数降序）。"""
    y, score = _as_1d(y, score)
    if y.sum() == 0:
        return float("nan")
    order = np.argsort(-score, kind="mergesort")
    y = y[order]
    tp = np.cumsum(y)
    k = np.arange(1, len(y) + 1)
    precision = tp / k
    return float(precision[y == 1].sum() / y.sum())


def recall_at_fraction(y, score, frac: float = 0.1) -> dict:
    """按分数取前 frac 比例作为预警名单时的召回/精确率（产品口径的核心指标）。"""
    y, score = _as_1d(y, score)
    n = len(y)
    if n == 0 or y.sum() == 0:
        return {"k": 0, "recall": float("nan"), "precision": float("nan")}
    k = max(1, int(round(n * frac)))
    order = np.argsort(-score, kind="mergesort")[:k]
    hit = int(y[order].sum())
    return {
        "k": k,
        "recall": float(hit / y.sum()),
        "precision": float(hit / k),
        "lift": float((hit / k) / (y.sum() / n)),
    }


def brier(y, prob) -> float:
    y, prob = _as_1d(y, prob)
    return float(np.mean((prob - y) ** 2))


def calibration_table(y, prob, bins: int = 10) -> list[dict]:
    y, prob = _as_1d(y, prob)
    edges = np.linspace(0.0, 1.0, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (prob >= lo) & (prob < hi if hi < 1.0 else prob <= hi)
        if m.sum() == 0:
            continue
        out.append(
            {"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()),
             "mean_pred": round(float(prob[m].mean()), 4),
             "mean_true": round(float(y[m].mean()), 4)}
        )
    return out


def evaluate(y, prob) -> dict:
    """统一指标包，所有模型报告同一组数字。"""
    y, prob = _as_1d(y, prob)
    return {
        "n": int(len(y)),
        "pos_rate": round(float(y.mean()) if len(y) else float("nan"), 4),
        "auc": round(roc_auc(y, prob), 4),
        "pr_auc": round(average_precision(y, prob), 4),
        "recall_at_10pct": round(recall_at_fraction(y, prob, 0.1)["recall"], 4),
        "precision_at_10pct": round(recall_at_fraction(y, prob, 0.1)["precision"], 4),
        "brier": round(brier(y, prob), 4),
    }


def save_json(obj, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return path


def standardizer(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """列标准化。常数列（std=0，例如 OULAD 的 video_watch_cum）标准差置 1，避免 NaN。"""
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std = np.where(std < 1e-8, 1.0, std)
    return mean, std


def setup_matplotlib():
    """matplotlib 中文字体 + 统一风格。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for font in ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC"]:
        try:
            plt.rcParams["font.sans-serif"] = [font, "DejaVu Sans"]
            break
        except Exception:  # pragma: no cover
            continue
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 140
    return plt


# 特征中文名（图表与前端"影响因子"共用一套，避免两边各写一份）
FEATURE_CN = {
    "attendance_rate": "出勤率",
    "ppt_clicks_cum": "课件点击数",
    "video_watch_cum": "视频观看时长",
    "absent_count_cum": "累计缺勤次数",
    "quiz_accuracy_cum": "测验正确率",
    "hw_score_cum": "作业平均分",
    "hw_days_late_cum": "作业迟交天数",
    "gender": "性别",
    "entrance_score": "入学成绩",
    "past_retake_count": "历史重修次数",
    "special_flag": "特殊情况标记",
    "credits": "课程学分",
    "elective": "是否选修",
    "major_A Level or Equivalent": "学历：A Level",
    "major_HE Qualification": "学历：高等教育",
    "major_Lower Than A Level": "学历：低于 A Level",
    "major_No Formal quals": "学历：无正式学历",
    "major_Post Graduate Qualification": "学历：研究生",
    "week_norm": "学期进度",
    "is_late_half": "是否学期后半段",
    "weeks_to_final": "距期末周数",
}


def cn(name: str) -> str:
    return FEATURE_CN.get(name, name)
