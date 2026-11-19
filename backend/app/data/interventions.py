"""预警干预记录（sqlite）。

辅导员在预警详情里登记的处理动作（约谈 / 家校沟通 / 学业帮扶等）。
与账号表共用同一个 app.db（`WP_DB_PATH`），容器化部署时挂载数据卷即可持久化。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.config import settings

# 前端下拉框直接取这个列表，新增处理方式只改这里
ACTIONS = ("谈话提醒", "约谈辅导", "家校沟通", "学业帮扶", "转介咨询", "其他")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS warn_intervention (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    warn_id    TEXT NOT NULL,
    action     TEXT NOT NULL,
    note       TEXT,
    operator   TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_warn_intervention_warn ON warn_intervention(warn_id);
"""


def _connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "warn_id": str(row["warn_id"]),
        "action": str(row["action"]),
        "note": row["note"] or "",
        "operator": row["operator"] or "",
        "created_at": str(row["created_at"] or ""),
    }


def add(warn_id: str, action: str, note: str, operator: str) -> dict[str, Any]:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO warn_intervention (warn_id, action, note, operator) VALUES (?,?,?,?)",
            (warn_id, action, note, operator),
        )
        row = conn.execute(
            "SELECT * FROM warn_intervention WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _row(row)


def list_by_warn(warn_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM warn_intervention WHERE warn_id = ? ORDER BY id DESC", (warn_id,)
        ).fetchall()
    return [_row(r) for r in rows]


def latest_map() -> dict[str, dict[str, Any]]:
    """warn_id -> 最新一条干预记录，供预警列表批量标注"已干预 / 待处理"。"""

    with _connect() as conn:
        rows = conn.execute(
            "SELECT w.* FROM warn_intervention w JOIN ("
            "  SELECT warn_id, MAX(id) AS mid FROM warn_intervention GROUP BY warn_id"
            ") t ON w.id = t.mid"
        ).fetchall()
    return {str(r["warn_id"]): _row(r) for r in rows}
