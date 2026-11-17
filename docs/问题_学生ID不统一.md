# 问题：风险分数据的学生 ID 与主数据不统一

> 提出方：后端　|　提出日期：2026-11-17　|　需要：算法侧确认 + 提供对照关系
> 影响：管理员端「学生详情 → 周度风险曲线」这条链路目前演示不出来（必然 404）

---

## 1. 一句话结论

系统里存在**两套互不相交的学生 ID 数字空间**，格式看起来一样（都是 `stu_` + 数字），但一套是 `100001–100200`，另一套是 OULAD 的 `88758 / 100621 / 997948`，**交集为 0**，而且仓库里**没有任何一张表能把它们对起来**，后端无法自己修。

---

## 2. 现象（可复现）

管理员登录后，从「学生列表」点进任意学生详情，查该生的周度风险曲线：

```bash
TOKEN=...  # POST /api/login/admin

# 旧学生列表里的学生（主数据学号）
curl -s "http://127.0.0.1:8000/api/risk/student/100001" -H "Authorization: Bearer $TOKEN"
# → {"code":404,"msg":"2014B 没有 100001 的风险记录","data":null}

# 风险数据里的学生（OULAD id）
curl -s "http://127.0.0.1:8000/api/risk/student/stu_100621" -H "Authorization: Bearer $TOKEN"
# → 200，返回 16 个周次的风险曲线
```

200 个学生**全部**是 404，不是个别数据缺失。

---

## 3. 证据（四条，均为实测）

### 3.1 主数据：200 人，学号是连续人工编号

```
students 表：stu_num = 100001 … 100200，共 200 个，无缺口
            student_no 与 stu_num 同值
            stu_id     = "stu_" + stu_num
```

学号连续无缺口，说明这批数据是**抽取后重新编号**的，不是 OULAD 原号。

### 3.2 风险分：7804 人，stu_id 是 OULAD 原始 `id_student`

`risk_scores.parquet`（阶段四产物，14 MB，未入仓）里的 `stu_id` 形如（样例取自 `docs/mock_api/admin_list.json`，该文件是算法侧从真实 parquet 导出的样本）：

```
stu_88758    stu_100621    stu_103891    stu_997948    stu_513796
```

都是 `stu_` + OULAD 的 `id_student`（5–6 位，区间 88758–997948）。规模 7804 人（见 `docs/mock_api/admin_stat.json` 的 `total`）。

**与主数据 200 人的交集：0。**

### 3.3 其他算法产物用的都是主数据那套

| 产物 | stu_id 样例 | 属于哪套 |
|---|---|---|
| `tft_weekly.parquet` | `stu_100001` | 主数据 ✅ |
| `dkt_sequences.parquet` | `stu_100001` | 主数据 ✅ |
| `deepsurv_events.parquet` | `stu_100096` | 主数据 ✅ |
| `graph/dim_student.parquet`（图节点 1141 个） | `stu_100096` | 主数据 ✅ |
| **`risk_scores.parquet`** | **`stu_88758`** | **OULAD ❌** |

也就是说：**只有阶段四的风险分这一份产物没有做 ID 归一**，其余全部是主数据 ID。这也是为什么前端只在风险曲线上遇到 404，其他页面都正常。

### 3.4 仓库里两张映射表都缺 OULAD 原始 ID

```
algorithm/data/interim/id_mapping.csv
  stu_num_raw, stu_num_offset, stu_uuid, stu_id
  100096, 1100096, bdd640fb-0667-..., stu_100096

algorithm/data/raw/id_crosswalk.csv
  stu_num_raw, stu_num_offset, stu_uuid, unified
```

两张表都只有「重编号后的号 → 各种派生表示」，**没有一列是 OULAD 的 `id_student`**。`stu_uuid` 是随机 UUID，也无法反推。

所以后端拿不到可靠对照，**手写映射等于凭空捏造**——会把两个真实学生绑成一个人，答辩被追问时无法解释。这是后端目前不做 ID 对齐的唯一原因。

---

## 4. 需要算法侧做的（三选一，按推荐度排序）

### 方案 A（推荐）：重跑 `risk_scores` 时把 stu_id 换成主数据 ID

抽样/重编号那一步既然已经存在（`id_crosswalk` 就是它的产物），只要在当时把 `id_student → stu_num` 一并落库，风险分就能直接用主数据 `stu_id` 落盘。

- 产出：`risk_scores.parquet` 的 `stu_id` 变成 `stu_100001` 这类
- 后端改动：零（现有 5 个接口自动就能和其余 15 个接口对上）
- 需要确认：7804 人与这 200 人是否同一批人（见 §5 问题 Q3）

### 方案 B（退一步）：单独给一张对照表

不重跑也行，只要给一份 CSV 放仓库：

```csv
id_student,stu_num,stu_id
100621,100016,stu_100016
...
```

- 演示集只有 200 人，200 行就够
- 后端加一层转换（读该表，风险接口出入口做映射），约半天
- 代价：多一张要长期维护的表

### 方案 C（如果两拨人完全无关）

若 7804 人与这 200 人不是同一批（比如 200 人是另造的演示数据），那就无法统一，只能二选一：

- 把演示集换成风险分覆盖的学生（主数据、图、DKT 全部要重跑）；
- 或接受「风险页独立演示」，不进学生详情弹窗（后端已按这个方案做了兜底，见 §6）。

---

## 5. 需要算法侧回答的三个问题

| # | 问题 | 为什么要问 |
|---|---|---|
| Q1 | 这 200 人是从 OULAD 里抽的吗？抽的是哪 200 个 `id_student`？ | 决定 A/B 方案可不可行 |
| Q2 | 重编号前的 `id_student` 还存在吗？在哪台机器/哪个目录？ | 若还在，B 方案 10 分钟能补完 |
| Q3 | `risk_scores` 的 7804 人与这 200 人**是不是同一批人**？ | 若是子集关系，A 方案最干净；若是两拨人，只能走 C |

> 提示：`algorithm/data/raw/` 下的表已经是重编号后的（`stu_num` 从 100001 起），OULAD 原表不在仓库里，所以在仓库内部查不到答案，需要算法同学确认。

---

## 6. 在此之前，后端/前端的临时处理（已上线，不用等）

| 角色 | 做法 |
|---|---|
| 后端 | `/api/admin/risk/list` 与 `/api/risk/student/{stu_id}` 已补 `student_name` / `major_name` / `class_name`（v1.2），风险榜可以独立成表，不依赖旧学生列表 |
| 前端 | 「本周风险榜」点行 → 详情弹窗看曲线（用风险数据自己的 `stu_id`）；旧入口进弹窗若 404，显示「该生暂无周度风险数据」空态 |
| 演示 | 学生端统一用 `riskdemo` 账号（自动绑定曲线最完整的学生） |

这些只是绕过，**根因仍在上面三选一**。

---

## 7. 改完怎么验收

```bash
# 1) 风险分的 stu_id 应该和主数据同一套
#    路径按 WP_PROCESSED 实际指向的目录替换
python -c "import pandas as pd; \
  print(pd.read_parquet('algorithm/data/oulad/processed_full_demo/risk_scores.parquet')['stu_id'].head())"
# 期望：stu_100001 这类；若还是 stu_100621 → 没改

# 2) 用主数据学号能查到风险曲线
curl -s "http://127.0.0.1:8000/api/risk/student/100001" -H "Authorization: Bearer $TOKEN"
# 期望：200 + weeks 数组；404 → 仍未对齐

# 3) 风险榜与学生列表能对上同一个人
curl -s "http://127.0.0.1:8000/api/admin/risk/list?size=1" -H "Authorization: Bearer $TOKEN"
curl -s "http://127.0.0.1:8000/api/admin/student/detail?student_id=100001" -H "Authorization: Bearer $TOKEN"
# 期望：两边的 student_name / stu_id 指向同一个学生
```

---

## 参考

- ID 现状与限制：`docs/接口约定.md` §九 已知限制 11、12
- 风险接口契约：`docs/阶段四_风险接口契约.md`
- 映射表现状：`algorithm/data/interim/id_mapping.csv`、`algorithm/data/raw/id_crosswalk.csv`
