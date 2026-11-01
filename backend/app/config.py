"""全局配置。

所有可调参数集中在这里，避免散落在业务代码里。
环境变量前缀统一用 WP_（Warning Platform）。
"""

from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    return Path(raw).expanduser().resolve() if raw else default


class Settings:
    app_name = "GNN 学业预警平台 API"
    version = "0.1.0"
    debug = os.getenv("WP_DEBUG", "1") == "1"

    # ---------- 鉴权 ----------
    secret_key = os.getenv("WP_SECRET_KEY", "dev-only-secret-please-change-me-32bytes")
    jwt_algorithm = "HS256"
    token_expire_hours = int(os.getenv("WP_TOKEN_HOURS", "12"))

    # ---------- 数据目录 ----------
    db_path = _env_path("WP_DB_PATH", BACKEND_DIR / "data" / "app.db")

    # 按优先级探测：演示增强层 -> OULAD 适配产物 -> mock 产物
    interim_candidates = [
        _env_path(
            "WP_INTERIM",
            PROJECT_ROOT / "algorithm" / "data" / "oulad" / "interim_demo",
        ),
        PROJECT_ROOT / "algorithm" / "data" / "oulad" / "interim",
        PROJECT_ROOT / "algorithm" / "data" / "interim",
    ]

    # 按优先级探测：演示增强层（graph_api 5MB）-> 适配产物 -> mock；
    # 最后才回退到 graph_nx.json（真实数据下约 100MB，启动解析慢且只用到 student/course）
    graph_candidates = [
        _env_path(
            "WP_GRAPH",
            PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_full_demo" / "graph_api.json",
        ),
        PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed_demo" / "graph_api.json",
        PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed" / "graph_api.json",
        PROJECT_ROOT / "algorithm" / "data" / "processed" / "graph_api.json",
        PROJECT_ROOT / "algorithm" / "data" / "oulad" / "processed" / "graph_nx.json",
        PROJECT_ROOT / "algorithm" / "data" / "processed" / "graph_nx.json",
    ]

    # ---------- 业务阈值 ----------
    attendance_threshold = 0.60  # 出勤率告警线
    quiz_threshold = 0.50        # 小测正确率告警线
    warn_min_level = 1           # 风险等级 >= 该值才生成预警记录（1=警惕, 2=高危）

    graph_default_students = 50  # 全局图默认学生上限
    page_size_default = 20
    page_size_max = 100

    # ---------- 演示账号 ----------
    demo_password = os.getenv("WP_DEMO_PASSWORD", "wp123456")
    demo_student_accounts = int(os.getenv("WP_DEMO_ACCOUNTS", "29"))
    demo_admin_account = os.getenv("WP_DEMO_ADMIN", "admin")


settings = Settings()
