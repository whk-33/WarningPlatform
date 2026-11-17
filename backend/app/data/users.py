"""演示账号库（sqlite）。

只存账号密码与角色，业务数据仍走 Parquet / 未来的 PostgreSQL。
OULAD 本身没有任何账号体系，这张表是必须自己造的。
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from app.config import settings
from app.core.security import hash_password, verify_password

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sys_user (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account      TEXT NOT NULL UNIQUE,
    password     TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('admin', 'student')),
    stu_id       TEXT,
    display_name TEXT,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sys_user_role ON sys_user(role);
"""


def _connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def count() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM sys_user").fetchone()
    return int(row["n"])


def sync(
    students: list[dict],
    admin_account: str,
    password: str,
    limit: int,
    risk_demo: dict | None = None,
    risk_demo_account: str = "riskdemo",
) -> int:
    """把演示账号对齐到当前数据源，返回新建条数。

    幂等，且能跟着数据源切换：mock 换成 OULAD 后学生 ID 全变，
    这里会清掉已不存在学生的账号、补上新的，避免"账号登录后查不到数据"。

    `risk_demo`（`RiskRepo.demo_student()` 的结果）不为空时，额外维护一个
    风险演示账号：学生端要能查到自己这条曲线，而演示学生账号的 stu_id
    与风险数据里的学生 ID 没有交集，必须单独绑一个。
    该账号的 stu_id 每次启动按当前风险数据重算，数据源换了会自动改绑。
    """

    init_db()
    created = 0
    desired = {str(s["student_no"]): s for s in students[:limit]}

    with _connect() as conn:
        if conn.execute("SELECT 1 FROM sys_user WHERE account = ?", (admin_account,)).fetchone() is None:
            conn.execute(
                "INSERT INTO sys_user (account, password, role, stu_id, display_name) VALUES (?,?,?,?,?)",
                (admin_account, hash_password(password), "admin", None, "管理员"),
            )
            created += 1

        existing = {
            row["account"]: row["stu_id"]
            for row in conn.execute("SELECT account, stu_id FROM sys_user WHERE role = 'student'")
        }

        # 风险演示账号不在 students 里，清理时不能把它当过期账号删掉
        stale = [acc for acc in existing if acc not in desired and acc != risk_demo_account]
        if stale:
            conn.executemany("DELETE FROM sys_user WHERE account = ?", [(acc,) for acc in stale])

        for no, stu in desired.items():
            if no in existing:
                continue
            conn.execute(
                "INSERT INTO sys_user (account, password, role, stu_id, display_name) VALUES (?,?,?,?,?)",
                (no, hash_password(password), "student", str(stu["stu_id"]), str(stu["student_name"])),
            )
            created += 1

        if risk_demo and risk_demo.get("stu_id"):
            demo_stu = str(risk_demo["stu_id"])
            demo_name = f"风险演示学生（{demo_stu}）"
            row = conn.execute(
                "SELECT stu_id FROM sys_user WHERE account = ? AND role = 'student'", (risk_demo_account,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO sys_user (account, password, role, stu_id, display_name) VALUES (?,?,?,?,?)",
                    (risk_demo_account, hash_password(password), "student", demo_stu, demo_name),
                )
                created += 1
            elif str(row["stu_id"] or "") != demo_stu:
                # 数据源切换后学生 ID 会变，这里跟着改绑，否则账号又会查到 404
                conn.execute(
                    "UPDATE sys_user SET stu_id = ?, display_name = ? WHERE account = ?",
                    (demo_stu, demo_name, risk_demo_account),
                )

    return created


def reset_passwords(password: str, accounts: list[str] | None = None) -> int:
    """把账号密码重置为 `password`，返回受影响条数。

    `sync()` 故意不覆盖已有账号的密码（免得每次启动冲掉人工改过的密码），
    所以「换过 WP_DEMO_PASSWORD」或「账号由旧配置建出来」时，需要显式调这里。
    `accounts=None` 表示全表重置。
    """

    init_db()
    hashed = hash_password(password)

    with _connect() as conn:
        if accounts is None:
            cur = conn.execute("UPDATE sys_user SET password = ?", (hashed,))
        else:
            if not accounts:
                return 0
            placeholders = ",".join("?" for _ in accounts)
            cur = conn.execute(
                f"UPDATE sys_user SET password = ? WHERE account IN ({placeholders})",
                (hashed, *accounts),
            )
    return int(cur.rowcount or 0)


def get_by_account(account: str, role: str) -> Optional[sqlite3.Row]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sys_user WHERE account = ? AND role = ?", (account, role)
        ).fetchone()
    return row


def authenticate(account: str, password: str, role: str) -> Optional[sqlite3.Row]:
    row = get_by_account(account, role)
    if row is None:
        return None
    if not verify_password(password, row["password"]):
        return None
    return row
