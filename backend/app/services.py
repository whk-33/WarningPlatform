"""业务拼装：预警原因、分析报告、学业建议。

模型没训练之前，这里的"归因"全部是规则产出，响应里带 source=rule 标记，
前端不要把它当模型解释来宣传。GNNExplainer 上线后替换 build_factors 即可。
"""

from __future__ import annotations

from app.config import settings


def build_factor_list(student: dict, warn: dict) -> list[dict]:
    """关键风险因子（规则版）。

    contribution 是"距离阈值的归一化缺口"，只保证排序合理，不是模型归因权重。
    """

    metrics = student.get("metrics") or {}
    att = metrics.get("attendance_rate")
    quiz = metrics.get("quiz_accuracy")
    course_count = max(student.get("course_count") or 1, 1)

    factors: list[dict] = []

    if att is not None:
        gap = max(0.0, settings.attendance_threshold - float(att))
        factors.append({
            "factor": "attendance_rate",
            "label": "出勤率",
            "value": round(float(att), 4),
            "threshold": settings.attendance_threshold,
            "contribution": round(gap / settings.attendance_threshold, 3),
            "direction": "lower_is_worse",
        })

    if quiz is not None:
        gap = max(0.0, settings.quiz_threshold - float(quiz))
        factors.append({
            "factor": "quiz_accuracy",
            "label": "小测正确率",
            "value": round(float(quiz), 4),
            "threshold": settings.quiz_threshold,
            "contribution": round(gap / settings.quiz_threshold, 3),
            "direction": "lower_is_worse",
        })

    factors.append({
        "factor": "fail_count",
        "label": "不及格课程数",
        "value": int(student.get("fail_count") or 0),
        "threshold": 0,
        "contribution": round(min(1.0, (student.get("fail_count") or 0) / course_count), 3),
        "direction": "higher_is_worse",
    })

    if warn.get("final_score") is not None:
        gap = max(0.0, 60.0 - float(warn["final_score"]))
        factors.append({
            "factor": "final_score",
            "label": f"{warn.get('course_name', '该课程')}期末成绩",
            "value": float(warn["final_score"]),
            "threshold": 60.0,
            "contribution": round(gap / 60.0, 3),
            "direction": "lower_is_worse",
        })

    factors.sort(key=lambda x: -x["contribution"])
    # 贡献度为 0 的因子只是"没触发告警"，不占前五的位置
    positive = [f for f in factors if f["contribution"] > 0]
    return (positive or factors)[:5]


def build_suggestions(student: dict, courses: list[dict]) -> list[str]:
    """学业建议模板。风险等级 + 薄弱课程 + 行为指标三个维度拼。"""

    level = int(student.get("risk_level") or 0)
    metrics = student.get("metrics") or {}
    att = metrics.get("attendance_rate")
    quiz = metrics.get("quiz_accuracy")
    weak = [c for c in courses if c.get("risk_level", 0) >= 2]
    watch = [c for c in courses if c.get("risk_level", 0) == 1]

    items: list[str] = []

    if weak:
        names = "、".join(c["course_name"] for c in weak[:3])
        items.append(f"优先处理《{names}》：这几门课已进入高危区间，先约任课老师确认补考/重修方案。")
    elif watch:
        names = "、".join(c["course_name"] for c in watch[:3])
        items.append(f"关注《{names}》：当前处于警惕区间，期末前把作业和小测分数补回来即可回落。")

    if att is not None and float(att) < settings.attendance_threshold:
        items.append(f"出勤率 {float(att):.0%}，低于 {settings.attendance_threshold:.0%} 警戒线，先把到课率拉起来。")

    if quiz is not None and float(quiz) < settings.quiz_threshold:
        items.append(f"小测正确率 {float(quiz):.0%}，建议按知识点逐个补漏，优先做错题复盘。")

    if level == 0:
        items.append("当前学业状态正常，保持出勤和作业提交节奏即可。")
    elif level == 1:
        items.append("每周固定时间复盘本周课程，避免拖到期末集中补救。")
    else:
        items.append("建议本周内与辅导员面谈一次，制定可执行的补救计划。")

    return items


def build_report(repo, warn: dict) -> dict:
    """预警详情 / 分析报告：把学生、课程、因子、趋势、子图拼成一份结构化数据。"""

    semester = warn["semester"]
    stu_id = repo.resolve_stu_id(warn["student_id"])
    student = repo.student_row(stu_id, semester)
    courses = repo.course_list(stu_id, semester)
    factors = build_factor_list(student, warn)
    suggestions = build_suggestions(student, courses)
    trend, trend_source = repo.weekly_trend(stu_id, semester)
    subgraph = repo.student_subgraph(stu_id, semester)

    top = factors[0] if factors else None
    if top and top["contribution"] > 0:
        detail = f"{top['label']}为 {top['value']}"
        if top.get("threshold"):
            detail += f"（阈值 {top['threshold']}）"
        summary = (
            f"{student['name']} 在《{warn['course_name']}》上判定为{warn['risk_level_text']}，"
            f"主要因为{detail}；"
            f"当学期 {student['course_count']} 门课中 {student['high_course_count']} 门高危、"
            f"{student['fail_count']} 门不及格。"
        )
    else:
        summary = (
            f"{student['name']} 在《{warn['course_name']}》上判定为{warn['risk_level_text']}，"
            f"当学期平均分 {student['avg_score']}。"
        )

    return {
        "warn_id": warn["warn_id"],
        "source": "rule",  # 规则归因，模型上线后改 model
        "student": student,
        "course": {
            "course_code": warn["course_code"],
            "course_name": warn["course_name"],
            "final_score": warn["final_score"],
            "status": warn["status"],
            "semester_week": warn["semester_week"],
            "warn_time": warn["warn_time"],
        },
        "summary": summary,
        "factors": factors,
        "trend": trend,
        "trend_source": trend_source,
        "course_list": courses,
        "suggestions": suggestions,
        "suggest_text": "；".join(suggestions),
        "subgraph": subgraph,
    }
