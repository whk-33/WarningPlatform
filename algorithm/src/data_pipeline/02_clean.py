"""
Mock 数据清洗 + ID 打通
用法：python algorithm/src/data_pipeline/02_clean.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

RAW = Path("algorithm/data/raw")
INTERIM = Path("algorithm/data/interim")
INTERIM.mkdir(parents=True, exist_ok=True)

with open("algorithm/configs/schema.yaml", encoding="utf-8") as f:
    SCHEMA = yaml.safe_load(f)


def load_raw() -> dict[str, pd.DataFrame]:
    return {p.stem: pd.read_csv(p, encoding="utf-8-sig")
            for p in RAW.glob("*.csv")}


def unify_timestamp(series: pd.Series) -> pd.Series:
    """三种时间格式统一转 datetime"""
    def parse(x):
        if pd.isna(x):
            return pd.NaT
        s = str(x).strip()
        if s.isdigit() and len(s) > 12:
            return pd.to_datetime(int(s), unit="ms")
        return pd.to_datetime(s, errors="coerce")
    return series.map(parse)


def build_id_mapping(crosswalk: pd.DataFrame) -> dict[str, dict[str, str]]:
    """从 id_crosswalk.csv 构建四套 ID → stu_ 前缀的映射
    全部 key 转 str，避免和 astype(str) 后的列类型不匹配"""
    unified = dict(zip(crosswalk["stu_num_raw"].astype(str),  crosswalk["unified"]))
    offset  = dict(zip(crosswalk["stu_num_offset"].astype(str), crosswalk["unified"]))
    uuid_   = dict(zip(crosswalk["stu_uuid"].astype(str),       crosswalk["unified"]))
    return {"num": unified, "offset": offset, "uuid": uuid_}


def clean(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    cw = tables["id_crosswalk"]
    id_map = build_id_mapping(cw)
    cal = tables["calendar"].copy()
    cal["start"] = pd.to_datetime(cal["start_date"])
    cal_map = dict(zip(cal["semester"], cal["start"]))

    # ── 1) 统一学生 ID ──
    out: dict[str, pd.DataFrame] = {}

    info = tables["student_info"].copy()
    info["stu_id"] = info["stu_num"].astype(str).map(id_map["num"])
    out["student_info"] = info

    reg = tables["registration"].copy()
    reg["stu_id"] = reg["stu_num"].astype(str).map(id_map["num"])
    reg["enroll_date"] = pd.to_datetime(reg["enroll_date"], errors="coerce")
    reg["drop_date"]   = pd.to_datetime(reg["drop_date"],   errors="coerce")
    # 退学学生 drop_date 之后的行为要在清洗中剔除，但 drop_date 本身保留做 DeepSurv
    out["registration"] = reg

    asm_stu = tables["student_assessment"].copy()
    asm_stu["stu_id"] = asm_stu["stu_num"].astype(str).map(id_map["num"])
    asm_stu["submit_time"] = unify_timestamp(asm_stu["submit_time"])
    out["student_assessment"] = asm_stu

    log = tables["platform_log"].copy()
    log["stu_id"] = log["stu_uuid"].map(id_map["uuid"])
    log["timestamp"] = unify_timestamp(log["timestamp"])
    # 故意混的迟到异常：>60 截断为 30
    log["late_minutes"] = log["late_minutes"].fillna(0).clip(upper=30).astype(int)
    # 缺勤却有签到时间 → 状态标记 unknown
    mask_contradict = (log["attendance_status"] == "absent") & log["timestamp"].notna()
    log.loc[mask_contradict, "attendance_status"] = "unknown"
    # 去重：同一学生+课次+日期
    log["date"] = log["timestamp"].dt.date
    log = (log.sort_values("timestamp")
              .drop_duplicates(subset=["stu_id", "course_code",
                                       "class_session_id", "date"],
                                keep="first")
              .drop(columns=["date"]))
    # 用校历换算 week
    log["week"] = ((log["timestamp"].dt.normalize()
                    - log["semester"].map(cal_map)).dt.days // 7
                   ).clip(lower=0, upper=16).astype("Int64")
    # 派生 late_flag：attended 且 late_minutes > 0
    log["late_flag"] = ((log["attendance_status"] == "attended")
                        & (log["late_minutes"] > 0)).astype("int8")
    out["platform_log"] = log

    quiz = tables["quiz_item_responses"].copy()
    quiz["stu_id"] = quiz["stu_num_offset"].astype(str).map(id_map["offset"])
    quiz["week"] = quiz["week"].astype(int)
    # 未标注知识点的题保留，但 kp_id 设为 None、kp_label_missing=True
    quiz["kp_id"] = quiz["kp_id"].where(~quiz["kp_label_missing"], None)
    out["quiz_item"] = quiz

    # 退学学生 drop_date 之后的考核/行为剔除
    drop_mask = reg["drop_date"].notna()
    if drop_mask.any():
        drops = reg.loc[drop_mask, ["stu_id", "course_code", "semester", "drop_date"]]
        # log：退学之后的行为剔除
        log_before = out["platform_log"].shape[0]
        out["platform_log"] = (
            out["platform_log"]
                .merge(drops, on=["stu_id", "course_code", "semester"], how="left")
                .query("drop_date.isna() or timestamp < drop_date")
                .drop(columns="drop_date")
                .reset_index(drop=True)
        )
        removed = log_before - out["platform_log"].shape[0]
        if removed:
            print(f"  剔除退学学生 log {removed} 行")

    asm_cfg = tables["assessment_config"].copy()
    asm_cfg["assessment_id"] = asm_cfg["assessment_id"].astype(str)
    out["assessment_config"] = asm_cfg

    final = tables["final_result"].copy()
    final["stu_id"] = final["stu_num"].astype(str).map(id_map["num"])
    final["is_positive"] = final["status"].isin(
        SCHEMA["label"]["positive_statuses"]).astype(int)
    # 三级风险（口径见 schema.yaml 的 label.risk_level）：0=低 1=中 2=高
    _risk_map = {st: int(lv) for lv, sts in SCHEMA["label"]["risk_level"].items()
                 for st in sts}
    final["risk_level"] = final["status"].map(_risk_map).fillna(0).astype("int8")
    final["risk_level_name"] = final["risk_level"].map({0: "低", 1: "中", 2: "高"})
    out["final_result"] = final

    out["courses"] = tables["courses"]
    out["course_prereq"] = tables["course_prereq"]
    out["calendar"] = cal
    out["id_mapping"] = cw.copy().rename(columns={"unified": "stu_id"})

    return out


def save(tables: dict[str, pd.DataFrame]):
    for name, df in tables.items():
        path = INTERIM / f"{name}.parquet"
        df.to_parquet(path, index=False)
        print(f"  {path.name:32s} rows={len(df):>7,}")


if __name__ == "__main__":
    raw = load_raw()
    print(f"Loaded {len(raw)} raw tables from {RAW}")
    cleaned = clean(raw)
    save(cleaned)
    print(f"\n清洗完毕 → {INTERIM.resolve()}")
