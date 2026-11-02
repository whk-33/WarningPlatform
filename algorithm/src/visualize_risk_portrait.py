"""
学生风险画像可视化 —— 替代难懂的节点关系图

核心思路：不画密集的节点连线，而是用「指标卡片 + 趋势图 + 雷达图」
直接回答"这个学生为什么有风险"。

产出：algorithm/data/processed/graph_viz/risk_portrait.png
运行：python algorithm/src/visualize_risk_portrait.py
"""
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
from matplotlib.gridspec import GridSpec

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

INTERIM = Path("algorithm/data/interim")
PROCESSED = Path("algorithm/data/processed")
OUT = Path("algorithm/data/processed/graph_viz")
OUT.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════
# 1. 载入数据
# ═══════════════════════════════════════════════════════════════════════════
fin = pd.read_parquet(INTERIM / "final_result.parquet")
log = pd.read_parquet(INTERIM / "platform_log.parquet")
quiz = pd.read_parquet(INTERIM / "quiz_item.parquet")
asm = pd.read_parquet(INTERIM / "student_assessment.parquet")
asm_cfg = pd.read_parquet(INTERIM / "assessment_config.parquet")
tft = pd.read_parquet(PROCESSED / "tft_weekly.parquet")

# 选一个正例学生（有挂科记录）
pos = fin[fin["is_positive"] == 1]
# 优先选有多门挂科的学生，画像更典型
fail_counts = pos.groupby("stu_id").size()
stu_id = fail_counts.idxmax()
print(f"目标学生: {stu_id} (挂科 {fail_counts.max()} 门)")

sfin = fin[fin["stu_id"] == stu_id].copy()
slog = log[log["stu_id"] == stu_id].copy()
squiz = quiz[quiz["stu_id"] == stu_id].copy()
sasm = asm[asm["stu_id"] == stu_id].copy()
stft = tft[tft["stu_id"] == stu_id].copy()

# 全体负例学生的均值（用于对比）
neg = fin[fin["is_positive"] == 0]["stu_id"].unique()
neg_tft = tft[tft["stu_id"].isin(neg)]


# ═══════════════════════════════════════════════════════════════════════════
# 2. 计算各项指标
# ═══════════════════════════════════════════════════════════════════════════
# 整体出勤率
att = slog["attendance_status"].isin(["attended", "attended_leave"]).mean()
# 小测正确率
quiz_correct = (squiz["score"] / squiz["max_score"]).mean() if len(squiz) else 0
# 挂科门数
n_fail = (sfin["status"].isin(["不合格", "缺考"])).sum()
# 平均迟到分钟
avg_late = slog["late_minutes"].mean()
# 作业迟交次数
hw_late = (sasm["days_late"] > 0).sum()

# 负例均值（对比基准）
neg_att = neg_tft.groupby("stu_id")["attendance_rate"].mean()
# attendance_rate 在 tft 里是累计值/周，需要换算：用 platform_log 算更准
neg_log = log[log["stu_id"].isin(neg)]
neg_att_rate = neg_log["attendance_status"].isin(["attended", "attended_leave"]).mean()
neg_quiz = (quiz[quiz["stu_id"].isin(neg)]["score"] /
            quiz[quiz["stu_id"].isin(neg)]["max_score"]).mean()


# ═══════════════════════════════════════════════════════════════════════════
# 3. 画图
# ═══════════════════════════════════════════════════════════════════════════
fig = plt.figure(figsize=(18, 12), facecolor="#f8f9fa")
gs = GridSpec(3, 4, figure=fig, hspace=0.45, wspace=0.35,
              left=0.06, right=0.96, top=0.92, bottom=0.06)

# ── 标题 ──
risk_color = "#e74c3c"
fig.suptitle(f"学生风险画像  ·  {stu_id}    "
             f"风险等级：高危（{n_fail} 门课程不合格/缺考）",
             fontsize=18, fontweight="bold", color=risk_color, y=0.97)

# ── 面板 1：关键指标卡片（占整行 4 列中的 4 个小格）──
card_axes = [fig.add_subplot(gs[0, i]) for i in range(4)]
cards = [
    ("整体出勤率", f"{att:.0%}", att, 0.80, "%"),
    ("小测正确率", f"{quiz_correct:.0%}", quiz_correct, 0.70, "%"),
    ("挂科门数", f"{n_fail}", n_fail, 1, "门"),
    ("平均迟到", f"{avg_late:.0f}", avg_late, 10, "分钟"),
]
for ax, (name, val, raw, threshold, unit) in zip(card_axes, cards):
    # 颜色：低于阈值=红，高于=绿
    if name in ("挂科门数", "平均迟到"):
        good = raw <= threshold
    else:
        good = raw >= threshold
    color = "#27ae60" if good else "#e74c3c"
    ax.set_facecolor("#ffffff")
    ax.text(0.5, 0.62, val, fontsize=36, fontweight="bold",
            color=color, ha="center", va="center", transform=ax.transAxes)
    ax.text(0.5, 0.28, name, fontsize=13, color="#555",
            ha="center", va="center", transform=ax.transAxes)
    # 基准线
    baseline = {"整体出勤率": f"正常均值 {neg_att_rate:.0%}",
                "小测正确率": f"正常均值 {neg_quiz:.0%}",
                "挂科门数": "正常 0 门",
                "平均迟到": "正常 <10 分钟"}[name]
    ax.text(0.5, 0.08, baseline, fontsize=9, color="#999",
            ha="center", va="center", transform=ax.transAxes)
    ax.set_xticks([]); ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color(color); spine.set_linewidth(2)

# ── 面板 2：各课程期末成绩（横向条形图）──
ax2 = fig.add_subplot(gs[1, 0:2])
course_scores = sfin.sort_values("final_score")
colors_bar = ["#e74c3c" if s < 60 else "#27ae60" for s in course_scores["final_score"]]
bars = ax2.barh(range(len(course_scores)), course_scores["final_score"],
                color=colors_bar, edgecolor="white", height=0.6)
ax2.set_yticks(range(len(course_scores)))
ax2.set_yticklabels([f"{r.course_code} ({r.semester})" for r in course_scores.itertuples()],
                    fontsize=11)
ax2.axvline(60, color="#e67e22", ls="--", lw=2, label="及格线 60")
ax2.set_xlim(0, 105)
ax2.set_xlabel("期末成绩", fontsize=11)
ax2.set_title("各课程期末成绩", fontsize=13, fontweight="bold", pad=10)
ax2.legend(fontsize=10)
for bar, score in zip(bars, course_scores["final_score"]):
    ax2.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
             f"{score:.1f}", va="center", fontsize=10, color="#333")

# ── 面板 3：每周出勤率趋势（按学期）──
ax3 = fig.add_subplot(gs[1, 2:4])
for sem in sorted(sfin["semester"].unique()):
    sub = stft[stft["semester"] == sem].sort_values("week")
    # 出勤率：attended / total，从 platform_log 按周算
    sem_log = slog[slog["semester"] == sem]
    weekly = sem_log.groupby("week").agg(
        att=("attendance_status", lambda s: (s.isin(["attended", "attended_leave"])).sum()),
        tot=("attendance_status", "count")
    ).reset_index()
    weekly["rate"] = weekly["att"] / weekly["tot"]
    ax3.plot(weekly["week"], weekly["rate"], marker="o", lw=2, label=f"{sem}")
ax3.axhline(0.80, color="#27ae60", ls="--", lw=1.5, alpha=0.7, label="达标线 80%")
ax3.set_xlabel("周次", fontsize=11)
ax3.set_ylabel("出勤率", fontsize=11)
ax3.set_title("每周出勤率趋势", fontsize=13, fontweight="bold", pad=10)
ax3.set_ylim(0, 1.05)
ax3.legend(fontsize=10)
ax3.grid(True, alpha=0.3)

# ── 面板 4：随堂小测正确率趋势（核心 DKT 信号）──
ax4 = fig.add_subplot(gs[2, 0:2])
for sem in sorted(sfin["semester"].unique()):
    sem_quiz = squiz[squiz["semester"] == sem].copy()
    if len(sem_quiz) == 0:
        continue
    sem_quiz["correct"] = sem_quiz["score"] / sem_quiz["max_score"]
    weekly_q = sem_quiz.groupby("week")["correct"].mean().reset_index()
    ax4.plot(weekly_q["week"], weekly_q["correct"], marker="s", lw=2, label=f"{sem}")
ax4.axhline(0.60, color="#e67e22", ls="--", lw=1.5, alpha=0.7, label="及格线 60%")
ax4.set_xlabel("周次", fontsize=11)
ax4.set_ylabel("小测正确率", fontsize=11)
ax4.set_title("随堂小测正确率趋势（DKT 信号）", fontsize=13, fontweight="bold", pad=10)
ax4.set_ylim(0, 1.05)
ax4.legend(fontsize=10)
ax4.grid(True, alpha=0.3)

# ── 面板 5：风险因子雷达图（多维度对比）──
ax5 = fig.add_subplot(gs[2, 2:4], polar=True)
factors = ["出勤率", "小测正确率", "作业按时", "少缺勤", "少迟到"]
# 该学生各因子得分（归一化 0-1，越高越好）
stu_scores = [
    att,
    quiz_correct,
    1 - min(hw_late / 10, 1),
    1 - min((slog["attendance_status"] == "absent").mean() / 0.3, 1),
    1 - min(avg_late / 30, 1),
]
# 负例均值
neg_hw_late = (asm[asm["stu_id"].isin(neg)]["days_late"] > 0).mean()
neg_absent = (neg_log["attendance_status"] == "absent").mean()
neg_scores = [
    neg_att_rate,
    neg_quiz,
    1 - min(neg_hw_late * 10, 1),
    1 - min(neg_absent / 0.3, 1),
    1 - min(5 / 30, 1),
]

angles = np.linspace(0, 2 * np.pi, len(factors), endpoint=False).tolist()
stu_scores += stu_scores[:1]
neg_scores += neg_scores[:1]
angles += angles[:1]

ax5.plot(angles, stu_scores, "o-", lw=2.5, color="#e74c3c", label=f"{stu_id[-6:]}")
ax5.fill(angles, stu_scores, alpha=0.2, color="#e74c3c")
ax5.plot(angles, neg_scores, "s--", lw=2, color="#27ae60", label="正常学生均值")
ax5.fill(angles, neg_scores, alpha=0.1, color="#27ae60")
ax5.set_xticks(angles[:-1])
ax5.set_xticklabels(factors, fontsize=11)
ax5.set_ylim(0, 1)
ax5.set_title("风险因子多维对比", fontsize=13, fontweight="bold", pad=20)
ax5.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1), fontsize=10)

out_path = OUT / "risk_portrait.png"
plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"\n✅ 风险画像 → {out_path}")
print("   包含：关键指标卡片 / 课程成绩 / 出勤趋势 / 小测趋势 / 风险雷达图")
