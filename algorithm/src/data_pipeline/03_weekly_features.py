"""
03_weekly_features.py  —— T3 时序特征提取
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

输入：algorithm/data/interim/*.parquet（清洗+ID 打通后）
输出：
  ① algorithm/data/processed/tft_weekly.parquet       宽表 (stu_id, crs, week, semester) —— TFT 用
  ② algorithm/data/processed/deepsurv_events.parquet  生存事件表 (start/end/event_type)   —— DeepSurv 用

核心设计：
  - 每行粒度 = 学生 × 课程 × 周（0..15）
  - 防泄漏：时变特征用「截至第 wk 周末的累计值」，不用瞬时值
  - 缺失周显式补零（全部学生×全部课程×全部周的笛卡尔积）
  - split_group：按 stu_id 分组切分（同一学生的不同班次不跨 train/test）

TFT 三类特征：
  static        —— 不变：性别、专业 one-hot、入学成绩、既往重修、特殊标记、学分、必修/选修
  observed      —— 时变（累计到本周）：出勤率、PPT 点击、视频完成率、随堂小测正确率、作业分数/迟交天数、缺勤次数
  known_future  —— 可预知：本周是学期第几周、是否后半学期、距离期末剩余周数
"""
from pathlib import Path

import numpy as np
import pandas as pd

# ── 直接读 schema.yaml（绕过 configs 模块） ────────────────────────────────
import yaml as _yaml
with open("algorithm/configs/schema.yaml", encoding="utf-8") as _f:
    _schema_raw = _yaml.safe_load(_f)


RAW = Path("algorithm/data/raw")
INTERIM = Path("algorithm/data/interim")
OUT = Path("algorithm/data/processed")
OUT.mkdir(parents=True, exist_ok=True)

# ── 常量 ────────────────────────────────────────────────────────────────────
N_WEEKS = 16  # 每学期 16 周（mock 里 wk0..wk15）

STATIC_COLS = [
    "gender", "major", "entrance_score", "past_retake_count", "special_flag",
    "credits", "elective",
]
OBSERVED_COLS = [
    "attendance_rate", "ppt_clicks_cum", "video_watch_cum",
    "quiz_accuracy_cum", "hw_score_cum", "hw_days_late_cum", "absent_count_cum",
]
KNOWN_FUTURE_COLS = [
    "week_norm", "is_late_half", "weeks_to_final",
]
LABEL_COLS = ["is_positive", "risk_event_time"]
KEY_COLS = ["stu_id", "course_code", "semester", "week"]


# ═════════════════════════════════════════════════════════════════════════════
# 1. 载入清洗后数据
# ═════════════════════════════════════════════════════════════════════════════
print("▶ 载入清洗后数据...")
platform = pd.read_parquet(INTERIM / "platform_log.parquet")
quiz = pd.read_parquet(INTERIM / "quiz_item.parquet")
asm_score = pd.read_parquet(INTERIM / "student_assessment.parquet")
asm_cfg = pd.read_parquet(INTERIM / "assessment_config.parquet")
finals = pd.read_parquet(INTERIM / "final_result.parquet")
stu_info = pd.read_parquet(INTERIM / "student_info.parquet")
courses = pd.read_parquet(INTERIM / "courses.parquet")
reg = pd.read_parquet(INTERIM / "registration.parquet")

# student_info 有重复行（同一学生跨学期），取一条（entrance 等是不变的）
stu_info = stu_info.drop_duplicates(subset="stu_id").reset_index(drop=True)

# assessment_config 加 week（release_date 对齐 calendar）
calendar = pd.read_parquet(INTERIM / "calendar.parquet")
asm_cfg = asm_cfg.merge(calendar, on="semester", how="left")
asm_cfg["deadline_dt"] = pd.to_datetime(asm_cfg["deadline"], errors="coerce")
asm_cfg["release_dt"] = pd.to_datetime(asm_cfg["release_date"], errors="coerce")
asm_cfg["deadline_week"] = (
    (asm_cfg["deadline_dt"] - asm_cfg["start"]).dt.days // 7
).clip(lower=0, upper=N_WEEKS - 1).fillna(0).astype(int)


# ═════════════════════════════════════════════════════════════════════════════
# 2. 构造完整笛卡尔积：所有学生 × 所有课程 × 所有学期 × 所有周
# ═════════════════════════════════════════════════════════════════════════════
print("▶ 构造完整周粒度网格...")
# 以 final_result 为基准（这是所有有效 学生×课×学期 组合）
base_keys = finals[["stu_id", "course_code", "semester"]].drop_duplicates()

weeks = pd.DataFrame({"week": range(N_WEEKS)})
grid = base_keys.merge(weeks, how="cross")
print(f"  网格: {len(grid):,} 行  (≈ {len(base_keys):,} 学生×课 × {N_WEEKS} 周)")


# ═════════════════════════════════════════════════════════════════════════════
# 3. 时变特征：platform_log → 周级累计
# ═════════════════════════════════════════════════════════════════════════════
print("▶ 聚合 platform_log ...")

# 3a. 出勤：每周到课 / 总课次
attend_per_week = (
    platform.groupby(["stu_id", "course_code", "semester", "week"])
    .agg(
        sessions_total=("class_session_id", "nunique"),
        sessions_attended=(
            "attendance_status",
            lambda s: (s.isin(["attended", "attended_leave"])).sum(),
        ),
        sessions_absent=(
            "attendance_status",
            lambda s: (s == "absent").sum(),
        ),
        ppt_clicks=("ppt_page_clicks", "sum"),
        video_watch=("video_watch_ratio", "mean"),
    )
    .reset_index()
)

# 3b. 累积到本周（防泄漏核心！之前周的值要包含在本周里）
attend_cum = attend_per_week.sort_values(["stu_id", "course_code", "semester", "week"])
grp = attend_cum.groupby(["stu_id", "course_code", "semester"], group_keys=False)
for col in ["sessions_total", "sessions_attended", "sessions_absent",
            "ppt_clicks"]:
    attend_cum[col] = grp[col].cumsum()
# video_watch 取均值更合理（累计均值 = 滚动平均）
attend_cum["video_watch"] = (
    attend_cum.groupby(["stu_id", "course_code", "semester"], group_keys=False)
    ["video_watch"].expanding().mean().reset_index(level=[0, 1, 2], drop=True)
)
# 出勤率 = 累计出勤 / 累计应有课次（应有课次不会变，但累计到本周）
attend_cum["attendance_rate"] = np.where(
    attend_cum["sessions_total"] > 0,
    attend_cum["sessions_attended"] / attend_cum["sessions_total"],
    0.0,
)

attend_cum = attend_cum.rename(columns={
    "ppt_clicks": "ppt_clicks_cum",
    "video_watch": "video_watch_cum",
    "sessions_absent": "absent_count_cum",
})
attend_cum = attend_cum[["stu_id", "course_code", "semester", "week",
                          "attendance_rate", "ppt_clicks_cum",
                          "video_watch_cum", "absent_count_cum"]]


# ═════════════════════════════════════════════════════════════════════════════
# 4. 随堂小测特征：quiz_item → 周级累计正确率
# ═════════════════════════════════════════════════════════════════════════════
print("▶ 聚合随堂小测 ...")
quiz_per_week = (
    quiz.groupby(["stu_id", "course_code", "semester", "week"])
    .agg(
        quiz_items=("item_id", "count"),
        quiz_correct=("score", lambda s: (s == 1).sum()),
        quiz_points=("score", "sum"),
        quiz_max=("max_score", "sum"),
    )
    .reset_index()
)
# 累积
quiz_per_week = quiz_per_week.sort_values(
    ["stu_id", "course_code", "semester", "week"])
grp_q = quiz_per_week.groupby(["stu_id", "course_code", "semester"],
                               group_keys=False)
for c in ["quiz_items", "quiz_correct", "quiz_points", "quiz_max"]:
    quiz_per_week[c] = grp_q[c].cumsum()
quiz_per_week["quiz_accuracy_cum"] = np.where(
    quiz_per_week["quiz_max"] > 0,
    quiz_per_week["quiz_points"] / quiz_per_week["quiz_max"],
    0.0,
)
quiz_per_week = quiz_per_week[["stu_id", "course_code", "semester", "week",
                                "quiz_accuracy_cum"]]


# ═════════════════════════════════════════════════════════════════════════════
# 5. 作业特征：student_assessment → 按 deadline_week 累计
# ═════════════════════════════════════════════════════════════════════════════
print("▶ 聚合作业/考核 ...")
asm_score_w = asm_score.merge(
    asm_cfg[["assessment_id", "course_code", "semester", "deadline_week",
             "max_score", "weight", "type"]],
    on="assessment_id", how="inner",
)
# 每(学生×课×deadline_week)聚合
asm_per_week = (
    asm_score_w.groupby(["stu_id", "course_code", "semester", "deadline_week"])
    .agg(
        hw_points=("score", "sum"),
        hw_max=("max_score", "sum"),
        hw_days_late=("days_late", "mean"),
        hw_is_absent=("is_absent", "sum"),
    )
    .reset_index()
    .rename(columns={"deadline_week": "week"})
)
# 累积到本周
asm_per_week = asm_per_week.sort_values(
    ["stu_id", "course_code", "semester", "week"])
grp_a = asm_per_week.groupby(["stu_id", "course_code", "semester"],
                              group_keys=False)
for c in ["hw_points", "hw_max", "hw_days_late", "hw_is_absent"]:
    asm_per_week[c] = grp_a[c].cumsum()
asm_per_week["hw_score_cum"] = np.where(
    asm_per_week["hw_max"] > 0,
    asm_per_week["hw_points"] / asm_per_week["hw_max"],
    0.0,
)
asm_per_week = asm_per_week[["stu_id", "course_code", "semester", "week",
                              "hw_score_cum", "hw_days_late_cum".replace(
                                  "_cum", "")]]
asm_per_week = asm_per_week.rename(columns={
    "hw_days_late": "hw_days_late_cum",
})


# ═════════════════════════════════════════════════════════════════════════════
# 6. 左连接到完整网格 → 向前填充累计值（防泄漏关键！）
# ═════════════════════════════════════════════════════════════════════════════
print("▶ 左连接 + 向前填充累计值...")
df = grid.merge(attend_cum, on=KEY_COLS, how="left")
df = df.merge(quiz_per_week, on=KEY_COLS, how="left")
df = df.merge(asm_per_week, on=KEY_COLS, how="left")

# 防泄漏核心：累计特征 = "截至本周的最新可用值"
#  如果某周没有数据，继承上一周的累计值（不是归零！）
obs_cols_now = [c for c in OBSERVED_COLS if c in df.columns]
if not obs_cols_now:
    obs_cols_now = [c for c in [
        "attendance_rate", "ppt_clicks_cum", "video_watch_cum",
        "quiz_accuracy_cum", "hw_score_cum", "hw_days_late_cum",
        "absent_count_cum",
    ] if c in df.columns]

df = df.sort_values(KEY_COLS)
grp_all = df.groupby(["stu_id", "course_code", "semester"], group_keys=False)
# 先按组内向前填充（同一学生在同一课上的前序周值）
for c in obs_cols_now:
    df[c] = grp_all[c].ffill().fillna(0.0)  # 最前面的无数据周填 0

print(f"  向前填充了 {len(obs_cols_now)} 个累计特征列")


# ═════════════════════════════════════════════════════════════════════════════
# 7. 静态特征：join student_info + courses
# ═════════════════════════════════════════════════════════════════════════════
print("▶ 拼接静态特征...")
# student_info
df = df.merge(
    stu_info[["stu_id", "gender", "major", "entrance_score",
              "past_retake_count", "special_flag"]],
    on="stu_id", how="left",
)
# courses
courses_small = courses.rename(columns={"code": "course_code"})
df = df.merge(
    courses_small[["course_code", "credits", "elective"]],
    on="course_code", how="left",
)

# 类别 one-hot
df["gender"] = df["gender"].map({"男": 1, "女": 0, True: 1, False: 0}).fillna(0)
major_dummies = pd.get_dummies(df["major"], prefix="major", dtype=float)
df = pd.concat([df, major_dummies], axis=1)
df["special_flag"] = df["special_flag"].astype(float)
df["elective"] = df["elective"].astype(float)

# 入学成绩标准化（z-score）
df["entrance_score"] = (
    df["entrance_score"].astype(float)
    .pipe(lambda s: (s - s.mean()) / (s.std() + 1e-8))
    .fillna(0.0)
)


# ═════════════════════════════════════════════════════════════════════════════
# 8. known_future 特征（可预知）
# ═════════════════════════════════════════════════════════════════════════════
print("▶ 计算 known_future 特征...")
df["week_norm"] = df["week"] / N_WEEKS              # 0→1 归一
df["is_late_half"] = (df["week"] >= N_WEEKS / 2).astype(float)
df["weeks_to_final"] = (N_WEEKS - 1 - df["week"]).clip(lower=0).astype(float)


# ═════════════════════════════════════════════════════════════════════════════
# 9. 拼接标签 + split_group
# ═════════════════════════════════════════════════════════════════════════════
print("▶ 拼接标签 + split_group ...")
label_cols = finals[["stu_id", "course_code", "semester",
                     "is_positive", "risk_event_time"]].copy()
# risk_event_time 用 -1 表示没有（负例）或事件发生在第几周
label_cols["risk_event_time"] = label_cols["risk_event_time"].fillna(-1).astype(int)
df = df.merge(label_cols, on=["stu_id", "course_code", "semester"], how="left")
df["is_positive"] = df["is_positive"].fillna(0).astype(int)

# split_group：按 stu_id 做哈希切分，保证同一学生不跨 train/val/test
rng = np.random.RandomState(42)
stu_ids_all = df["stu_id"].unique()
rng.shuffle(stu_ids_all)
n = len(stu_ids_all)
stu_to_split = {}
for i, sid in enumerate(stu_ids_all):
    if i < int(n * 0.7):
        stu_to_split[sid] = "train"
    elif i < int(n * 0.85):
        stu_to_split[sid] = "val"
    else:
        stu_to_split[sid] = "test"
df["split_group"] = df["stu_id"].map(stu_to_split)


# ═════════════════════════════════════════════════════════════════════════════
# 10. DeepSurv 事件表
# ═════════════════════════════════════════════════════════════════════════════
print("▶ 生成 DeepSurv 事件表...")
surv = finals[["stu_id", "course_code", "semester", "is_positive",
                "risk_event_time"]].copy()
surv["start_time"] = 0  # 从 wk0 开始观察
surv["event_time"] = surv["risk_event_time"].where(surv["is_positive"] == 1,
                                                    N_WEEKS - 1)
surv["event_time"] = surv["event_time"].fillna(N_WEEKS - 1).astype(int)
surv["event"] = surv["is_positive"].astype(int)  # 1=发生事件，0=删失
surv = surv[["stu_id", "course_code", "semester", "start_time",
              "event_time", "event"]]
surv.to_parquet(OUT / "deepsurv_events.parquet", index=False)
print(f"  DeepSurv: {len(surv):,} 行")


# ═════════════════════════════════════════════════════════════════════════════
# 11. 保存宽表
# ═════════════════════════════════════════════════════════════════════════════
df = df.sort_values(KEY_COLS).reset_index(drop=True)
df.to_parquet(OUT / "tft_weekly.parquet", index=False)

print(f"\n✅ TFT 宽表: {len(df):,} 行 × {df.shape[1]} 列")
print(f"   学生×课×学期数: {len(base_keys):,}")
print(f"   每周样本: {len(df) / N_WEEKS:,.0f}")
print(f"   正例占比（按 student×course 粒度）: "
      f"{df.groupby(['stu_id','course_code','semester'])['is_positive'].max().mean():.2%}")
print(f"   split: train={sum(df['split_group']=='train')/len(df):.1%}  "
      f"val={sum(df['split_group']=='val')/len(df):.1%}  "
      f"test={sum(df['split_group']=='test')/len(df):.1%}")

# 特征分类打印
print("\n── 特征分类 ──")
all_cols = list(df.columns)
print(f"  KEY  (4)       : {[c for c in KEY_COLS if c in all_cols]}")
print(f"  STATIC ({len(STATIC_COLS)}) : {STATIC_COLS}")
print(f"  OBSERVED ({len(OBSERVED_COLS)}): {OBSERVED_COLS}")
print(f"  KNOWN_FUTURE ({len(KNOWN_FUTURE_COLS)}): {KNOWN_FUTURE_COLS}")
print(f"  LABEL (2)      : {LABEL_COLS}")
print(f"  split_group    : 1")

# 简单 sanity check：原始计数型累计特征应单调不减
# 比率/均值/正确率类不是单调的（合法波动），不校验
CUM_COUNT_COLS = ["ppt_clicks_cum", "hw_days_late_cum", "absent_count_cum"]
check = df.sort_values(KEY_COLS)
print(f"\n── 防泄漏校验（仅计数型累计特征需单调不减）──")
for c in CUM_COUNT_COLS:
    if c not in check.columns:
        continue
    chk = (check.groupby(["stu_id", "course_code", "semester"], group_keys=False)
           [c].diff().fillna(0) < -1e-6).sum()
    tag = "✅" if chk == 0 else f"❌ {chk} 处下降"
    print(f"  {c}: {tag}")
print("  比率/均值/正确率类（attendance_rate / quiz_accuracy / hw_score / video_watch）: 不校验（天然波动）")
