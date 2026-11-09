"""阶段四：从 risk_scores.parquet 导出 5 个风险接口的 mock JSON，供前端并行开发（C6 路线 B）。

用法：
    py -3 algorithm/src/serving/40_export_mock_api.py
输出：
    docs/mock_api/weekly.json      GET /api/risk/weekly
    docs/mock_api/student.json     GET /api/risk/student/{stu_id}
    docs/mock_api/factors.json     GET /api/risk/factors
    docs/mock_api/admin_list.json  GET /api/admin/risk/list
    docs/mock_api/admin_stat.json  GET /api/admin/risk/stat
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROCESSED = PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_full_demo"
OUT_DIR = PROJECT_ROOT / "docs" / "mock_api"

# 采样锚点：week=8 高风险学生，保证 mock 里 3 个等级 + top_factors 都有真实样本
ANCHOR_STU = "stu_100621"
ANCHOR_COURSE = "CCC"
ANCHOR_SEMESTER = "2014B"
MOCK_WEEK = 8

CONTRACT_COLS = [
    "stu_id", "course_code", "semester", "week",
    "risk_prob", "risk_level", "risk_level_name", "pred_event_week",
]


def _row_to_dict(row: pd.Series) -> dict:
    d = {c: row[c] for c in CONTRACT_COLS}
    d["week"] = int(d["week"])
    d["risk_prob"] = round(float(d["risk_prob"]), 4)
    d["risk_level"] = int(d["risk_level"])
    d["pred_event_week"] = int(d["pred_event_week"])
    return d


def _factors(row: pd.Series) -> list[dict]:
    """top_factors 由 JSON 字符串解析为数组（B5 建议后端解析后返回）。"""
    return json.loads(row["top_factors"]) if row["top_factors"] else []


def main() -> int:
    df = pd.read_parquet(PROCESSED / "risk_scores.parquet")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    one = df[(df.stu_id == ANCHOR_STU) & (df.course_code == ANCHOR_COURSE)
             & (df.semester == ANCHOR_SEMESTER)].sort_values("week")

    # 1) weekly：单周风险
    wk = one[one.week == MOCK_WEEK].iloc[0]
    weekly = {**_row_to_dict(wk), "top_factors": _factors(wk)}
    (OUT_DIR / "weekly.json").write_text(
        json.dumps(weekly, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2) student：全学期 16 周曲线（多门课取最高风险，与 30_online_risk.predict_student 同口径）
    stu_all = df[(df.stu_id == ANCHOR_STU) & (df.semester == ANCHOR_SEMESTER)]
    weeks = []
    for t, grp in stu_all.groupby("week"):
        best = grp.loc[grp.risk_level.idxmax()]
        weeks.append({**_row_to_dict(best), "top_factors": _factors(best)})
    student = {"stu_id": ANCHOR_STU, "semester": ANCHOR_SEMESTER, "weeks": weeks}
    (OUT_DIR / "student.json").write_text(
        json.dumps(student, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3) factors：单周归因
    factors = {
        "stu_id": ANCHOR_STU, "course_code": ANCHOR_COURSE,
        "semester": ANCHOR_SEMESTER, "week": MOCK_WEEK,
        "top_factors": _factors(wk),
    }
    (OUT_DIR / "factors.json").write_text(
        json.dumps(factors, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4) admin_list：分页列表（week=8，按 risk_prob 降序，第 1 页 20 条）
    pool = df[(df.semester == ANCHOR_SEMESTER) & (df.week == MOCK_WEEK)]
    total = len(pool)
    page_rows = pool.sort_values("risk_prob", ascending=False).head(20)
    admin_list = {
        "semester": ANCHOR_SEMESTER, "week": MOCK_WEEK,
        "page": 1, "size": 20, "total": int(total),
        "items": [_row_to_dict(r) for _, r in page_rows.iterrows()],
    }
    (OUT_DIR / "admin_list.json").write_text(
        json.dumps(admin_list, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5) admin_stat：各等级人数 + 预警率
    cnt = pool["risk_level"].value_counts().to_dict()
    n_high, n_mid = int(cnt.get(2, 0)), int(cnt.get(1, 0))
    admin_stat = {
        "semester": ANCHOR_SEMESTER, "week": MOCK_WEEK,
        "total": int(total),
        "level_0": int(cnt.get(0, 0)),
        "level_1": n_mid,
        "level_2": n_high,
        "alert_rate": round((n_high + n_mid) / total, 4) if total else 0.0,
    }
    (OUT_DIR / "admin_stat.json").write_text(
        json.dumps(admin_stat, ensure_ascii=False, indent=2), encoding="utf-8")

    for f in sorted(OUT_DIR.glob("*.json")):
        print(f"[mock] {f.name}: {f.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
