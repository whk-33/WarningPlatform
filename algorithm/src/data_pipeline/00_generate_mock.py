"""
Mock 数据生成器 · 学业预警平台阶段一
固定 seed=42，故意制造脏数据 + 可学习信号

故意造脏：
  1) 四套跨系统学生 ID 不一致（纯数字 / 偏移纯数字 / UUID / 另一套）
  2) 三种时间格式混（ISO8601 / 纯日期 / Unix 毫秒）
  3) 噪声：重复签到、迟到 > 60、缺勤却有签到时间

故意注入可学习信号（模型必须能从这些模式里学到东西）：
  A) 正例学生出勤率均值 0.45，负例 0.85
  B) 先修课 AAA < 60 → BBB 挂科概率 70%
  C) 正例学生随堂小测正确率从 wk4 开始明显下滑

用法：python algorithm/src/data_pipeline/00_generate_mock.py --out algorithm/data/raw
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

# ── 固定 seed 保证可复现 ──
SEED = 42
np.random.seed(SEED)
Faker.seed(SEED)
fake = Faker("zh_CN")

# ── 配置 ──
N_STUDENTS = 200
N_POSITIVE = 50
WEEKS_PER_SEM = 16
COURSES = [
    {"code": "AAA", "name": "高等数学",   "credits": 4, "elective": False},
    {"code": "BBB", "name": "线性代数",   "credits": 3, "elective": False, "prereq": "AAA"},
    {"code": "CCC", "name": "大学英语",   "credits": 3, "elective": False},
    {"code": "DDD", "name": "Python编程", "credits": 2, "elective": True},
    {"code": "EEE", "name": "数据结构",   "credits": 4, "elective": False, "prereq": "BBB"},
]
COURSE_CODES = [c["code"] for c in COURSES]
COURSE_PREREQ = {c["code"]: c.get("prereq") for c in COURSES}
SEMESTERS = ["2024F", "2025S"]
CALENDAR = {
    "2024F": pd.Timestamp("2024-09-02"),
    "2025S": pd.Timestamp("2025-02-24"),
}
POSITIVE_STATUS_POOL = ["不合格"] * 35 + ["缺考"] * 5 + ["退学"] * 5 + ["休学"] * 5
NEGATIVE_STATUS_POOL = ["优秀"] * 20 + ["良好"] * 30 + ["合格"] * 50
KP_PER_COURSE = 10


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def uni_format(t: pd.Timestamp) -> str:
    """生成器内部统一用 pd.Timestamp，脏数据导出时故意转混格式"""
    return t


def dirty_time(ts: pd.Timestamp) -> str:
    """把 pd.Timestamp 故意转成三种格式之一，模拟真实数据导出混乱"""
    fmt = np.random.choice(["iso", "date", "unix_ms"], p=[0.5, 0.25, 0.25])
    if fmt == "iso":
        return ts.strftime("%Y-%m-%dT%H:%M:%S")
    if fmt == "date":
        return ts.strftime("%Y-%m-%d")
    return str(int(ts.timestamp() * 1000))


def dirty_date(ts: pd.Timestamp) -> str:
    """纯日期格式，也混一下"""
    fmt = np.random.choice(["%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"])
    return ts.strftime(fmt)


def week_to_date(week: int, sem: str) -> pd.Timestamp:
    return CALENDAR[sem] + pd.Timedelta(days=7 * week + np.random.randint(0, 7))


# ═══════════════════════════════════════════════════════════════════════════════
# 主生成函数
# ═══════════════════════════════════════════════════════════════════════════════

def generate():
    # ── 先建立统一的学生基础表 ──
    stu_nums = np.arange(100001, 100001 + N_STUDENTS)  # 纯数字基准 ID
    np.random.shuffle(stu_nums)
    pos_nums = set(stu_nums[:N_POSITIVE])     # 正例学生（挂科/退学/休学）
    neg_nums = set(stu_nums[N_POSITIVE:])

    # 每个学生的静态特征
    stu_static = []
    for num in stu_nums:
        stu_static.append({
            "stu_num": num,
            "gender": np.random.choice(["男", "女"], p=[0.52, 0.48]),
            "major": np.random.choice(["计算机", "软件工程", "人工智能", "电子信息", "数学"]),
            "entrance_score": int(np.clip(np.random.normal(560, 45), 430, 660)),
            "past_retake_count": int(np.random.choice([0, 0, 0, 1, 2])),
            "special_flag": np.random.random() < 0.05,
        })
    df_stu_base = pd.DataFrame(stu_static)

    # ── 故意制造四套跨系统 ID，导出 id_crosswalk 供清洗脚本打通 ──
    crosswalk_rows = []
    for _, s in df_stu_base.iterrows():
        num = s["stu_num"]
        crosswalk_rows.append({
            "stu_num_raw": str(num),          # 教务系统用
            "stu_num_offset": str(int(num) + 1_000_000),  # quiz_item 表故意偏移
            "stu_uuid": fake.uuid4(),         # 学习平台用
            "unified": f"stu_{num}",          # 清洗后统一格式
        })
    df_crosswalk = pd.DataFrame(crosswalk_rows)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 表 1：student_info（故意用纯数字 stu_num，不加前缀）
    # ═══════════════════════════════════════════════════════════════════════════════
    info_rows = []
    for _, s in df_stu_base.iterrows():
        for sem in SEMESTERS:
            info_rows.append({
                "stu_num": str(s["stu_num"]),        # 纯数字！故意不统一
                "semester": sem,
                "gender": s["gender"],
                "major": s["major"],
                "entrance_score": s["entrance_score"],
                "past_retake_count": s["past_retake_count"],
                "special_flag": s["special_flag"],
            })
    df_info = pd.DataFrame(info_rows)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 表 2：courses
    # ═══════════════════════════════════════════════════════════════════════════════
    df_courses = pd.DataFrame(COURSES)
    df_prereq = pd.DataFrame(
        [(c, p) for c, p in COURSE_PREREQ.items() if p is not None],
        columns=["course_code", "prereq_course_code"],
    )
    df_prereq["strength"] = 1.0
    df_prereq["source"] = "manual"

    # ═══════════════════════════════════════════════════════════════════════════════
    # 表 3：registration（故意用纯数字 stu_num，和 info 一致的一套）
    # ═══════════════════════════════════════════════════════════════════════════════
    reg_rows = []
    # 5 门课，2 学期，每学期每学生选 ~80%
    for num in stu_nums:
        for sem in SEMESTERS:
            for code in COURSE_CODES:
                # 强制先修链（BBB 的先修 AAA、EEE 的先修 BBB 在 2024F）
                if code == "BBB" and sem == "2024F" and (num in pos_nums):
                    pass  # 正例学生可能不修 BBB（但修 AAA）
                if np.random.random() < 0.80:
                    enroll_date = CALENDAR[sem] + pd.Timedelta(days=np.random.randint(-5, 3))
                    reg_rows.append({
                        "stu_num": str(num),          # 纯数字！和 info 一套
                        "course_code": code,
                        "semester": sem,
                        "enroll_date": dirty_date(enroll_date),  # 故意混日期格式
                        "drop_date": None,            # 退学学生后面填
                        "is_retake": False,
                    })
    df_reg = pd.DataFrame(reg_rows)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 表 4：assessment_config
    # ═══════════════════════════════════════════════════════════════════════════════
    asm_rows = []
    asm_id_counter = 0
    asm_types = [
        ("hw1", "homework", 0.10), ("hw2", "homework", 0.10),
        ("quiz1", "quiz", 0.08), ("quiz2", "quiz", 0.08),
        ("midterm", "midterm", 0.14),
        ("quiz3", "quiz", 0.08), ("quiz4", "quiz", 0.08),
        ("final", "final", 0.34),
    ]
    for sem in SEMESTERS:
        for code in COURSE_CODES:
            for label, atype, weight in asm_types:
                asm_id = f"asm_{asm_id_counter:04d}"
                asm_id_counter += 1
                # 考试周大致分布：homework wk2/4, quiz wk5/7, midterm wk8, quiz wk10/12, final wk15
                wks_guess = {"hw1": 2, "hw2": 5, "quiz1": 4, "quiz2": 7,
                             "midterm": 8, "quiz3": 11, "quiz4": 13, "final": 15}
                wk = wks_guess.get(label, 8)
                deadline = week_to_date(wk, sem) + pd.Timedelta(hours=23)
                release = deadline - pd.Timedelta(days=np.random.randint(1, 4))
                asm_rows.append({
                    "assessment_id": asm_id,
                    "course_code": code,
                    "semester": sem,
                    "type": atype,
                    "release_date": dirty_date(release),
                    "deadline": dirty_date(deadline),
                    "max_score": 100,
                    "weight": weight,
                })
    df_asm_cfg = pd.DataFrame(asm_rows)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 表 5：final_result + 先算好学生×课程最终状态
    # ═══════════════════════════════════════════════════════════════════════════════
    # 关键：先修链信号 + 出勤率信号
    result_rows = []
    # 先算 2024F 学期的先修课成绩，供 2025S 决策
    sem_2024f_score: dict[tuple, float] = {}  # (stu_num, course_code) → 期末分

    # 退学学生的 drop_date 位置（在哪个学期哪周）
    dropout_info = {}  # stu_num → (semester, week_idx, reason)

    # 分配 5 个退学 + 5 个休学学生
    pool = list(pos_nums)
    np.random.shuffle(pool)
    dropouts = set(pool[:5])
    leaves   = set(pool[5:10])

    for num in stu_nums:
        for sem in SEMESTERS:
            # 退学学生：如果在 2024F 退学，就不再有 2025S 成绩
            if num in dropouts and sem == "2025S":
                continue
            for code in COURSE_CODES:
                # 只有注册过的才出成绩
                mask = (df_reg["stu_num"] == str(num)) & \
                       (df_reg["course_code"] == code) & \
                       (df_reg["semester"] == sem)
                if not mask.any():
                    continue
                # 退学/休学：只给已结束周之前的成绩（或者直接退学状态）
                if num in dropouts and sem == "2024F":
                    drop_week = np.random.randint(5, 14)
                    dropout_info[num] = (sem, drop_week, "退学")
                    final_score = np.clip(np.random.normal(55, 15), 0, 100)
                    result_rows.append({
                        "stu_num": str(num), "course_code": code, "semester": sem,
                        "final_score": round(final_score, 1), "status": "退学",
                        "gpa": 0,
                        "risk_event_time": drop_week,
                    })
                    # 注册表里填 drop_date
                    df_reg.loc[mask, "drop_date"] = \
                        dirty_date(week_to_date(drop_week, sem))
                    continue
                if num in leaves and sem == "2024F":
                    drop_week = np.random.randint(8, 14)
                    dropout_info[num] = (sem, drop_week, "休学")
                    final_score = np.clip(np.random.normal(45, 20), 0, 100)
                    result_rows.append({
                        "stu_num": str(num), "course_code": code, "semester": sem,
                        "final_score": round(final_score, 1), "status": "休学",
                        "gpa": 0,
                        "risk_event_time": drop_week,
                    })
                    df_reg.loc[mask, "drop_date"] = \
                        dirty_date(week_to_date(drop_week, sem))
                    continue

                # ── 核心信号注入：先修链 + 出勤率 ──
                # 出勤率均值
                attend_mean = 0.45 if num in pos_nums else 0.85
                # 先修影响
                score_base = 75.0
                # 2025S 的 BBB：看 2024F 的 AAA
                if code == "BBB" and sem == "2025S" and (num, "AAA") in sem_2024f_score:
                    prev = sem_2024f_score[(num, "AAA")]
                    if prev < 60:
                        # 挂科概率 70%
                        score_base = np.random.choice([40, 85], p=[0.7, 0.3])
                    elif prev >= 75:
                        score_base = np.random.choice([90, 55], p=[0.9, 0.1])
                    else:
                        score_base = 70
                # 2025S 的 EEE：看 2024F 的 BBB
                if code == "EEE" and sem == "2025S" and (num, "BBB") in sem_2024f_score:
                    prev = sem_2024f_score[(num, "BBB")]
                    if prev < 60:
                        score_base = np.random.choice([35, 70], p=[0.7, 0.3])
                    elif prev >= 75:
                        score_base = np.random.choice([85, 50], p=[0.85, 0.15])
                # 出勤率影响
                if attend_mean < 0.50:
                    score_base -= np.random.uniform(15, 30)
                elif attend_mean > 0.80:
                    score_base += np.random.uniform(5, 15)
                # 学生个体水平
                score_base += np.random.normal(0, 10)
                final_score = float(np.clip(score_base, 0, 100))
                # 特殊处理：缺勤 → 缺考
                if num in pos_nums and np.random.random() < 0.06:
                    status = "缺考"
                    final_score = 0
                    event_time = np.random.randint(10, 16)
                elif final_score < 60:
                    status = "不合格"
                    event_time = np.random.randint(8, 14)
                elif final_score < 70:
                    status = "合格"
                    event_time = WEEKS_PER_SEM
                elif final_score < 85:
                    status = "良好"
                    event_time = WEEKS_PER_SEM
                else:
                    status = "优秀"
                    event_time = WEEKS_PER_SEM
                result_rows.append({
                    "stu_num": str(num), "course_code": code, "semester": sem,
                    "final_score": round(final_score, 1), "status": status,
                    "gpa": round(final_score / 25.0, 2),
                    "risk_event_time": event_time,
                })
                sem_2024f_score[(num, code)] = final_score  # 记录给下学期用

    df_final = pd.DataFrame(result_rows)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 表 6：student_assessment（故意用纯数字 stu_num + 三种时间格式）
    # ═══════════════════════════════════════════════════════════════════════════════
    asm_stu_rows = []
    # 10 个补考学生（正例里挑，首次低、补考高）
    retake_pool = [num for num in pos_nums if num not in dropouts and num not in leaves][:10]

    # 按 final_result 里的成绩，反推每次考核得分
    asm_cfg_lookup = df_asm_cfg.set_index("assessment_id").to_dict("index")

    for _, r in df_final.iterrows():
        num = int(r.stu_num)
        sem = r.semester
        code = r.course_code
        final_score = r.final_score
        status = r.status

        # 找到这学期这门课的所有考核
        course_asms = df_asm_cfg[
            (df_asm_cfg["course_code"] == code) & (df_asm_cfg["semester"] == sem)
        ]
        if course_asms.empty:
            continue

        # 退学/休学学生：只给 drop_week 之前的考核有成绩
        drop_wk = None
        if num in dropout_info and dropout_info[num][0] == sem:
            drop_wk = dropout_info[num][1]

        for _, a in course_asms.iterrows():
            aid = a["assessment_id"]
            atype = a["type"]
            # 考核 deadline 在哪周（从 dirty_date 里反解）
            try:
                dl = pd.to_datetime(a["deadline"])
                wk = (dl - CALENDAR[sem]).days // 7
            except Exception:
                wk = 8
            if drop_wk is not None and wk > drop_wk:
                continue  # 退学之后的考核不算

            # 得分策略
            # 正例学生（含退学/休学）在 wk4 后逐步下滑，负例稳定
            attend_mean = 0.45 if num in pos_nums else 0.85
            if status in ("退学", "休学"):
                base = np.random.normal(50, 15)
            elif num in pos_nums:
                # 前 4 周还有点希望，之后下滑
                trend = 0 if wk <= 4 else -3 * (wk - 4)
                base = 70 + trend + np.random.normal(0, 10)
            else:
                base = 78 + np.random.normal(0, 8)
            score = float(np.clip(base, 0, 100))

            # 补考学生：40% 概率某一次作业补考
            attempt_index = 1
            if num in retake_pool and atype in ("homework", "quiz") \
                    and np.random.random() < 0.4:
                # 首次低
                asm_stu_rows.append({
                    "stu_num": str(num), "assessment_id": aid, "score": round(score - 20, 1),
                    "submit_time": dirty_time(dl - pd.Timedelta(days=2)),
                    "days_late": 2, "is_absent": False, "attempt_index": 1,
                })
                # 补考
                score = float(np.clip(base + 10, 0, 100))
                attempt_index = 2

            submit = dl - pd.Timedelta(hours=np.random.randint(0, 72))
            asm_stu_rows.append({
                "stu_num": str(num), "assessment_id": aid, "score": round(score, 1),
                "submit_time": dirty_time(submit),  # 故意混格式
                "days_late": 0 if num not in pos_nums else np.random.choice([0, 0, 1, 3, 5]),
                "is_absent": False, "attempt_index": attempt_index,
            })

    df_asm_stu = pd.DataFrame(asm_stu_rows)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 表 7：platform_log（**故意用 UUID 格式的 stu_num**，混三种时间格式）
    # ═══════════════════════════════════════════════════════════════════════════════
    log_rows = []
    log_stu_uuid = {str(row["stu_num_raw"]): row["stu_uuid"]
                    for _, row in df_crosswalk.iterrows()}
    resource_types = ["ppt", "video", "quiz", "forum", "assignment"]

    def sample_status(attend_mean: float) -> str:
        """按 attend_mean 动态决定出勤率分布
        - 负例 attend_mean≈0.85 → attended 85%, attended_leave 10%, absent 5%
        - 正例 attend_mean≈0.45 → attended 45%, attended_leave 15%, absent 40%
        """
        p_attend   = attend_mean
        p_leave    = max(0.08, 0.5 * (1 - attend_mean))
        p_absent   = 1 - p_attend - p_leave
        return np.random.choice(
            ["attended", "attended_leave", "absent"],
            p=[p_attend, p_leave, p_absent],
        )

    # 每个学生×课程×每周 ~ 15 次行为
    for _, r in df_final.iterrows():
        num = int(r.stu_num)
        sem = r.semester
        code = r.course_code
        uuid_id = log_stu_uuid[str(num)]
        # 出勤率均值（信号）
        attend_mean = 0.45 if num in pos_nums else 0.85

        # 退学/休学：只生成 drop_week 之前的行为
        drop_wk = None
        if num in dropout_info and dropout_info[num][0] == sem:
            drop_wk = dropout_info[num][1]

        for wk in range(WEEKS_PER_SEM):
            if drop_wk is not None and wk >= drop_wk:
                break
            n_actions = np.random.randint(6, 20)
            for _ in range(n_actions):
                # 按 attend_mean 概率加权决定出勤状态
                status = sample_status(attend_mean)

                ts = week_to_date(wk, sem) + pd.Timedelta(
                    hours=np.random.randint(8, 22),
                    minutes=np.random.randint(0, 60),
                )
                res_type = np.random.choice(resource_types)

                row = {
                    "stu_uuid": uuid_id,  # 故意：平台表用 UUID，和其他表完全不同！
                    "course_code": code,
                    "semester": sem,
                    "resource_id": f"res_{code}_{res_type}_{np.random.randint(1, 30):02d}",
                    "resource_type": res_type,
                    "timestamp": dirty_time(ts),   # 故意混三种时间格式
                    "attendance_status": status,
                    "late_minutes": 0 if status == "attended_leave"
                                    else np.random.choice([0, 0, 0, 5, 15, 25])
                                        if status == "attended"
                                    else 0,
                    "ppt_page_clicks": np.random.randint(30, 200)
                                        if res_type == "ppt" else 0,
                    "video_watch_ratio": round(np.random.uniform(0.3, 1.0), 2)
                                        if res_type == "video" else 0.0,
                    "class_session_id": f"cs_{code}_{sem}_wk{wk}",
                }
                # 故意制造 3% 迟到异常（>60 分钟）、2% 缺勤却有签到时间（矛盾）
                if np.random.random() < 0.03:
                    row["late_minutes"] = np.random.randint(90, 200)
                if np.random.random() < 0.02 and row["attendance_status"] == "absent":
                    row["timestamp"] = dirty_time(ts)  # 矛盾：缺勤却有签到时间
                log_rows.append(row)

    df_log = pd.DataFrame(log_rows)

    # 故意制造 5% 重复签到噪声
    n_dup = int(len(df_log) * 0.05)
    dup_idx = np.random.choice(df_log.index, size=n_dup, replace=False)
    df_dup = df_log.loc[dup_idx].copy()
    df_log = pd.concat([df_log, df_dup], ignore_index=True)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 表 8：quiz_item_responses（**故意用偏移 + 1_000_000 的 stu_num**，15% kp_id 为空）
    # ═══════════════════════════════════════════════════════════════════════════════
    quiz_rows = []
    # 随堂小测从 2024F wk3 开始，每两周一次（共 7 次）
    quiz_weeks = [3, 5, 7, 9, 11, 13, 15]
    for _, r in df_final.iterrows():
        num = int(r.stu_num)
        offset_num = num + 1_000_000       # 故意偏移 100 万！
        sem = r.semester
        code = r.course_code
        status = r.status
        kps = [f"kp_{code}_{i:02d}" for i in range(1, KP_PER_COURSE + 1)]

        drop_wk = None
        if num in dropout_info and dropout_info[num][0] == sem:
            drop_wk = dropout_info[num][1]

        for qi, wk in enumerate(quiz_weeks):
            if drop_wk is not None and wk >= drop_wk:
                break
            # 正例学生 wk4 起正确率下滑（信号注入）
            if num in pos_nums and wk >= 4:
                correct_prob = max(0.15, 0.65 - 0.06 * (wk - 4))
            elif num in pos_nums:
                correct_prob = 0.55
            else:
                correct_prob = 0.85

            for item_i in range(1, 11):   # 每次小测 10 题
                # 故意 15% 的题没有知识点标签
                has_kp = np.random.random() > 0.15
                kp_id = np.random.choice(kps) if has_kp else None
                correct = np.random.random() < correct_prob
                score = 1 if correct else 0
                quiz_rows.append({
                    "stu_num_offset": str(offset_num),   # 故意偏移！和其他表都不一样！
                    "item_id": f"qi_{code}_w{wk}_q{item_i:02d}",
                    "kp_id": kp_id,
                    "kp_label_missing": not has_kp,
                    "score": score,
                    "max_score": 1,
                    "week": wk,
                    "course_code": code,
                    "semester": sem,
                })

    df_quiz = pd.DataFrame(quiz_rows)

    # ═══════════════════════════════════════════════════════════════════════════════
    # 表 9：calendar（干净的）
    # ═══════════════════════════════════════════════════════════════════════════════
    df_calendar = pd.DataFrame({
        "semester": list(CALENDAR.keys()),
        "start_date": [CALENDAR[s].strftime("%Y-%m-%d") for s in CALENDAR],
    })

    return {
        "student_info": df_info,
        "courses": df_courses,
        "course_prereq": df_prereq,
        "registration": df_reg,
        "assessment_config": df_asm_cfg,
        "student_assessment": df_asm_stu,
        "platform_log": df_log,
        "quiz_item_responses": df_quiz,
        "final_result": df_final,
        "calendar": df_calendar,
        "id_crosswalk": df_crosswalk,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="algorithm/data/raw")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tables = generate()
    for name, df in tables.items():
        path = out / f"{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  {path.name:28s}  rows={len(df):>7,}  cols={len(df.columns)}")

    print(f"\n全部生成完毕 → {out.resolve()}")
    print(f"  id_crosswalk.csv → 清洗脚本用这个打通四套学生 ID")
    # 快速自检
    df_final = tables["final_result"]
    pos_rate = df_final[df_final["status"].isin(["不合格", "缺考", "退学", "休学"])].shape[0] / len(df_final)
    print(f"  正例比例 ≈ {pos_rate:.1%}")


if __name__ == "__main__":
    main()
