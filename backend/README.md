# backend · 学业预警平台 API

FastAPI + pandas 实现，对外提供 `docs/接口约定.md` 里约定的 15 个接口。

**设计前提：不依赖 PostgreSQL / MySQL，也不强依赖算法产物。** 启动时自动探测数据源，读不到 Parquet 就用内置的 200 人演示数据兜底，`/api/health` 会明确告诉你当前用的是哪一种。

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
│   │   └── users.py       # 账号表（SQLite）
│   └── api/
│       ├── auth.py        # /api/login/admin、/api/login/student、/api/logout
│       ├── admin.py       # /api/admin/*
│       └── student.py     # /api/student/*
├── requirements.txt
└── .env.example
```

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

## 业务口径落在哪

改口径时只需动这两处，接口层不用碰：

| 口径 | 位置 |
|---|---|
| `risk_level = max(课程级)`、`risk_score = 学分加权/2`、`avg_score` 剔除退学+缺考按 0 | `app/data/repo.py` · `_aggregate_students` |
| `warn_id = W + md5(stu\|course\|sem\|week)[:9]`（幂等） | `app/data/repo.py` · `make_warn_id` |
| 图裁剪（top N 学生 + 其课程，学生端 1 跳子图） | `app/data/repo.py` · `graph` / `student_subgraph` |
| 归因因子（规则产出，响应带 `source: "rule"`） | `app/services.py` · `build_factors` |
| 建议模板 | `app/services.py` · `build_suggestions` |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `WP_INTERIM` | 自动探测 | 指定 interim 数据目录 |
| `WP_DB_PATH` | `data/app.db` | 账号库（SQLite）路径 |
| `WP_SECRET_KEY` | 开发用固定值 | **生产必须换**，否则 token 可伪造 |
| `WP_TOKEN_HOURS` | 12 | token 有效期 |
| `WP_DEMO_ADMIN` | `admin` | 管理员账号名 |
| `WP_DEMO_ACCOUNTS` | 29 | 自动创建的学生账号数 |
| `WP_DEMO_PASSWORD` | 见 `.env.example` | 演示账号初始密码 |
| `WP_DEBUG` | true | 调试模式 |

## 已知限制

1. **没有数据库**：数据在进程内 pandas 里，启动全量加载。OULAD 全量（3 万学生 / 1065 万条 VLE）加载会占几百 MB，演示够用，真实并发部署要换 PostgreSQL 并预聚合。
2. **聚合是每次请求惰性算 + 内存缓存**：首次请求约 1~2 秒，缓存后实测首页 1.8ms / 学生列表 5.0ms / 全局图 7.8ms / 预警列表 2.4ms（200 人演示数据）。真实数据的量级没测过，别拿这组数字当 SLA。
3. **没有逐题数据**：知识点掌握度是代理指标（考核项按截止日期分桶），答辩时要说明。
4. **`/api/dkt/...` 知识点诊断未实现**：等阶段三 DKT 产物。
5. **推理层是规则**：`services.py` 的因子和建议都是规则产出，模型上线后只换实现、不动字段。
