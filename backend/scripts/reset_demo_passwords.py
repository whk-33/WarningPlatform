"""把演示账号的密码重置为当前 `WP_DEMO_PASSWORD`。

用途：换数据源、或改过 `WP_DEMO_PASSWORD` 之后，旧账号里存的哈希还是老密码，
表现为「账号存在但登录 401」。`users.sync()` 只负责建号/改绑学号，**不会**覆盖
已有密码（避免每次启动把人工改过的密码冲掉），所以需要这个脚本来显式重置。

    python scripts/reset_demo_passwords.py            # 只重置管理员 + riskdemo
    python scripts/reset_demo_passwords.py --all      # 连学生账号一起重置
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.data import users  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="连学生账号一起重置")
    args = parser.parse_args()

    users.init_db()

    targets = ["admin", settings.risk_demo_account]
    if args.all:
        targets = None  # None 表示全表

    n = users.reset_passwords(settings.demo_password, targets)
    scope = "全部账号" if targets is None else "、".join(targets)
    print(f"已重置 {n} 个账号的密码（{scope}），密码取自当前 WP_DEMO_PASSWORD 配置")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
