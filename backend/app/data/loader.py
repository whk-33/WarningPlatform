"""数据加载：优先读算法侧 Parquet，读不到则用内置演示数据兜底。

这样做的目的：算法产物还没合并进当前分支时，后端也能直接 uvicorn 跑起来，
前端可以立刻联调；数据到位后不用改一行业务代码。
"""

from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

from app.config import settings

REQUIRED_TABLES = ("student_info", "registration", "final_result", "courses")

COURSES = [
    ("AAA", "高等数学", 4),
    ("BBB", "线性代数", 3),
    ("CCC", "大学英语", 3),
    ("DDD", "Python 编程", 2),
    ("EEE", "数据结构", 4),
]
MAJORS = ["计算机科学与技术", "软件工程", "人工智能", "数据科学与大数据技术", "网络工程"]


def find_interim() -> Path | None:
    """按优先级找一个包含全部必需表的目录。"""

    for root in settings.interim_candidates:
        if not root.exists():
            continue
        if all(_table_path(root, name) for name in REQUIRED_TABLES):
            return root
    return None


def _table_path(root: Path, name: str) -> Path | None:
    for suffix in (".parquet", ".csv"):
        p = root / f"{name}{suffix}"
        if p.exists():
            return p
    return None


def read_table(root: Path, name: str) -> pd.DataFrame:
    p = _table_path(root, name)
    if p is None:
        raise FileNotFoundError(f"{root} 下缺少 {name}.parquet / .csv")
    if p.suffix == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p)


def load_all() -> tuple[str, dict[str, pd.DataFrame]]:
    """返回 (数据来源标识, 表字典)。source ∈ {parquet, demo}。"""

    root = find_interim()
    if root is not None:
        tables = {name: read_table(root, name) for name in REQUIRED_TABLES}
        for opt in ("platform_log", "quiz_item", "calendar", "dim_knowledge_point"):
            if _table_path(root, opt):
                tables[opt] = read_table(root, opt)
        return "parquet", tables
    return "demo", _demo_tables()


def _demo_tables() -> dict[str, pd.DataFrame]:
    """内置演示数据：200 学生 / 5 门课 / 2 学期。

    结构与 interim 对齐（stu_id 用 stu_ 前缀，标签口径与 schema.yaml 一致），
    仅用于让后端在没有算法产物时也能跑通全部接口。
    """

    rng = random.Random(42)
    students, finals, regs = [], [], []

    for i in range(200):
        seq = i + 1
        stu_id = f"stu_{100000 + seq}"
        year = 2021 + (i // 50)
        major = MAJORS[i % len(MAJORS)]
        short = {"计算机科学与技术": "计科", "软件工程": "软工", "人工智能": "智能",
                 "数据科学与大数据技术": "数科", "网络工程": "网工"}[major]
        students.append({
            "stu_id": stu_id,
            "stu_num": 100000 + seq,
            "student_no": f"{year}{seq:04d}",
            "student_name": f"学生{seq:03d}",
            "major": major,
            "major_name": major,
            "class_name": f"{short}{str(year)[-2:]}{i % 4 + 1:02d}班",
            "grade": f"{year}级",
            "gender": "男" if i % 2 else "女",
            "semester": "2024F",
            "entrance_score": rng.randint(480, 680),
        })

        # 每个学生每学期选 2~4 门课
        for semester in ("2024F", "2025S"):
            picked = rng.sample(COURSES, k=rng.randint(2, 4))
            for code, cname, credit in picked:
                regs.append({"stu_id": stu_id, "course_code": code, "semester": semester})
                score = round(min(100.0, max(20.0, rng.gauss(72, 13))), 1)
                withdrawn = rng.random() < 0.12
                if withdrawn:
                    status, score = "退学", round(score * 0.4, 1)
                elif score >= 85:
                    status = "优秀"
                elif score >= 75:
                    status = "良好"
                elif score >= 60:
                    status = "合格"
                else:
                    status = "不合格"

                level = {"优秀": 0, "良好": 0, "合格": 0, "不合格": 1, "退学": 2}[status]
                finals.append({
                    "stu_id": stu_id,
                    "course_code": code,
                    "semester": semester,
                    "final_score": score,
                    "status": status,
                    "gpa": round(score / 100 * 4, 2),
                    "risk_level": level,
                    "risk_event_time": rng.randint(6, 15) if level else -1,
                    "is_positive": 1 if level else 0,
                })

    return {
        "student_info": pd.DataFrame(students),
        "courses": pd.DataFrame(COURSES, columns=["code", "name", "credits"]),
        "registration": pd.DataFrame(regs),
        "final_result": pd.DataFrame(finals),
    }
