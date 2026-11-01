"""M5 因果估计（DoWhy 等价实现，不引入 dowhy 依赖）。

问题：学习参与度（周均课件点击，二值化）对期末成绩的因果效应估计。
- 处理变量 T：该学生-课程的周均课件点击高于中位数 = 1
- 结果变量 Y：final_score（只在"有成绩"的样本上估计，剔除退学，避免选择偏差）
- 混杂集 X：性别 / 学历 / 入学成绩 / 历史重修 / 学分 / 选修
- 估计量：后门线性回归（OLS，含截距 + 学历哑变量）
- 反驳测试：placebo treatment、random common cause、80% 子集重估

⚠️ 观察性数据只能给"关联性因果估计"，报告措辞必须写"估计"。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.common import REPORTS_DIR, SEED, resolve_interim, save_json, set_seed, setup_matplotlib  # noqa: E402

KEYS = ["stu_id", "course_code", "semester"]


def ols(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    dof = max(len(y) - x.shape[1], 1)
    sigma2 = float(resid @ resid / dof)
    cov = sigma2 * np.linalg.pinv(x.T @ x)
    return beta, np.sqrt(np.clip(np.diag(cov), 0, None))


def build_design(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """返回 (设计矩阵, 结果变量, 列名)，T 固定在第 0 列。"""
    major_dummies = pd.get_dummies(df["major"], prefix="major", drop_first=True, dtype=float)
    cols = pd.concat([
        df[["treatment"]],
        df[["gender", "entrance_score", "past_retake_count", "special_flag", "credits", "elective"]]
          .astype(float),
        major_dummies,
    ], axis=1)
    x = np.c_[np.ones(len(df)), cols.to_numpy(dtype=float)]
    return x, df["final_score"].to_numpy(dtype=float), ["intercept"] + list(cols.columns)


def estimate(df: pd.DataFrame, seed: int = SEED, n_boot: int = 200) -> dict:
    x, y, names = build_design(df)
    beta, se = ols(y, x)
    t_stat = beta[1] / se[1]
    # 用正态近似给 p 值（大样本下与 t 分布几乎一致）
    from math import erf, sqrt

    p = float(2 * (1 - 0.5 * (1 + erf(abs(t_stat) / sqrt(2)))))
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(y), len(y))
        b, _ = ols(y[idx], x[idx])
        boots.append(b[1])
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "ate": round(float(beta[1]), 4),
        "se": round(float(se[1]), 4),
        "ci95": [round(float(lo), 4), round(float(hi), 4)],
        "p_value": round(p, 6),
        "n": int(len(y)),
        "coefficients": {n: round(float(b), 4) for n, b in zip(names, beta)},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interim", default=None)
    args = ap.parse_args()
    set_seed(SEED)
    interim = resolve_interim(args.interim)

    final = pd.read_parquet(interim / "final_result.parquet")
    logs = pd.read_parquet(interim / "platform_log.parquet",
                           columns=KEYS + ["ppt_page_clicks"])
    students = pd.read_parquet(interim / "student_info.parquet")
    courses = pd.read_parquet(interim / "courses.parquet")

    activity = logs.groupby(KEYS, as_index=False)["ppt_page_clicks"].mean()
    activity = activity.rename(columns={"ppt_page_clicks": "weekly_clicks"})
    df = (final.merge(activity, on=KEYS, how="inner")
                 .merge(students[["stu_id", "gender", "major", "entrance_score", "past_retake_count",
                                  "special_flag"]], on="stu_id", how="left")
                 .merge(courses[["code", "credits", "elective"]],
                        left_on="course_code", right_on="code", how="left"))
    df = df[df["status"] != "退学"].copy()                     # 退学无期末成绩，剔除
    # 演示增强层里 gender 是中文（男/女），建模前统一编码为 0/1
    df["gender"] = (df["gender"].astype(str).str.lower()
                    .map({"男": 1, "女": 0, "m": 1, "male": 1, "f": 0, "female": 0})
                    .fillna(0).astype(int))
    df["treatment"] = (df["weekly_clicks"] > df["weekly_clicks"].median()).astype(int)
    print(f"[数据] 有成绩样本={len(df)}  退学已剔除  "
          f"处理组占比={df.treatment.mean():.3f}  成绩均值 处理组={df[df.treatment==1].final_score.mean():.1f} "
          f"对照组={df[df.treatment==0].final_score.mean():.1f}")

    main_est = estimate(df)
    print(f"[估计] ATE={main_est['ate']}  SE={main_est['se']}  95%CI={main_est['ci95']}  "
          f"p={main_est['p_value']}")

    rng = np.random.default_rng(SEED)
    # 反驳 1：placebo treatment（打乱处理变量，效应应接近 0 且不显著）
    placebo = estimate(df.assign(treatment=rng.permutation(df["treatment"].to_numpy())), n_boot=50)
    # 反驳 2：random common cause（加入一个与结果无关的随机协变量，ATE 应基本不变）
    df_rcc = df.copy()
    df_rcc["_rcc"] = rng.normal(size=len(df_rcc))
    x, y, names = build_design(df_rcc)
    x2 = np.c_[x, df_rcc["_rcc"].to_numpy()]
    beta, se = ols(y, x2)
    rcc = {"ate": round(float(beta[1]), 4), "se": round(float(se[1]), 4)}
    # 反驳 3：80% 子集重估
    sub = df_rcc.sample(frac=0.8, random_state=SEED)
    subset = estimate(sub, n_boot=50)

    print(f"[反驳] placebo ATE={placebo['ate']} (p={placebo['p_value']})  "
          f"random_common_cause ATE={rcc['ate']}  80%子集 ATE={subset['ate']}")

    plt = setup_matplotlib()
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    labels = ["主估计", "placebo", "随机共同原因", "80% 子集"]
    vals = [main_est["ate"], placebo["ate"], rcc["ate"], subset["ate"]]
    errs = [[main_est["ate"] - main_est["ci95"][0], placebo["ate"] - placebo["ci95"][0],
             rcc["se"] * 1.96, subset["ate"] - subset["ci95"][0]],
            [main_est["ci95"][1] - main_est["ate"], placebo["ci95"][1] - placebo["ate"],
             rcc["se"] * 1.96, subset["ci95"][1] - subset["ate"]]]
    ax.bar(range(len(vals)), vals, yerr=errs, capsize=5, color=["#4C78A8", "#9E9E9E", "#9E9E9E", "#9E9E9E"])
    ax.axhline(0, color="#D64545", lw=1)
    ax.set_xticks(range(len(vals)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("对期末成绩的估计效应（分）")
    ax.set_title("参与度 → 期末成绩：估计与反驳测试")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(REPORTS_DIR / "fig_causal_effect.png")

    save_json({
        "model": "M5_causal_backdoor_ols",
        "treatment": "周均课件点击高于中位数",
        "outcome": "final_score（剔除退学样本）",
        "confounders": ["gender", "entrance_score", "past_retake_count", "special_flag",
                        "credits", "elective", "major(哑变量)"],
        "main": main_est,
        "refutations": {"placebo_treatment": placebo, "random_common_cause": rcc,
                        "subset_80pct": subset},
        "wording": "观察性数据的因果估计，不是因果证明。",
    }, REPORTS_DIR / "causal_metrics.json")
    print(f"[产物] {REPORTS_DIR / 'causal_metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
