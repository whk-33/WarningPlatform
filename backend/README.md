# backend · 学业预警平台 API

FastAPI + pandas 实现，对外提供两套并行接口：

| 套 | 契约文档 | 接口数 | 数据源 |
|---|---|---|---|
| 阶段一业务接口 | [`../docs/接口约定.md`](../docs/接口约定.md) | 15 | `interim/*.parquet`（学生×课程成绩、行为日志） |
| 阶段四风险分接口 | [`../docs/阶段四_风险接口契约.md`](../docs/阶段四_风险接口契约.md) | 5 | `risk_scores.parquet`（周级风险分，缺失时用 `docs/mock_api/*.json`） |

两套**并行不替换**（契约 B3）：阶段四的 `risk_level` 是周级模型口径，阶段一的是学期末成绩口径；前端对比验收后再决定是否切换。

**设计前提：不依赖 PostgreSQL / MySQL，也不强依赖算法产物。** 启动时自动探测数据源，读不到就用兜底数据起飞，`/api/health` 会明确告诉你当前用的是哪一种。

## 快速开始

```bash
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

| 入口 | 地址 |
|---|---|
| Swagger | http://127.0.0.1:8000/docs |
| 健康检查（含数据源） | http://127.0.0.1:8000/api/health |

演示账号（首次启动自动写入 `backend/data/app.db`）：

| 角色 | 账号 | 说明 |
|---|---|---|
| 管理员 | `admin` | 密码见 `.env.example` 的 `WP_DEMO_PASSWORD` 默认值 |
| 学生 | 学号，如 `20220100` | 共 29 个，取风险最高的那批学生 |
| 风险演示 | `riskdemo` | 启动时自动绑到风险数据里**曲线最完整**的学生（mock 下是 `stu_100621`，16 周齐全）。学生端要演示风险曲线必须用这个账号——普通演示学生的 `stu_id` 与风险数据无交集 |

密码统一为同一个初始值，演示环境够用，**上线前必须改**。

## 数据源探测

启动时按顺序找第一个存在且齐全的目录（`student_info / registration / final_result / courses` 四张表）：

```
algorithm/data/oulad/interim_demo   ← 算法侧 OULAD 小样本，优先
algorithm/data/oulad/interim        ← OULAD 全量清洗结果
algorithm/data/interim              ← 阶段一 mock 数据（29 万行那套）
（都不存在）→ 内置演示数据：200 学生 / 5 课程 / 约 1000 条成绩
```

也可以用环境变量直接指定：

```bash
WP_INTERIM=/path/to/interim uvicorn app.main:app --port 8000
```

`/api/health` 里的 `data_source` 字段：`parquet` = 读到了算法产物，`demo` = 内置兜底数据。
`behaviour_metrics_is_proxy` 为 `true` 时，说明当前数据源没有逐条行为日志，出勤率/小测正确率是按 `final_score` 反推的代理值——**演示和答辩时不要说成真实埋点**。

### 阶段四风险分数据源（独立探测）

```
WP_PROCESSED（环境变量）
algorithm/data/oulad/processed_full_demo/risk_scores.parquet
algorithm/data/oulad/processed_demo/risk_scores.parquet
（都不存在）→ docs/mock_api/*.json   ← 算法侧导出的样本，默认走这条
```

`/api/health` 的 `risk_source` 与每个风险接口响应里的 `data_source` 字段：`parquet` / `mock` / `none`。

**mock 模式的两个口径，前端务必注意：**

| 字段 | 含义 |
|---|---|
| `sample_based: true` | 当前是样本数据，不是全量 |
| `/admin/risk/list` 的 `total: 20` | mock 只导出了 top20 高危条目，翻页到底就是 20 条 |
| `/admin/risk/stat` 的 `total: 7804` | 这是算法侧从真实 parquet 导出的全量分布，**与列表条数不一致是正常的** |
| `list_available: 20` | stat 里额外给出"列表实际能取到多少条"，用来解释上面的不一致 |
| `partial: true` | 该学生只有单周数据，曲线不完整（mock 中非样本学生都是这种情况） |
| `student_name` 等 3 个姓名字段 | 风险数据 9 字段里没有姓名，来自 `docs/mock_api/student_meta.json`（**合成数据**，界面上要标"演示数据"）。真实数据到位后若主数据学号能对上，优先用主数据姓名 |

真实 parquet 到位后（`WP_PROCESSED` 指过去即可），`total / list_available / sample_based` 会自动变成全量口径，接口字段不用改。

## 目录

```
backend/
├── app/
│   ├── main.py            # FastAPI 入口、异常处理、CORS、健康检查
│   ├── config.py          # 配置（全部可用 WP_* 环境变量覆盖）
│   ├── schemas.py         # 请求体模型（登录等）
│   ├── services.py        # 归因因子、报告拼装、建议模板
│   ├── core/
│   │   ├── response.py    # 统一响应 {code,msg,data} + ApiError
│   │   ├── security.py    # pbkdf2 密码哈希 + JWT 签发/校验
│   │   └── deps.py        # current_user / require_admin / self_student_id
│   ├── data/
│   │   ├── loader.py      # 数据源探测 + Parquet 读取 + 演示数据生成
│   │   ├── repo.py        # 核心业务聚合：风险分级、预警记录、图、趋势
│   │   ├── risk_repo.py   # 阶段四风险分：risk_scores.parquet 优先，mock 兜底
│   │   └── users.py       # 账号表（SQLite）
│   └── api/
│       ├── auth.py        # /api/login/admin、/api/login/student、/api/logout
│       ├── admin.py       # /api/admin/*
│       ├── student.py     # /api/student/*
│       └── risk.py        # /api/risk/*、/api/admin/risk/*
├── tests/
│   ├── conftest.py        # 强制 mock 数据源 + 临时账号库，不污染开发数据
│   └── test_risk_api.py   # 阶段四 5 个接口的回归测试（19 例）
├── requirements.txt
└── .env.example
```

## 测试

```bash
cd backend
python -m pytest tests/ -q
```

`conftest.py` 会把风险数据源强制指向 mock、账号库指向临时文件，所以**测试结果不随本机是否放了 `risk_scores.parquet` 而变**，clone 下来就能跑。

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 + 数据源信息 |
| GET | `/api/meta/semesters` | 学期列表（前端下拉框） |
| POST | `/api/login/admin` | 管理员登录 |
| POST | `/api/login/student` | 学生登录 |
| POST | `/api/logout` | 退出（前端清 token 即可，服务端不维护黑名单） |
| GET | `/api/admin/home/stat` | 首页统计卡片 + 风险分布 |
| GET | `/api/admin/student/list` | 学生列表（分页 / 搜索 / 风险筛选） |
| GET | `/api/admin/student/detail` | 学生详情 + 当学期课程 |
| GET | `/api/admin/gnn/graph` | 全局学生-课程异质图（默认 top50 高风险） |
| GET | `/api/admin/warn/list` | 预警列表（学期 / 时间范围 / 等级） |
| GET | `/api/admin/warn/report` | 预警分析报告（结构化） |
| GET | `/api/student/home/info` | 个人学业画像 |
| GET | `/api/student/home/suggest` | 学业建议 |
| GET | `/api/student/gnn/graph` | 个人相关子图 |
| GET | `/api/student/warn/list` | 我的预警记录 |

字段、参数、错误码、示例响应以 [`../docs/接口约定.md`](../docs/接口约定.md) 为准。

### 阶段四风险分接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/risk/weekly` | 登录（学生只能查自己） | 单周风险 + 归因，参数 `stu_id&course_code&semester&week` |
| GET | `/api/risk/student/{stu_id}` | 登录（学生只能查自己） | 整学期曲线，一次返回 16 周 |
| GET | `/api/risk/factors` | 登录（学生只能查自己） | 归因 top3，按 `contrib` 降序 |
| GET | `/api/admin/risk/list` | admin | 风险列表，按 `risk_prob` 降序，`level` 过滤 |
| GET | `/api/admin/risk/stat` | admin | 等级分布 + `alert_rate` |

字段以 [`../docs/阶段四_风险接口契约.md`](../docs/阶段四_风险接口契约.md) 为准。三处实现取舍：

1. `week` 0 起（契约要求），前端展示 `+1`；缺省取「mock 导出周 / parquet 最新周」
2. `pred_event_week = -1` 是语义值（学期内无风险），后端原样返回，中文由前端转（B7）
3. `top_factors` 后端已 `json.loads` 成数组（B5），贡献度可能 < 100%，**前端不要归一**

### 前端接入片段（不改现有文件，粘贴到 `src/api/index.js` 末尾即可）

```js
/* ---------------- 阶段四 · 风险分 ---------------- */

export function getRiskWeekly(params) {
  return request.get('/risk/weekly', { params })
}

export function getRiskStudentCurve(stuId, params) {
  return request.get(`/risk/student/${stuId}`, { params })
}

export function getRiskFactors(params) {
  return request.get('/risk/factors', { params })
}

export function getRiskList(params) {
  return request.get('/admin/risk/list', { params })
}

export function getRiskStat(params) {
  return request.get('/admin/risk/stat', { params })
}
```

这 5 个路径与现有 15 个**完全不重叠**，现有函数一行不用改；`/admin/risk/list` 同时接受 `size` 和 `page_size`，前端沿用现有命名即可。

## 业务口径落在哪

改口径时只需动这两处，接口层不用碰：

| 口径 | 位置 |
|---|---|
| `risk_level = max(课程级)`、`risk_score = 学分加权/2`、`avg_score` 剔除退学+缺考按 0 | `app/data/repo.py` · `_aggregate_students` |
| `warn_id = W + md5(stu\|course\|sem\|week)[:9]`（幂等） | `app/data/repo.py` · `make_warn_id` |
| 图裁剪（top N 学生 + 其课程，学生端 1 跳子图） | `app/data/repo.py` · `graph` / `student_subgraph` |
| 归因因子（规则产出，响应带 `source: "rule"`） | `app/services.py` · `build_factors` |
| 建议模板 | `app/services.py` · `build_suggestions` |
| 阶段四周级风险（含 mock 兜底、默认周次、最大 3 个因子） | `app/data/risk_repo.py` |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `WP_INTERIM` | 自动探测 | 指定 interim 数据目录 |
| `WP_PROCESSED` | 自动探测 | 指定 `risk_scores.parquet` 所在目录（阶段四） |
| `WP_MOCK_API` | `../docs/mock_api` | risk 数据包目录（parquet 缺失时兜底） |
| `WP_GRAPH` | 自动探测 | 优先加载的精简图 `graph_api.json` |
| `WP_DB_PATH` | `data/app.db` | 账号库（SQLite）路径 |
| `WP_SECRET_KEY` | 开发用固定值 | **生产必须换**，否则 token 可伪造 |
| `WP_TOKEN_HOURS` | 12 | token 有效期 |
| `WP_DEMO_ADMIN` | `admin` | 管理员账号名 |
| `WP_DEMO_ACCOUNTS` | 29 | 自动创建的学生账号数 |
| `WP_DEMO_PASSWORD` | 见 `.env.example` | 演示账号初始密码 |
| `WP_RISK_DEMO_ACCOUNT` | `riskdemo` | 风险演示账号名（绑到曲线最完整的学生） |
| `WP_DEBUG` | true | 调试模式 |

## 已知限制

1. **没有数据库**：数据在进程内 pandas 里，启动全量加载。OULAD 全量（3 万学生 / 1065 万条 VLE）加载会占几百 MB，演示够用，真实并发部署要换 PostgreSQL 并预聚合。
2. **聚合是每次请求惰性算 + 内存缓存**：首次请求约 1~2 秒，缓存后实测首页 1.8ms / 学生列表 5.0ms / 全局图 7.8ms / 预警列表 2.4ms（200 人演示数据）。真实数据的量级没测过，别拿这组数字当 SLA。
3. **没有逐题数据**：知识点掌握度是代理指标（考核项按截止日期分桶），答辩时要说明。
4. **`/api/dkt/...` 知识点诊断未实现**：等阶段三 DKT 产物。
5. **推理层是规则**：`services.py` 的因子和建议都是规则产出，模型上线后只换实现、不动字段。
6. **阶段四风险接口目前跑在 mock 样本上**：`risk_scores.parquet` 被 `.gitignore` 忽略，仓库里没有。mock 仅 35 行（top20 高危 + 一个学生的 16 周曲线），所以列表只能翻到 20 条、低/中风险筛选为空。真实文件到位后改 `WP_PROCESSED` 即可，代码不用动。
7. **未接推理桥**（契约 B6）：新学生不在 `risk_scores` 里时直接 404，没有 `subprocess` 调 `30_online_risk.py`。等算法侧确认部署环境能 `import torch` 再接，避免后端进程被 torch DLL 拖挂。
