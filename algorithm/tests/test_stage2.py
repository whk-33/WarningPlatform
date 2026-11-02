"""阶段二验收测试 · pytest（轻量，不重新训练）

覆盖：
1. 公共指标函数（纯 numpy，与 sklearn 口径一致）
2. 5 个模型的 forward 形状
3. 对外契约 risk_scores.parquet 的字段/取值范围（数据存在时才跑）
4. 模型卡与报告是否存在

运行：py -3 -m pytest algorithm/tests/test_stage2.py -q
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "algorithm" / "src"))

PROCESSED = ROOT / "algorithm" / "data" / "oulad" / "processed_full_demo"
REPORTS = ROOT / "algorithm" / "reports"

from training.common import average_precision, brier, evaluate, recall_at_fraction, roc_auc  # noqa: E402

# ═════════════════════════════════════════════════════════════════════════════
# 1. 指标函数
# ═════════════════════════════════════════════════════════════════════════════
def test_auc_perfect_and_reversed():
    y = np.array([0, 0, 1, 1])
    assert roc_auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == pytest.approx(1.0)
    assert roc_auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == pytest.approx(0.0)


def test_auc_all_ties():
    y = np.array([0, 1, 0, 1])
    assert roc_auc(y, np.zeros(4)) == pytest.approx(0.5)


def test_auc_single_class_is_nan():
    assert np.isnan(roc_auc(np.array([0, 0]), np.array([0.3, 0.4])))


def test_average_precision():
    # 正例排在最前 → AP = 1
    assert average_precision(np.array([1, 1, 0, 0]), np.array([0.9, 0.8, 0.2, 0.1])) == pytest.approx(1.0)


def test_recall_at_fraction_lift():
    y = np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    score = np.linspace(1, 0, 10)
    r = recall_at_fraction(y, score, 0.1)
    assert r["k"] == 1 and r["recall"] == pytest.approx(1.0) and r["lift"] == pytest.approx(10.0)


def test_brier_and_evaluate_keys():
    y = np.array([0, 1, 1, 0])
    p = np.array([0.1, 0.9, 0.8, 0.2])
    assert brier(y, p) == pytest.approx((0.01 + 0.01 + 0.04 + 0.04) / 4)
    keys = {"n", "pos_rate", "auc", "pr_auc", "recall_at_10pct", "precision_at_10pct", "brier"}
    assert keys <= set(evaluate(y, p))


def test_metrics_ignore_nan_scores():
    y = np.array([0, 1, 1])
    p = np.array([0.2, 0.9, np.nan])
    assert np.isfinite(roc_auc(y, p))


# ═════════════════════════════════════════════════════════════════════════════
# 2. 模型 forward 形状
# ═════════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def torch():
    return pytest.importorskip("torch")


def test_tft_forward(torch):
    from models.tft import TFTLite

    m = TFTLite(n_static=5, n_obs=4, n_known=3)
    logits, w = m(torch.randn(8, 5), torch.randn(8, 16, 4), torch.randn(8, 16, 3))
    assert logits.shape == (8, 16)
    assert w["obs"].shape == (8, 16, 4)


def test_dkt_forward_and_mastery(torch):
    from models.dkt import DKT

    m = DKT(n_items=20, n_kps=10)
    item = torch.randint(0, 20, (4, 6))
    correct = torch.randint(0, 2, (4, 6))
    kp = torch.randint(0, 10, (4, 6))
    out = m(item, correct, kp)
    assert out.shape == (4, 6)
    assert m.mastery(item, correct, torch.full((4,), 6), np.arange(5)).shape == (4, 5)


def test_deepsurv_concordance(torch):
    from models.deepsurv import concordance_index

    # 风险分与事件时间完全一致（越早出事风险越高）→ C=1
    time = np.array([1, 2, 3, 4])
    event = np.array([1, 1, 1, 1])
    assert concordance_index(np.array([4, 3, 2, 1]), time, event) == pytest.approx(1.0)
    assert concordance_index(np.array([1, 2, 3, 4]), time, event) == pytest.approx(0.0)


def test_han_forward_with_edge_scale(torch):
    from models.han import HAN, RELATIONS

    in_dims = {"student": 3, "course": 2, "class_session": 2, "knowledge_point": 2, "resource": 2}
    m = HAN(in_dims, hidden=16)
    feats = {t: torch.randn(3, d) for t, d in in_dims.items()}
    # 只构造"消息汇入学生"的关系表，其余类型在第一层没有邻居 → 直接透传
    tables, scale = {}, {}
    for etype, _src, dst in RELATIONS:
        if dst == "student":
            tables[etype] = torch.full((3, 2), 1, dtype=torch.long)
            scale[etype] = torch.ones(2)
    nbr = {"student": np.array([0, 1]), "course": np.array([0]), "class_session": np.array([0]),
           "knowledge_point": np.array([0]), "resource": np.array([0])}
    logit = m(feats, np.array([0, 1]), tables, nbr, edge_scale=scale)
    assert logit.shape == (2,) and torch.isfinite(logit).all()


# ═════════════════════════════════════════════════════════════════════════════
# 3. 对外契约：risk_scores.parquet（需要数据，CI 无数据时跳过）
# ═════════════════════════════════════════════════════════════════════════════
CONTRACT = ["stu_id", "course_code", "semester", "week", "risk_prob",
            "risk_level", "risk_level_name", "pred_event_week", "top_factors"]


@pytest.fixture(scope="module")
def scores():
    path = PROCESSED / "risk_scores.parquet"
    if not path.exists():
        pytest.skip("缺少 risk_scores.parquet，先跑 serving/20_score_offline.py")
    return __import__("pandas").read_parquet(path)


def test_scores_contract_fields(scores):
    assert list(scores.columns) == CONTRACT


def test_scores_value_ranges(scores):
    assert scores["week"].between(0, 15).all()
    assert scores["risk_prob"].between(0, 1).all() and not scores["risk_prob"].isna().any()
    assert set(scores["risk_level"].unique()) <= {0, 1, 2}
    assert scores["pred_event_week"].between(-1, 15).all()


def test_scores_id_prefixes(scores):
    assert scores["stu_id"].str.startswith("stu_").all()
    assert scores["course_code"].str.match(r"^[A-Z]{3}$").all()


def test_scores_top_factors_is_json(scores):
    for v in scores["top_factors"].head(200):
        arr = json.loads(v)
        assert isinstance(arr, list) and len(arr) >= 1
        for item in arr:
            assert {"factor", "value", "contrib"} <= set(item)
            assert 0.0 <= item["contrib"] <= 1.0


def test_scores_one_row_per_sample_week(scores):
    n = scores.groupby(["stu_id", "course_code", "semester"]).size()
    assert n.min() == 16 and n.max() == 16


# ═════════════════════════════════════════════════════════════════════════════
# 4. 报告与模型卡
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("name", [
    "baseline_metrics.json", "dkt_metrics.json", "tft_metrics.json", "deepsurv_metrics.json",
    "han_metrics.json", "causal_metrics.json", "explain_metrics.json", "model_metrics.json",
])
def test_reports_exist(name):
    p = REPORTS / name
    if not p.exists():
        pytest.skip(f"{name} 未生成（需要先跑对应训练脚本）")
    json.loads(p.read_text(encoding="utf-8"))


def test_model_card_covers_six_models():
    p = REPORTS / "model_metrics.json"
    if not p.exists():
        pytest.skip("model_metrics.json 未生成")
    card = json.loads(p.read_text(encoding="utf-8"))
    assert {"M0_baseline", "M1_dkt", "M2_tft", "M3_deepsurv", "M4_han", "M5_causal"} <= set(card["models"])
