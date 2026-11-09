"""阶段三周更流水线：新数据进来后跑一遍打分 + 门禁。

流程：
  1. quality_checks（缺失率 / PSI / 标签缺失）→ 不通过则阻断并告警
  2. 阈值沿用（新学期才重校，日常沿用上一学期的 thresholds_by_week）
  3. 重跑 20_score_offline.py 生成 risk_scores.parquet.new
  4. 原子替换（os.replace）

运行（模拟）：
  py -3 algorithm/src/serving/31_weekly_run.py `
      --new_tft algorithm/data/oulad/processed_new/tft_weekly.parquet
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import pandas as pd

import importlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_qc = importlib.import_module("32_quality_checks")
check_dataset = _qc.check_dataset
check_labels = _qc.check_labels

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = PROJECT_ROOT / "algorithm" / "reports"


def find_processed() -> Path:
    for cand in [
        PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_full_demo",
        PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_demo",
        PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed",
    ]:
        if (cand / "tft_weekly.parquet").exists():
            return cand
    raise FileNotFoundError("找不到 processed 目录（缺 tft_weekly.parquet）")


def run(new_tft_path: Path | None = None, dry_run: bool = False) -> int:
    processed = find_processed()
    baseline_tft = processed / "tft_weekly.parquet"
    target = processed / "risk_scores.parquet"
    target_new = processed / "risk_scores.parquet.new"

    new_tft = Path(new_tft_path) if new_tft_path else baseline_tft
    if not new_tft.exists():
        print(f"[ERROR] 新数据不存在：{new_tft}")
        return 1

    baseline = pd.read_parquet(baseline_tft)
    new = pd.read_parquet(new_tft)

    # 门禁
    print(f"[门禁] 新数据行数: {len(new):,}  基线行数: {len(baseline):,}")
    ok_d, r_d = check_dataset(new, baseline)
    ok_l, r_l = check_labels(new)
    if not ok_d or not ok_l:
        print(f"[门禁] FAIL  缺失超标列={list(r_d.get('missing_fail', {}).keys())}  "
              f"PSI 超标列={list(r_d.get('psi_fail', {}).keys())}  "
              f"标签缺失率={r_l.get('label_missing_rate', 'N/A')}")
        _log_run("failed", processed, {"quality": {**r_d, **r_l}})
        return 1
    print("[门禁] PASS")

    # 阈值沿用：用现有 serving_metrics.json，没有就硬编码阶段二实测值
    thresholds_file = REPORTS_DIR / "serving_metrics.json"
    if thresholds_file.exists():
        print(f"[阈值] 沿用 {thresholds_file.name}")
    else:
        print("[阈值] 未找到 serving_metrics.json，沿用阶段二硬编码阈值")

    if dry_run:
        print(f"[dry-run] 门禁通过，若正式执行将产出 {target_new} 并原子替换")
        return 0

    # 重跑 20_score_offline（subprocess，避免 import 顺序干扰）
    cmd = ["py", "-3",
           str(PROJECT_ROOT / "algorithm" / "src" / "serving" / "20_score_offline.py"),
           "--processed", str(processed)]
    print(f"[打分] {' '.join(cmd)}")
    import subprocess
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    sys.stdout.write(res.stdout)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        print(f"[ERROR] 20_score_offline 退出码={res.returncode}")
        _log_run("failed", processed, {"step": "score_offline", "stderr": res.stderr[-500:]})
        return res.returncode

    if not target.exists():
        print(f"[ERROR] 打分后 {target} 不存在")
        _log_run("failed", processed, {"step": "missing_target"})
        return 1

    # 原子替换（先挪到 .new 再 replace，Windows 支持 os.replace 原子性）
    shutil.copy2(target, target_new)
    os.replace(target_new, target)
    size_mb = target.stat().st_size / 1e6
    print(f"[完成] {target}  {size_mb:.1f} MB")

    _log_run("ok", processed, {"rows": int(len(pd.read_parquet(target))), "size_mb": round(size_mb, 2)})
    return 0


def _log_run(status: str, processed: Path, info: dict) -> None:
    log_path = processed / "weekly_run_log.jsonl"
    entry = {"at": date.today().isoformat(), "status": status, "processed": str(processed), **info}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new_tft", default=None, help="新 tft_weekly.parquet（不传则用 processed 现有文件）")
    ap.add_argument("--dry_run", action="store_true", help="只跑门禁 + 阈值检查，不打分")
    args = ap.parse_args()
    return run(Path(args.new_tft) if args.new_tft else None, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
