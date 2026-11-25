# H05 硬件 DEMO 数据与 Mock 接口契约

## 0. 文档说明

| 项目 | 内容 |
| --- | --- |
| 任务 | H05：ESP32-S3 4.3C 硬件端 DEMO 数据、Mock 测试接口与自动化测试 |
| 设备 | Waveshare ESP32-S3-Touch-LCD-4.3C，SKU 33799，无外壳版；800×480、16 MiB Flash、8 MiB PSRAM |
| 固件基线 | ESP-IDF v5.5.5；H02 正在实现 LCD、GT911 和 LVGL 底层适配 |
| UI 依据 | `hardware/docs/ui-flow.md`（H04） |
| 素材依据 | `hardware/docs/media-format.md`、`hardware/assets/generated/assets-manifest.json`（H03） |
| 数据依据 | `data/seed/storybooks.json`、`story_versions.json`、`story_pages.json`、`cover_mapping.json`（2026-11-24 基线） |
| 接口依据 | `docs/design/api-contract.md`、`docs/design/api-contract-v0.4.json`、`backend/app/api/v1/reading_sessions.py` |
| 本文版本 | 2026-11-25，H05 审查修复版 |
| 适用范围 | 本地 DEMO、离线自动化测试、H06 UI 联调准备；不替代真实后端、设备认证、真机解码或上板验收 |

本文描述的是**本地 Mock 层**，不是新增正式 API 契约。所有响应和 fixtures 都标记 `demoOnly=true`；不得把 Mock 的成功结果写成真实后端联调成功，也不得把 H03 本地封面路径写成 API-024 的受控媒体 `resource_key`。

## 1. 目的、范围与限制

### 1.1 目的

H05 让 H06 在没有真实后端、没有云端 ASR/TTS/LLM、没有 ESP32 实板的情况下，能够重复完成以下链路：

```text
开机 → DEMO 首页 → 绘本书架 → 绘本详情 → 开始阅读 → 翻页 → AI 互动状态展示 → 结束阅读
```

同时提供：

- 由仓库真实种子和 H03 manifest 自动生成的数据，而不是手写 20 条记录；
- 可注入 401/403/404/409/422/429/503/504 和空数据、缺封面的 Mock HTTP 服务；
- 可重复的会话状态和幂等行为，用于验证 H04 数据流；
- 不依赖 Pillow、真实模型或实板的离线测试。

### 1.2 明确不负责

- 不绘制 LVGL 页面，不实现触摸屏驱动；这些属于 H02/H06。
- 不实现 API-022 的真实 multipart ASR，不实现 API-023 的真实 TTS，不实现 API-024 的媒体读取。
- 不模拟正式设备认证、家长 Cookie/CSRF、儿童档案服务端授权；H05 的 401/403 只是错误注入。
- 不生成真实录音、真实音频、真实模型回答、真实阅读报告或模型评测结果。
- 不把 `DRAFT` 联调绘本纳入设备书架；不把封面当成 104 页正文插画；不把 H05 的 `PUBLISHED` 标记当作在线发布授权。
- 不验证 JPEG 在 ESP32-S3/LVGL 上的解码兼容性；该结论必须由 H06 实板确认。

## 2. 数据来源与转换规则

### 2.1 输入

`hardware/mock/build_fixtures.py` 只读以下文件：

| 文件 | 用途 |
| --- | --- |
| `data/seed/storybooks.json` | 原始绘本记录与标题、年龄段、主题 |
| `data/seed/story_versions.json` | 版本、`storybook_id`、`version_no`、`status`、授权/年龄字段 |
| `data/seed/story_pages.json` | 正文、`page_no`、`default_next_page_number`、原始 `image_ref` |
| `data/seed/cover_mapping.json` | 20 条书号、`storybook_id`、封面源路径 |
| `hardware/assets/generated/assets-manifest.json` | H03 的 THUMBNAIL/DETAIL 资源、SHA-256、尺寸和 `devicePath` |

源文件只读；构建过程不会重命名、覆盖、删除或重新压缩任何原图。

### 2.2 选取规则

- 只选 `cover_mapping.json` 中的 20 条原创绘本，`book_no` 为 `B-002`～`B-021`。
- 最新种子包含 20 本 `PUBLISHED` 原创绘本和 1 本《逃家小兔》6 页 `DRAFT` 联调绘本；H05 排除该 DRAFT 绘本，不按数组下标或状态文字硬编码。
- 通过 `story_versions.json` 的 `storybook_id` 查找**恰好一个**版本；不按数组下标，也不假设 `storybookId == storyVersionId`。
- 选中版本必须为 `PUBLISHED`；构建器从种子读取真实状态并写入 fixtures，不强制原创绘本为 `DRAFT`。
- 每条详情页必须从 1 开始连续编号，最后一页的 `default_next_page_number` 必须为 `null`。
- 只使用种子已有的正文和页面数；不编造 104 页之外的分页正文或插画。
- `demoOnly=true` 和 `notice` 明确标识本地演示；`PUBLISHED` 仅表示种子状态，不代表在线发布授权、家长确认或 API-024 媒体访问权。
- `coverRef` 和 `imageRef` 输出为 `null`。H03 的本地封面只放在 `localCover`，不让它看起来像服务端受控媒体键。
- `story-details` 中每页的 `imageStatus` 为 `MISSING_INNER_ILLUSTRATION`，`localPageAsset=null`；H06 使用占位图，不用封面顶替正文插画。

### 2.3 结果计数

实际构建输出：

| 指标 | 值 |
| --- | --- |
| 原创绘本 | 20 本（`storybookId 2～21`） |
| 原创正文 | 104 页 |
| 年龄分布 | 3～4 岁 7 本、4～5 岁 7 本、5～6 岁 6 本 |
| 封面资源引用 | 40 个（20 本 × THUMBNAIL/DETAIL） |
| 版本状态 | 20 个均为 `PUBLISHED`（源种子真实状态，`demoOnly=true`） |
| 联调草稿 | 1 本《逃家小兔》，`storybookId=1`、6 页、`DRAFT`，H05 不选 |
| 原始页面总数 | 110 页（含联调草稿 6 页）；H05 只取 104 页 |
| 内页资源 | 0 个；`imageRef=null`、`localPageAsset=null`，等待独立 H03 内页任务 |
| fixtures 文件 | 5 个，共 98,154 B |

H03 清单的 `assetBytes=806,090`、`sourceBytes=117,248,465`、`compressionRatio=0.006875` 只描述文件体积，不等于解码后的内存。H05 不重复做 H03 的图像审计。

### 2.4 确定性和源文件可追溯

- JSON 使用 UTF-8、`sort_keys=True`、2 空格缩进和固定 LF 换行。
- 通过同目录临时文件 + `os.replace` 写出，避免半截文件。
- `storybooks.json` 和 `story-details.json` 根节点的 `sourceFiles` 记录 5 个输入文件的仓库相对路径，`sourceDigest` 固定为 `sha256-utf8-lf-v1`。
  - 规范：按无 BOM 的 UTF-8 解码文本，先将 `CRLF` 和单独的 `CR` 归一为 `LF`，再对规范化文本的 UTF-8 字节计算 SHA-256。
  - 该规范只消除 Git/Windows 检出造成的换行差异，不删除空白、不重排 JSON、不忽略内容；其他文本变化仍会导致 `--check` 失败。
  - H03 JPEG 等二进制资源仍按原始字节计算 SHA-256。
- `python hardware/mock/build_fixtures.py --check` 只对比重新计算的内容，不写入。
- 同一输入重复生成应得到相同业务内容；`requestId` 只在 HTTP 响应中随机生成，不进入 fixtures。

## 3. Fixtures JSON

所有路径使用仓库相对 POSIX 路径；不写开发者电脑绝对路径。

`storybooks.json` 只承载书架元数据，不包含 `pages` 或 `branches`；完整分页和正文只从 `story-details.json` / `GET /api/v1/story-versions/{id}` 读取，避免书架响应携带全部正文。

### 3.1 `storybooks.json`：书架元数据

根节点：

```json
{
  "schemaVersion": "1.0",
  "kind": "zhiya-hardware-demo-storybooks",
  "notice": "本地 DEMO 数据……",
  "demoOnly": true,
  "sourceDigest": "sha256-utf8-lf-v1",
  "sourceFiles": [
    {"path": "data/seed/storybooks.json", "sha256": "……"}
  ],
  "items": []
}
```

每个 `items` 条目（字段节选）：

```json
{
  "storybookId": 2,
  "storyVersionId": 2,
  "versionNumber": 1,
  "title": "小狐狸的雨伞",
  "status": "PUBLISHED",
  "ageMin": 3,
  "ageMax": 4,
  "ageBand": "AGE_3_4",
  "topics": ["勇气", "亲子依恋"],
  "pageCount": 6,
  "totalPages": 6,
  "coverRef": null,
  "localCover": {
    "thumbnail": {
      "assetRole": "THUMBNAIL",
      "assetPath": "hardware/assets/generated/book-b002-thumb.jpg",
      "devicePath": "/assets/covers/book-b002-thumb.jpg",
      "width": 213,
      "height": 160,
      "format": "JPEG",
      "fileSize": 12011,
      "sha256": "ae20451a……"
    },
    "detail": {
      "assetRole": "DETAIL",
      "assetPath": "hardware/assets/generated/book-b002-detail.jpg",
      "devicePath": "/assets/covers/book-b002-detail.jpg",
      "width": 400,
      "height": 300,
      "format": "JPEG",
      "fileSize": 38105,
      "sha256": "69a4af33……"
    }
  },
  "demoOnly": true
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `storybookId` | integer | 绘本逻辑 ID，不等于版本 ID |
| `storyVersionId` | integer | 由 `storybook_id` 查询得到的版本 ID |
| `versionNumber` | integer | 版本序号；不是会话 `revision` |
| `status` | string | 源种子中 20 本均为 `PUBLISHED`；仅表示数据状态，不是在线发布授权 |
| `pageCount` / `totalPages` | integer | 本种子中两者相同；H06 仍应以 `pages` 实际数据为准 |
| `coverRef` | null | 服务端受控封面引用；DEMO 中为空 |
| `localCover` | object | H03 本地封面，仅用于 DEMO 书架和详情 |
| `demoOnly` | boolean | 所有层级必须为 `true` |

### 3.2 `story-details.json`：版本详情与分页

每个条目在书架字段基础上增加：

```json
{
  "pages": [
    {
      "pageNumber": 1,
      "text": "下雨了。小狐狸站在门口，看着外面的雨。",
      "imageRef": null,
      "defaultNextPageNumber": 2,
      "imageStatus": "MISSING_INNER_ILLUSTRATION",
      "localPageAsset": null
    },
    {
      "pageNumber": 6,
      "text": "雨停了。小狐狸收起伞，笑了。",
      "imageRef": null,
      "defaultNextPageNumber": null,
      "imageStatus": "MISSING_INNER_ILLUSTRATION",
      "localPageAsset": null
    }
  ],
  "branches": [],
  "summary": null
}
```

- 页码从 1 开始。
- 末页 `defaultNextPageNumber=null`，不能把数组长度当作永远可连续翻页。
- `imageRef=null` 表示本 DEMO 没有服务端逐页插画引用；H06 显示占位，不得拿封面冒充正文插画。
- `localPageAsset=null` 是为后续独立 H03 内页资源任务预留的本地接入位。该任务完成前不得填入路径、尺寸或 SHA-256；H05 不复制、不猜测 104 张内页 PNG。
- `storyVersionId` 与 `storybookId` 是两个字段；H06 不得用后者替换前者。

### 3.3 `demo-session.json`：初始 DEMO 会话

```json
{
  "demoOnly": true,
  "profile": {
    "childProfileId": 1,
    "nickname": "本地演示",
    "ageBand": "AGE_3_4",
    "preferredMinutes": 10,
    "demoOnly": true
  },
  "session": {
    "readingSessionId": 1,
    "storyVersionId": 2,
    "currentPage": 1,
    "visitedPageNumbers": [],
    "mode": "PARENT_CHILD",
    "status": "CREATED",
    "revision": 0,
    "expiresAt": null,
    "demoOnly": true
  }
}
```

这是 Mock 的初始内存会话；重启 Mock 后由 `demo-session.json` 恢复。它不是服务端真实会话。

### 3.4 `voice-scenarios.json`：语音 UI 状态

根节点包含：

- `states`：H04 的 7 个主状态 `IDLE/RECORDING/UPLOADING/RECOGNIZING/THINKING/SYNTHESIZING/PLAYING`；
- `errorStates`：`ERROR_ASR/ERROR_TIMEOUT/ERROR_TTS/ERROR_NETWORK/ERROR_TOO_SHORT`；
- `scenarios`：8 个可注入场景。

每个场景字段：

| 字段 | 说明 |
| --- | --- |
| `scenarioId` | 注入标识，如 `success`、`ai_timeout` |
| `states` | 时间线，每项含 `state`、`label`、`hint`、`delayMs`、`isError` |
| `finalState` | 场景结束状态 |
| `outcome` | `SUCCESS/CANCELLED/NETWORK_LOST/...` |
| `asrText` / `answerText` | 固定测试文本；不是真实 ASR/模型结果 |
| `reliable` + `reliableIsSimulated` | 仅用于演示；`success` 的 true 是模拟值，真实 API-022 当前固定 false |
| `consumer` | `PARENT_CONFIRMED` 或 `CHILD`；儿童模式不得自动消费不可信结果 |
| `tts` | `{status: "SIMULATED", audioUrl: null, playbackMode: "SIMULATED", durationMs}`；`tts_failed` 为 `null`，表示没有可播放音频 |
| `recoverTo` | 错误态建议恢复目标；`tts_failed` 为 `SYNTHESIZING`，其余错误态为 `IDLE` |
| `recoveryActions` | 仅 `tts_failed` 提供 `RETRY_SYNTHESIS` 与 `TEXT_ONLY` 两个操作目标 |
| `demoOnly` | 固定 true |

### 3.5 `error-scenarios.json`：错误注入

共 13 个场景。字段：

```json
{
  "scenarioId": "SESSION_CONFLICT",
  "status": 409,
  "code": "SESSION_CONFLICT",
  "message": "Mock 会话修订冲突，请先读取会话",
  "retryable": false,
  "appliesTo": ["/api/v1/reading-sessions/*/events"],
  "mode": "error",
  "demoOnly": true
}
```

`mode=payload` 的 `NO_BOOKS` 和 `COVER_MISSING` 返回 200 数据变形，不返回错误；`COVER_MISSING` 的 `action=stripLocalCover` 会把 `localCover` 置为 `null`。`VOICE_BUSY` 额外带 `retryAfter=1`。

`DEPENDENCY_UNAVAILABLE` 的 `retryable` 不是固定值：Mock 按请求方法读取 `retryableByMethod`，GET 为 `true`，POST 为 `false`。这对应正式契约“读取可重试，API-015 写入在已知未提交时由用户决定是否重试”。`REQUEST_OUTCOME_UNKNOWN` 覆盖 `/events`、`/questions`、`/completions`；注入后不修改会话，H06 必须先 `GET` 会话再决定。

## 4. Mock HTTP 服务

### 4.1 启动

```powershell
python hardware/mock/build_fixtures.py
python hardware/mock/server.py
# 指定端口
python hardware/mock/server.py --port 8765
```

默认监听 `127.0.0.1:8765`。只有显式传入非回环 `--host` 才允许受控局域网演示；不得开放公网。服务没有账号、Cookie、设备 token、CSRF 或云端 API Key，也不会调用 DeepSeek/Qwen-ASR/TTS。

### 4.2 响应封装

成功：

```json
{"data": {}, "requestId": "mock-7f3a9c2b1d4e"}
```

错误：

```json
{
  "error": {"code": "SESSION_CONFLICT", "message": "会话修订号已变化，请先读取会话", "retryable": false},
  "requestId": "mock-7f3a9c2b1d4e"
}
```

`requestId` 为 `mock-` + 12 位十六进制随机串；同键重放会保留原响应数据，但当前响应仍使用新的 `requestId`。服务响应头包含 `X-Mock-Server: zhiya-h05` 和 `Cache-Control: no-store`。

### 4.3 路径

| 方法 | 路径 | Mock 语义 | 对应真实接口/说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | Mock 健康检查 | 不是 API-032 的正式实现 |
| GET | `/mock/meta` | 数据计数、模式、`demoOnly` | Mock 专用 |
| GET | `/api/v1/storybooks` | 20 本轻量书架元数据，支持 `ageBand/topic/page/pageSize` | API-008 的 DEMO 投影 |
| GET | `/api/v1/story-versions/{version_id}` | 版本详情和分页 | API-009 的 DEMO 投影 |
| GET | `/api/v1/reading-sessions` | 列出内存会话 | API-012 的 DEMO 投影 |
| POST | `/api/v1/reading-sessions` | 创建初始会话，可选 `clientRequestId` 幂等 | API-011 的 DEMO 投影 |
| GET | `/api/v1/reading-sessions/{id}` | `session/events/report` | API-013 的 DEMO 投影 |
| POST | `/api/v1/reading-sessions/{id}/events` | STARTED/PAGE_CHANGED | API-015 的 DEMO 投影 |
| POST | `/api/v1/reading-sessions/{id}/completions` | USER_FINISH | API-019 的 DEMO 投影 |
| POST | `/api/v1/reading-sessions/{id}/questions` | 固定模板问答 | API-016 的 Mock 投影，不是完整 G10 |
| GET | `/mock/voice/scenarios` | 全部语音场景 | H04 UI 演示数据 |
| GET | `/mock/voice/scenarios/{id}` | 单个场景 | H04 UI 演示数据 |
| POST | `/mock/voice/simulate` | 返回时间线，`audioUrl=null` | **不是** API-022/023/024 |
| GET | `/mock/errors` | 错误场景列表 | Mock 专用 |
| POST | `/mock/reset` | 重置内存会话和 fixtures 状态 | Mock 专用，测试使用 |

`scope=mine` 会返回 403 `FORBIDDEN`，因为 H05 没有实现家长历史书架；这不是正式权限实现。

`GET /api/v1/reading-sessions` 在分页前按 `readingSessionId` 倒序，等价于正式 API-012 的“最新创建优先”；`pageSize=1` 的第一页是最新会话。列表只返回 `items/page/pageSize`，不返回测试辅助 `total`。

`POST /api/v1/reading-sessions` 支持可选 `clientRequestId`（1～64 字符）。同键同体首次返回 201，成功重放返回 200 和原会话；同键改体返回 409 `IDEMPOTENCY_CONFLICT`；修正请求必须换新键。空串或显式 `null` 返回 422。该映射由 `MockState.create_idempotency` 在同一把 `RLock` 内检查/写入，`/mock/reset` 时清空。

`GET /api/v1/storybooks` 只返回 `storybooks.json` 的轻量元数据，**不包含 `pages` / `branches`**；`load_fixtures()` 会在启动时拒绝被误塞入 `pages/branches` 的书架 fixture。H06 进入详情页时必须再请求 `GET /api/v1/story-versions/{id}`。Mock 的会话、书籍、计数、语音和错误场景读取都通过 `MockState` 的锁内快照方法取副本，避免与 `/mock/reset` 或并发写入互相撕裂。

### 4.4 示例

书架：

```http
GET /api/v1/storybooks?ageBand=AGE_5_6
```

```json
{
  "data": {
    "items": [
      {
        "storybookId": 2,
        "storyVersionId": 2,
        "title": "小狐狸的雨伞",
        "status": "PUBLISHED",
        "coverRef": null,
        "localCover": {"thumbnail": {"assetRole": "THUMBNAIL", "devicePath": "/assets/covers/book-b002-thumb.jpg"}},
        "demoOnly": true
      }
    ],
    "page": 1,
    "pageSize": 20
  },
  "requestId": "mock-..."
}
```

> 示例为字段节选；完整条目还包含 `ageMin/ageMax/ageBand/topics/pageCount/totalPages` 和 `localCover.detail`。

开始阅读：

```http
POST /api/v1/reading-sessions/1/events
Content-Type: application/json

{"eventType":"STARTED","pageNumber":1,"expectedRevision":0,"clientEventId":"demo-start-001"}
```

```json
{"data": {"interactionEventId": 1, "revision": 1}, "requestId": "mock-..."}
```

首次成功 HTTP 状态为 `201`；原样重放同一键/同一请求体为 `200`，事件 ID 与 revision 不变。翻页 `409 SESSION_CONFLICT` 后，原样重放仍为 `409`；只改 `expectedRevision` 而沿用原键会得到 `409 IDEMPOTENCY_CONFLICT`，必须换新键提交。

错误：

```json
{"error":{"code":"SESSION_CONFLICT","message":"会话修订号已变化，请先读取会话","retryable":false},"requestId":"mock-..."}
```

错误注入：

```http
GET /api/v1/health
X-Mock-Error: AUTH_REQUIRED
```

或：

```http
GET /api/v1/storybooks?mockError=NO_BOOKS
```

`X-Mock-Error` 优先于查询参数。未知场景返回 422 `VALIDATION_ERROR`。只有路径命中 `appliesTo` 才生效；`NO_BOOKS`/`COVER_MISSING` 是数据变形，不是 HTTP 错误。

## 5. 阅读会话状态机

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> IN_PROGRESS: STARTED\npage=1, expectedRevision=0
    IN_PROGRESS --> IN_PROGRESS: PAGE_CHANGED\nforward only
    IN_PROGRESS --> FINISHED: USER_FINISH\nrevision 匹配
    FINISHED --> FINISHED: 同键重放 / 新键返回既有终态
```

### 5.1 初始状态

`demo-session.json` 和 `POST /api/v1/reading-sessions` 都产生：

```text
status=CREATED, currentPage=1, visitedPageNumbers=[], revision=0
```

### 5.2 事件规则

| 请求 | 允许条件 | 成功效果 |
| --- | --- | --- |
| `STARTED` | `CREATED`、`pageNumber=1`、`expectedRevision=0` | `IN_PROGRESS`、currentPage=1、visited 增加 1、revision +1 |
| `PAGE_CHANGED` | `IN_PROGRESS`，目标等于当前页的 `defaultNextPageNumber`，且目标不在 `visitedPageNumbers` | currentPage=目标、visited 追加、revision +1 |
| `USER_FINISH` | `IN_PROGRESS`，`expectedRevision` 匹配 | `FINISHED`、revision +1、生成 `report` |

- `expectedRevision` 不匹配：409 `SESSION_CONFLICT`。
- `FINISHED` 后新键翻页/提问：409 `SESSION_CONFLICT`。
- 目标页不存在、非当前页后继、已访问旧页，或畸形 fixture 把 `defaultNextPageNumber` 指回旧页：409 `SESSION_CONFLICT`。
- 页码小于 1、字段类型错误：422 `VALIDATION_ERROR`。
- 会话不存在：404 `RESOURCE_NOT_FOUND`。

`PAGE_CHANGED` 必须与正式 API-015 一致，只接受当前页的 `defaultNextPageNumber`。DEMO 种子是线性分页，因此 Mock 额外拒绝任何已访问页：即使某个畸形/旧版 fixture 把 `defaultNextPageNumber` 指回旧页，也不能把 H06 的本地回看伪装成服务端接受的翻页事件。H04 的“返回上一页”是 H06 阅读器的本地回看行为：本地改显示页即可，**不能发送旧页的 `PAGE_CHANGED`**；如果要重新进入正式同步，应由 H06 按自身业务决定后续动作，不能复用旧页的 `PAGE_CHANGED`。

### 5.3 幂等与重放

幂等键是**每个会话内**的 `clientEventId`，摘要由 `kind + 规范化请求体` 计算：

| 情况 | 结果 |
| --- | --- |
| 同键、同请求体，首次成功 | API-015/016 返回 201；API-019 返回 200 |
| 同键、同请求体，成功重放 | 该操作返回 200，原 `interactionEventId`、revision 和 data 不重复写入 |
| 同键、同请求体，确定性失败重放 | 返回首次失败的原 HTTP 状态、错误码和 `retryable`；不重新校验或推进页码 |
| 同键、不同请求体（包括修订号不同） | 409 `IDEMPOTENCY_CONFLICT`，原失败记录不被覆盖 |
| 不同键、错误 revision | 409 `SESSION_CONFLICT`，并保存该键的确定性失败 |
| 修正后重试 | 必须使用**新** `clientEventId`；沿原键修改请求体会被拒绝 |
| 新键在 `FINISHED` 后结束 | 200，返回既有终态和 `report`，不再改 revision |
| 504 结果未知 | 客户端先 `GET` 会话；保留原键；不得自动创建新事件 |

“确定性失败”包括会话状态/revision/页码不允许、目标页不可达等已能在服务端判定的业务失败。参数类型错误在进入会话幂等区间前返回 422，不保存键。成功记录的 `status` 保存首次 201，`replayStatus` 固定 200；失败记录保存原错误对象。

### 5.4 读取与恢复

`GET /api/v1/reading-sessions/{id}` 返回：

```json
{
  "data": {
    "session": {"readingSessionId": 1, "storyVersionId": 2, "currentPage": 1, "visitedPageNumbers": [1], "status": "IN_PROGRESS", "revision": 2, "mode": "PARENT_CHILD", "expiresAt": null, "demoOnly": true},
    "events": [
      {"interactionEventId": 1, "eventType": "STARTED", "pageNumber": 1},
      {"interactionEventId": 2, "eventType": "ASK", "pageNumber": 1, "outputText": "这是本地 Mock 的固定回答，不是真实 AI 输出。", "strategyType": "CONTEXT_QUESTION", "revision": 2}
    ],
    "report": null
  },
  "requestId": "mock-..."
}
```

Mock 的 ASK 事件在写入时保存 `outputText`、`strategyType` 和事件写入后的 `revision`；`GET` 会话通过 `public_event()` 恢复这些字段，而不是只返回事件编号、类型和页码。`STARTED` / `PAGE_CHANGED` 仍保持 API-013 的三字段投影。Mock 没有真实角色投影；`report` 是演示对象，H06 不得在儿童界面展示完整报告。正式 API-013 的儿童投影建议 `report=null`，服务端权限仍以真实后端为准。

### 5.5 问答

`POST /api/v1/reading-sessions/{id}/questions` 仅用于验证 UI 数据流：

- 要求 `IN_PROGRESS`、`pageNumber` 等于当前页、`expectedRevision` 等于当前 revision；
- 首次成功返回 **201**，成功重放返回 **200**，与 API-016 相同；
- 返回固定 `outputText`、`status=GENERATED`、`strategyType=CONTEXT_QUESTION`、`sources=[]`、`demoOnly=true`；
- 追加一条 `eventType=ASK` 的内存事件并递增 session revision；事件内保存 `outputText`、`strategyType`、`revision` 和 `resultStatus=SUCCESS`，返回新的 `interactionEventId` 与 revision；
- 随后 `GET /api/v1/reading-sessions/{id}` 能恢复同一 `interactionEventId`、`outputText`、`strategyType` 和 `revision`；同键重放不追加事件、不推进 revision；
- 该 `interactionEventId` 可供 `/mock/voice/simulate` 的 `tts_failed` 复用；模拟返回的 `answerText` 覆盖为会话中实际保存的 ASK `outputText`，并与该事件编号绑定；
- 这不是 API-016/G10 的真实结果，不保存真实互动记录，也不调用模型。

正式链路应为：API-022 识别（`reliable=false` 时儿童不自动进入问答；家长确认或后端可信策略）→ API-016/G10 生成并持久化互动事件 → 必要时 API-023 合成 → API-024 读取。H05 仅模拟 UI 状态，不把该链路写成已完成。

### 5.6 并发与快照

`MockState` 用一把 `threading.RLock` 保护所有共享可变状态；HTTP handler 不再直接读取 live 的 `books/details/voice/errors/sessions` 字段，而是调用统一的快照或事务方法：

- `reset()` 在锁内整体替换 books、details、voice、error scenarios 和 sessions，因此读请求只能看到替换前或替换后的完整一代状态；
- `books_snapshot()`、`meta_snapshot()`、`sessions_snapshot()`、`session_snapshot()`、`voice_snapshot()`、`voice_scenario()`、`errors_snapshot()`、`error_scenario()` 在锁内复制响应数据，释放锁后再写 HTTP；
- `session_snapshot()` 在同一个锁区间内取 session、公开 events 和 report，避免 `revision/currentPage/visitedPageNumbers` 来自不同写入代；
- `create_session()` 在锁内检查版本、分配 ID、插入 session，并返回已构造的 session 对象副本；不会把 live `SessionState` 泄漏给响应层；
- `sessions_snapshot()` 在锁内按 `readingSessionId` 倒序复制会话对象，再在锁外分页；第一页首条是最新创建的会话；
- `POST /events`、`POST /completions`、`POST /questions` 用 `session_for_update()` 在同一锁区间内完成幂等键检查、revision 校验和状态写入；成功与确定性失败都在锁内写入幂等记录；`PAGE_CHANGED` 的 visited 检查也在同一事务内完成；
- `GET /reading-sessions/{id}`、`GET /reading-sessions`、`GET /api/v1/storybooks`、`/mock/meta`、`/mock/voice/*`、`/mock/errors` 全部使用快照。

HTTP 响应在释放锁后写出，避免慢客户端长期占用状态锁。并发回归测试覆盖：

- 8 个相同 `STARTED` 请求同时到达：恰好 1 个 201、7 个 200，只产生 1 条事件并递增一次 revision；
- 8 个并发相同请求只产生一次有效写入；并验证会话事件数与 revision 一致；
- 创建两个新会话后，`pageSize=1` 的第一页返回最新会话，而不是初始会话；
- 16 个并发创建会话：ID 唯一，响应均为 `CREATED/revision=0/visited=[]`；
- 读取会话、列表、书架、meta、语音、错误场景与 `/mock/reset` 交错执行：不出现 500、空会话或半更新响应；
- 每次 session 快照满足 `currentPage` 在 `visitedPageNumbers` 内、`CREATED` 的 revision 为 0 等基本一致性约束。

## 6. 语音状态模拟

### 6.1 状态

```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> RECORDING: 点击麦克风
    RECORDING --> UPLOADING: 点击结束
    UPLOADING --> RECOGNIZING
    RECOGNIZING --> THINKING: 识别成功
    THINKING --> SYNTHESIZING
    SYNTHESIZING --> PLAYING
    PLAYING --> IDLE: 播放完成/关闭
    RECORDING --> IDLE: 取消
    UPLOADING --> ERROR_NETWORK: 网络中断
    RECOGNIZING --> ERROR_ASR: 识别失败
    THINKING --> ERROR_TIMEOUT: AI 超时
    SYNTHESIZING --> ERROR_TTS: 合成失败
    RECORDING --> ERROR_TOO_SHORT: 空/过短
    ERROR_ASR --> IDLE: 重试/取消
    ERROR_TIMEOUT --> IDLE: 重试/取消
    ERROR_TTS --> SYNTHESIZING: 重试合成\n复用已保存 interactionEventId
    ERROR_TTS --> IDLE: 只看文字
    ERROR_NETWORK --> IDLE: 网络恢复/取消
    ERROR_TOO_SHORT --> IDLE: 重试/取消
```

主状态 7 个：`IDLE/RECORDING/UPLOADING/RECOGNIZING/THINKING/SYNTHESIZING/PLAYING`。`success` 时间线含首尾两个 `IDLE`，所以是 8 个时间线条目，不是 8 个主状态。

### 6.2 场景

| `scenarioId` | 结束状态 | 用途 |
| --- | --- | --- |
| `success` | `IDLE` | 家长确认后的目标链路；显示固定识别/回答文本 |
| `reliable_false_child` | `ERROR_ASR` | `reliable=false` 时儿童重录 |
| `asr_failed` | `ERROR_ASR` | 识别失败 |
| `ai_timeout` | `ERROR_TIMEOUT` | AI 响应超时 |
| `tts_failed` | `ERROR_TTS` | 合成失败 |
| `cancelled` | `IDLE` | 录音取消 |
| `network_lost` | `IDLE` | 上传时断网并恢复 |
| `too_short` | `ERROR_TOO_SHORT` | 空/过短录音 |

### 6.3 模拟边界

- 所有场景 `audioUrl=null`，`tts.playbackMode=SIMULATED`；不得当作 API-023 的真实音频。
- `tts_failed` 的 `RETRY_SYNTHESIS` 只回到 `SYNTHESIZING`，复用已保存的 `interactionEventId`，不重新录音、不重新问答、不新增事件；`TEXT_ONLY` 才回 `IDLE`。
- `/mock/voice/simulate` 对 `tts_failed` 要求同时传入 `sessionId` 与已存在的 ASK `interactionEventId`；缺失或不匹配返回 422。返回的 `answerText` 使用该 ASK 事件实际保存的 `outputText`，不沿用固定场景文本；`interactionEventId` 仍绑定该已保存事件，因此重试不会重新问答、录音或新增事件。
- `success` 的 `reliable=true` 只是 Mock 字段，`reliableIsSimulated=true`；真实 API-022 当前 `reliable` 固定为 false。
- 不生成可播放音频，不创建真实互动事件，不写真实会话记录。
- 动画建议只使用 H04 的低资源 spinner/呼吸圈；H05 不规定帧率、延迟或内存指标。
- 儿童模式不能因为场景显示“成功”就自动进入下一问；真实权限和 `reliable` 必须由服务端返回。

## 7. 错误码与恢复

| HTTP | `code` / 场景 | `retryable` | H05 示例 | 客户端动作 |
| --- | --- | --- | --- | --- |
| 401 | `AUTH_REQUIRED` | false | `X-Mock-Error: AUTH_REQUIRED` | 进入授权失效提示；不伪造登录 |
| 403 | `FORBIDDEN` | false | `FORBIDDEN` | 受限页/返回；不显示受控内容 |
| 404 | `RESOURCE_NOT_FOUND` | false | 版本 999、会话 999 | 占位或返回书架 |
| 409 | `SESSION_CONFLICT` | false | 错误 revision、结束态翻页 | 读取 API-013/GET 会话后再决定 |
| 409 | `IDEMPOTENCY_CONFLICT` | false | 同键不同请求 | 保留原键，先核对原请求；不重放新键 |
| 409 | `SESSION_ENDED` | false | 结束页后的翻页 | 进入结束页，不继续普通阅读 |
| 422 | `VALIDATION_ERROR` | false | 页码 0、字段缺失 | 修正输入；不自动重试 |
| 429 | `VOICE_BUSY` | true（带 `Retry-After`） | `X-Mock-Error: VOICE_BUSY` | 等待后重试；不并发发语音 |
| 503 | `DEPENDENCY_UNAVAILABLE` | GET=true / POST=false | `DEPENDENCY_UNAVAILABLE` | 读取可稍后重试；写入在已知未提交时由用户决定是否重试，不静默自动重放 |
| 504 | `REQUEST_OUTCOME_UNKNOWN` | false | `/events`、`/questions`、`/completions` | 先 GET 会话；不自动提交新事件或新问题 |
| 200 | `NO_BOOKS` | — | `?mockError=NO_BOOKS` | 空书架页 |
| 200 | `COVER_MISSING` | — | `?mockCover=missing` | 使用本地占位，不伪造封面 |

`INVALID_PAGE` 的 HTTP 状态是 422，`code` 仍为 `VALIDATION_ERROR`；`SESSION_ENDED` 的 `code` 仍为 `SESSION_CONFLICT`。不要按 `scenarioId` 直接展示给儿童，只用于测试和日志。

当前 `REQUEST_OUTCOME_UNKNOWN` 的注入发生在请求处理前，代表“未写入、客户端未收到正常响应”的 504；测试验证 504 后 GET 的 revision/事件数不变，再由新键恢复。Mock 暂不模拟“服务端已落库、客户端未收到响应”的 504，H06 若需覆盖该分支，应在真实后端或后续独立场景中验证，不能把本 Mock 的预写入 504 当成已写入恢复证据。

## 8. DEMO 与 ONLINE 边界

| 维度 | DEMO / Mock | ONLINE / 正式 |
| --- | --- | --- |
| 数据来源 | `hardware/mock/fixtures/*.json`、内存会话 | 业务后端、数据库、真实媒体 |
| 绘本状态 | 20 本源种子为 `PUBLISHED`，`demoOnly=true`；联调 DRAFT 不纳入 | 仅服务端授权范围内的已发布/已批准内容 |
| 身份 | 无真实认证；`childProfileId=1` | 家长登录、儿童档案、设备绑定/凭证 |
| 封面 | H03 本地 JPEG + `localCover` | API-024 受控媒体 `resource_key` |
| 内页插画 | `imageRef=null` + `localPageAsset=null` + 占位；等独立 H03 内页任务 | 服务端真实 `imageRef`；无图仍占位 |
| 语音 | `/mock/voice/*` 状态时间线 | API-022/023/024 + 真实 ASR/TTS/媒体 |
| 结束报告 | 固定 DEMO 文案 | API-019 + 服务端小结 |
| 错误注入 | 请求头/查询参数 | 真实 401/403/404/409/422/503/504 |
| 结果可信度 | 只验证 UI/数据流 | 必须通过真实服务端和实板验收 |

任何 UI 页面都必须在 DEMO 模式显示明确标识；Mock 的 `demoOnly` 不能当作发布授权，也不能替代家长确认。

## 9. H06 交接

### 9.1 统一数据提供者

H06 应通过一个适配层消费数据，不要把 `fixtures/*.json` 的字段访问散落在每个 LVGL 页面：

```text
HardwareDataSource
├── listBooks(filter) -> BookPage
├── getBookDetail(storyVersionId) -> StoryDetail
├── createSession(childProfileId, storyVersionId, mode) -> Session
├── getSession(readingSessionId) -> SessionSnapshot
├── submitEvent(sessionId, eventType, pageNumber, expectedRevision, clientEventId) -> EventAck
├── finishSession(sessionId, expectedRevision, clientEventId) -> CompletionAck
├── submitQuestion(sessionId, inputText, pageNumber, expectedRevision, clientEventId) -> QuestionAck
└── getVoiceScenario(scenarioId) -> VoiceScenario
```

DEMO 实现读取 fixtures 或调用 Mock HTTP；ONLINE 实现使用正式 API 客户端。两者返回同一套内部 DTO；页面只依赖 DTO，不直接判断“当前是 Mock 还是 HTTP”。

### 9.2 页面与数据

| 页面 | 使用字段 | 说明 |
| --- | --- | --- |
| HW-UI-01 开机/网络 | `/api/v1/health`、`/mock/meta` | DEMO 标识；不把它当成正式 API-032 |
| HW-UI-03 首页 | `storybooks.items[].localCover.detail` | 继续阅读卡只用 DETAIL；无图占位 |
| HW-UI-04 书架 | `storybooks.items[].localCover.thumbnail`、`status` | 20 本源种子状态为 `PUBLISHED`，仍仅 DEMO；不得据此宣称在线发布 |
| HW-UI-05 详情 | `story-details`、`localCover.detail` | `storybookId` 与 `storyVersionId` 分开显示/传参 |
| HW-UI-06 阅读 | `pages[].text/pageNumber/defaultNextPageNumber/localPageAsset` | `imageRef=null`、`localPageAsset=null` 时用占位；不用封面当正文 |
| HW-UI-07 语音 | `voice-scenarios.json` / `/mock/voice/*` | 只演示状态和文案；`audioUrl=null` |
| HW-UI-08 结束 | `completions` 返回的 `session/report` | 儿童端不展示完整报告；`report` 为 DEMO |
| HW-UI-09 异常 | `error-scenarios.json` | 至少覆盖 401/403/404/409/422/503/504、空书架、缺封面 |
| HW-UI-10 家长锁 | 无真实凭据 | 只做界面保护；真实身份由服务端验证 |

### 9.3 素材挂载

- H03 `devicePath` 形如 `/assets/covers/book-b002-thumb.jpg` / `/assets/covers/book-b002-detail.jpg`。
- H06 可将这些逻辑路径映射到 Flash 资源分区；具体分区由 H06 决定，H05 不修改 `firmware/partitions.csv`。
- `THUMBNAIL` 只用于书架卡；`DETAIL` 用于详情/继续阅读；阅读页内页只用 `imageRef` 或占位。
- `localPageAsset` 是后续 H03 内页资源的接入位。当前为 `null`；独立任务交付真实路径/哈希前，H05 不编造 104 页内页清单，也不把封面或 `knowledge-base_v2.5/图片/内页/**` 中的未审计文件直接写入。
- 缺图、缺封面、解码失败统一显示占位；不得用封面冒充正文插画。

### 9.4 事件边界

LVGL 事件回调只更新 UI 状态或投递到应用状态机；不得在回调中执行阻塞式 HTTP、文件解码或云端调用。推荐：

```text
touch event -> UI intent -> controller/state machine -> data provider (async) -> UI state update
```

## 10. 安装、运行与验证

### 10.1 生成和一致性

```powershell
Set-Location D:\zhiya-hardware-workspace\worktrees\zhiya-h05
python hardware/mock/build_fixtures.py
python hardware/mock/build_fixtures.py --check
```

实际输出（Windows，Python 3.13.18）：

```text
写出 storybooks.json: hardware/mock/fixtures/storybooks.json
写出 story-details.json: hardware/mock/fixtures/story-details.json
写出 demo-session.json: hardware/mock/fixtures/demo-session.json
写出 voice-scenarios.json: hardware/mock/fixtures/voice-scenarios.json
写出 error-scenarios.json: hardware/mock/fixtures/error-scenarios.json
H05 fixtures 完成：5 个文件，20 本 / 104 页 / 40 个封面资源
```

`--check` 实际输出：

```text
H05 fixtures 一致：hardware/mock/fixtures
```

### 10.2 启动 Mock

```powershell
python hardware/mock/server.py --port 8765
```

另开终端执行：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/v1/health
Invoke-RestMethod http://127.0.0.1:8765/api/v1/storybooks
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/v1/reading-sessions/1/events `
  -ContentType 'application/json' `
  -Body '{"eventType":"STARTED","pageNumber":1,"expectedRevision":0,"clientEventId":"smoke-start"}'
```

### 10.3 测试

```powershell
python -m unittest discover -s hardware/tests/mock -v
```

实际结果：

```text
Ran 37 tests in 6.798s
OK
```

覆盖范围：

- 20 本原创绘本、104 页、40 个 H03 引用；20 本源状态为 `PUBLISHED`、`demoOnly=true`；
- 书号、`storybookId`、`storyVersionId`、`sourcePath` 映射；DRAFT 联调绘本排除；
- 页码 1 开始、末页 null、`imageRef=null`、`localPageAsset=null`；
- 书架/详情一致；书架列表不包含 `pages` / `branches` / `total`；书架 fixture 误塞入 `pages` 时启动失败；
- H03 40 个资源存在、SHA/大小/JPEG；
- `STARTED`、前进、错误返回旧页、跳过页、最后一页、结束态；
- 畸形 fixture 把 `defaultNextPageNumber` 指回旧页时仍拒绝 `PAGE_CHANGED`；
- 成功首次 201 / 重放 200；8 并发相同请求为 1×201 + 7×200，只写一次；
- API-011 创建会话 `clientRequestId`：同键同体 201→200 且会话不重复，同键改体 409 `IDEMPOTENCY_CONFLICT`，新键创建新会话；空串 422；
- 翻页 409 后原键重放原错误、同键改体 `IDEMPOTENCY_CONFLICT`、新键修正成功；
- 问答首次 201、重放 200、递增 revision；旧 revision 冲突；
- ASK 结果可恢复：首次提问后 GET 会话返回同一 `interactionEventId/outputText/strategyType/revision`；同键重放不新增事件；
- 列表分页前最新会话优先；
- 语音 7 主状态、8 场景、TTS 重试复用 ASK 事件、返回保存的 `answerText`、无真实音频、无新问答/录音；
- 401/403/404/409/422/429/503/504、空书架、缺封面；503 读写 retryable 差异；questions 504 后 GET 恢复；
- 重复生成确定性、`--check`、源文件未修改、`/mock/reset`；
- 并发相同 `STARTED` 幂等、16 个并发创建会话、读取/列表/书架/meta/语音/错误/重置交错快照一致性。

### 10.4 只读边界

测试和构建都只读 `data/seed/`、`hardware/assets/generated/`；不修改 H02 固件、H03 脚本/图片、H04 UI 文档、前端、后端、数据库或正式 API 契约。

## 11. 已验证、未验证与契约差异

### 11.1 已验证

- fixtures 由真实种子生成：20 本 `PUBLISHED` 原创、104 页、40 个资源引用；1 本 `DRAFT` 联调绘本排除。
- `storyVersionId` 按 `storybook_id` 查找，不按数组下标或 ID 等值假设。
- 所有原创版本 `demoOnly=true`、`coverRef=null`、`imageRef=null`、`localPageAsset=null`；H03 本地封面只出现在 `localCover`。
- Mock 服务的阅读会话、成功/失败幂等、ASK 结果恢复、TTS 答案复用、结束态、错误注入、分页规则和并发快照通过 37 项离线测试。
- `GET /api/v1/storybooks` 只返回轻量元数据，不携带 `pages` / `branches` / `total`；详情由 API-009 投影单独读取。
- `PAGE_CHANGED` 只接受当前页 `defaultNextPageNumber` 且未访问的目标；错误的后向翻页、已访问页和畸形回环均返回 409，不增加 revision。
- 测试不依赖 Pillow、FastAPI、数据库、云端模型或 ESP32 板卡。
- 源文本使用 `sha256-utf8-lf-v1`；工作树 CRLF 与 Git LF 原始字节归一后得到相同摘要，干净检出可直接运行 `--check`。
- 2026-11-25 已在独立 detached clean worktree 运行 `python hardware/mock/build_fixtures.py --check`，输出“H05 fixtures 一致”，退出码 0。
- H03 40 个封面 JPEG 与 manifest 的 SHA-256/大小一致；原图和种子未被构建或测试改写。

### 11.2 未验证 / 待 H06

- JPEG 在 ESP32-S3 4.3C + LVGL 上的真实解码、颜色空间、解码耗时和内存占用。
- LVGL 9.2.0 的图片缓存、PSRAM 使用、帧率和触摸延迟；本文不声称任何实测数字。
- Flash 资源分区、H03 JPEG 烧录/挂载路径、真机启动后的资源可读性。
- API-022 真实 ASR、API-023 真实 TTS、API-024 图片分派和设备认证；H05 没有实现这些。
- API-016/G10 的真实模型问答、持久化与跨请求执行记录；H05 只验证固定回答、201/200、revision 和 ASK 事件复用。
- 家长/儿童角色投影、家长锁安全强度、设备绑定 API；H05 的 401/403 是模拟。
- 104 页正文插画：当前全部缺失；`localPageAsset=null`，等待独立 H03 内页任务，H05 不编造清单/哈希/路径。

### 11.3 与正式契约的已知差异

1. **问答**：Mock 的 `/questions` 返回固定模板，不调用 G10、不写数据库；但它已对齐 API-016 的首次 201、重放 200、递增 revision、ASK `interactionEventId`，并按 API-013 恢复 ASK 的 `outputText/strategyType/revision`。
2. **语音**：Mock 的 `/mock/voice/*` 不是 API-022/023/024；`success` 的 `reliable=true` 是模拟，真实 API-022 固定 false。
3. **角色投影**：Mock 无真实身份，不实现儿童 `report=null` 投影；H06 不能把 Mock 的 `report` 直接展示给儿童。
4. **书架列表投影**：H05 按设备书架要求只返回轻量元数据，不返回 `pages` / `branches` / `total`；当前正式 API-008 响应示例仍包含完整版本对象。H06 进入详情必须走 API-009，接正式后端时以服务端实际响应为准。
5. **状态**：20 本源种子为 `PUBLISHED` 且全部 `demoOnly=true`；这只用于本地 DEMO，不代表在线授权、审核通过或家长可见。
6. **媒体**：`localCover.assetPath/devicePath` 不是 API-024 `resource_key`；正式媒体必须由 API-024 逐次校验权限。`localPageAsset` 当前为 null，不接入未审计的 H03 内页。
7. **健康检查**：Mock `/api/v1/health` 不是正式 API-032 实现，不能作为生产健康检查结论。
8. **阅读能力子集**：Mock 只实现 STARTED/PAGE_CHANGED 事件和固定问答；未实现正式 API-015 的 ANSWER/FEEDBACK、API-017 guidance、API-018 branches、API-020 mode-settings 等。

## 12. H06 验收建议

最小验收（DEMO，无实板也可做）：

1. 启动 Mock，`GET /api/v1/storybooks` 返回 20 本、`PUBLISHED/demoOnly=true` 和 DEMO 标识。
2. 书架只使用 `THUMBNAIL`；详情只使用 `DETAIL`；缺图显示占位。
3. 开始阅读、前进、末页和结束使用 `revision`；返回旧页仅本地回看，不发送 `PAGE_CHANGED`，不把 `versionNumber` 当 `revision`。
4. 同键同体重放不重复递增；同键改体返回 `IDEMPOTENCY_CONFLICT`；错误 revision 显示冲突恢复提示；结束后不继续普通翻页。
5. 语音覆盖 7 主状态；`ERROR_TTS` 的“重试”复用已保存的 `interactionEventId` 回 `SYNTHESIZING`，返回的 `answerText` 与 GET 会话中的 ASK `outputText` 一致；“只看文字”回 `IDLE`；`audioUrl=null` 时不调用真实播放。
6. 断网时保留当前页；恢复后先读取会话，不假设自动补写成功。
7. 401/403/404/422 不显示受控内容；503 读到可重试、写不自动重放；504 先读取会话，不自动创建新事件或新问题。
8. 创建会话可带 `clientRequestId`；结果未确认时用同键同体重试，必须返回原会话而不是新建第二个会话。
9. 问答成功后 GET 会话，ASK 事件必须恢复相同 `interactionEventId/outputText/strategyType/revision`，不能只显示事件编号和页码。

实板验收（H06）：

1. H03 JPEG 能被真机解码，书架/详情/占位实际可见；记录实测分辨率、解码耗时和内存。
2. 资源分区、挂载路径和 `devicePath` 映射由 H06 验证；H05 未修改分区。
3. 触摸、滚动、点击、返回与 H04 坐标一致；JPEG 解码不阻塞 LVGL 事件。
4. 断网、缺图、授权失败、语音失败的屏幕表现与 H04 一致。
5. 不把 Mock JSON 的成功当成真实后端/正式授权/正式发布验收。

> H05 的完成标准是“数据与 Mock 层可运行、可测试、边界清晰”；不是“JPEG 已能在真机显示”或“完整 AI 语音问答已实现”。
