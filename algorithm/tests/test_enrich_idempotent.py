"""41_enrich_risk_scores 的「不许改结论」回归测试。

背景：这个脚本是为了在**没有 torch** 的机器上给旧 parquet 补 `risk_state` /
`risk_score` 两列。它顺手会重算 `risk_level`，而它用的阈值来自
`serving_metrics.json`（已 round 到 4 位），与 `20_score_offline.py` 用的原始分位点
有极小差异——实测让 34 行的等级被翻转，risk_score 也有 22 万行差在 1e-6 级。

所以这里钉住两条不变量：
1. 除期末周外，`risk_level` 一律不许变（补列脚本不能改模型结论）；
2. 已有 `risk_score` 时不许重算。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "algorithm" / "src" / "serving" / "41_enrich_risk_scores.py"


def _make_processed(tmp_path: Path) -> Path:
    """造一份最小可用的 processed 目录：4 周 × 3 名学生。"""
    rows, weekly = [], []
    for i, stu in enumerate(["s1", "s2", "s3"]):
        for wk in range(4):
            prob = [0.10, 0.62, 0.99][(i + wk) % 3]
            rows.append({
                "stu_id": stu, "course_code": "C1", "semester": "2014J", "week": wk,
                "risk_prob": prob,
                # 故意写成「和阈值算出来的不一样」的值：脚本若不重算就应当原样保留
                "risk_level": 1,
                "risk_level_name": "中",
                "pred_event_week": -1,
                "top_factors": "[]",
                "risk_state": 0,
                "risk_score": np.float32(0.1234 + 0.01 * wk),
            })
            # s1 第 2 周已发生
            weekly.append({
                "stu_id": stu, "course_code": "C1", "semester": "2014J", "week": wk,
                "risk_event_time": 2 if stu == "s1" else -1,
            })
    pd.DataFrame(rows).to_parquet(tmp_path / "risk_scores.parquet", index=False)
    pd.DataFrame(weekly).to_parquet(tmp_path / "tft_weekly.parquet", index=False)
    return tmp_path


def test_enrich_keeps_levels_and_scores(tmp_path):
    d = _make_processed(tmp_path)
    before = pd.read_parquet(d / "risk_scores.parquet")

    res = subprocess.run([sys.executable, str(SCRIPT), "--processed", str(d)],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[-800:]

    after = pd.read_parquet(d / "risk_scores.parquet")
    last_week = int(after["week"].max())

    # 期末周以外的等级必须原样保留
    keep = after["week"] != last_week
    assert (after.loc[keep, "risk_level"] == before.loc[keep, "risk_level"]).all()
    # 已有 risk_score 不许重算
    assert np.allclose(after["risk_score"], before["risk_score"], atol=0, rtol=0)
    # risk_state 仍按 tft_weekly 的 risk_event_time 补齐
    s1 = after[after["stu_id"] == "s1"]
    assert int(s1["risk_state"].max()) == 1
