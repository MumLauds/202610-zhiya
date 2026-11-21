# 知芽 Web

Vue 3 + Vite 前端。桌面端 `src/` 提供家长首页、绘本馆、共读、成长记录、儿童档案、绘本工坊和内容管理工作台。移动端 `mobile/` 提供手机亲子共读和成长记录。[移动阅读端说明](mobile/README.md)

## 本地开发

在 `frontend/` 使用 Node.js 20.19+（20.x）、22.12+（22.x）或 24+：

```powershell
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173/` 进入桌面端，打开 `/mobile.html` 进入移动端。Vite 将 `/api` 代理至本机 FastAPI 的 8000 端口。后端设置了 `Secure` 会话 Cookie。跨设备登录、同源 API 和手机麦克风联调请使用受信任的 HTTPS。[环境启动](../README.md#本地启动) · [本机 HTTPS 部署预演](../docs/testing/g15/local-https/README.md)

默认开发模式提供桌面设计预览和移动端演示书架。移动端可以切换到“实机”，使用当前账号的书目和阅读会话。运行 `npm run dev -- --mode public` 可在本机查看公网模式，两端书目从后端读取，移动端从登录和档案选择进入共读。

## 检查与构建

| 命令 | 用途 |
| --- | --- |
| `npm run check` | 运行 Vitest、移动端 Node.js 测试及公开站点构建，与 GitHub Actions 一致。 |
| `npm test` | 单独运行组件、状态和 API 测试（Vitest）。 |
| `npm run test:mobile` | 单独运行移动端会话状态机和写入恢复测试（Node.js）。 |
| `npm run build:public` | 构建部署到公网的站点。在 `dist/` 生成 `index.html` 和 `mobile.html`，排除演示绘本数据和 `demo/` 媒体。 |
| `npm run build` | 构建含演示数据的本地版本，同样写入 `dist/`，用于本地演示与联调。 |
| `npm run build:html-review` | 重新生成可双击打开的移动离线审查页 `html-review/index.html`，媒体从相邻的 `public/demo/` 读取。 |

公网部署使用 `npm run build:public` 生成的 `dist/`。[离线审查页说明](html-review/README.md) · [香港站部署说明](../infra/hk/README.md)

## 与后端的交互

- `src/api/` 和 `mobile/api/` 封装接口。请求携带会话 Cookie，写请求从 `zhiya_csrf` 读取 `X-CSRF-Token`。绘本馆通过 API-008 分页加载，详情从 API-009 读取。正式绘本的作者、适龄范围和配图来自接口。
- 共读按服务端会话修订号提交翻页、答题、分支选择和结束操作，提交结果不确定时先通过 API-013 核对。桌面绘本馆点击中间绘本即可打开并自动开始家长共读。到达最后一页后可以确认完成，随后在弹窗查看共读小结；生成中的小结自动刷新。完成后可按实际访问顺序回看；小结生成失败时，仍可查看已保存的事实摘要。
- 桌面共读的主动问答走 API-016，首题和理解反馈走 API-017，分支选择走 API-018。录音通过 API-022 转写，家长可以修改文字或手动输入，再确认提交。朗读与问答音频由 API-023 合成，通过 API-024 播放。
- 从绘本详情的「设置儿童模式」进入配置，可为未开始的共读设置儿童阅读时限和授权绘本。桌面端负责家长授权、受限交接及密码解锁。绘本工坊支持生成原创或续写草稿、家长审核及继续阅读。
- 儿童档案与阅读记录由后端保存；桌面端收藏、阅读计划和家长昵称使用浏览器本地存储。阅读计划按儿童档案记录登录后在本机桌面阅读页前台停留的时间，以及本机完成的绘本和阅读天数，首页显示各项目标与完成量。

[接口契约](../docs/design/api-contract.md) · [系统联调记录](../docs/integration.md)
