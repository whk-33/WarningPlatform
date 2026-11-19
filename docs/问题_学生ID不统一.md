# 问题：风险曲线 404 —— 后端主数据跑在合成演示集上

> 提出方：后端　|　提出日期：2026-11-17　|　**状态：根因已定位（算法侧核查后修正），待补数据文件**
> 影响：管理员端「学生详情 → 周度风险曲线」这条链路演示不出来（必然 404）
> 结论一句话：**不是两套 ID 无法对齐，是后端根本没加载算法真实产物，读的是 200 人合成演示集。**

---

## 1. 现象（可复现）

管理员登录后，从「学生列表」点进任意学生详情，查该生的周度风险曲线：

```bash
TOKEN=...  # POST /api/login/admin

# 学生列表里的学生
curl -s "http://127.0.0.1:8000/api/risk/student/100001" -H "Authorization: Bearer $TOKEN"
# → {"code":404,"msg":"2014B 没有 100001 的风险记录","data":null}

# 风险数据里的学生（OULAD id）
curl -s "http://127.0.0.1:8000/api/risk/student/stu_100621" -H "Authorization: Bearer $TOKEN"
# → 200，返回 16 个周次的风险曲线
```

200 个学生**全部**是 404，不是个别数据缺失。

---

## 2. 根因（已实测到具体目录）

### 2.1 后端没有加载算法真实产物

`backend/app/data/loader.py` 按 `settings.interim_candidates` 的顺序找目录，命中第一个"表齐全"的就用：

| 优先级 | 目录 | 本机状态 |
|---|---|---|
| 1 | `algorithm/data/oulad/interim_demo`（算法真实产物，28,785 人） | ❌ **缺失** |
| 2 | `algorithm/data/oulad/interim` | ❌ 缺失 |
| 3 | `algorithm/data/interim`（**200 人合成演示残留**） | ✅ 命中 |

实测输出：

```
interim 命中: C:\Users\liucan\Desktop\WarningPlatform\algorithm\data\interim
实际 source: parquet | 学生数: 200 | 学期: ['2024F', '2025S']
姓名样例: 学生0096 / 学生0016 / 学生0031，班级全是「未分班」
```

`学生0016`、`未分班`、学号 100001–100200 连续无缺口——这些都是**合成数据特征**，OULAD 里没有这些人。

### 2.2 为什么真实产物不在机器上

`.gitignore` 第 60 行忽略了整个 `algorithm/data/oulad/`：

```
algorithm/data/oulad_raw/
algorithm/data/oulad/
algorithm/data/public/
```

所以 `git clone` / `git pull` **永远拉不到** `interim_demo/` 与 `processed_full_demo/`，必须单独拷。这件事之前没人交代过，后端就一直在跑合成集。

### 2.3 风险数据却是真数据

`risk_scores.parquet` 也不在仓库（同样被忽略），本机目前连它都没有，风险接口走的是 `docs/mock_api/*.json`（算法从真实 parquet 导出的样本，`stu_100621` 这类 OULAD ID）。

于是：**主数据 = 200 个合成学生；风险数据 = OULAD 真实学生 → 交集为 0**。

### 2.4 算法侧的两份产物本来是 100% 同源的

| 产物 | 规模 | `stu_id` 样例 |
|---|---|---|
| `oulad/interim_demo/student_info.parquet` | 32,593 行 / 28,785 人 | `stu_11391` … |
| `oulad/processed_full_demo/risk_scores.parquet` | 521,488 行 / 28,785 人 | `stu_11391` … |

交集 **28,785（100%）**，学期都是 `2013B / 2013J / 2014B / 2014J`，样例 `stu_100621` = 许浩然 / 软件工程 / 软工1401班 / 2014B。

也就是说：**只要把这两份文件放到位，问题自动消失，一行代码都不用改。**

---

## 3. 解法

> **2026-11-19 更新：本节的人工操作已经不需要了。** 真实数据包（约 26 MB）已提交进仓库，
> `config.py` 的探测链也改成「真实数据优先、200 人合成集垫底」，`git clone` 后直接就是 28,785 人，
> 不用拷文件、不用设环境变量、不用建目录联接。下面保留原记录，作为排查过程与备选方案的说明。

### 方案 A（当时采纳，现已被入库方案取代）：把算法产物拷到后端机器

`backend/app/config.py` 已经预留了环境变量，**代码不用动**：

```bash
# 1) 拷贝（走网盘/U 盘，不要指望 git），目标 D:\WarningPlatform\algorithm\data\oulad\
#    ├── interim_demo/                       6.7 MB，12 张表          ← 主数据
#    └── processed_full_demo/
#         ├── risk_scores.parquet           13.7 MB                  ← 风险分
#         └── graph_api.json                约 5 MB                  ← 图（有就一起拷）

# 2) 指向目录后重启
export WP_INTERIM=D:/WarningPlatform/algorithm/data/oulad/interim_demo
export WP_PROCESSED=D:/WarningPlatform/algorithm/data/oulad/processed_full_demo
# 或者直接在 backend/.env 里写这两行，改完重启 uvicorn

# 3) 体检（在 backend 目录下）
python scripts/verify_data.py
```

`WP_INTERIM` / `WP_PROCESSED` 在 `config.py:37` 与 `config.py:48` 已支持（`.env.example` 里也有注释示例），设了就会优先于默认路径。

> **必须成套换**：现在图产物也是 200 人那套（`processed/graph_nx.json`，1141 节点 / 200 个学生）。
> 只换主数据和风险分、不换图，会出现新错配——主数据 28785 人，图里只有 200 个学生节点，图页面点谁都对不上。
> 好消息是 `config.graph_candidates` 第一候选就是 `processed_full_demo/graph_api.json`，把文件放进去会自动优先使用。
> **所以向算法要文件时要多问一句：`processed_full_demo/` 里有没有 `graph_api.json`？** 没有就需要它生成一份。

### 方案 B（演示必须小数据集时）：生成 200 人自洽子集

由算法侧按同一批 200 个 `id_student` 从两份产物里过滤，产出 `interim_demo_200` + `risk_scores_200`，两张表 ID 完全一致，不用重跑模型。后端同样只改环境变量。

### ❌ 明确不要做

- **不要重跑 `risk_scores` 把 `stu_id` 改成 `stu_100001`**：那是给合成学生套真实风险，答辩解释不了。
- **不要手写 ID 映射**：两拨人没有对应关系，写出来就是捏造。
- **不要再参考 `algorithm/data/interim/id_mapping.csv`**（200 行）和 `algorithm/data/raw/id_crosswalk.csv`：它们是合成演示残留，`stu_num_raw` 已经是重编号后的值，不是 OULAD 原始 ID。真实的映射在 `oulad/interim_demo/id_mapping.parquet`（28,785 行，`stu_id = stu_<id_student>` 恒等）。

---

## 4. 验收（换数据后三条命令）

```bash
# 1) 规模对不对：应该是 28785，不是 200
curl -s http://127.0.0.1:8000/api/health
# 期望：students=28785，semesters 含 2013B/2013J/2014B/2014J

# 2) 风险接口本身还通
curl -s "http://127.0.0.1:8000/api/risk/student/stu_100621" -H "Authorization: Bearer $TOKEN"
# 期望：200 + 16 周曲线

# 3) 同一个人能被两个接口识别（这才是要证明的）
curl -s "http://127.0.0.1:8000/api/admin/student/detail?student_id=stu_100621" -H "Authorization: Bearer $TOKEN"
# 期望：许浩然 / 软件工程 / 软工1401班 —— 与风险数据里的 stu_100621 是同一个人
```

---

## 5. 在这次修正之前，先前的错误判断（存档，避免重犯）

| 先前的判断 | 实际情况 |
|---|---|
| 200 人是 OULAD 抽样后重编号 | ❌ 是后端合成演示集（`loader.py` 的 `_demo_tables()` 与 `algorithm/data/interim` 那套残留） |
| 重编号前的 `id_student` 已丢失 | ❌ 一直在 `oulad/interim_demo/id_mapping.parquet`，28,785 行且是恒等映射 |
| 仓库里没有任何对照表，后端无法修 | ❌ 对照表存在；真正的问题是**这份产物没被拷到后端机器**，不是缺对照 |

教训：查 ID 问题时，先确认**接口实际喂的是哪份数据**（`/api/health` 的 `data_source` 与 `students` 数量），再去比对 ID 空间——否则会拿合成集和真实集做对比，得出"无法对齐"的错误结论。

---

## 6. 附：在此之前的前端兜底（已上线）

| 角色 | 做法 |
|---|---|
| 后端 | `/api/admin/risk/list`、`/api/risk/student/{stu_id}` 已补 `student_name` / `major_name` / `class_name`（v1.2），风险榜可独立成表 |
| 前端 | 「本周风险榜」点行 → 弹窗看曲线（用风险数据自己的 `stu_id`）；旧入口 404 显示「该生暂无周度风险数据」空态 |
| 演示 | 学生端用 `riskdemo` 账号（自动绑曲线最完整的学生） |

换数据之后这些兜底都不用动，`student_meta.json` 会自动被主数据的真实姓名覆盖（`_load_meta()` 优先级已按此设计）。

---

## 参考

- 数据源探测顺序：`backend/app/config.py` 的 `interim_candidates`、`processed_candidates`
- 后端加载逻辑：`backend/app/data/loader.py` 的 `find_interim()` / `load_all()`
- ID 现状限制：`docs/接口约定.md` §九 已知限制 11、12
- 风险接口契约：`docs/阶段四_风险接口契约.md`

---

## 状态更新（2026-11-17）：已解决

数据到位后按方案 A 接入，结论反转：

| 项 | 结果 |
|---|---|
| 主数据 | `interim_demo`，28,785 学生，姓名/专业/班级均为真实值（如「徐逸飞 / 计科1301班」） |
| 风险分 | `risk_scores.parquet`，521,488 行，4 个学期 |
| 两套 ID 交集 | **28,785 = 学生总数**，完全同源 |
| 普通学生账号查自己 | 5/5 返回 `200` + 16 周曲线 |
| 姓名补全 | `_load_meta` 已支持 `stu_id` / `stu_num` / `student_no` 三套写法，实测有值 |

`python scripts/verify_data.py` 判定通过（退出码 0）。**唯一新发现的两个口径问题**已写入
`docs/接口约定.md` 第十节：`week=15` 是期末结算周（默认周改为第 8 周）、`avg_score` 因剔除退学可能为 `null`。
