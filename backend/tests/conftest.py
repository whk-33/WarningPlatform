"""pytest 公共夹具。

两件事必须在导入 app 之前完成：
1. 账号库指向临时文件，不污染开发用的 backend/data/app.db；
2. 强制 risk 走 mock 分支，测试结果不随本机是否放了 risk_scores.parquet 而变。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("WP_DB_PATH", str(Path(tempfile.gettempdir()) / "wp_test_accounts.db"))

from app.config import settings  # noqa: E402  必须在 sys.path 就绪后导入

settings.processed_candidates = [PROJECT_ROOT / "algorithm" / "data" / "__no_parquet_in_tests__"]
settings.mock_api_dir = PROJECT_ROOT / "docs" / "mock_api"
settings.demo_student_accounts = 5

from app.data.repo import get_repo  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_headers(client) -> dict:
    resp = client.post(
        "/api/login/admin",
        json={"username": settings.demo_admin_account, "password": settings.demo_password},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


@pytest.fixture(scope="session")
def student_headers(client) -> dict:
    account = get_repo().student_accounts()[0]
    resp = client.post(
        "/api/login/student",
        json={"student_id": account["student_no"], "password": settings.demo_password},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


@pytest.fixture(scope="session")
def student_own_id() -> str:
    """学生账号自己的 stu_id（mock 风险数据里没有这个人，用于区分 403 与 404）。"""

    return str(get_repo().student_accounts()[0]["stu_id"])
