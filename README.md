# 大学生学业关联分析与预警平台

3 人小组项目（前端 / 后端 / 算法），采用 **主分支开发模式（Trunk-Based Development）**。

## 目录结构

```
WarningPlatform/
├── frontend/     # 前端工程（前端负责人专属）
├── backend/      # 后端工程：FastAPI（后端负责人专属）
├── algorithm/    # 算法模型 + 推理代码（算法负责人专属）
├── docs/         # 需求文档、设计稿、接口约定
├── .gitignore
├── .gitattributes # Git LFS 大文件配置
└── README.md
```

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Vue 3 + Vite + AntV G6 |
| 后端 | **Python 3.11 + FastAPI + pandas** |
| 算法 | Python + pandas + PyTorch（Parquet 产出，直接被后端读取） |
| 鉴权 | JWT + 角色（admin / student） |

后端启动见 [`backend/README.md`](backend/README.md)，接口字段见 [`docs/接口约定.md`](docs/接口约定.md)。

## 一键部署（Docker Compose）

前置：本机装好 Docker Desktop。仓库里的大文件由 Git LFS 管理，首次克隆后先执行 `git lfs pull`，
否则 `algorithm/data` 下是 LFS 指针文件，后端读不到 Parquet。

```bash
# 可选：在项目根目录建 .env 覆盖默认配置
# WP_SECRET_KEY=换成你自己的密钥
# WP_DEMO_PASSWORD=换成你自己的演示密码

docker compose up -d --build
```

- 访问 <http://localhost:8080>；nginx 把 `/api` 反代到 `backend:8000`，前后端同源，不用处理跨域
- 后端只读挂载 `./algorithm`、`./docs`，`backend-data` 数据卷保存账号库与干预记录（重建容器不丢）
- 健康检查走 `/api/health`，`docker compose ps` 显示 `healthy` 即启动完成
- 改代码后重新执行 `docker compose up -d --build`；停止用 `docker compose down`（加 `-v` 会连数据卷一起删）

演示账号：`admin / wp123456`、`riskdemo / wp123456`（详见 [`docs/接口约定.md`](docs/接口约定.md) §八）。
不走 Docker 的本地开发按 `backend/README.md`、`frontend/README.md` 启动。

## 协作规范

- **禁止直接往 main 推送代码**，所有改动必须通过 PR 合并
- 分支命名：`feat/模块-功能`、`fix/模块-问题`、`docs/内容`
- 提交信息：`feat(模块): 描述`、`fix(模块): 描述`
- 合并方式：**Squash and merge**（压缩合并）
- 分支生命周期不超过 2 天，功能粒度要小

## 阶段进度

> 最后更新：2026-11-19。详细验收标准见 [`docs/PRD_学业关联分析与预警平台.md`](docs/PRD_学业关联分析与预警平台.md) §七–§八。

| 阶段 | 内容 | 状态 | 依据 |
|---|---|---|---|
| 阶段一 | 数据基座搭建与 pipeline 验证 | ✅ 已完成 | T0–T4 脚本可一键重跑，验收项全过（2026-11-02 合并） |
| 阶段二 | 模型训练（6 个模型） | ✅ 已完成 | 指标全达标（**test 集**）：TFT w8 AUC 0.806 / w14 0.852、HAN 0.921、DKT 0.812，均超验收线；DeepSurv C-index 0.640 仅作趋势参考 |
| 阶段三 | 可解释性与系统集成 | 🟡 进行中（约 60%） | 归因/因果已有产出；前后端联调中（PR #16 / #17 未合）；知识点诊断页面未开始 |
| 阶段四 | 真实数据适配与上线 | 🟡 进行中（约 70%） | 28,785 人全量数据已接入并入库、真实数据重训完成；容器化配置已写但未上服务器 |

### 当前收尾项

| 事项 | 状态 |
|---|---|
| 后端风险接口（契约 v1.4） | PR [#17](https://github.com/whk-33/WarningPlatform/pull/17) 待合并 |
| 前端接入风险接口 + 干预记录 + 部署脚本 | PR [#16](https://github.com/whk-33/WarningPlatform/pull/16) 待拆分合并 |
| 知识点诊断 `/api/dkt/*` | 未开始（`kp_mastery` 表已建） |
| 推理桥（不在 `risk_scores` 里的新学生） | 未开始，当前一律 404 |
