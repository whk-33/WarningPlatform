# 大学生学业关联分析与预警平台

3 人小组项目（前端 / 后端 / 算法），采用 **主分支开发模式（Trunk-Based Development）**。

## 目录结构

```
WarningPlatform/
├── frontend/     # 前端工程（前端负责人专属）
├── backend/      # 后端工程（后端负责人专属）
├── algorithm/    # 算法模型 + 推理代码（算法负责人专属）
├── docs/         # 需求文档、设计稿、接口约定
├── .gitignore
├── .gitattributes # Git LFS 大文件配置
└── README.md
```

## 协作规范

- **禁止直接往 main 推送代码**，所有改动必须通过 PR 合并
- 分支命名：`feat/模块-功能`、`fix/模块-问题`、`docs/内容`
- 提交信息：`feat(模块): 描述`、`fix(模块): 描述`
- 合并方式：**Squash and merge**（压缩合并）
- 分支生命周期不超过 2 天，功能粒度要小

## 阶段进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段一 | 数据基座搭建与 pipeline 验证 | 进行中 |
