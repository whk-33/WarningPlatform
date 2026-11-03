"""阶段三算法侧测试：30_online_risk 桥 + 32_quality_checks 门禁。

运行：py -3 -m pytest algorithm/tests/test_stage3.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "algorithm" / "src"
SERVING = SRC / "serving"


# ================================================= 32_quality_checks

@pytest.fixture(scope="module")
def _qc():
    sys.path.insert(0, str(SERVING))
    import importlib
    qc = importlib.import_module("32_quality_checks")
    sys.path.pop(0)
    return qc


class TestQualityChecks:
    def test_missing_rate_zero(self, _qc):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4.0, 5.0, 6.0]})
        r = _qc.missing_rate(df)
        assert r["a"] == 0.0
        assert r["b"] == 0.0

    def test_missing_rate_penalty(self, _qc):
        vals = [1, np.nan, 2, np.nan, 3, np.nan, 4, np.nan, 5, np.nan, 6, np.nan,
                7, np.nan, 8, np.nan, 9, np.nan, 10, np.nan]
        df = pd.DataFrame({"a": vals})
        r = _qc.missing_rate(df)
        assert r["a"] == pytest.approx(0.5, abs=1e-6)

    def test_psi_identical(self, _qc):
        np.random.seed(0)
        x = np.random.normal(0, 1, 2000)
        y = x.copy()
        # 完全相同分布的 PSI 应 ≈ 0
        assert _qc.psi(x, y) < 0.01

    def test_psi_drifted(self, _qc):
        np.random.seed(0)
        base = np.random.normal(0, 1, 2000)
        drifted = np.random.normal(2, 1, 2000)   # 均值飘了 2σ
        assert _qc.psi(drifted, base) > 0.25

    def test_checks_pass(self, _qc):
        np.random.seed(0)
        baseline = pd.DataFrame({"x": np.random.normal(0, 1, 2000)})
        new = pd.DataFrame({"x": np.random.normal(0, 1, 2000)})
        ok, r = _qc.check_dataset(new, baseline)
        assert ok is True
        assert r["passed"] is True

    def test_checks_fail_psi(self, _qc):
        np.random.seed(0)
        baseline = pd.DataFrame({"x": np.random.normal(0, 1, 2000)})
        new = pd.DataFrame({"x": np.random.normal(3, 1, 2000)})
        ok, r = _qc.check_dataset(new, baseline)
        assert ok is False
        assert "x" in r["psi_fail"]

    def test_check_labels_ok(self, _qc):
        df = pd.DataFrame({"risk_event_time": [-1, 0, 5, -1, 10] * 50})
        ok, r = _qc.check_labels(df)
        assert ok is True
        assert r["label_missing_rate"] == 0.0

    def test_check_labels_fail(self, _qc):
        vals = [-1] * 98 + [np.nan] * 2
        df = pd.DataFrame({"risk_event_time": vals})
        ok, r = _qc.check_labels(df)
        assert ok is False
        assert r["label_missing_rate"] == pytest.approx(0.02, abs=1e-6)


# ================================================= 30_online_risk

def _online(cmd: list[str]) -> dict:
    """subprocess 调桥脚本，stdout 返回 JSON。"""
    res = subprocess.run(
        ["py", "-3", str(SERVING / "30_online_risk.py"), *cmd],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    out = (res.stdout or "").strip()
    if not out:
        raise RuntimeError(f"30_online_risk 无 stdout，stderr={res.stderr[:500]}")
    return json.loads(out)


STU_AT_RISK = "stu_101187"
COURSE_AT_RISK = "DDD"
SEM = "2014J"


class TestOnlineRiskBridge:
    def test_weekly_offline_consistent(self):
        """online weekly 与 offline parquet 一致（关键字段 risk_level / risk_prob / pred_event_week）。"""
        offline = pd.read_parquet(ROOT / "algorithm" / "data" / "oulad" /
                                   "processed_full_demo" / "risk_scores.parquet")
        hit = offline[(offline["stu_id"] == STU_AT_RISK)
                      & (offline["course_code"] == COURSE_AT_RISK)
                      & (offline["semester"] == SEM)
                      & (offline["week"] == 8)]
        assert not hit.empty, "测试数据里找不到目标学生，先跑 20_score_offline.py"
        of_prob = float(hit.iloc[0]["risk_prob"])
        of_level = int(hit.iloc[0]["risk_level"])
        of_pred = int(hit.iloc[0]["pred_event_week"])

        d = _online(["weekly", "--stu_id", STU_AT_RISK, "--course_code", COURSE_AT_RISK,
                     "--semester", SEM, "--week", "8"])
        assert d["stu_id"] == STU_AT_RISK
        assert d["risk_level"] == of_level
        assert d["pred_event_week"] == of_pred
        assert abs(float(d["risk_prob"]) - of_prob) < 1e-3
        assert d["event_occurred"] is True
        assert len(d["top_factors"]) >= 1
        assert d["factor_source"] == "online"

    def test_weekly_safe_student(self):
        """无风险学生 → level=0, pred_event_week=-1"""
        # 从 offline 表里找 level=0 的样本
        offline = pd.read_parquet(ROOT / "algorithm" / "data" / "oulad" /
                                   "processed_full_demo" / "risk_scores.parquet")
        safe = offline[offline["week"] == 8]
        safe = safe[safe["risk_level"] == 0]
        if safe.empty:
            pytest.skip("找不到 level=0 的测试样本")
        row = safe.iloc[0]
        d = _online(["weekly", "--stu_id", row["stu_id"], "--course_code", row["course_code"],
                     "--semester", row["semester"], "--week", "8"])
        assert d["risk_level"] == 0
        assert int(d["pred_event_week"]) >= -1
        assert d["event_occurred"] is False

    def test_student_mode_returns_16_weeks(self):
        d = _online(["student", "--stu_id", STU_AT_RISK, "--semester", SEM])
        assert isinstance(d, dict)
        assert "weeks" in d
        assert 0 < len(d["weeks"]) <= 16
        w0 = d["weeks"][0]
        assert "risk_prob" in w0
        assert "risk_level" in w0
        assert "top_factors" in w0

    def test_subgraph_fallback(self):
        """subgraph 模式：对不存在的 stu_id 返回空图（不报错）。"""
        d = _online(["subgraph", "--stu_id", "stu_999999", "--semester", SEM, "--week", "8"])
        assert "nodes" in d
        assert "edges" in d
        # 兜底节点有 student 类型
        assert any(n.get("type") == "student" for n in d["nodes"])


# ================================================= 31_weekly_run（门禁 + 现有 baseline 应通过）

class TestWeeklyRun:
    def test_dry_run_passes(self):
        sys.path.insert(0, str(SERVING))
        import importlib
        wr = importlib.import_module("31_weekly_run")
        sys.path.pop(0)
        rc = wr.run(dry_run=True)
        assert rc == 0
