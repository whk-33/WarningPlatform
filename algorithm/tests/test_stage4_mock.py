"""阶段四契约测试：mock_api JSON 与 parquet 数据源的一致性。

不依赖 torch，仅校验字段契约（§2）与 mock 文件结构（§3）。
"""
import json
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
MOCK_DIR = ROOT / "docs" / "mock_api"
PROCESSED = ROOT / "algorithm" / "data" / "oulad" / "processed_full_demo"

REQUIRED_COLS = [
    "stu_id", "course_code", "semester", "week",
    "risk_prob", "risk_level", "risk_level_name", "pred_event_week", "top_factors",
]
WEEKLY_KEYS = set(REQUIRED_COLS) - {"top_factors"} | {"top_factors"}


@pytest.fixture(scope="module")
def parquet():
    return pd.read_parquet(PROCESSED / "risk_scores.parquet")


def test_parquet_contract(parquet):
    """risk_scores.parquet 保持 9 字段契约。"""
    assert list(parquet.columns) == REQUIRED_COLS
    assert parquet["week"].between(0, 15).all()
    assert set(parquet["risk_level"].unique()) <= {0, 1, 2}
    assert parquet["risk_prob"].between(0, 1).all()


@pytest.mark.parametrize("name", ["weekly", "student", "factors", "admin_list", "admin_stat"])
def test_mock_files_exist(name):
    assert (MOCK_DIR / f"{name}.json").exists(), f"缺 mock 文件 {name}.json，请运行 40_export_mock_api.py"


def test_weekly_schema(parquet):
    wk = json.loads((MOCK_DIR / "weekly.json").read_text(encoding="utf-8"))
    assert set(wk.keys()) == WEEKLY_KEYS
    assert 0 <= wk["week"] <= 15
    assert wk["risk_level"] in (0, 1, 2)
    assert wk["risk_level_name"] in ("低", "中", "高")
    assert isinstance(wk["top_factors"], list)
    # 与 parquet 中真实记录一致
    hit = parquet[(parquet.stu_id == wk["stu_id"]) & (parquet.course_code == wk["course_code"])
                  & (parquet.semester == wk["semester"]) & (parquet.week == wk["week"])]
    assert len(hit) == 1
    assert abs(float(hit.iloc[0]["risk_prob"]) - wk["risk_prob"]) < 1e-3


def test_student_16_weeks_sorted():
    stu = json.loads((MOCK_DIR / "student.json").read_text(encoding="utf-8"))
    weeks = stu["weeks"]
    assert len(weeks) == 16
    assert [w["week"] for w in weeks] == list(range(16))
    for w in weeks:
        assert isinstance(w["top_factors"], list)
        assert w["risk_level"] in (0, 1, 2)


def test_factors_max3_desc():
    f = json.loads((MOCK_DIR / "factors.json").read_text(encoding="utf-8"))
    contribs = [x["contrib"] for x in f["top_factors"]]
    assert len(contribs) <= 3
    assert contribs == sorted(contribs, reverse=True)
    for x in f["top_factors"]:
        assert set(x.keys()) == {"factor", "value", "contrib"}


def test_admin_list_pagination():
    al = json.loads((MOCK_DIR / "admin_list.json").read_text(encoding="utf-8"))
    assert al["page"] == 1 and al["size"] == 20
    assert len(al["items"]) == 20
    assert al["total"] >= 20
    probs = [i["risk_prob"] for i in al["items"]]
    assert probs == sorted(probs, reverse=True), "管理员列表应按 risk_prob 降序"


def test_admin_stat_consistent():
    stat = json.loads((MOCK_DIR / "admin_stat.json").read_text(encoding="utf-8"))
    assert stat["level_0"] + stat["level_1"] + stat["level_2"] == stat["total"]
    expected = (stat["level_1"] + stat["level_2"]) / stat["total"]
    assert abs(stat["alert_rate"] - expected) < 1e-3
