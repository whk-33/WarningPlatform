"""数据源体检：一条命令确认后端到底在跑哪份数据、风险分与主数据是否同源。

背景：`algorithm/data/oulad/` 被 .gitignore 忽略，新机器 clone 后必然只剩 200 人合成演示集。
只拷 risk_scores.parquet 而漏拷 interim_demo/，就会出现「主数据 200 人、风险数据 28785 人」
的错配，表现为学生详情弹窗的风险曲线全部 404。

用法（在 backend 目录下）：

    python scripts/verify_data.py

退出码：0 = 数据自洽；1 = 有错配或缺文件（会打印该拷什么、拷到哪）。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402

OK = "[OK]"
BAD = "[!!]"
INFO = "[--]"


def _mark(flag: bool) -> str:
    return OK if flag else BAD


def main() -> int:
    problems: list[str] = []

    print("=" * 72)
    print("1. 主数据（interim）候选目录")
    print("=" * 72)
    from app.data.loader import find_interim

    hit = find_interim()
    for i, root in enumerate(settings.interim_candidates, 1):
        exists = root.exists()
        flag = OK if exists else INFO
        print(f"  {flag} [{i}] {root}" + ("" if exists else "  （不存在）"))

    print()
    if hit is None:
        print(f"  {BAD} 一个都没命中 → 后端会使用内置 200 人合成演示数据")
        problems.append("没有任何 interim 目录命中，主数据是内置合成数据")
    else:
        print(f"  实际使用：{hit}")

    # ---------------------------------------------------------------- 主数据
    from app.data.repo import get_repo

    repo = get_repo()
    students = repo.students
    n_students = int(len(students))
    ids = set(map(str, students["stu_id"])) if "stu_id" in students.columns else set()

    print()
    print("=" * 72)
    print("2. 主数据实际内容")
    print("=" * 72)
    print(f"  source      : {repo.source}")
    print(f"  学生数      : {n_students}")
    print(f"  学期        : {repo.semesters}")

    is_synthetic = 200 <= n_students <= 200 and "student_name" in students.columns and \
        str(students["student_name"].iloc[0]).startswith("学生")
    if is_synthetic:
        print(f"  {BAD} 姓名形如「{students['student_name'].iloc[0]}」→ 这是**合成演示集**，不是算法真实产物")
        problems.append("主数据是合成演示集（200 人，姓名「学生xxxx」）")
    else:
        sample = students["student_name"].iloc[0] if "student_name" in students.columns else "-"
        print(f"  {OK} 姓名样例「{sample}」→ 看起来是真实产物")

    # ---------------------------------------------------------------- 风险分
    print()
    print("=" * 72)
    print("3. 风险分数据（risk_scores.parquet / mock）")
    print("=" * 72)
    for i, root in enumerate(settings.processed_candidates, 1):
        p = Path(root) / "risk_scores.parquet"
        print(f"  {OK if p.exists() else INFO} [{i}] {p}" + ("" if p.exists() else "  （不存在）"))

    from app.data.risk_repo import get_risk_repo

    risk = get_risk_repo()
    risk_ids = set(map(str, risk._df["stu_id"].unique())) if not risk._df.empty else set()
    print()
    print(f"  source      : {risk.source}")
    print(f"  行数        : {len(risk._df)}")
    print(f"  学生数      : {len(risk_ids)}")
    print(f"  学期        : {risk.semesters}")

    if risk.source == "mock":
        print(f"  {INFO} 走的是 docs/mock_api 样本（只有 20 个学生），不是真实全量")
    if risk.source == "none":
        print(f"  {BAD} 风险数据完全没有加载")
        problems.append("风险分数据未加载")

    # ---------------------------------------------------------------- 同源
    print()
    print("=" * 72)
    print("4. 两套 ID 是否同源")
    print("=" * 72)
    inter = ids & risk_ids
    print(f"  主数据学生数 : {len(ids)}")
    print(f"  风险学生数   : {len(risk_ids)}")
    print(f"  交集         : {len(inter)}")
    if ids and risk_ids and not inter:
        sample_old = sorted(ids)[:3]
        sample_new = sorted(risk_ids)[:3]
        print(f"  {BAD} 交集为 0 → 主数据 {sample_old} vs 风险 {sample_new}")
        problems.append("主数据与风险分零交集（数据源错配）")
    elif inter:
        print(f"  {OK} 有交集，样例：{sorted(inter)[:3]}")
        if len(inter) == len(ids):
            print(f"  {OK} 主数据学生全部能在风险分里找到（完全同源）")

    # ---------------------------------------------------------------- 图
    print()
    print("=" * 72)
    print("5. 图产物")
    print("=" * 72)
    for i, p in enumerate(settings.graph_candidates, 1):
        print(f"  {OK if p.exists() else INFO} [{i}] {p}" + ("" if p.exists() else "  （不存在）"))
    graph_hit = next((p for p in settings.graph_candidates if p.exists()), None)
    if graph_hit is None:
        print(f"  {BAD} 没有可用的图数据")
        problems.append("图产物缺失")
    else:
        print(f"  实际使用：{graph_hit}")
        if "processed_full_demo" not in str(graph_hit):
            print(f"  {INFO} 注意：这个图不是真实产物目录里的，学生节点数可能与主数据不匹配")

    # ---------------------------------------------------------------- 结论
    print()
    print("=" * 72)
    if problems:
        print(f"{BAD} 体检不通过（{len(problems)} 个问题）")
        for i, p in enumerate(problems, 1):
            print(f"   {i}. {p}")
        print()
        print("该怎么做：")
        print("  1) 把算法产物拷到 algorithm/data/oulad/ 下（走网盘/U 盘，git 拉不到）：")
        print("       interim_demo/            约 6.7 MB，12 张表")
        print("       processed_full_demo/     risk_scores.parquet 约 13.7 MB")
        print("  2) backend/.env 里加：")
        print("       WP_INTERIM=D:/WarningPlatform/algorithm/data/oulad/interim_demo")
        print("       WP_PROCESSED=D:/WarningPlatform/algorithm/data/oulad/processed_full_demo")
        print("  3) 重新跑本脚本，期望：学生数 28785、交集 = 学生数")
        return 1

    print(f"{OK} 体检通过：主数据 {n_students} 人，与风险分同源，可以演示")
    print()
    print("下一步（起服务后）：")
    print("  curl -s localhost:8000/api/health   # students 应为 28785")
    print("  curl -s localhost:8000/api/risk/student/<某个stu_id> -H 'Authorization: Bearer <token>'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
