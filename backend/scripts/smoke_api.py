"""全接口冒烟：起服务后把 15 个旧接口 + 5 个风险接口跑一遍。

用法：
    python -m uvicorn app.main:app --port 8000      # 另开一个终端
    python scripts/smoke_api.py                    # 默认打 8000
    python scripts/smoke_api.py --base http://127.0.0.1:8015

只打印状态码与结构要点，**不打印任何口令**；退出码 1 表示有接口异常。
"""
import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

import argparse

_ap = argparse.ArgumentParser()
_ap.add_argument("--base", default="http://127.0.0.1:8000")
BASE = _ap.parse_args().base.rstrip("/")
sys.path.insert(0, ".")
from app.config import settings  # noqa: E402


def call(path, token=None, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:200]}


def brief(obj, keys=None, limit=200):
    if isinstance(obj, dict):
        if keys:
            obj = {k: obj.get(k) for k in keys}
        return json.dumps(obj, ensure_ascii=False)[:limit]
    return str(obj)[:limit]


ok, bad = [], []


def check(label, st, cond, note="", expect=200):
    good = st == expect and cond
    (ok if good else bad).append(label)
    print(f"{'OK ' if good else '!! '}{label}  [{st}] {note}")


st, b = call("/api/login/admin", body={"username": settings.demo_admin_account,
                                       "password": settings.demo_password})
admin = b["data"]["token"] if st == 200 else None
print(f"[登录] admin {st}")

# ---------------- 管理端旧接口 ----------------
st, b = call("/api/admin/home/stat", admin)
d = b.get("data") or {}
check("admin/home/stat", st, bool(d), brief(d, limit=260))

st, b = call("/api/admin/student/list?page=1&page_size=5", admin)
d = b.get("data") or {}
rows = d.get("list") or []
avg_none = sum(1 for r in rows if r.get("avg_score") is None)
check("admin/student/list", st, len(rows) == 5,
      f"total={d.get('total')} avg_score为null {avg_none}/5")

first_stu = rows[0] if rows else {}
stu_no = first_stu.get("student_id")

st, b = call(f"/api/admin/student/detail?student_id={stu_no}", admin)
d = b.get("data") or {}
courses = d.get("courses") or d.get("course_list") or []
check("admin/student/detail", st, bool(d), f"keys={list(d)[:8]} courses={len(courses)}")

st, b = call("/api/admin/gnn/graph?top_n=10", admin)
d = b.get("data") or {}
nodes = d.get("nodes") or []
edges = d.get("edges") or d.get("links") or []
check("admin/gnn/graph", st, len(nodes) > 0, f"nodes={len(nodes)} edges={len(edges)}")

st, b = call("/api/admin/warn/list?page=1&page_size=5", admin)
d = b.get("data") or {}
wl = d.get("list") or d.get("items") or []
check("admin/warn/list", st, True, f"total={d.get('total')} 返回={len(wl)}")

warn_id = wl[0]["warn_id"] if wl else None
st, b = call(f"/api/admin/warn/report?warn_id={warn_id}", admin)
check("admin/warn/report", st, bool(b.get("data")),
      f"warn_id={warn_id} " + brief(b.get("data"), limit=140))

for q in ("张", "stu_", "130189"):
    path = "/api/admin/student/list?page=1&page_size=3&keyword=" + urllib.parse.quote(q)
    st, b = call(path, admin)
    d = b.get("data") or {}
    check(f"student/list 搜索「{q}」", st, True, f"total={d.get('total')}")

# ---------------- 学生端旧接口 ----------------
conn = sqlite3.connect("data/app.db")
acc = conn.execute(
    "SELECT account, stu_id FROM sys_user WHERE role='student' AND account != ? LIMIT 1",
    (settings.risk_demo_account,)).fetchone()
st, b = call("/api/login/student", body={"student_id": acc[0], "password": settings.demo_password})
stu_token = b["data"]["token"] if st == 200 else None
print(f"[登录] 学生 {acc[0]} -> {acc[1]}  {st}")

if stu_token:
    for path in ("/api/student/home/info", "/api/student/home/suggest",
                 "/api/student/gnn/graph", "/api/student/warn/list"):
        st, b = call(path, stu_token)
        d = b.get("data")
        size = len(json.dumps(d, ensure_ascii=False)) if d is not None else 0
        check(path, st, d is not None, f"payload={size}B")

# ---------------- 风险接口边界 ----------------
st, b = call("/api/risk/student/stu_999999", admin)
check("risk/student 不存在 → 404", st, True, b.get("msg"), expect=404)
st, b = call("/api/risk/weekly?stu_id=stu_100561", admin)
check("risk/weekly 缺 course_code → 422", st, True, str(b.get("msg"))[:60], expect=422)
st, b = call("/api/admin/risk/list?size=3&level=2", admin)
d = b.get("data") or {}
check("risk/list level=2", st, all(i["risk_level"] == 2 for i in d.get("items", [])),
      f"total={d.get('total')} list_available={d.get('list_available')}")

print()
print(f"通过 {len(ok)} 项，异常 {len(bad)} 项")
if bad:
    print("异常项：", "、".join(bad))
sys.exit(1 if bad else 0)
