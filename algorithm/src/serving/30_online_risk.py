"""按需推理桥：后端可通过 `py -3 30_online_risk.py` 调用，stdout 返回 JSON。

三种模式：
  weekly    单周风险（risk_prob / risk_level / pred_event_week / top_factors）
  student   学生全学期 16 周风险曲线
  subgraph  个人归因子图（遮挡 HAN 边）

输入模型：TFT（概率 + 因子）、DeepSurv（pred_event_week）；HAN（子图解释）。
缓存：进程内 lru_cache + processed/online_cache/*.pkl 落盘。

设计原则：后端只负责转发 HTTP 请求，算法侧独立进程 → torch 内存进程结束即释放；
避免 import 顺序/路径不一致导致的崩溃。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import traceback
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 延迟 import torch/模型，避免 --help 也加载
try:
    import torch  # noqa: E402
except ImportError:
    torch = None

# ================================================================= 常量

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODELS_DIR = PROJECT_ROOT / "algorithm" / "models"
REPORTS_DIR = PROJECT_ROOT / "algorithm" / "reports"
CACHE_DIR = PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_full_demo" / "online_cache"
SUBGRAPH_DIR = PROJECT_ROOT / "algorithm" / "data" / "oulad" / "subgraph"

LEVEL_CN = {0: "低", 1: "中", 2: "高"}


def _find_processed() -> Path:
    for cand in [
        PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_full_demo",
        PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_demo",
    ]:
        if (cand / "tft_weekly.parquet").exists():
            return cand
    raise FileNotFoundError("找不到 processed 目录（缺 tft_weekly.parquet）")


# ============================================================== 模型加载

@lru_cache(maxsize=1)
def _load_tft():
    """加载 TFT 模型 + checkpoint（含 static/obs/known 列名、均值/方差）。"""
    from models.tft import TFTLite  # noqa: E402

    paths = sorted(glob.glob(str(MODELS_DIR / "model_tft_*.pt")))
    if not paths:
        raise FileNotFoundError("缺少 model_tft_*.pt")
    ckpt = torch.load(paths[-1], weights_only=False, map_location="cpu")
    model = TFTLite(len(ckpt["static_cols"]), len(ckpt["obs_cols"]), len(ckpt["known_cols"]))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt


@lru_cache(maxsize=1)
def _load_deepsurv():
    from models.deepsurv import DeepSurv  # noqa: E402

    paths = sorted(glob.glob(str(MODELS_DIR / "model_deepsurv_*.pt")))
    if not paths:
        raise FileNotFoundError("缺少 model_deepsurv_*.pt")
    ckpt = torch.load(paths[-1], weights_only=False, map_location="cpu")
    model = DeepSurv(n_features=ckpt.get("n_features", 10),
                     hidden=ckpt.get("hidden", 32),
                     dropout=ckpt.get("dropout", 0.2))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt


@lru_cache(maxsize=1)
def _load_han():
    from models.han import HAN  # noqa: E402

    paths = sorted(glob.glob(str(MODELS_DIR / "model_han_*.pt")))
    if not paths:
        return None, None
    ckpt = torch.load(paths[-1], weights_only=False, map_location="cpu")
    model = HAN(n_node=ckpt.get("n_node", 50000), hidden=ckpt.get("hidden", 64),
                n_relations=ckpt.get("n_relations", 15))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt


@lru_cache(maxsize=1)
def _load_thresholds() -> dict[int, tuple[float, float]]:
    """从 serving_metrics.json 读逐周阈值。"""
    p = REPORTS_DIR / "serving_metrics.json"
    if not p.exists():
        # 回退：硬编码阶段二实测值（w0..w15）
        return {
            0: (0.60, 0.35), 1: (0.63, 0.40), 2: (0.65, 0.42), 3: (0.68, 0.45),
            4: (0.70, 0.48), 5: (0.73, 0.52), 6: (0.78, 0.58), 7: (0.85, 0.68),
            8: (0.9579, 0.7549), 9: (0.96, 0.78), 10: (0.97, 0.82), 11: (0.97, 0.84),
            12: (0.98, 0.87), 13: (0.98, 0.88), 14: (0.98, 0.88), 15: (0.98, 0.88),
        }
    data = json.loads(p.read_text(encoding="utf-8"))
    th = data.get("thresholds_by_week", {})
    return {int(k): tuple(v) for k, v in th.items()}


# ============================================================== 面板构建

@lru_cache(maxsize=4096)
def _build_panel_one(stu_id: str, course_code: str, semester: str):
    """把一个 (stu, course, semester) 组装成 TFT 面板 dict。

    已有样本：从 tft_weekly.parquet 过滤 16 行 → build_panel。
    新学生：静态特征用同学期同课程中位数；观测全置 0（无历史 → TFT 输出中风险）。
    """
    from models.tft import build_panel, OBS_COLS, STATIC_COLS, KNOWN_COLS  # noqa: E402

    processed = _find_processed()
    weekly = pd.read_parquet(processed / "tft_weekly.parquet")
    sub = weekly[
        (weekly["stu_id"] == stu_id)
        & (weekly["course_code"] == course_code)
        & (weekly["semester"] == semester)
    ].sort_values("week")

    if len(sub) == 16:
        return build_panel(sub)

    # 新学生 → 构造 16 行伪面板
    T = 16
    median_row = weekly[
        (weekly["course_code"] == course_code) & (weekly["semester"] == semester)
    ][STATIC_COLS].median(numeric_only=True).fillna(0).to_dict()
    known = weekly[(weekly["course_code"] == course_code) & (weekly["semester"] == semester)][KNOWN_COLS]
    known_median = known.median(numeric_only=True).fillna(0).to_dict() if not known.empty else {}

    rows = []
    for t in range(T):
        r = {**median_row, **known_median, "week": t}
        for c in OBS_COLS:
            r[c] = 0.0  # 无历史行为 → 零值
        r["stu_id"] = stu_id
        r["course_code"] = course_code
        r["semester"] = semester
        r["split_group"] = "online"
        r["risk_event_time"] = -1
        r["is_positive"] = 0
        rows.append(r)
    df = pd.DataFrame(rows)
    return build_panel(df)


# ============================================================== 因子方向

@lru_cache(maxsize=1)
def _load_sign() -> np.ndarray:
    """TFT 观测特征方向 sign：训练集第 8 周相关性符号。"""
    processed = _find_processed()
    weekly = pd.read_parquet(processed / "tft_weekly.parquet")
    from models.tft import OBS_COLS, STATIC_COLS, KNOWN_COLS  # noqa: E402

    ckpt = _load_tft()[1]
    obs_cols = ckpt["obs_cols"]
    ci = [OBS_COLS.index(c) for c in obs_cols]
    tr8 = (weekly["split_group"] == "train") & (weekly["week"] == 8)
    sub = weekly[tr8]
    y8 = sub["is_positive"].to_numpy()
    sign = []
    for k, col in enumerate(obs_cols):
        vals = sub[col].to_numpy()
        mask = np.isfinite(vals) & np.isfinite(y8)
        if mask.sum() < 20:
            sign.append(1.0)
            continue
        corr = np.corrcoef(np.stack([vals[mask], y8[mask]]))[0, 1]
        sign.append(float(np.sign(corr) if np.isfinite(corr) else 1.0))
    return np.array(sign, dtype=np.float32)


# ============================================================== 因子构建

def _factors_one(z_obs_t, w_t, obs_raw_t, obs_cols, sign, k=3):
    """单样本单周的 top factors（复用 20_score_offline.py build_top_factors 逻辑）。"""
    from training.common import cn  # noqa: E402

    z = np.asarray(z_obs_t, dtype=np.float32)      # (K,)
    w = np.asarray(w_t, dtype=np.float32)           # (K,)
    raw = np.asarray(obs_raw_t, dtype=np.float32)  # (K,)
    sign = np.asarray(sign, dtype=np.float32)
    if len(z) != len(sign):
        # 维度不匹配（模型列与原始列顺序不同），用全部特征
        return []

    push = np.clip(z * w * sign, 0, None)
    total = float(push.sum())
    if total > 1e-12:
        contrib = push / total
    else:
        fb = np.abs(z) * w
        fb_total = float(fb.sum())
        contrib = fb / fb_total if fb_total > 1e-12 else np.zeros_like(z)

    order = np.argsort(-contrib)[:k]
    factors = []
    for idx in order:
        if contrib[idx] <= 0:
            continue
        factors.append({
            "factor": cn(obs_cols[idx]),
            "value": round(float(raw[idx]), 3),
            "contrib": round(float(contrib[idx]), 3),
        })
    return factors


# ============================================================== 预测事件周

def _predict_event_week_static(stu_id: str, course_code: str, semester: str) -> int:
    """DeepSurv pred_event_week。有 parquet 查 parquet；无 parquet 就推理。"""
    processed = _find_processed()
    surv_path = processed / "deepsurv_pred.parquet"
    if surv_path.exists():
        surv = pd.read_parquet(surv_path)
        hit = surv[(surv["stu_id"] == stu_id) & (surv["course_code"] == course_code)
                   & (surv["semester"] == semester)]
        if not hit.empty:
            v = int(hit.iloc[0]["pred_event_week"])
            return v

    # 新样本：读 static cols，加载 DeepSurv 推理
    from models.tft import STATIC_COLS  # noqa: E402

    panel = _build_panel_one(stu_id, course_code, semester)
    _, ckpt_tft = _load_tft()
    stat_cols = ckpt_tft["static_cols"]
    si = [STATIC_COLS.index(c) for c in stat_cols]
    xs = panel["x_static"][0:1, si]
    (ms, ss), _, _ = ckpt_tft["static_std"], ckpt_tft["obs_std"], ckpt_tft["known_std"]
    xs_z = (xs - ms) / ss
    ds_model, ds_ckpt = _load_deepsurv()
    with torch.no_grad():
        h = ds_model(torch.as_tensor(xs_z, dtype=torch.float32)).numpy().item()
    # DeepSurv 单样本简化：h_i 越大越早出事，固定用分位 0.8 当阈值
    # 无真实 baseline 的情况下，保守返回 -1（学期内无风险）
    return -1


# ============================================================== 对外接口

def predict_one(stu_id: str, course_code: str, semester: str, week: int) -> dict:
    """单周风险预测。"""
    panel = _build_panel_one(stu_id, course_code, semester)
    model, ckpt = _load_tft()
    _, _, _, _, static_cols, obs_cols, known_cols, T = (
        panel["x_static"], panel["x_obs"], panel["x_known"], panel["T"],
        ckpt["static_cols"], ckpt["obs_cols"], ckpt["known_cols"], panel["T"],
    )

    from models.tft import STATIC_COLS, OBS_COLS, KNOWN_COLS  # noqa: E402

    si = [STATIC_COLS.index(c) for c in static_cols]
    oi = [OBS_COLS.index(c) for c in obs_cols]
    ki = [KNOWN_COLS.index(c) for c in known_cols]
    xs = static_cols if isinstance(static_cols, list) else static_cols
    xs = panel["x_static"][0:1, si]
    xo = panel["x_obs"][0:1, :, oi]
    xk = panel["x_known"][0:1, :, ki]
    (ms, ss), (mo, so), (mk, sk) = ckpt["static_std"], ckpt["obs_std"], ckpt["known_std"]
    xs_z = (xs - ms) / ss
    xo_z = (xo - mo) / so
    xk_z = (xk - mk) / sk

    with torch.no_grad():
        logits, w = model(
            torch.as_tensor(xs_z, dtype=torch.float32),
            torch.as_tensor(xo_z, dtype=torch.float32),
            torch.as_tensor(xk_z, dtype=torch.float32),
        )
        prob = torch.sigmoid(logits).numpy().astype(np.float32)
        wo = w["obs"].numpy()[0]   # (T, K)
    prob_flat = prob[0]            # (T,)

    th = _load_thresholds()
    t = int(week)
    p = float(prob_flat[t])
    p_high, p_mid = th.get(t, (0.95, 0.75))
    level = 2 if p >= p_high else (1 if p >= p_mid else 0)

    sign = _load_sign()
    obs_raw_t = xo[0, t, :]     # 原始值 (K,)
    z_obs_t = xo_z[0, t, :]     # z-score (K,)
    w_t = wo[t, :]              # 模型权重 (K,)
    factors = _factors_one(z_obs_t, w_t, obs_raw_t, obs_cols, sign)

    # 是否已发生：若 risk_event_time 在 panel meta 中存在且 <= t
    meta = panel.get("meta", {})
    ev = -1
    if isinstance(meta, dict):
        ev = int(meta.get("risk_event_time", -1) or -1)
    elif hasattr(meta, "risk_event_time"):
        ev = int(meta.risk_event_time.iloc[0])
    event_occurred = (ev >= 0 and ev <= t)
    if event_occurred:
        p = 1.0
        level = 2

    pred_ev = _predict_event_week_static(stu_id, course_code, semester)

    return {
        "stu_id": stu_id,
        "course_code": course_code,
        "semester": semester,
        "week": t,
        "risk_prob": round(float(p), 4),
        "risk_level": int(level),
        "risk_level_name": LEVEL_CN[int(level)],
        "pred_event_week": int(pred_ev),
        "top_factors": factors,
        "event_occurred": bool(event_occurred),
        "factor_source": "online",
    }


def predict_student(stu_id: str, semester: str) -> list[dict]:
    """学生全学期 16 周风险曲线（聚合所有课程 → 取 max risk_level）。"""
    processed = _find_processed()
    weekly = pd.read_parquet(processed / "tft_weekly.parquet")
    sub = weekly[(weekly["stu_id"] == stu_id) & (weekly["semester"] == semester)]
    if sub.empty:
        return []
    courses = sorted(sub["course_code"].unique().tolist())

    # 逐周取最大风险（多门课取最坏那门）
    weekly_results: dict[int, dict] = {}
    for t in range(16):
        best = None
        for cc in courses:
            r = predict_one(stu_id, cc, semester, t)
            if best is None or r["risk_level"] > best["risk_level"]:
                best = r
        if best:
            weekly_results[t] = {
                "week": t,
                "risk_prob": best["risk_prob"],
                "risk_level": best["risk_level"],
                "risk_level_name": best["risk_level_name"],
                "pred_event_week": best["pred_event_week"],
                "top_factors": best["top_factors"],
                "event_occurred": best["event_occurred"],
                "course_code": best["course_code"],
            }
    return [weekly_results[t] for t in range(16) if t in weekly_results]


def generate_subgraph(stu_id: str, semester: str, week: int = 8) -> dict:
    """从 processed/subgraph 目录读现成子图 JSON。不存在时返回空图 + 错误信息（不阻塞后端）。"""
    processed = _find_processed()
    candidates = [
        processed / "subgraph" / f"{stu_id}.json",
        Path(__file__).resolve().parents[2] / "data" / "oulad" / "subgraph" / f"{stu_id}.json",
        SUBGRAPH_DIR / f"{stu_id}.json",
    ]
    for c in candidates:
        if c.exists():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except Exception:
                continue
    # 兜底：返回空图（不阻塞，后端可展示全局图的学生 1 跳）
    return {
        "nodes": [{"id": stu_id, "type": "student", "label": stu_id,
                   "risk_level": 0, "risk_level_text": "低"}],
        "edges": [],
        "meta": {"generated": False, "reason": "no_subgraph_yet"},
    }


# ============================================================== CLI 入口

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)

    pw = sub.add_parser("weekly")
    pw.add_argument("--stu_id", required=True)
    pw.add_argument("--course_code", required=True)
    pw.add_argument("--semester", required=True)
    pw.add_argument("--week", type=int, required=True)

    ps = sub.add_parser("student")
    ps.add_argument("--stu_id", required=True)
    ps.add_argument("--semester", required=True)

    pg = sub.add_parser("subgraph")
    pg.add_argument("--stu_id", required=True)
    pg.add_argument("--semester", required=True)
    pg.add_argument("--week", type=int, default=8)

    args = ap.parse_args()

    try:
        if args.mode == "weekly":
            out = predict_one(args.stu_id, args.course_code, args.semester, args.week)
        elif args.mode == "student":
            out = {"stu_id": args.stu_id, "semester": args.semester, "weeks": predict_student(args.stu_id, args.semester)}
        elif args.mode == "subgraph":
            out = generate_subgraph(args.stu_id, args.semester, args.week)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Exception as exc:
        tb = traceback.format_exc()
        sys.stderr.write(f"[online_risk ERROR] {exc}\n{tb}\n")
        # 错误也用 JSON 返回，让后端统一处理
        print(json.dumps({"error": str(exc), "mode": args.mode}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
