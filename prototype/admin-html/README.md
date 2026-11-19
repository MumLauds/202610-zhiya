# 知芽后台管理原型

## 交付入口

| 版本 | 链接 |
|---|---|
| HTML 后台在线预览 | [打开 HTML 原型](https://htmlpreview.github.io/?https://github.com/MumLauds/202610-zhiya/blob/main/prototype/admin-html/index.html) |
| HTML 后台源码 | [打开 index.html](./index.html) |
| Figma 后台插件 | [打开插件目录](./figma-plugin/) |
| Figma 插件 manifest | [打开 manifest.json](./figma-plugin/manifest.json) |
| Figma 在线主原型 | [打开 Figma 文件](https://www.figma.com/design/cjSoo0RkFSTaBbJiwNXs8D/%E7%9F%A5%E8%8A%BD%EF%BC%8C%E6%89%8B%E6%9C%BA%E7%AB%AF%E5%8E%9F%E5%9E%8B?node-id=10-7812&t=Fx6DpqmSlAP1NVLA-1) |


直接双击 `index.html`，使用 Edge 或 Chrome 打开，无需安装依赖。

## 页面

- 工作台
- 绘本管理
- 版本审核
- 领域知识库
- 接口与权限

## 交互

- 左侧导航切换页面
- 绘本搜索、状态筛选和年龄筛选
- 知识条目搜索、审核状态与类别筛选
- 点击绘本打开详情抽屉
- 审核通过后更新发布状态
- 新增绘本、新增知识和版本审核弹窗
- 发布与审核状态保存在浏览器本地

## 数据来源

数据来自 `MumLauds/202610-zhiya` 仓库的 `data/seed/story_versions.json`、`story_pages.json`、`knowledge_items.json`，共 21 个绘本版本（均为 `DRAFT`）、19 张真实封面和 20 条领域知识。
