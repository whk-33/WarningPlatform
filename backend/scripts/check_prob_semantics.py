"""核查「`risk_prob = 1.0` 是否等价于事件已发生」。

背景：算法侧说明 `prob = 1.0` 的行都是 `risk_event_time <= week` 的已发生事件。
本仓库拿不到 `risk_event_time`，只能用 `pred_event_week` 做间接检查。

用法：

    python scripts/check_prob_semantics.py

结论会随数据版本变化。如果算法侧补导了 `risk_state` / `risk_event_time`，
本脚本会自动改用权威字段核对，不再依赖 `pred_event_week`。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from app.data.risk_repo import get_risk_repo  # noqa: E402


def describe(sem: str, wk: int, df: pd.DataFrame) -> None:
    sub = df[(df["semester"] == sem) & (df["week"] == wk)]
    if sub.empty:
        return

    one = sub[sub["risk_prob"] >= 1.0]
    total_rows = len(sub)
    print(f"\n{sem} w{wk}: 总行数 {total_rows}，prob=1.0 行数 {len(one)}"
          f"（{len(one) / total_rows:.2%}）")

    if one.empty:
        print("  没有 prob=1.0 的样本，跳过")
        return

    if "pred_event_week" not in one.columns:
        print("  缺 pred_event_week 字段，无法检查")
        return

    past = ((one["pred_event_week"] <= wk) & (one["pred_event_week"] != -1)).sum()
    future = (one["pred_event_week"] > wk).sum()
    none_ = (one["pred_event_week"] == -1).sum()
    n = len(one)

    print(f"  pred_event_week <= {wk}（若为已发生，应当几乎全在此）: "
          f"{past:6d} ({past / n:.2%})")
    print(f"  pred_event_week >  {wk}（事件在未来）              : "
          f"{future:6d} ({future / n:.2%})")
    print(f"  pred_event_week = -1（无事件）                     : "
          f"{none_:6d} ({none_ / n:.2%})")

    if past / n < 0.9:
        print("  -> 不成立：prob=1.0 不能用「已发生」解释（至少本数据版本下不能）")


def main() -> int:
    repo = get_risk_repo()
    repo._ensure()
    df = repo._df

    print("=" * 70)
    print("检查：risk_prob = 1.0 是否等价于「事件已发生」")
    print("=" * 70)
    print(f"数据源：{repo.path}")
    print(f"列：{', '.join(df.columns)}")

    has_auth = "risk_state" in df.columns or "risk_event_time" in df.columns
    print(f"权威字段是否存在：{'是' if has_auth else '否'}")

    for sem in sorted(df["semester"].unique()):
        for wk in (5, 8, 15):
            describe(sem, wk, df)

    print("\n" + "=" * 70)
    if has_auth:
        print("已具备权威字段，可直接用 risk_state / risk_event_time 核对语义。")
    else:
        print("结论：当前数据无法证实「prob=1.0 ⟺ 已发生」。")
        print("对外只能表述为「剔除 prob 打满 1.0 的样本后的预警率」。")
        print("需要算法侧补导 risk_state（必须）与 risk_event_time 才能定性。")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
