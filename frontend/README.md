# 知芽 Vue 阅读端

正式 Vue 3 + Vite 阅读组件，基于 `prototype/mobile-html` 的视觉原型重写。默认进入演示模式；切换到“实机接口”后可连接同域后端。

## 运行

```powershell
cd C:\Users\Willa\Desktop\知芽-html-draft\frontend
npm install
npm run dev
```

默认 Vite 将 `/api` 代理到 `http://127.0.0.1:8000`。生产构建：

```powershell
npm run build
npm test
```

## 已落地的能力

- `ReaderView.vue`：可复用的完整阅读页编排。
- `ReadingText.vue`：逐字拼音与朗读高亮。
- `PageAudioPlayer.vue`：页面朗读，切页自动停止。
- `InteractionCard.vue`：G10 结果、来源、状态与操作入口。
- `VoiceAnswerDialog.vue`：G08 录音、转写、家长确认、修正、重录和手动输入回退。
- `ReadingSummary.vue`：仅展示服务端实际返回的阅读事实与小结。
- `ReadingSessionMachine`：创建、恢复、STARTED、PAGE_CHANGED、结束、幂等键、revision 和 409 冲突恢复。

## 接口映射

| 能力 | 接口 |
| --- | --- |
| 创建 / 恢复 / 读取会话 | API-011 / API-013 |
| 开始与向后翻页 | API-015 `STARTED` / `PAGE_CHANGED` |
| 共读问题与反馈 | API-016 / API-017 |
| 结束阅读 | API-019 |
| 录音转写 | API-022 |
| 回答与页面朗读音频 | API-023 / API-024 |

## 关键行为

### revision、幂等与冲突

每次写操作生成一个 `clientEventId`，并携带当前 `expectedRevision`。同一次自动重试保持原键和原请求体。

- `REQUEST_IN_PROGRESS`：等待 `Retry-After` 后使用原键重试。
- `SESSION_CONFLICT` / `IDEMPOTENCY_CONFLICT`：先调用 API-013 恢复最新会话，再提示用户。
- `REQUEST_OUTCOME_UNKNOWN`：不自动重放外部请求，先读取最新会话。

### G08 转写

录音使用 `MediaRecorder`。转写请求为 multipart：

```text
file, readingSessionId, pageNumber, revision
```

识别结果必须经家长查看和修改后才能确认。麦克风拒绝、转写失败或浏览器不支持时，可切换到手动输入。

### G10 结果与音频

只渲染接口实际返回的 `outputText`、`strategyType`、`status` 和 `sources`。回答音频通过 `interactionEventId` 请求 API-023，不使用静态回答或伪造评分。

### 朗读高亮

当前没有字级时间戳，因此高亮按音频时长比例模拟，并在界面上明确标注“进度模拟”。

## 契约边界

- G07 当前后端实现只接受 `STARTED` 和 `PAGE_CHANGED`，因此实机模式暂不实现向后翻页。
- 当前接口没有“孩子答案文本持久化”字段。家长确认后的文本留在本次页面状态中，提交给 API-017 的是 `UNDERSTOOD` 反馈。
- 不展示固定互动次数、能力评分或“内容安全检查通过”等未经接口验证的结论。
- 演示模式的转写、问题和音频均为界面联调数据，界面会明确标记“演示结果”。

## 移动端画布
桌面浏览器中固定以 430px 手机画布预览；在 680px 以下视口中自动铺满真机屏幕。

## HTML 审查版

生成可直接双击打开的静态审查版：

```powershell
npm run build:html-review
```

输出位于 `frontend/html-review/index.html`。该版本内联了 Vue 构建后的 CSS 和 JavaScript，绘本图片与音频使用相对路径，和 Vue 前端保持同一套界面与演示交互。
