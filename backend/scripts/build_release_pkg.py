"""打包对外交付件：数据包 / mock 包，**可复现**地打。

背景：甲方与前端按 SHA256 校验交付包，但普通 `zip` 每次打包都会把「当前时间」
写进 zip 头，同样的内容打两次哈希不同——于是每一轮都在争论「包对不对」。
这个脚本把时间戳、文件顺序、压缩参数全部固定，**同样的输入必然得到同样的包哈希**，
并把逐文件哈希写进包内的 `MANIFEST.sha256`。

用法（仓库根目录）：

    python backend/scripts/build_release_pkg.py            # 两个包都打
    python backend/scripts/build_release_pkg.py data       # 只打数据包
    python backend/scripts/build_release_pkg.py mock       # 只打 mock 包

输出：
    <release_dir>/WarningPlatform_data_11col_v3.zip
    <release_dir>/WarningPlatform_mock_api_v3.zip

约定：
- 数据包内容 = **仓库 HEAD 里的文件**（不是工作区），保证「git pull」与「解包」逐位一致；
- 每个包内附带 `MANIFEST.sha256`，形如 `sha256sum` 输出，可直接
  `sha256sum -c MANIFEST.sha256` 校验；
- 包哈希与文件哈希都会打印，报给验收方时两个都给。
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = "algorithm/data/processed/processed_full_demo"
REPORT_DIR = "algorithm/reports"
MOCK_DIR = "docs/mock_api"

DATA_FILES = [
    f"{DATA_DIR}/risk_scores.parquet",
    f"{DATA_DIR}/tft_weekly.parquet",
    f"{DATA_DIR}/deepsurv_pred.parquet",
    f"{REPORT_DIR}/serving_metrics.json",
]
MOCK_FILES = [
    f"{MOCK_DIR}/weekly.json",
    f"{MOCK_DIR}/student.json",
    f"{MOCK_DIR}/factors.json",
    f"{MOCK_DIR}/admin_list.json",
    f"{MOCK_DIR}/admin_stat.json",
    f"{MOCK_DIR}/student_meta.json",
]

# 固定时间戳：1980-01-01 是 zip 格式的下界，写死它才能保证可复现
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
COMPRESS_LEVEL = 9


def _git_blob(path: str) -> bytes:
    """从 HEAD 取文件内容。工作区可能改过、也可能缺文件（大数据被 gitignore 过），
    以「最后提交进去的那份」为准，才能让收包方 `git pull` 后逐位对得上。"""

    res = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=PROJECT_ROOT, capture_output=True,
    )
    if res.returncode != 0:
        raise SystemExit(f"[pkg] HEAD 里没有 {path}：{res.stderr.decode('utf-8', 'ignore')[:200]}")
    return res.stdout


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _build(out_path: Path, members: dict[str, bytes]) -> None:
    manifest = "".join(f"{_sha256(d)}  {n}\n" for n, d in members.items()).encode("utf-8")
    payload = {**members, "MANIFEST.sha256": manifest}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=COMPRESS_LEVEL) as z:
        for name, data in payload.items():
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            # 0644 固定权限，否则打包机的 umask 会渗进 zip 头
            info.external_attr = 0o644 << 16
            z.writestr(info, data)

    print(f"\n[{out_path.name}]  {out_path.stat().st_size:,} B "
          f"({out_path.stat().st_size / 1024 / 1024:.2f} MiB)")
    print(f"  包 SHA256  {_sha256(out_path.read_bytes())}")
    for name, data in payload.items():
        print(f"    {name:28s} {len(data):>10,} B  {_sha256(data)}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", nargs="?", default="all", choices=["all", "data", "mock"])
    ap.add_argument("--out", default=str(Path.home() / "Desktop" / "WarningPlatform_release"))
    args = ap.parse_args(argv)
    out_dir = Path(args.out)

    if args.which in ("all", "data"):
        _build(out_dir / "WarningPlatform_data_11col_v3.zip",
               {Path(p).name: _git_blob(p) for p in DATA_FILES})
    if args.which in ("all", "mock"):
        _build(out_dir / "WarningPlatform_mock_api_v3.zip",
               {Path(p).name: _git_blob(p) for p in MOCK_FILES})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
