"""
11_enrich_demo_fields.py —— 演示增强层：中文业务字段

目的：公开数据集（OULAD）只有匿名 ID 和 AAA..GGG 模块代码，无法直接做
      产品演示。本脚本在**不改任何既有列名/类型/取值**的前提下，追加一
      层可读的中文业务字段，让前端表格、风险画像、知识点诊断有真实语
      义可展示。

输入：interim（10_adapt_oulad.py 产物，或 02_clean.py 的 mock 产物）
输出：interim_demo（11 张表原样复制 + 追加展示列）
      另产出 kp_name_map.parquet，供 04_knowledge_map.py 给知识点起中文名

追加的展示列（仅用于展示，不参与模型训练）：
  student_info : student_name  student_no  major_name  class_name  grade
  courses      : name（若原来是 "OULAD模块XXX" 则替换为中文课程名）
  final_result : risk_level（已有则不动） risk_level_name

重要约定：
  1. stu_id 保持匿名且不变 —— 姓名/班级只是标签层，不改变任何主键
  2. 所有合成字段由 (seed, stu_id) 的 md5 决定，**可复现**
  3. 用法与风险口径（哪些字段是合成、哪些是代理）见 docs/数据字段约定.md

用法：
  python algorithm/src/data_pipeline/11_enrich_demo_fields.py \
      --interim algorithm/data/oulad/interim \
      --out algorithm/data/oulad/interim_demo \
      [--seed 42]
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

import pandas as pd

# ── 中文课程名 + 每课 10 个知识点（与 04 脚本生成的 kp_{课}_{01..10} 对齐）──
CURRICULUM: dict[str, tuple[str, list[str]]] = {
    "AAA": ("计算机导论", ["计算机系统组成", "数据表示与编码", "操作系统概述",
                          "计算机网络基础", "数据库基础", "算法与程序",
                          "软件工程概述", "信息安全基础", "人工智能概述",
                          "计算思维"]),
    "BBB": ("程序设计基础", ["变量与数据类型", "运算符与表达式", "分支结构",
                            "循环结构", "函数与参数", "列表与字典",
                            "字符串处理", "文件读写", "异常处理",
                            "综合程序设计"]),
    "CCC": ("数据结构与算法", ["线性表", "栈与队列", "树与二叉树",
                              "二叉树遍历", "图与最短路径", "查找算法",
                              "排序算法", "哈希表", "递归与分治",
                              "动态规划"]),
    "DDD": ("数据库原理", ["关系模型", "SQL 查询", "SQL 更新与视图",
                          "函数依赖与范式", "ER 建模", "索引与存储",
                          "事务与并发控制", "恢复技术", "查询优化",
                          "数据库设计实战"]),
    "EEE": ("计算机网络", ["网络体系结构", "物理层", "数据链路层",
                          "网络层与路由", "传输层 TCP/UDP", "应用层协议",
                          "IP 地址与子网", "网络互联设备", "网络安全",
                          "网络编程"]),
    "FFF": ("操作系统", ["进程与线程", "CPU 调度", "进程同步与互斥",
                        "死锁", "内存管理", "虚拟内存", "文件系统",
                        "磁盘调度", "设备管理", "系统调优"]),
    "GGG": ("软件工程", ["需求分析", "用例建模", "软件设计原则",
                        "UML 建模", "设计模式", "编码规范",
                        "单元测试", "集成与系统测试", "项目进度管理",
                        "软件维护"]),
}

MAJORS = [("计算机科学与技术", "计科"),
          ("软件工程", "软工"),
          ("数据科学与大数据技术", "数科"),
          ("人工智能", "智能"),
          ("网络工程", "网工")]

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹")
GIVEN_M = ["浩然", "子轩", "宇航", "泽宇", "明轩", "俊杰", "志远", "博文",
           "昊天", "嘉豪", "文杰", "鹏程", "思远", "天佑", "昊然", "逸飞"]
GIVEN_F = ["雨欣", "佳怡", "梓涵", "欣怡", "思琪", "诗涵", "雅静", "雨桐",
           "梦琪", "子怡", "静怡", "若曦", "嘉怡", "语嫣", "紫萱", "依诺"]

RISK_NAME = {0: "低", 1: "中", 2: "高"}


def h(key: str, seed: int) -> int:
    """稳定哈希：同一 (seed, key) 每次运行结果一致（不能用内置 hash，它有随机盐）"""
    return int(hashlib.md5(f"{seed}|{key}".encode()).hexdigest(), 16)


def build_student_display(info: pd.DataFrame, reg: pd.DataFrame,
                          seed: int) -> pd.DataFrame:
    """按 stu_id 生成姓名/学号/专业/班级/年级（与学生一一对应，跨学期不变）"""
    base = (info[["stu_id", "stu_num", "gender"]]
            .drop_duplicates("stu_id")
            .sort_values("stu_id")
            .reset_index(drop=True))

    if "enroll_date" in reg.columns:
        year_map = (pd.to_datetime(reg["enroll_date"], errors="coerce")
                    .dt.year.groupby(reg["stu_id"]).min()
                    .to_dict())
    else:
        year_map = {}

    names, nos, majors, classes, grades = [], [], [], [], []
    for seq, r in enumerate(base.itertuples(index=False), start=1):
        sid, gender = r.stu_id, r.gender
        k = h(sid, seed)
        # 年级：注册年份；缺失则按哈希取 2021..2024
        year = year_map.get(sid)
        year = int(year) if pd.notna(year) and year else 2021 + (k // 7) % 4
        yy = str(year)[-2:]

        name = SURNAMES[k % len(SURNAMES)]
        pool = GIVEN_F if str(gender) == "女" else GIVEN_M
        name += pool[(k // 31) % len(pool)]

        mname, mshort = MAJORS[(k // 101) % len(MAJORS)]
        section = (k // 1009) % 4 + 1

        names.append(name)
        nos.append(f"{yy}{seq:04d}")            # 学号：入学年级 + 顺序号（合成，定长）
        majors.append(mname)
        classes.append(f"{mshort}{yy}{section:02d}班")
        grades.append(f"{year}级")

    return pd.DataFrame({"stu_id": base["stu_id"], "student_name": names,
                         "student_no": nos, "major_name": majors,
                         "class_name": classes, "grade": grades})


def build_kp_name_map(courses: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for code in courses["code"].astype(str):
        if code not in CURRICULUM:
            continue
        for i, kp_name in enumerate(CURRICULUM[code][1], start=1):
            rows.append({"kp_id": f"kp_{code}_{i:02d}",
                         "course_code": code,
                         "name_cn": kp_name})
    rows.append({"kp_id": "kp_unknown", "course_code": "unknown",
                 "name_cn": "未标注知识点"})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description="演示增强层：中文业务字段")
    ap.add_argument("--interim", default="algorithm/data/oulad/interim")
    ap.add_argument("--out", default="algorithm/data/oulad/interim_demo")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    src, out = Path(args.interim), Path(args.out)
    if not src.exists():
        sys.exit(f"❌ 找不到 {src}，请先跑 10_adapt_oulad.py 或 02_clean.py")
    if out.resolve() == src.resolve():
        sys.exit("❌ --out 不能等于 --interim，避免覆盖原始 interim")

    out.mkdir(parents=True, exist_ok=True)
    print(f"▶ {src} → {out}")

    # 1. 原样复制全部表，再替换需要增强的三张
    for p in src.glob("*.parquet"):
        shutil.copy2(p, out / p.name)

    info = pd.read_parquet(src / "student_info.parquet")
    reg = pd.read_parquet(src / "registration.parquet")
    courses = pd.read_parquet(src / "courses.parquet")
    final = pd.read_parquet(src / "final_result.parquet")

    # 2. 学生展示字段（按 stu_id 左连接，不改行数、不改主键）
    disp = build_student_display(info, reg, args.seed)
    for c in disp.columns[1:]:
        info[c] = info["stu_id"].map(disp.set_index("stu_id")[c])

    # 3. 课程中文名：仅当原 name 是适配器自动生成的占位名（"OULAD模块XXX"）时才替换
    #    （不能用"是否含中文"判断——占位名里也带中文"模块"二字）
    auto = courses["name"].astype(str).str.startswith("OULAD模块")
    courses.loc[auto, "name"] = courses.loc[auto, "code"].map(
        {c: v[0] for c, v in CURRICULUM.items()}).fillna(courses.loc[auto, "name"])

    # 4. 风险等级：已有 risk_level 则保留，缺失则按 status 现算
    if "risk_level" not in final.columns:
        status_map = {st: lv for lv, sts in
                      {0: ["优秀", "良好", "合格"], 1: ["不合格", "缺考"],
                       2: ["退学", "休学", "学业警示"]}.items() for st in sts}
        final["risk_level"] = final["status"].map(status_map).fillna(0).astype("int8")
    final["risk_level_name"] = final["risk_level"].map(RISK_NAME)

    info.to_parquet(out / "student_info.parquet", index=False)
    courses.to_parquet(out / "courses.parquet", index=False)
    final.to_parquet(out / "final_result.parquet", index=False)

    # 5. 知识点中文名映射（04 脚本会自动读取）
    if auto.any():
        kp_map = build_kp_name_map(courses)
        kp_map.to_parquet(out / "kp_name_map.parquet", index=False)
        print(f"  kp_name_map.parquet: {len(kp_map)} 个知识点中文名")
    else:
        print("  课程名已是中文，跳过知识点中文名映射（mock 数据）")

    # ── 摘要 ──
    print("\n── 增强结果 ──")
    print(f"  学生数: {info['stu_id'].nunique():,} · 班级数: "
          f"{info['class_name'].nunique():,} · 专业数: {info['major_name'].nunique()}")
    print(f"  专业分布: {info.drop_duplicates('stu_id')['major_name'].value_counts().to_dict()}")
    print(f"  课程名: {courses.set_index('code')['name'].to_dict()}")
    dist = final["risk_level"].value_counts(normalize=True).sort_index()
    cnt = final["risk_level"].value_counts().sort_index()
    for lv in (0, 1, 2):
        print(f"  风险{RISK_NAME[lv]}: {cnt.get(lv, 0):>6,} 行 "
              f"({dist.get(lv, 0):.1%})")
    print(f"  预警率（中+高）: {(final['risk_level'] > 0).mean():.1%}")

    print(f"\n✅ 增强完成 → {out.resolve()}")
    print('下一步: $env:WP_INTERIM="%s"; $env:WP_PROCESSED="algorithm/data/oulad/processed_demo"'
          % out)


if __name__ == "__main__":
    main()
