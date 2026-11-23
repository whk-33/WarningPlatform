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

    # 从列表里挑一个「不是曲线样本」的学生：mock 的曲线只导出 SAMPLE_STU，
    # 其余学生都只有导出那一周。不写死 stu_id，避免 mock 重生成后 404。
    listing = client.get("/api/admin/risk/list", headers=admin_headers,
                         params={"page": 1, "size": 100}).json()["data"]
    candidates = [i["stu_id"] for i in listing["items"] if i["stu_id"] != SAMPLE_STU]
    assert candidates, "mock 列表里应当有非曲线样本学生"

    resp = client.get(f"/api/risk/student/{candidates[0]}", headers=admin_headers)
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
    assert len(p1["items"]) == 5
    assert len(p2["items"]) == 5
    assert {i["stu_id"] for i in p1["items"]} & {i["stu_id"] for i in p2["items"]} == set()

    # total 必须等于实际能翻到的学生数。不写死 20：mock 由 admin_list 的 18 名学生
    # 加上曲线样本学生组成，导出规则一变就过期（上一版就是这么挂的）。
    full = client.get("/api/admin/risk/list", headers=admin_headers,
                      params={"page": 1, "size": 100}).json()["data"]
    assert p1["total"] == full["total"] == len(full["items"]) > 0

    # 契约 §3.4：风险池按 risk_score 降序（与 prob 严格单调、缺列时退回 prob）
    probs = [i["risk_prob"] for i in p1["items"]]
    assert probs == sorted(probs, reverse=True)


def test_admin_list_page_size_alias(client, admin_headers):
    """兼容现有接口的 page_size 命名，前端不用为新接口改习惯。"""

    data = client.get("/api/admin/risk/list", headers=admin_headers,
                      params={"page": 1, "page_size": 3}).json()["data"]
    assert data["size"] == 3
    assert len(data["items"]) == 3


def test_admin_list_level_filter(client, admin_headers):
    """level 过滤的各级人数必须加得回总数——这是列表与统计对账的前提。"""

    def total_of(level):
        return client.get("/api/admin/risk/list", headers=admin_headers,
                          params={"level": level, "size": 100}).json()["data"]["total"]

    overall = client.get("/api/admin/risk/list", headers=admin_headers,
                         params={"size": 100}).json()["data"]["total"]
    assert sum(total_of(lv) for lv in (0, 1, 2)) == overall

    high = client.get("/api/admin/risk/list", headers=admin_headers,
                      params={"level": 2}).json()["data"]
    assert all(i["risk_level"] == 2 for i in high["items"])


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
    # mock 的 total/level_* 来自算法侧导出的全量分布（作为"应有规模"参考），
    # list_available 是样本里实际能翻到的记录行数（聚合前的学生-课程对），
    # 所以样本量远小于 total 属正常，只要都为正、且样本不超过全量即可。
    assert data["total"] > 0
    assert 0 < data["list_available"] <= data["total"]
    assert data["sample_based"] is True


def test_admin_stat_alert_rate_is_real_not_sample(client, admin_headers):
    """防止回退：若有人改成按 20 条样本统计，alert_rate 会变成 1.0。"""

    data = client.get("/api/admin/risk/stat", headers=admin_headers).json()["data"]
    assert data["alert_rate"] < 0.9
    assert data["total"] > 1000


# ---------------------------------------------------------------- 首页口径统一

HOME_LEGACY_FIELDS = ("total_student", "warn_count", "high_count", "mid_count",
                      "normal_count", "risk_distribute", "warn_rate")


def test_home_stat_shares_caliber_with_risk_stat(client, admin_headers):
    """v1.3 起首页与风险统计同源，数字必须逐项相等。

    历史问题：home/stat 走规则式整学期结算（51.4%），risk/stat 走模型第 8 周（34.7%），
    差值 16.7pp，同一块看板上并列展示必然被问「到底哪个对」。
    """

    home = client.get("/api/admin/home/stat", headers=admin_headers).json()["data"]
    risk = client.get("/api/admin/risk/stat", headers=admin_headers).json()["data"]

    assert home["semester"] == risk["semester"]
    assert home["week"] == risk["week"]
    assert home["total_student"] == risk["total"]
    assert home["high_count"] == risk["level_2"]
    assert home["mid_count"] == risk["level_1"]
    assert home["normal_count"] == risk["level_0"]
    assert home["warn_count"] == risk["level_1"] + risk["level_2"]
    assert abs(home["warn_rate"] - risk["alert_rate"]) < 1e-9
    assert home["caliber"] == f"model_week{risk['week']}"


def test_home_stat_keeps_legacy_field_names(client, admin_headers):
    """前端 Home.vue 已在用旧字段，改口径不能改字段名，否则联调返工。"""

    home = client.get("/api/admin/home/stat", headers=admin_headers).json()["data"]
    for field in HOME_LEGACY_FIELDS:
        assert field in home, f"丢掉旧字段 {field} 会打断前端"


def test_home_stat_rejects_bad_week(client, admin_headers):
    assert client.get("/api/admin/home/stat?week=0", headers=admin_headers).status_code == 422
    assert client.get("/api/admin/home/stat?week=99", headers=admin_headers).status_code == 422


def test_student_avg_score_null_has_explicit_reason(client, admin_headers):
    """avg_score 为 None 时必须同时给出 has_valid_score=False。

    真实数据里 32.5% 的学生当学期所有课程都退学，剔除退学后无有效成绩，
    avg_score 天然为空。不解释清楚，前端只能各自猜「是缺数据还是 0 分」。
    """

    data = client.get("/api/admin/student/list?page=1&page_size=50",
                      headers=admin_headers).json()["data"]

    for item in data["list"]:
        assert "has_valid_score" in item
        assert "valid_course_count" in item
        # 两者必须自洽：有有效成绩 ⇔ avg_score 非空
        assert item["has_valid_score"] == (item["avg_score"] is not None)
        if not item["has_valid_score"]:
            assert item["valid_course_count"] == 0


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


# ---------------------------------------------------------------- 真实数据下的口径
# 下面三例都跑在内存构造的数据上，不依赖真实 parquet，也不走 HTTP。

def _fake_repo_with_weeks(weeks=(0, 8, 14, 15)):
    """造一个只有周次的最小 RiskRepo，用来验证默认周策略。"""
    import pandas as pd
    from app.data.risk_repo import REQUIRED_COLUMNS, RiskRepo

    df = pd.DataFrame([
        {"stu_id": "stu_1", "course_code": "AAA", "semester": "2014J", "week": w,
         "risk_prob": 0.1 * w, "risk_level": 1, "risk_level_name": "中",
         "pred_event_week": -1, "top_factors": "[]"}
        for w in weeks
    ], columns=REQUIRED_COLUMNS)

    repo = RiskRepo.__new__(RiskRepo)
    repo.source = "parquet"
    repo.path = None
    repo._df = repo._normalize(df)
    repo._sample_based = False
    repo._expected_total = None
    repo._default_week = None      # 关键：parquet 来源没有导出周
    repo._mock_stat = None
    repo._meta = {}
    return repo


def test_default_week_prefers_midterm_not_settlement_week():
    """默认周不能取最大值：week15 是期末结算周，高危率会比 w14 突增 20pp 以上。"""

    repo = _fake_repo_with_weeks()
    assert repo.resolve_week(None, "2014J") == 8


def test_default_week_honours_env_override(monkeypatch):
    from app.data.risk_repo import RiskRepo

    repo = _fake_repo_with_weeks()
    monkeypatch.setenv("WP_DEFAULT_WEEK", "14")
    assert repo.resolve_week(None, "2014J") == 14
    # 传了非法值就回落到期中，不能炸
    monkeypatch.setenv("WP_DEFAULT_WEEK", "abc")
    assert RiskRepo.resolve_week(repo, None, "2014J") == 8


def test_student_meta_matches_prefixed_and_plain_ids(monkeypatch):
    """主数据 stu_num=11391、风险数据 stu_id=stu_11391，别名没登记就会「有学生没姓名」。"""

    import pandas as pd
    import app.data.repo as old_repo

    students = pd.DataFrame([{
        "stu_num": 11391, "student_no": 130189, "stu_id": "stu_11391",
        "student_name": "徐逸飞", "major_name": "计算机科学与技术", "class_name": "计科1301班",
    }])

    class _Fake:
        pass

    fake = _Fake()
    fake.students = students
    monkeypatch.setattr(old_repo, "get_repo", lambda: fake)

    repo = _fake_repo_with_weeks()
    repo._load_meta()

    for key in ("stu_11391", "11391", "130189", "stu_130189"):
        info = repo._student_meta(key)
        assert info["student_name"] == "徐逸飞", f"{key} 没匹配到姓名"
    assert repo._student_meta("stu_999999")["student_name"] is None
