"""
10_adapt_oulad.py —— OULAD → interim schema 适配器

把 Open University Learning Analytics Dataset 的 7 张原始表，转换成与
02_clean.py 输出完全一致的 interim 11 张表，使 03/04/05 三个脚本可以
一行不改地跑在真实数据上。

用法：
  python algorithm/src/data_pipeline/10_adapt_oulad.py \
      --raw algorithm/data/oulad_raw \
      --out algorithm/data/oulad/interim \
      [--limit-students 2000]

OULAD 原始文件（放到 --raw 目录下）：
  courses.csv / assessments.csv / vle.csv / studentInfo.csv /
  studentRegistration.csv / studentAssessment.csv / studentVle.csv

字段映射（OULAD → interim）：
  studentInfo.id_student            → stu_num, stu_id = "stu_" + id
  studentInfo.code_presentation     → semester
  studentInfo.gender                → gender（M/F → 男/女）
  studentInfo.highest_education     → major        ← 代理字段：OULAD 无专业
  studentInfo.studied_credits       → entrance_score ← 代理字段：学分负荷
  studentInfo.num_of_prev_attempts  → past_retake_count
  studentInfo.disability            → special_flag（Y → 1）
  studentInfo.final_result          → status / is_positive
  studentRegistration.date_registration  → enroll_date
  studentRegistration.date_unregistration→ drop_date
  assessments.id_assessment         → assessment_id
  assessments.date                  → deadline（相对天数 + 开课锚点）
  assessments.weight                → weight（max_score 固定 100）
  studentAssessment.score           → score（0..100）
  studentAssessment.date_submitted  → submit_time / days_late / is_absent
  studentVle（按周聚合）             → platform_log（见下）
  vle.id_site / activity_type       → resource_id / resource_type

需要人工介入的三处代理（真实数据到位后应替换为真实字段）：
  1. OULAD 无"课次"概念 → 用 (模块, 学期, 周) 合成 class_session_id=cs_{课}_{期}_wk{周}，
     并把 studentVle 按周聚合。原 schema 的"出勤"因此变为"周活跃"：
     本周有点击 = attended，无点击 = absent。这是唯一可用的代理口径。
  2. OULAD 无逐题作答 → 用 studentAssessment（每门考核 = 1 道"题"），
     并把考核项按截止日期均匀映射到 04_knowledge_map.py 生成的 10 个伪知识点
     （kp_{课}_{01..10}），保证 scored_on / has_kt 边能正常生成。
     判定 correct 的口径：score >= 40（英国开放大学 40 分及格线）。
  3. OULAD 无视频数据 → video_watch_ratio 固定 0。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

N_WEEKS = 16            # 与 03_weekly_features.py 的 N_WEEKS 保持一致
PASS_SCORE = 40         # OU 及格线
PPT_TYPES = {"oucontent", "ouslide", "subpage", "resource",
             "ouwiki", "folder", "glossary"}
STATUS_MAP = {"Pass": "合格", "Distinction": "优秀",
              "Fail": "不合格", "Withdrawn": "退学"}
POSITIVE_STATUSES = ["不合格", "缺考", "退学", "休学", "学业警示"]

REQUIRED = ["courses.csv", "assessments.csv", "vle.csv", "studentInfo.csv",
            "studentRegistration.csv", "studentAssessment.csv", "studentVle.csv"]


# ═══════════════════════════════════════════════════════════════════════════════
# 0. 工具
# ═══════════════════════════════════════════════════════════════════════════════
def presentation_start(code_presentation: str) -> pd.Timestamp:
    """OULAD 学期只有 B（2 月开课）/ J（10 月开课）两档，用它们做日期锚点。
    OULAD 的 date 字段都是"相对开课日的天数"，必须锚定成绝对日期才能算 week。"""
    year = int(str(code_presentation)[:4])
    term = str(code_presentation)[4:]
    month, day = (2, 1) if term.upper() == "B" else (10, 1)
    return pd.Timestamp(year=year, month=month, day=day)


def load_raw(raw: Path) -> dict[str, pd.DataFrame]:
    missing = [f for f in REQUIRED if not (raw / f).exists()]
    if missing:
        sys.exit(
            f"❌ {raw} 缺少文件: {missing}\n"
            f"   下载地址: https://research.stem.open.ac.uk/ouanalyse/open-dataset-more/\n"
            f"   解压后把 7 个 csv 放到 {raw} 下即可。")
    return {f.replace(".csv", ""): pd.read_csv(raw / f, encoding="utf-8-sig")
            for f in REQUIRED}


def save(tables: dict[str, pd.DataFrame], out: Path):
    out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(out / f"{name}.parquet", index=False)
        print(f"  {name + '.parquet':30s} rows={len(df):>9,}  cols={df.shape[1]}")


def validate_against_mock(out: Path, mock: Path = Path("algorithm/data/interim")):
    """逐表比对列名 + 字段类型，确保下游脚本和后端不会因字段漂移而报错"""
    if not mock.exists():
        print("  （未找到 mock interim，跳过校验）")
        return True
    ok = True
    print("\n── 与 mock interim 列名/类型比对 ──")
    for p in sorted(mock.glob("*.parquet")):
        target = out / p.name
        if not target.exists():
            print(f"  ⚠️  {p.name}: 适配器未产出")
            continue
        exp = pd.read_parquet(p)
        got = pd.read_parquet(target)
        miss, extra = set(exp.columns) - set(got.columns), set(got.columns) - set(exp.columns)
        diff_type = [(c, str(exp.dtypes[c]), str(got.dtypes[c]))
                     for c in exp.columns if c in got.columns
                     and str(exp.dtypes[c]) != str(got.dtypes[c])]
        if miss or extra:
            ok = False
            print(f"  ❌ {p.name}: 缺={sorted(miss)} 多={sorted(extra)}")
        elif diff_type:
            ok = False
            detail = ", ".join(f"{c}: {a}→{b}" for c, a, b in diff_type)
            print(f"  ❌ {p.name}: 类型不一致 [{detail}]")
        else:
            print(f"  ✅ {p.name}: {len(exp.columns)} 列 + 类型完全一致")
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 学籍 / 注册 / 课程 / 校历
# ═══════════════════════════════════════════════════════════════════════════════
def build_student_info(info: pd.DataFrame) -> pd.DataFrame:
    df = info.copy()
    df["stu_num"] = df["id_student"].astype("int64")
    df["semester"] = df["code_presentation"].astype(str)
    df["gender"] = df["gender"].map({"M": "男", "F": "女"}).fillna("未知")
    df["major"] = df["highest_education"].fillna("未知")
    df["entrance_score"] = pd.to_numeric(df["studied_credits"],
                                        errors="coerce").fillna(0).astype("int64")
    df["past_retake_count"] = pd.to_numeric(df["num_of_prev_attempts"],
                                            errors="coerce").fillna(0).astype("int64")
    df["special_flag"] = df["disability"].eq("Y").astype(bool)
    df["stu_id"] = "stu_" + df["stu_num"].astype(str)
    return df[["stu_num", "semester", "gender", "major", "entrance_score",
               "past_retake_count", "special_flag", "stu_id"]]


def build_registration(reg: pd.DataFrame, info: pd.DataFrame) -> pd.DataFrame:
    df = reg.copy()
    df["stu_num"] = df["id_student"].astype("int64")
    df["semester"] = df["code_presentation"].astype(str)
    df["course_code"] = df["code_module"].astype(str)

    starts = df["semester"].map(presentation_start)
    # date_registration 为相对开课日天数（负值表示提前注册）
    df["enroll_date"] = (starts + pd.to_timedelta(
        pd.to_numeric(df["date_registration"], errors="coerce").fillna(0), unit="D"))
    unreg = pd.to_numeric(df["date_unregistration"], errors="coerce")
    df["drop_date"] = starts + pd.to_timedelta(unreg, unit="D")
    df.loc[unreg.isna(), "drop_date"] = pd.NaT

    retake = info.drop_duplicates(["id_student"])[
        ["id_student", "num_of_prev_attempts"]].set_index("id_student")[
        "num_of_prev_attempts"]
    df["is_retake"] = df["stu_num"].map(retake).fillna(0).gt(0).astype(bool)
    df["stu_id"] = "stu_" + df["stu_num"].astype(str)
    return df[["stu_num", "course_code", "semester", "enroll_date", "drop_date",
               "is_retake", "stu_id"]]


def build_courses(courses: pd.DataFrame,
                  assessments: pd.DataFrame) -> pd.DataFrame:
    df = courses.drop_duplicates("code_module").copy()
    # OULAD 模块名被匿名化（AAA..GGG），保留代码作为名称
    df["code"] = df["code_module"].astype(str)
    df["name"] = "OULAD模块" + df["code"]
    # 无学分字段 → 用该模块考核项数量作为结构代理（必须非常数，否则标准化除零）
    n_asm = (assessments.groupby("code_module")["id_assessment"]
             .nunique())
    df["credits"] = df["code"].map(n_asm).fillna(1).astype("int64")
    df["elective"] = False          # OULAD 无必修/选修字段
    df["prereq"] = None             # OULAD 无先修关系
    return df[["code", "name", "credits", "elective", "prereq"]].reset_index(drop=True)


def build_course_prereq() -> pd.DataFrame:
    """OULAD 没有课程先修链 → 空表（下游 _course_prereq 会自动 dropna 成 0 条边）"""
    return pd.DataFrame({
        "course_code": pd.Series(dtype="object"),
        "prereq_course_code": pd.Series(dtype="object"),
        "strength": pd.Series(dtype="float64"),
        "source": pd.Series(dtype="object"),
    })


def build_calendar(reg: pd.DataFrame) -> pd.DataFrame:
    sems = sorted(reg["semester"].astype(str).unique())
    df = pd.DataFrame({"semester": sems})
    df["start"] = df["semester"].map(presentation_start)
    df["start_date"] = df["start"].dt.strftime("%Y-%m-%d")
    return df[["semester", "start_date", "start"]]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 考核：配置 / 学生成绩 / 逐题
# ═══════════════════════════════════════════════════════════════════════════════
def build_assessment_config(asm: pd.DataFrame,
                            courses: pd.DataFrame) -> pd.DataFrame:
    df = asm.copy()
    df["assessment_id"] = df["id_assessment"].astype(str)
    df["course_code"] = df["code_module"].astype(str)
    df["semester"] = df["code_presentation"].astype(str)
    starts = df["semester"].map(presentation_start)
    length = df["course_code"].map(
        courses.drop_duplicates("code_module")
        .set_index("code_module")["module_presentation_length"])

    rel = pd.to_numeric(df["date"], errors="coerce")
    absolute = starts + pd.to_timedelta(rel, unit="D")
    # OULAD 部分 Exam 的 date 为空 → 用学期末兜底
    fallback = starts + pd.to_timedelta(length, unit="D")
    df["deadline"] = absolute.where(rel.notna(), fallback)
    # 无发布日期字段 → 约定为截止前 14 天（03 脚本不消费该列，仅保持 schema 完整）
    df["release_date"] = (df["deadline"] - pd.Timedelta(days=14)).dt.strftime("%Y-%m-%d")
    df["deadline"] = df["deadline"].dt.strftime("%Y-%m-%d")
    df["type"] = df["assessment_type"].astype(str)
    df["max_score"] = 100
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce").fillna(0.0)
    return df[["assessment_id", "course_code", "semester", "type",
               "release_date", "deadline", "max_score", "weight"]]


def build_student_assessment(sa: pd.DataFrame,
                             asm: pd.DataFrame) -> pd.DataFrame:
    df = sa.copy()
    cfg = asm[["id_assessment", "code_module", "code_presentation", "date"]].copy()
    cfg = cfg.rename(columns={"id_assessment": "assessment_id"})
    cfg["assessment_id"] = cfg["assessment_id"].astype(str)
    df["assessment_id"] = df["id_assessment"].astype(str)

    df = df[df["is_banked"].fillna(0).eq(0)]          # 剔除带入成绩
    df = df.drop_duplicates(["id_student", "assessment_id"], keep="first")
    df = df.merge(cfg, on="assessment_id", how="left")

    df["stu_num"] = df["id_student"].astype("int64")
    starts = df["code_presentation"].map(presentation_start)
    rel = pd.to_numeric(df["date_submitted"], errors="coerce")
    df["submit_time"] = starts + pd.to_timedelta(rel, unit="D")
    df["is_absent"] = rel.isna().astype(bool)

    asm_rel = pd.to_numeric(df["date"], errors="coerce")
    late = (rel - asm_rel).where(rel.notna() & asm_rel.notna(), 0)
    df["days_late"] = late.clip(lower=0).fillna(0).round().astype(int)

    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0.0)
    df["attempt_index"] = 1                            # OULAD 无补考序号字段
    df["stu_id"] = "stu_" + df["stu_num"].astype(str)
    return df[["stu_num", "assessment_id", "score", "submit_time", "days_late",
               "is_absent", "attempt_index", "stu_id"]]


def attach_deadline_week(cfg: pd.DataFrame) -> pd.DataFrame:
    """给考核配置补一列 week（按该考核所属学期的锚点换算）"""
    df = cfg.copy()
    df["deadline_dt"] = pd.to_datetime(df["deadline"], errors="coerce")
    df["start"] = df["semester"].map(presentation_start)
    df["week"] = ((df["deadline_dt"] - df["start"]).dt.days // 7
                  ).clip(lower=0, upper=N_WEEKS - 1).fillna(0).astype(int)
    return df


def build_quiz_item(sa: pd.DataFrame, cfg: pd.DataFrame) -> pd.DataFrame:
    """studentAssessment → 逐题序列：每门考核视作 1 道题，
    并按截止日期把考核均匀映射到 kp_{课}_{01..10}（与 04 脚本生成的伪知识点对齐）。
    """
    df = sa[~sa["is_absent"]].copy()
    cfg = attach_deadline_week(cfg)

    # 考核项按截止日期排序 → 均匀分到 10 档伪知识点，并生成题目编号
    k = cfg[["assessment_id", "course_code", "semester", "week",
             "deadline_dt"]].copy()
    k = k.sort_values(["course_code", "deadline_dt", "assessment_id"])
    k["rank"] = k.groupby("course_code").cumcount()
    k["n"] = k.groupby("course_code")["assessment_id"].transform("size")
    k["kp_no"] = ((k["rank"] * 10) // k["n"]).clip(0, 9) + 1
    k["kp_id"] = ("kp_" + k["course_code"] + "_"
                  + k["kp_no"].map(lambda x: f"{x:02d}"))
    k["item_no"] = k["rank"] + 1

    df = df.merge(k[["assessment_id", "course_code", "semester", "week",
                     "kp_id", "item_no"]], on="assessment_id", how="left")

    df["week"] = df["week"].fillna(0).astype(int)
    df["stu_num_offset"] = df["stu_num"].astype("int64")
    # 题目 ID 与 mock 保持同一命名法：qi_{课}_w{周}_q{序号}
    df["item_id"] = ("qi_" + df["course_code"].astype(str)
                     + "_w" + df["week"].astype(str)
                     + "_q" + df["item_no"].map(lambda x: f"{x:02d}"))
    df["kp_label_missing"] = False   # 伪知识点：由考核截止日期派生（见文件头说明）
    df["score"] = (df["score"] >= PASS_SCORE).astype("int64")
    df["max_score"] = 1
    df["stu_id"] = "stu_" + df["stu_num"].astype(str)
    return df[["stu_num_offset", "item_id", "kp_id", "kp_label_missing",
               "score", "max_score", "week", "course_code", "semester",
               "stu_id"]].reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 行为日志：studentVle 按周聚合 → platform_log
# ═══════════════════════════════════════════════════════════════════════════════
def build_platform_log(vle: pd.DataFrame, sites: pd.DataFrame,
                       reg: pd.DataFrame) -> pd.DataFrame:
    KEYS = ["stu_num", "course_code", "semester", "week"]

    v = vle.copy()
    v["stu_num"] = v["id_student"].astype("int64")
    v["course_code"] = v["code_module"].astype(str)
    v["semester"] = v["code_presentation"].astype(str)
    v = v.merge(sites[["id_site", "activity_type"]], on="id_site", how="left")
    # 相对天数 → 周次（OULAD 的 date 就是相对开课日）
    v["week"] = (pd.to_numeric(v["date"], errors="coerce").fillna(0) // 7
                 ).clip(lower=0, upper=N_WEEKS - 1).astype(int)

    # 周级聚合
    agg = v.groupby(KEYS, observed=True).agg(
        clicks=("sum_click", "sum"),
        min_date=("date", "min"),
    ).reset_index()

    ppt = (v[v["activity_type"].isin(PPT_TYPES)]
           .groupby(KEYS, observed=True)["sum_click"].sum()
           .rename("ppt_clicks").reset_index())

    # 该周点击最多的资源 → 作为主导资源节点
    dom = (v.sort_values("sum_click", ascending=False)
           .drop_duplicates(subset=KEYS)[KEYS + ["id_site", "activity_type"]]
           .rename(columns={"id_site": "res_site",
                            "activity_type": "res_type"}))
    dom["res_site"] = "res_" + dom["res_site"].astype(str)

    # 完整网格：每个 (学生, 课, 学期) × 16 周都要有行，否则"出勤率"会永远为 1
    pairs = reg[["stu_num", "course_code", "semester"]].drop_duplicates()
    weeks = pd.DataFrame({"week": range(N_WEEKS)})
    grid = pairs.merge(weeks, how="cross")

    log = (grid.merge(agg, on=KEYS, how="left")
                .merge(ppt, on=KEYS, how="left")
                .merge(dom, on=KEYS, how="left"))

    log["attendance_status"] = np.where(log["clicks"].fillna(0) > 0,
                                        "attended", "absent")
    log["resource_id"] = log["res_site"].fillna("res_none")
    log["resource_type"] = log["res_type"].fillna("none")
    log["ppt_page_clicks"] = log["ppt_clicks"].fillna(0).astype(int)
    log["video_watch_ratio"] = 0.0            # OULAD 无视频数据
    log["late_minutes"] = 0
    log["late_flag"] = np.zeros(len(log), dtype="int8")
    log["week"] = log["week"].astype("Int64")
    # 课次 ID 与 mock 保持同一命名法：cs_{课}_{学期}_wk{周}
    log["class_session_id"] = ("cs_" + log["course_code"] + "_"
                               + log["semester"] + "_wk"
                               + log["week"].astype(str))
    log["stu_id"] = "stu_" + log["stu_num"].astype(str)
    log["stu_uuid"] = "oulad_" + log["stu_num"].astype(str)
    # 时间戳：优先用该周最早的真实日期，无行为的周用周起始
    starts = log["semester"].map(presentation_start)
    anchor_dt = starts + pd.to_timedelta(log["week"] * 7, unit="D")
    real_dt = starts + pd.to_timedelta(log["min_date"], unit="D")
    log["timestamp"] = real_dt.where(log["min_date"].notna(), anchor_dt)

    return log[["stu_uuid", "course_code", "semester", "resource_id",
                "resource_type", "timestamp", "attendance_status",
                "late_minutes", "ppt_page_clicks", "video_watch_ratio",
                "class_session_id", "stu_id", "week", "late_flag"]]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 最终结果（标签 / 生存时间）
# ═══════════════════════════════════════════════════════════════════════════════
def build_final_result(info: pd.DataFrame, sa: pd.DataFrame,
                       cfg: pd.DataFrame, reg: pd.DataFrame) -> pd.DataFrame:
    df = info.copy()
    df["stu_num"] = df["id_student"].astype("int64")
    df["course_code"] = df["code_module"].astype(str)
    df["semester"] = df["code_presentation"].astype(str)
    df["status"] = df["final_result"].map(STATUS_MAP).fillna("未知")

    # 加权总分（仅统计已提交的考核）
    w = cfg[["assessment_id", "weight"]].copy()
    s = sa.merge(w, on="assessment_id", how="left")
    s["weight"] = s["weight"].fillna(0.0)
    g = s.groupby(["stu_id", "assessment_id"], as_index=False).first()
    g["weighted"] = g["score"] * g["weight"]
    tot_w = g.groupby("stu_id")["weight"].sum().rename("w_sum")
    tot_s = g.groupby("stu_id")["weighted"].sum().rename("w_score")
    score_map = ((tot_s / tot_w.replace(0, np.nan)).fillna(0.0)).to_dict()

    df["stu_id"] = "stu_" + df["stu_num"].astype(str)
    df["final_score"] = df["stu_id"].map(score_map).fillna(0.0).clip(0, 100)
    df["gpa"] = (df["final_score"] / 25).round(2)      # 0..100 → 0..4.0

    # 风险事件周：退学 → 退课周；不合格 → 首次不及格的考核周；其余 -1
    reg2 = reg.copy()
    reg2["drop_week"] = ((reg2["drop_date"]
                          - reg2["semester"].map(presentation_start)
                          ).dt.days // 7).clip(lower=0, upper=N_WEEKS - 1)
    drop_map = dict(zip(reg2["stu_id"] + "|" + reg2["course_code"]
                        + "|" + reg2["semester"], reg2["drop_week"]))

    cfg_w = attach_deadline_week(cfg)
    week_map = dict(zip(cfg_w["assessment_id"], cfg_w["week"]))
    failed = sa[(sa["score"] < PASS_SCORE) & ~sa["is_absent"]].copy()
    failed["deadline_dt"] = failed["assessment_id"].map(
        dict(zip(cfg_w["assessment_id"], cfg_w["deadline_dt"])))
    failed["week"] = failed["assessment_id"].map(week_map)
    failed = failed[failed["deadline_dt"].notna()]
    ff_map = dict(failed.sort_values("deadline_dt")
                  .drop_duplicates("stu_id")[["stu_id", "week"]]
                  .itertuples(index=False, name=None))

    def risk_week(r):
        if r.status == "退学":
            wk = drop_map.get(f"{r.stu_id}|{r.course_code}|{r.semester}")
            return int(wk) if pd.notna(wk) else N_WEEKS - 1
        if r.status == "不合格":
            return int(ff_map.get(r.stu_id, N_WEEKS - 1))
        return -1

    df["risk_event_time"] = df.apply(risk_week, axis=1).astype(int)
    df["is_positive"] = df["status"].isin(POSITIVE_STATUSES).astype(int)
    return df[["stu_num", "course_code", "semester", "final_score", "status",
               "gpa", "risk_event_time", "stu_id", "is_positive"]]


def build_id_mapping(info: pd.DataFrame) -> pd.DataFrame:
    df = info.drop_duplicates("id_student").copy()
    df["stu_num_raw"] = df["id_student"].astype("int64")
    df["stu_num_offset"] = df["stu_num_raw"]          # OULAD 只有一套 ID
    df["stu_uuid"] = "oulad_" + df["stu_num_raw"].astype(str)
    df["stu_id"] = "stu_" + df["stu_num_raw"].astype(str)
    return df[["stu_num_raw", "stu_num_offset", "stu_uuid", "stu_id"]]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. main
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="OULAD → interim schema 适配器")
    ap.add_argument("--raw", default="algorithm/data/oulad_raw")
    ap.add_argument("--out", default="algorithm/data/oulad/interim")
    ap.add_argument("--limit-students", type=int, default=0,
                    help="只保留前 N 个学生（0=全部）。节点规模太大时用于快速验证")
    ap.add_argument("--no-check", action="store_true", help="跳过列名比对")
    args = ap.parse_args()

    raw, out = Path(args.raw), Path(args.out)
    print(f"▶ 读取 OULAD: {raw.resolve()}")
    t = load_raw(raw)
    for k, v in t.items():
        print(f"  {k:22s} rows={len(v):>10,}")

    if args.limit_students > 0:
        rng = np.random.RandomState(42)
        all_ids = np.sort(t["studentInfo"]["id_student"].unique())
        keep = set(rng.choice(all_ids,
                              size=min(args.limit_students, len(all_ids)),
                              replace=False).tolist())
        print(f"▶ 采样学生: {len(keep):,} / {len(all_ids):,}")
        for name in ["studentInfo", "studentRegistration", "studentAssessment",
                     "studentVle"]:
            t[name] = t[name][t[name]["id_student"].isin(keep)]

    print("\n▶ 构建 interim 表 ...")
    info = build_student_info(t["studentInfo"])
    reg = build_registration(t["studentRegistration"], t["studentInfo"])
    courses = build_courses(t["courses"], t["assessments"])
    calendar = build_calendar(reg)
    asm_cfg = build_assessment_config(t["assessments"], t["courses"])
    sa = build_student_assessment(t["studentAssessment"], t["assessments"])
    quiz = build_quiz_item(sa, asm_cfg)
    log = build_platform_log(t["studentVle"], t["vle"], reg)
    final = build_final_result(t["studentInfo"], sa, asm_cfg, reg)
    id_map = build_id_mapping(t["studentInfo"])

    tables = {"student_info": info, "registration": reg, "courses": courses,
              "calendar": calendar, "assessment_config": asm_cfg,
              "student_assessment": sa, "quiz_item": quiz,
              "platform_log": log, "final_result": final,
              "id_mapping": id_map, "course_prereq": build_course_prereq()}

    print()
    save(tables, out)

    # ── 摘要 ──
    print("\n── 数据摘要 ──")
    print(f"  学生×课×学期: {final.shape[0]:,}")
    print(f"  正例（FAIL+WITHDRAWN）占比: {final['is_positive'].mean():.2%}")
    print(f"  出勤/活跃率均值: "
          f"{(log['attendance_status'] == 'attended').mean():.2%}")
    print(f"  week 范围: {int(log['week'].min())}..{int(log['week'].max())}")
    print(f"  状态分布: {final['status'].value_counts().to_dict()}")

    ok = True
    if not args.no_check:
        ok = validate_against_mock(out) and ok

    print(f"\n{'✅' if ok else '⚠️'} 适配完成 → {out.resolve()}")
    if ok:
        print("\n下一步（不覆盖 mock 数据）：")
        print(f'  $env:WP_INTERIM="{out}"; $env:WP_PROCESSED="algorithm/data/oulad/processed"')
        print("  python algorithm/src/data_pipeline/03_weekly_features.py")
        print("  python algorithm/src/data_pipeline/04_knowledge_map.py")
        print("  python algorithm/src/data_pipeline/05_build_graph.py")


if __name__ == "__main__":
    main()
