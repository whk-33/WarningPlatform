"""推理桥（契约 B6）测试。

分两层，都不需要装 torch：

1. **子进程层**：用临时脚本冒充 `30_online_risk.py`，验证 JSON 解析、非 0 退出、
   超时、噪声输出这些真实会遇到的边界；
2. **接口层**：直接替换 `bridge.student_weeks` / `bridge.weekly` 的返回，
   验证 200 时的字段映射，以及桥不可用时**退回 404 而不是 500**。
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from app.data import bridge

SAMPLE_SEM = "2014B"
NEW_STU = "stu_new_0001"
NEW_COURSE = "CCC"


# ------------------------------------------------------------------ 工具

@pytest.fixture
def fake_bridge(tmp_path, monkeypatch):
    """把桥指向一个临时脚本，返回「写脚本」的工厂。"""

    monkeypatch.setenv("WP_BRIDGE", "1")

    def _make(code: str):
        script = tmp_path / "30_online_risk.py"
        script.write_text(textwrap.dedent(code), encoding="utf-8")
        monkeypatch.setenv("WP_BRIDGE_SCRIPT", str(script))
        monkeypatch.setenv("WP_BRIDGE_PYTHON", sys.executable)
        return script

    return _make


def _fake_week(i: int) -> dict:
    return {
        "stu_id": NEW_STU,
        "course_code": NEW_COURSE,
        "semester": SAMPLE_SEM,
        "week": i,
        "risk_prob": 0.5,
        "risk_level": 0,
        "risk_level_name": "低",
        "pred_event_week": -1,
        "top_factors": [{"factor": "点击次数", "value": 12.0, "contrib": 0.6}],
        "event_occurred": i >= 10,
    }


# ------------------------------------------------------- 子进程层：正常路径

def test_run_parses_json(fake_bridge):
    fake_bridge(
        """
        import json
        print(json.dumps({"stu_id": "s1", "weeks": [{"week": 0}], "名字": "张三"}, ensure_ascii=False))
        """
    )
    payload = bridge.run("student", stu_id="s1", semester=SAMPLE_SEM)
    assert payload["stu_id"] == "s1"
    # 中文不能变乱码：子进程强制 PYTHONIOENCODING=utf-8
    assert payload["名字"] == "张三"


def test_run_ignores_noise_before_json(fake_bridge):
    """桥被 import 时会打 warning，解析要取最后一行合法 JSON，不能整段 json.loads。"""

    fake_bridge(
        """
        import json
        print("WARNING: torch not found, falling back")
        print("loading models...")
        print(json.dumps({"weeks": [{"week": 3}]}))
        """
    )
    assert bridge.run("student", stu_id="s1", semester=SAMPLE_SEM)["weeks"][0]["week"] == 3


def test_run_returns_none_on_nonzero_exit(fake_bridge):
    """典型场景：机器上没有 torch，桥走 except 分支 exit 1。"""

    fake_bridge(
        """
        import json, sys
        print(json.dumps({"error": "No module named 'torch'", "mode": "student"}))
        sys.exit(1)
        """
    )
    assert bridge.run("student", stu_id="s1", semester=SAMPLE_SEM) is None


def test_run_returns_none_on_garbage(fake_bridge):
    fake_bridge("print('not json at all')")
    assert bridge.run("student", stu_id="s1", semester=SAMPLE_SEM) is None


def test_run_returns_none_on_timeout(fake_bridge, monkeypatch):
    fake_bridge(
        """
        import time
        time.sleep(5)
        print('{"weeks": []}')
        """
    )
    monkeypatch.setenv("WP_BRIDGE_TIMEOUT", "0.4")
    assert bridge.run("student", stu_id="s1", semester=SAMPLE_SEM) is None


def test_run_skipped_when_disabled(tmp_path, monkeypatch):
    """WP_BRIDGE=0 时连脚本都不该启动（脚本故意写成会留痕的形式）。"""

    marker = tmp_path / "spawned.txt"
    script = tmp_path / "30_online_risk.py"
    script.write_text(f"open(r'{marker}', 'w').write('yes')\n", encoding="utf-8")
    monkeypatch.setenv("WP_BRIDGE", "0")
    monkeypatch.setenv("WP_BRIDGE_SCRIPT", str(script))
    monkeypatch.setenv("WP_BRIDGE_PYTHON", sys.executable)

    assert bridge.run("student", stu_id="s1", semester=SAMPLE_SEM) is None
    assert not marker.exists()


def test_failure_is_cached_not_retried(fake_bridge, monkeypatch, tmp_path):
    """失败结果也要缓存：否则新学生每次请求都会 spawn 一个注定失败的子进程。"""

    counter_file = tmp_path / "calls.txt"
    monkeypatch.setenv("WP_TEST_COUNTER", str(counter_file))
    fake_bridge(
        """
        import json, os, sys
        with open(os.environ["WP_TEST_COUNTER"], "a") as f:
            f.write("x")
        print(json.dumps({"error": "boom", "mode": "student"}))
        sys.exit(1)
        """
    )

    assert bridge.run("student", stu_id="s1", semester=SAMPLE_SEM) is None
    assert bridge.run("student", stu_id="s1", semester=SAMPLE_SEM) is None
    assert counter_file.read_text() == "x"        # 只真的跑了一次


# ------------------------------------------------------- 接口层：字段映射

def test_unknown_student_falls_back_to_bridge(client, admin_headers, monkeypatch):
    """新学生：parquet/mock 里没有 → 走桥 → 返回 16 周，并标 inferred。"""

    monkeypatch.setenv("WP_BRIDGE", "1")
    monkeypatch.setattr(bridge, "student_weeks", lambda stu, sem: [_fake_week(i) for i in range(16)])

    resp = client.get(f"/api/risk/student/{NEW_STU}", headers=admin_headers,
                      params={"semester": SAMPLE_SEM})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["data_source"] == "bridge"
    assert data["inferred"] is True
    assert data["sample_based"] is False
    assert data["week_count"] == 16
    assert data["partial"] is False
    assert data["course_count"] == 1
    assert [w["week"] for w in data["weeks"]] == list(range(16))
    # 11 字段齐全（契约 §3.1），桥缺的 risk_score 必须是 null
    assert data["weeks"][0].keys() >= {
        "stu_id", "course_code", "semester", "week", "risk_prob", "risk_level",
        "risk_level_name", "pred_event_week", "risk_state", "risk_score", "top_factors",
    }
    assert data["weeks"][0]["risk_score"] is None
    # event_occurred → risk_state
    assert data["weeks"][9]["risk_state"] == 0
    assert data["weeks"][10]["risk_state"] == 1


def test_bridge_unavailable_keeps_404(client, admin_headers, monkeypatch):
    """桥挂了/没装 torch：必须还是 404，不能变成 500——这是接桥的底线。"""

    monkeypatch.setenv("WP_BRIDGE", "1")
    monkeypatch.setattr(bridge, "student_weeks", lambda stu, sem: None)

    resp = client.get(f"/api/risk/student/{NEW_STU}", headers=admin_headers,
                      params={"semester": SAMPLE_SEM})
    assert resp.status_code == 404


def test_bridge_disabled_keeps_404(client, admin_headers, monkeypatch):
    monkeypatch.setenv("WP_BRIDGE", "0")
    resp = client.get(f"/api/risk/student/{NEW_STU}", headers=admin_headers,
                      params={"semester": SAMPLE_SEM})
    assert resp.status_code == 404


def test_weekly_falls_back_to_bridge(client, admin_headers, monkeypatch):
    monkeypatch.setenv("WP_BRIDGE", "1")
    monkeypatch.setattr(bridge, "weekly", lambda *a, **kw: _fake_week(8))

    resp = client.get("/api/risk/weekly", headers=admin_headers, params={
        "stu_id": NEW_STU, "course_code": NEW_COURSE, "semester": SAMPLE_SEM, "week": 8,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["data_source"] == "bridge"
    assert data["inferred"] is True
    assert data["week"] == 8
    assert data["risk_prob"] == 0.5


def test_factors_follow_weekly_fallback(client, admin_headers, monkeypatch):
    """归因接口是 weekly 的派生，桥接上后应当自动可用。"""

    monkeypatch.setenv("WP_BRIDGE", "1")
    monkeypatch.setattr(bridge, "weekly", lambda *a, **kw: _fake_week(8))

    resp = client.get("/api/risk/factors", headers=admin_headers, params={
        "stu_id": NEW_STU, "course_code": NEW_COURSE, "semester": SAMPLE_SEM, "week": 8,
    })
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["top_factors"]
    assert len(items) == 1 and items[0]["contrib"] == 0.6


# ------------------------------------------------------- 健康检查暴露状态

def test_health_reports_bridge(client):
    data = client.get("/api/health").json()["data"]
    info = data["bridge"]
    assert set(info) >= {"enabled", "script", "script_exists", "python", "timeout_s", "reason"}
    assert info["enabled"] is False          # conftest 默认 WP_BRIDGE=0
    assert info["reason"] == "disabled_by_env"


def test_describe_reports_missing_script(tmp_path, monkeypatch):
    monkeypatch.setenv("WP_BRIDGE", "auto")
    monkeypatch.setenv("WP_BRIDGE_SCRIPT", str(tmp_path / "nope.py"))
    info = bridge.describe()
    assert info["enabled"] is False
    assert info["reason"] == "script_not_found"


# --------------------------------------------- 端到端：HTTP → repo → 子进程

def test_end_to_end_through_real_subprocess(client, admin_headers, fake_bridge):
    """整条链路跑一遍：路由 → RiskRepo 回退 → 真的 spawn 一个 python 子进程 → 映射。

    用一个打印固定 JSON 的假脚本代替真桥，所以不依赖 torch，也不依赖模型文件。
    """

    import json

    payload = {"stu_id": "x", "semester": "y",
               "weeks": [_fake_week(i) for i in range(16)]}
    # 把 JSON 当成字符串字面量嵌进脚本：直接拼 dict 会写出 false/true，Python 不认
    fake_bridge(f"import sys\nsys.stdout.write({json.dumps(json.dumps(payload, ensure_ascii=False))})\n")

    resp = client.get(f"/api/risk/student/{NEW_STU}", headers=admin_headers,
                      params={"semester": SAMPLE_SEM})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["data_source"] == "bridge"
    assert data["week_count"] == 16
    assert data["weeks"][15]["risk_state"] == 1


def test_malformed_bridge_payload_degrades_to_404(client, admin_headers, monkeypatch):
    """桥回了字段不全的 JSON：必须 404，不能因为 int(None) 变成 500。"""

    monkeypatch.setenv("WP_BRIDGE", "1")
    # weekly：缺 week
    monkeypatch.setattr(bridge, "weekly", lambda *a, **kw: {"risk_prob": 0.5})
    resp = client.get("/api/risk/weekly", headers=admin_headers, params={
        "stu_id": NEW_STU, "course_code": NEW_COURSE, "semester": SAMPLE_SEM, "week": 8,
    })
    assert resp.status_code == 404

    # student：整条曲线都是坏行
    monkeypatch.setattr(bridge, "student_weeks", lambda stu, sem: [{"risk_prob": 0.5}])
    resp = client.get(f"/api/risk/student/{NEW_STU}", headers=admin_headers,
                      params={"semester": SAMPLE_SEM})
    assert resp.status_code == 404


# ------------------------------------------- 契约 D-2：pred_event_week_source

def test_pred_week_source_passthrough(client, admin_headers, monkeypatch):
    """桥自己给了合法三态就照用，后端不得改写。"""

    monkeypatch.setenv("WP_BRIDGE", "1")

    def _weeks(stu, sem):
        out = []
        for i in range(16):
            w = _fake_week(i)
            w["pred_event_week"] = 12 if i < 8 else -1
            w["pred_event_week_source"] = "inference" if i < 8 else "unknown"
            out.append(w)
        return out

    monkeypatch.setattr(bridge, "student_weeks", _weeks)
    resp = client.get(f"/api/risk/student/{NEW_STU}", headers=admin_headers,
                      params={"semester": SAMPLE_SEM})
    assert resp.status_code == 200, resp.text
    weeks = resp.json()["data"]["weeks"]
    assert weeks[0]["pred_event_week_source"] == "inference"
    assert weeks[0]["pred_event_week"] == 12
    assert weeks[15]["pred_event_week_source"] == "unknown"
    assert weeks[15]["pred_event_week"] == -1


def test_pred_week_source_backfilled_for_old_bridge(client, admin_headers, monkeypatch):
    """老版本桥没有这个字段：按「-1 → unknown，非 -1 → inference」补齐，前端不必判空。"""

    monkeypatch.setenv("WP_BRIDGE", "1")

    def _weeks(stu, sem):
        out = []
        for i in range(16):
            w = _fake_week(i)          # _fake_week 本来就没有 pred_event_week_source
            w["pred_event_week"] = 9 if i == 0 else -1
            out.append(w)
        return out

    monkeypatch.setattr(bridge, "student_weeks", _weeks)
    resp = client.get(f"/api/risk/student/{NEW_STU}", headers=admin_headers,
                      params={"semester": SAMPLE_SEM})
    assert resp.status_code == 200, resp.text
    weeks = resp.json()["data"]["weeks"]
    assert weeks[0]["pred_event_week_source"] == "inference"
    assert all(w["pred_event_week_source"] == "unknown" for w in weeks[1:])


def test_pred_week_source_rejects_garbage(client, admin_headers, monkeypatch):
    """桥回了个值域外的字符串：按周次反推，不要原样透给前端。"""

    monkeypatch.setenv("WP_BRIDGE", "1")
    w = _fake_week(8)
    w["pred_event_week_source"] = "probably"
    monkeypatch.setattr(bridge, "weekly", lambda *a, **kw: w)

    resp = client.get("/api/risk/weekly", headers=admin_headers, params={
        "stu_id": NEW_STU, "course_code": NEW_COURSE, "semester": SAMPLE_SEM, "week": 8,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["pred_event_week_source"] == "unknown"
