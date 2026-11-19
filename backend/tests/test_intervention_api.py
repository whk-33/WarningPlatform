"""干预记录接口测试（阶段三）。

覆盖：登记 → 列表 → 预警列表状态标注 → 报告内嵌干预记录，以及非法入参。
"""

from __future__ import annotations


def _first_warn_id(client, headers) -> str:
    resp = client.get(
        "/api/admin/warn/list",
        params={"page": 1, "page_size": 1},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["list"]
    assert items, "演示数据里必须至少有一条预警，否则干预记录无从测试"
    return items[0]["warn_id"]


def test_intervene_flow(client, admin_headers):
    warn_id = _first_warn_id(client, admin_headers)

    resp = client.post(
        "/api/admin/warn/intervene",
        json={"warn_id": warn_id, "action": "约谈辅导", "note": "已约谈，承诺补齐作业"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["warn_id"] == warn_id
    assert data["action"] == "约谈辅导"
    assert data["operator"] == "admin"

    resp = client.get(
        "/api/admin/warn/interventions", params={"warn_id": warn_id}, headers=admin_headers
    )
    body = resp.json()["data"]
    assert "约谈辅导" in body["actions"]
    assert any(i["note"] == "已约谈，承诺补齐作业" for i in body["list"])

    # 预警列表要能直接看到"已干预"，不用再逐条查
    resp = client.get(
        "/api/admin/warn/list", params={"page": 1, "page_size": 100}, headers=admin_headers
    )
    hit = next(i for i in resp.json()["data"]["list"] if i["warn_id"] == warn_id)
    assert hit["intervened"] is True
    assert hit["last_intervention_action"] == "约谈辅导"

    # 分析报告里带干预记录，前端弹窗一次请求拿全
    resp = client.get(
        "/api/admin/warn/report", params={"warn_id": warn_id}, headers=admin_headers
    )
    assert resp.json()["data"]["interventions"]


def test_intervene_rejects_unknown_action(client, admin_headers):
    warn_id = _first_warn_id(client, admin_headers)
    resp = client.post(
        "/api/admin/warn/intervene",
        json={"warn_id": warn_id, "action": "不存在的处理方式"},
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 400


def test_intervene_rejects_unknown_warn(client, admin_headers):
    resp = client.post(
        "/api/admin/warn/intervene",
        json={"warn_id": "W-NOT-EXIST", "action": "谈话提醒"},
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_intervene_requires_admin(client, student_headers):
    resp = client.post(
        "/api/admin/warn/intervene",
        json={"warn_id": "W-NOT-EXIST", "action": "谈话提醒"},
        headers=student_headers,
    )
    assert resp.status_code == 403
