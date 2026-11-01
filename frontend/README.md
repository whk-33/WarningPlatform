# frontend — 前端工程

> 前端负责人专属目录，与 backend / algorithm 物理隔离，日常开发几乎不会冲突。

## 技术栈

- Vue 3 + Vite
- Element Plus（UI 组件库）
- ECharts（统计图表）
- AntV G6（图可视化）
- Vue Router + Axios

## 本地运行

```bash
cd frontend
npm install
npm run dev
```

> 当前所有数据均为前端 mock，接口层见 `src/api/`，待后端 `docs/接口约定.md` 确定后替换。

## 开发约定

- 所有前端代码放此目录下
- 接口字段以后端 `docs/接口约定.md` 为准
- 可视化组件（如子图渲染）与算法输出格式对齐

## 分支命名

```
feat/frontend-功能简称
fix/frontend-问题
```
