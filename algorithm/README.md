# algorithm — 算法模型与推理代码

> 算法负责人专属目录。阶段一产出：数据 pipeline、异质图、TFT 时序特征、DKT 序列、DeepSurv 事件表。

## 阶段一产出

| 模块 | 说明 |
|---|---|
| `src/data_pipeline/00_generate_mock.py` | Mock 数据生成器（故意造脏 + 注入信号） |
| `src/data_pipeline/02_clean.py` | 清洗 + ID 打通 |
| `src/data_pipeline/05_build_graph.py` | 异质图构建（5 节点 / 9 边） |
| `src/data_pipeline/03_weekly_features.py` | TFT 时序特征 |
| `src/data_pipeline/04_knowledge_map.py` | 知识点体系 + DKT 序列 |
| `tests/` | pytest 30 条验收 |
| `configs/schema.yaml` | 节点/边/字段单一事实源 |
| `data/` | raw（原始）/ interim（清洗）/ processed（模型输入） |

## 验收标准

```powershell
python -m pytest tests/ -v        # 30/30 全绿
python _check_stage1.py           # 数据完整性检查
```

## 分支命名

```
feat/algorithm-功能简称
fix/algorithm-问题
```
