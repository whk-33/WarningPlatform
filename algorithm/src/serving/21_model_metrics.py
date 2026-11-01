"""模型卡（model_metrics.json）：把 6 份训练报告汇总成后端/前端/答辩共用的一张表。

只读 reports/*.json 与 models/ 下的产物，不重新训练、不改任何数字，
保证"报告里的指标"和"模型卡里的指标"永远一致。

运行：py -3 algorithm/src/serving/21_model_metrics.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.common import MODELS_DIR, REPORTS_DIR, PROJECT_ROOT, save_json  # noqa: E402


def read(name: str) -> dict:
    p = REPORTS_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def version(prefix: str) -> dict:
    paths = sorted(MODELS_DIR.glob(f"model_{prefix}_*.pt"))
    if not paths:
        return {"file": None, "trained_at": None}
    p = paths[-1]
    return {"file": p.name, "trained_at": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")}


def main() -> int:
    base, dkt, tft = read("baseline_metrics.json"), read("dkt_metrics.json"), read("tft_metrics.json")
    surv, han, causal = read("deepsurv_metrics.json"), read("han_metrics.json"), read("causal_metrics.json")
    explain, serving = read("explain_metrics.json"), read("serving_metrics.json")

    models = {
        "M0_baseline": {
            **version("mlp"),
            "role": "评估下限：第 8 周快照的静态+观测特征",
            "positive_rate": base.get("positive_rate"),
            "results": {name: r.get("test") for name, r in base.get("results", {}).items()},
            "caveat": "逻辑回归/MLP 手写实现（本机装不上 scikit-learn），指标与 sklearn 口径一致（AUC/PR-AUC/Brier）。",
        },
        "M1_dkt": {
            **version("dkt"),
            "role": "下一题答对预测 + 知识点掌握度",
            "n_sequences": dkt.get("n_sequences"),
            "positive_rate": dkt.get("results", {}).get("test", {}).get("pos_rate"),
            "results": dkt.get("results"),
            "caveat": dkt.get("caveat"),
        },
        "M2_tft": {
            **version("tft"),
            "role": "周度风险概率（risk_scores 的 risk_prob 来源）",
            "n_samples": tft.get("n_samples"),
            "results": tft.get("results"),
            "variable_weights": tft.get("variable_weights"),
            "caveat": tft.get("note"),
        },
        "M3_deepsurv": {
            **version("deepsurv"),
            "role": "风险发生时间（risk_scores 的 pred_event_week 来源）",
            "results": surv.get("results"),
            "test_event_rate": surv.get("test_event_rate"),
            "test_pred_event_rate": surv.get("test_pred_event_rate"),
            "test_pred_precision": surv.get("test_pred_precision"),
            "test_pred_recall": surv.get("test_pred_recall"),
            "caveat": "C-index≈0.64 属弱到中等；静态特征只有 10 维，预测周次仅作趋势参考。",
        },
        "M4_han": {
            **version("han"),
            "role": "异质图风险 + 归因子图",
            "results": han.get("results"),
            "caveat": han.get("note"),
        },
        "M5_causal": {
            "file": None,
            "trained_at": None,
            "role": "参与度 → 成绩的因果估计（DoWhy 等价实现）",
            "main": causal.get("main"),
            "refutations": causal.get("refutations"),
            "caveat": "观察性数据，报告只能写'因果估计'，不能写'证明'。",
        },
        "M6_explain": {
            "role": "边遮挡归因（删除 top-3 边后 logit 下降）",
            "method": explain.get("method"),
            "faithfulness": explain.get("faithfulness"),
            "caveat": explain.get("why_not_mask"),
        },
    }

    out = {
        "generated_at": date.today().isoformat(),
        "dataset": {
            "name": "OULAD (Open University Learning Analytics Dataset)",
            "license": "CC BY 4.0",
            "n_students": 28785,
            "n_stu_course_semester": 32593,
            "weekly_rows": 521488,
            "positive_rate": 0.528,
            "label": "is_positive = 不合格 + 退学；risk_event_time = 首次事件周（-1 表示无风险）",
        },
        "models": models,
        "product": {
            "table": "risk_scores.parquet",
            "rows": serving.get("n_rows"),
            "weeks": serving.get("weeks"),
            "alert_target": "逐周约 15% 预警率（高 5% + 中 10%）",
            "calibration_week8": (serving.get("thresholds_by_week") or {}).get("8"),
            "reproduce": [
                "py -3 algorithm/src/training/train_baseline.py",
                "py -3 algorithm/src/training/train_dkt.py",
                "py -3 algorithm/src/training/train_tft.py",
                "py -3 algorithm/src/training/train_deepsurv.py",
                "py -3 algorithm/src/training/train_han.py",
                "py -3 algorithm/src/training/train_causal.py",
                "py -3 algorithm/src/training/explain_han.py",
                "py -3 algorithm/src/serving/20_score_offline.py",
            ],
        },
        "environment": {"python": "3.13", "torch": "2.15.0+cpu", "sklearn": "不可用（指标与模型均为手写）"},
        "root": str(PROJECT_ROOT),
    }
    save_json(out, REPORTS_DIR / "model_metrics.json")
    print(f"[产物] {REPORTS_DIR / 'model_metrics.json'}")
    for k, v in models.items():
        print(f"  {k:14s} {str(v.get('file')):34s} {v.get('role', '')[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
