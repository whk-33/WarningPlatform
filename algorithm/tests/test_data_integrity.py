"""
数据完整性测试 · pytest
所有测试基于 algorithm/data/interim/*.parquet
"""
import pandas as pd
import pytest
from pathlib import Path

INTERIM = Path("algorithm/data/interim")
RAW = Path("algorithm/data/raw")

REQUIRED_TABLES = [
    "student_info", "registration", "platform_log",
    "quiz_item", "student_assessment", "final_result",
    "assessment_config", "courses", "course_prereq",
    "calendar", "id_mapping",
]


@pytest.fixture(scope="module")
def tables():
    """载入所有 interim 表供多测例复用"""
    return {p.stem: pd.read_parquet(p)
            for p in INTERIM.glob("*.parquet")}


def test_tables_exist():
    for name in REQUIRED_TABLES:
        assert (INTERIM / f"{name}.parquet").exists(), f"缺少 {name}.parquet"


def test_id_unified(tables):
    """所有核心表的 stu_id 都是 stu_ 前缀"""
    for tname in ["student_info", "registration", "platform_log",
                  "quiz_item", "student_assessment", "final_result"]:
        df = tables[tname]
        bad = df[~df.stu_id.str.startswith("stu_", na=True)]
        assert bad.empty, f"{tname} 有 {len(bad)} 条未打通的 stu_id"


def test_no_duplicate_signin(tables):
    log = tables["platform_log"]
    # 清洗脚本里 drop_duplicates 已处理；此处做一个轻量断言：
    # 至少没有重复的 (stu_id, class_session_id) 且完全相同 timestamp 的行
    dup = log[log.duplicated(subset=["stu_id", "class_session_id", "timestamp"],
                              keep=False)]
    assert dup.empty, f"有 {len(dup)} 条完全重复的签到记录"


def test_score_range(tables):
    asm = tables["student_assessment"]
    assert asm["score"].dropna().between(0, 100).all(), "有超出 [0,100] 的分数"


def test_no_future_leak_in_static(tables):
    info = tables["student_info"]
    for col in ["final_score", "status", "gpa", "retaken_after",
                "dropout", "final_status"]:
        assert col not in info.columns, f"静态特征不应含 {col}"


def test_ref_integrity(tables):
    """注册表的学生都在学籍表中"""
    info_stus = set(tables["student_info"].stu_id)
    reg_stus  = set(tables["registration"].stu_id)
    orphan = reg_stus - info_stus
    assert not orphan, f"注册表有 {len(orphan)} 个学生不在学籍表中"


def test_attendance_signal_present(tables):
    """正例学生出勤率显著低于负例（mock 注入的信号）"""
    log = tables["platform_log"]
    target = tables["final_result"]
    attend = (log.groupby(["stu_id", "course_code", "semester"])
                  .agg(attended=("attendance_status",
                                 lambda x: (x == "attended").sum()),
                       total=("attendance_status", "count")).reset_index())
    attend["rate"] = attend["attended"] / attend["total"].clip(lower=1)
    merged = attend.merge(target, on=["stu_id", "course_code", "semester"])
    pos = merged[merged["is_positive"] == 1]["rate"].mean()
    neg = merged[merged["is_positive"] == 0]["rate"].mean()
    print(f"\n正例出勤率均值 {pos:.3f}, 负例 {neg:.3f}")
    assert pos < 0.50, f"正例出勤率 {pos:.2f} >= 0.50，mock 信号没注入成功"
    assert neg > 0.78, f"负例出勤率 {neg:.2f} <= 0.78，mock 信号没注入成功"


def test_quiz_kp_coverage(tables):
    """随堂小测知识点覆盖率 ≥ 80%（15% 故意标注缺失）"""
    quiz = tables["quiz_item"]
    coverage = quiz["kp_id"].notna().mean()
    print(f"随堂小测知识点覆盖率 {coverage:.3f}")
    assert coverage >= 0.80, f"覆盖率 {coverage:.2f} < 0.80"


def test_final_result_positive_ratio(tables):
    target = tables["final_result"]
    pos_rate = target["is_positive"].mean()
    print(f"正例比例 {pos_rate:.3f}")
    assert 0.20 <= pos_rate <= 0.35, f"正例比例 {pos_rate:.1%} 不在预期范围"


def test_dropout_cleaned(tables):
    """退学学生 drop_date 之后的行为已剔除"""
    reg = tables["registration"]
    log = tables["platform_log"]
    dropout_reg = reg[reg["drop_date"].notna()][["stu_id", "course_code",
                                                  "semester", "drop_date"]]
    if dropout_reg.empty:
        pytest.skip("没有退学学生")
    dropout_reg["drop_date"] = pd.to_datetime(dropout_reg["drop_date"])
    merged = log.merge(dropout_reg,
                       on=["stu_id", "course_code", "semester"], how="inner")
    if merged.empty:
        return
    later = merged[merged["timestamp"] >= merged["drop_date"]]
    assert later.empty, f"退学之后仍有 {len(later)} 条行为记录未剔除"
