"""阶段四风险接口回归测试（mock 数据源）。

契约见 `docs/阶段四_风险接口契约.md`，样本见 `docs/mock_api/*.json`。
这些用例不依赖算法产物，clone 下来就能跑。
"""

from __future__ import annotations

SAMPLE_STU = "stu_100621"
SAMPLE_COURSE = "CCC"
SAMPLE_SEM = "2014B"

WEEKLY_FIELDS = {
    "stu_id", "course_code", "semester", "week", "risk_prob",
    "risk_level", "risk_level_name", "pred_event_week", "top_factors",
}


# ---------------------------------------------------------------- 健康检查

def test_health_exposes_risk_source(client):
    data = client.get("/api/health").json()["data"]
    assert data["risk_source"] == "mock"
    assert data["risk"]["sample_based"] is True
    assert data["risk"]["rows"] > 0


# ---------------------------------------------------------------- 鉴权

def test_risk_endpoints_require_token(client):
    assert client.get("/api/risk/weekly", params={
        "stu_id": SAMPLE_STU, "course_code": SAMPLE_COURSE}).status_code == 401
    assert client.get("/api/admin/risk/list").status_code == 401
    assert client.get("/api/admin/risk/stat").status_code == 401


def test_student_cannot_read_admin_risk(client, student_headers):
    assert client.get("/api/admin/risk/list", headers=student_headers).status_code == 403
    assert client.get("/api/admin/risk/stat", headers=student_headers).status_code == 403


def test_student_cannot_read_others(client, student_headers):
    """学生查别人的 stu_id：403（不是 404，否则等于告诉对方"这个学生存在/不存在"）。"""

    resp = client.get("/api/risk/weekly", headers=student_headers,
                      params={"stu_id": SAMPLE_STU, "course_code": SAMPLE_COURSE})
    assert resp.status_code == 403
    assert resp.json()["code"] == 403


# ---------------------------------------------------------------- 单周风险

def test_weekly_matches_contract(client, admin_headers):
    resp = client.get("/api/risk/weekly", headers=admin_headers, params={
        "stu_id": SAMPLE_STU, "course_code": SAMPLE_COURSE})
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert WEEKLY_FIELDS <= set(data)
    assert data["data_source"] == "mock"
    assert data["risk_level"] in (0, 1, 2)
    assert data["risk_level_name"] in ("低", "中", "高")
    assert 0.0 <= data["risk_prob"] <= 1.0
    # B5：top_factors 必须是数组，不能把 parquet 里的 JSON 字符串原样透给前端
    assert isinstance(data["top_factors"], list)


def test_weekly_week_out_of_range(client, admin_headers):
    resp = client.get("/api/risk/weekly", headers=admin_headers, params={
        "stu_id": SAMPLE_STU, "course_code": SAMPLE_COURSE, "week": 16})
    assert resp.status_code == 422


def test_weekly_unknown_student(client, admin_headers):
    resp = client.get("/api/risk/weekly", headers=admin_headers, params={
        "stu_id": "stu_999999", "course_code": SAMPLE_COURSE})
    assert resp.status_code == 404


def test_weekly_bad_semester(client, admin_headers):
    resp = client.get("/api/risk/weekly", headers=admin_headers, params={
        "stu_id": SAMPLE_STU, "course_code": SAMPLE_COURSE, "semester": "2099X"})
    assert resp.status_code == 400


def test_weekly_missing_required_param(client, admin_headers):
    resp = client.get("/api/risk/weekly", headers=admin_headers, params={"stu_id": SAMPLE_STU})
    assert resp.status_code == 422


# ---------------------------------------------------------------- 学期曲线

def test_student_curve_is_one_shot_array(client, admin_headers):
    resp = client.get(f"/api/risk/student/{SAMPLE_STU}", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["semester"] == SAMPLE_SEM
    weeks = data["weeks"]
    assert len(weeks) == 16                     # 0..15，一次返回，前端不用循环 16 次
    assert [w["week"] for w in weeks] == sorted(w["week"] for w in weeks)
    assert data["partial"] is False             # 样本学生有完整 16 周
    assert all(isinstance(w["top_factors"], list) for w in weeks)


def test_student_curve_unknown_student(client, admin_headers):
    assert client.get("/api/risk/student/stu_999999", headers=admin_headers).status_code == 404


def test_student_curve_partial_flag(client, admin_headers):
    """非样本学生在 mock 里只有导出那一周，必须标 partial，前端才不会把单点当趋势。"""

    resp = client.get("/api/risk/student/stu_103891", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["weeks"]) == 1
    assert data["partial"] is True


# ---------------------------------------------------------------- 归因

def test_factors_sorted_and_capped(client, admin_headers):
    resp = client.get("/api/risk/factors", headers=admin_headers, params={
        "stu_id": SAMPLE_STU, "course_code": SAMPLE_COURSE})
    assert resp.status_code == 200
    factors = resp.json()["data"]["top_factors"]

    assert 0 < len(factors) <= 3
    contribs = [f["contrib"] for f in factors]
    assert contribs == sorted(contribs, reverse=True)
    assert all({"factor", "value", "contrib"} <= set(f) for f in factors)


# ---------------------------------------------------------------- 管理员列表

def test_admin_list_pagination(client, admin_headers):
    p1 = client.get("/api/admin/risk/list", headers=admin_headers,
                    params={"page": 1, "size": 5}).json()["data"]
    p2 = client.get("/api/admin/risk/list", headers=admin_headers,
                    params={"page": 2, "size": 5}).json()["data"]

    assert p1["page"] == 1 and p1["size"] == 5
    assert p1["week"] == 8                    # mock 导出周，不是 week15 的单条
    assert p1["total"] == 20                  # mock 只有 top20 样本
    assert len(p1["items"]) == 5
    assert len(p2["items"]) == 5
    assert {i["stu_id"] for i in p1["items"]} & {i["stu_id"] for i in p2["items"]} == set()
    # 契约：按 risk_prob 降序
    probs = [i["risk_prob"] for i in p1["items"]]
    assert probs == sorted(probs, reverse=True)


def test_admin_list_page_size_alias(client, admin_headers):
    """兼容现有接口的 page_size 命名，前端不用为新接口改习惯。"""

    data = client.get("/api/admin/risk/list", headers=admin_headers,
                      params={"page": 1, "page_size": 3}).json()["data"]
    assert data["size"] == 3
    assert len(data["items"]) == 3


def test_admin_list_level_filter(client, admin_headers):
    high = client.get("/api/admin/risk/list", headers=admin_headers,
                      params={"level": 2}).json()["data"]
    low = client.get("/api/admin/risk/list", headers=admin_headers,
                     params={"level": 0}).json()["data"]

    assert high["total"] == 20                            # mock 样本全是高危
    assert all(i["risk_level"] == 2 for i in high["items"])
    assert low["total"] == 0


def test_admin_list_bad_level(client, admin_headers):
    assert client.get("/api/admin/risk/list", headers=admin_headers,
                      params={"level": 5}).status_code == 422


# ---------------------------------------------------------------- 统计

def test_admin_stat_distribution(client, admin_headers):
    data = client.get("/api/admin/risk/stat", headers=admin_headers).json()["data"]

    assert data["semester"] == SAMPLE_SEM
    assert data["week"] == 8
    assert data["total"] == data["level_0"] + data["level_1"] + data["level_2"]
    # alert_rate = (中 + 高) / total，允许 4 位小数的舍入
    expected = (data["level_1"] + data["level_2"]) / data["total"]
    assert abs(data["alert_rate"] - expected) < 1e-4
    assert data["data_source"] == "mock"
    # mock 只导出 top20 高危，真实分布必须来自算法侧导出，并告知列表可得条数
    assert data["list_available"] == 20
    assert data["sample_based"] is True


def test_admin_stat_alert_rate_is_real_not_sample(client, admin_headers):
    """防止回退：若有人改成按 20 条样本统计，alert_rate 会变成 1.0。"""

    data = client.get("/api/admin/risk/stat", headers=admin_headers).json()["data"]
    assert data["alert_rate"] < 0.9
    assert data["total"] > 1000


# ---------------------------------------------------------------- 风险演示账号

def test_risk_demo_account_can_read_own_curve(client):
    """学生端要能演示曲线：演示账号 stu_id 与风险数据无交集，靠 riskdemo 账号补上。

    这个账号由 `users.sync` 在启动时绑定到"曲线最完整的学生"（mock 下是 stu_100621）。
    """

    from app.config import settings

    login = client.post("/api/login/student", json={
        "student_id": settings.risk_demo_account, "password": settings.demo_password})
    assert login.status_code == 200, login.text

    body = login.json()["data"]
    headers = {"Authorization": f"Bearer {body['token']}"}

    resp = client.get(f"/api/risk/student/{body['stu_id']}", headers=headers)
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert data["week_count"] >= 2      # 至少两点才算一条曲线
    assert data["partial"] is False
    assert data["stu_id"] == body["stu_id"]


def test_risk_demo_account_survives_resync(client):
    """再次同步不能把 riskdemo 当成"过期学生账号"删掉，也不能改断它绑定的 stu_id。"""

    from app.config import settings
    from app.data import users
    from app.data.repo import get_repo
    from app.data.risk_repo import get_risk_repo

    repo = get_repo()
    risk_repo = get_risk_repo()
    demo = risk_repo.demo_student()

    users.sync(repo.student_accounts(), settings.demo_admin_account,
               settings.demo_password, settings.demo_student_accounts,
               risk_demo=demo, risk_demo_account=settings.risk_demo_account)

    row = users.get_by_account(settings.risk_demo_account, "student")
    assert row is not None, "riskdemo 被当成过期账号删掉了"
    assert str(row["stu_id"]) == str(demo["stu_id"])


# ---------------------------------------------------------------- 学生姓名补全

def test_admin_risk_list_carries_student_name(client, admin_headers):
    """风险榜要能独立成表：姓名/专业/班级由 risk 接口自带，前端不必再 join 旧学生列表。

    风险分数据 9 个冻结字段里没有姓名，后端从主数据 + docs/mock_api/student_meta.json 补。
    """

    resp = client.get("/api/admin/risk/list?size=3", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    item = resp.json()["data"]["items"][0]
    for key in ("student_name", "major_name", "class_name"):
        assert key in item, f"列表项缺少字段 {key}，前端又要回头 join 一次"
    assert item["student_name"], "mock 下姓名不应为空"


def test_student_curve_carries_student_name(client, admin_headers):
    """弹窗标题要显示"王雨桐 · 大数据2201"，曲线接口得自己带姓名。"""

    resp = client.get("/api/risk/student/stu_100621", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert data["student_name"]
    assert data["class_name"]


def test_student_meta_missing_returns_null_not_500(admin_headers, client, monkeypatch):
    """姓名映射缺人时要返回 null，不能 500 —— 姓名只是展示字段，不该拖垮风险榜。"""

    from app.data.risk_repo import get_risk_repo

    repo = get_risk_repo()
    monkeypatch.setattr(repo, "_meta", {})

    resp = client.get("/api/admin/risk/list?size=2", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    item = resp.json()["data"]["items"][0]
    assert item["student_name"] is None
    assert item["stu_id"]
