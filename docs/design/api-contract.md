# 知芽接口契约

版本：v0.4；配套系统、数据库设计v1.1；状态：待团队评审。

会话 Cookie `zhiya_session` 使用 Secure、HttpOnly 和 SameSite=Lax。受保护的写请求须在 `X-CSRF-Token` 中提交可读 Cookie `zhiya_csrf` 的值。进入儿童模式时，在同一事务内吊销当前设备的家长 auth_sessions、签发 child_grants 及版本范围并绑定会话。恢复家长权限时重新验证账号密码，撤销儿童授权并签发全新家长凭证。

儿童设备的 Cookie 保留 24 小时。业务访问到 `expiresAt` 截止。持有未吊销儿童凭证的设备使用 API-021 验证所属家长密码，业务时限届满后仍可解锁。此时调用普通登录 API-002 返回 403 `FORBIDDEN`。

公共术语、标识语义与枚举见[公共术语表](../standards/glossary.md)。详细请求响应、错误及幂等规则见[接口示例](api-contract-v0.4.json)。

| 编号 | 方法与路径 | 权限 |
| --- | --- | --- |
| API-001 | POST /api/v1/auth/registrations | 匿名 |
| API-002 | POST /api/v1/auth/sessions | 匿名 |
| API-003 | DELETE /api/v1/auth/sessions/current | 家长或当前受限儿童 |
| API-004 | GET /api/v1/child-profiles | 已认证家长且归本人 |
| API-005 | POST /api/v1/child-profiles | 已认证家长 |
| API-006 | PATCH /api/v1/child-profiles/{profile_id} | 已认证家长且归本人 |
| API-007 | DELETE /api/v1/child-profiles/{profile_id} | 已认证家长且归本人 |
| API-008 | GET /api/v1/storybooks | 家长或绑定故事范围内儿童 |
| API-009 | GET /api/v1/story-versions/{version_id} | 家长或绑定故事范围内儿童 |
| API-010 | POST /api/v1/recommendations | 已认证家长 |
| API-011 | POST /api/v1/reading-sessions | 已认证家长且档案归本人 |
| API-012 | GET /api/v1/reading-sessions | 已认证家长且归本人 |
| API-013 | GET /api/v1/reading-sessions/{session_id} | 本人家长或绑定会话儿童 |
| API-014 | DELETE /api/v1/reading-sessions/{session_id} | 已认证家长且归本人 |
| API-015 | POST /api/v1/reading-sessions/{session_id}/events | 本人家长或绑定会话儿童 |
| API-016 | POST /api/v1/reading-sessions/{session_id}/questions | 本人家长或绑定会话儿童 |
| API-017 | POST /api/v1/reading-sessions/{session_id}/guidance | 本人家长或绑定会话儿童 |
| API-018 | POST /api/v1/reading-sessions/{session_id}/branches | 本人家长或绑定会话儿童 |
| API-019 | POST /api/v1/reading-sessions/{session_id}/completions | 本人家长或绑定会话儿童 |
| API-020 | PUT /api/v1/reading-sessions/{session_id}/mode-settings | 已认证家长且归本人 |
| API-021 | POST /api/v1/auth/parent-unlocks | 重新认证的家长 |
| API-022 | POST /api/v1/voice/transcriptions | 本人家长或绑定会话儿童 |
| API-023 | POST /api/v1/voice/syntheses | 本人家长或绑定会话儿童 |
| API-024 | GET /api/v1/media/{resource_key} | 有效会话且拥有资源访问权 |
| API-025 | POST /api/v1/story-generations | 已认证家长 |
| API-026 | POST /api/v1/story-versions/{version_id}/approvals | 已认证家长且草稿归本人 |
| API-027 | POST /api/v1/admin/storybooks | 内容管理员 |
| API-028 | PATCH /api/v1/admin/story-versions/{version_id} | 内容管理员 |
| API-029 | GET /api/v1/admin/knowledge-items | 内容管理员 |
| API-030 | POST /api/v1/admin/knowledge-items | 内容管理员 |
| API-031 | PATCH /api/v1/admin/knowledge-items/{knowledge_id} | 内容管理员 |
| API-032 | GET /api/v1/health | 匿名 |

## G04 最小业务接口评审稿

2026-11-12，基线 `8c4172c`。下列 DCL-005～008 是供前端、业务后端、设计及 Agent 对照的候选约定。团队确认后同步到正式端点约束。JSON 中 `minimumBusinessReview` 对应本节，原接口目录的编号、方法、路径和权限继续适用。

### 首阶段字段与成功响应

资源 ID 采用公共整数范围，页码从 1 开始，`revision` 和 `expectedRevision` 用非负整数。JSON 成功响应使用 `{data, requestId}`，204 无响应体，API-024 成功返回二进制。登录成功由服务端设置会话 Cookie，受保护的写操作沿用公共 CSRF 约定。

| 接口 | 请求字段 | 成功响应 `data` |
| --- | --- | --- |
| API-001 / 002 注册与登录 | `account`、`password` 必填字符串。建议账号去除两端空白后 3～64 字符并保持大小写，密码 8～128 字符且原样提交 | 201，`accountId` |
| API-003 退出 | 无请求体。家长退出会撤销本机家长登录及该账号名下全部儿童授权，其他设备的家长登录继续有效。儿童退出只撤销当前儿童授权 | 204 |
| API-004 档案列表 | 无请求体 | 200，`items` 为档案对象数组 |
| API-005 新建档案 | `ageBand`、`preferredMinutes` 必填，`nickname` 和 `interests` 可省略 | 201，档案对象 |
| API-006 修改档案 | 路径 `profile_id`。至少提供 `nickname`、`ageBand`、`interests`、`preferredMinutes` 之一，省略字段保留原值 | 200，更新后的完整档案对象 |
| API-007 删除档案 | 路径 `profile_id`，无请求体 | 204，删除范围按现行数据清理策略 |
| API-008 绘本列表 | 可选查询 `ageBand`、`topic`、`scope=mine`、`page`、`pageSize`。页码默认 1，每页默认 20、最多 50 | 200，`items` 为绘本版本对象数组，附 `page`、`pageSize` |
| API-009 绘本详情 | 路径 `version_id`，无请求体 | 200，绘本版本对象 |
| API-011 创建共读会话 | `childProfileId`、`storyVersionId`、`mode=PARENT_CHILD` 必填 | 201，会话对象，`status=CREATED`、`revision=0` |
| API-012 会话列表 | 可选 `page`、`pageSize`，规则同 API-008 | 200，`items` 为会话对象数组，附 `page`、`pageSize` |
| API-013 读取或恢复会话 | 路径 `session_id`，无请求体 | 200，`session`、`events`、`report`，未生成小结时 `report=null` |
| API-015 开始与翻页 | 路径 `session_id`。必填 `eventType=STARTED/PAGE_CHANGED`、`pageNumber`、`expectedRevision`、`clientEventId`（1～64 字符） | 首次 201，`interactionEventId`、更新后的 `revision`。同键成功重放 200 |
| API-019 结束 | 路径 `session_id`。必填 `reason=USER_FINISH`、`expectedRevision`、`clientEventId`（1～64 字符） | 首次和成功重放均为 200，`session`、`report` |
| API-024 媒体 | 路径 `resource_key`，无请求体 | 200，资源二进制及实际 `Content-Type` |

### DCL-005 封面与媒体

API-008/009 保留现有版本对象字段 `storybookId`、`storyVersionId`、`versionNumber`、`title`、`status`、`pages`、`branches`。建议新增 `coverRef`（字符串或 null）。`pages` 项包含 `pageNumber`、`text`、`imageRef`、`defaultNextPageNumber`，后继页为空时为 null。封面或内页缺图时对应字段返回 null，前端显示本地占位图。有素材时返回服务端分配、绑定该版本的资源键，由 API-024 读取。封面导入可按[封面映射](../../data/seed/cover_mapping.json)的 `storybook_id` 找到素材，仓库路径和种子占位值仅用于导入。素材可用性、授权和发布仍按 DCL-002。

数据库的 `story_pages.image_ref` 保持 `TEXT NOT NULL`。API-008/009 校验版本访问权后，按版本和页号解析该字段。仅当引用能解析为该版本中已核验可用的图片时，`imageRef` 才返回受控媒体键。种子中的 `/assets/placeholder.png`、旧版页面的画面描述，以及确认缺少素材的引用，都序列化为 `imageRef=null`。解析依赖暂不可用时返回下表的 503。API-024 仍逐次核验版本和资源访问权，过期或不可访问的资源键按现有规则处理。

### DCL-006 结束并发与幂等

通过鉴权、资源归属及请求格式校验后，先查会话内的 `clientEventId`。同键同请求重放已保存的原响应数据，同键不同请求（包括修订号不同）返回 409 `IDEMPOTENCY_CONFLICT`。新键遇到 `FINISHED` 时返回已有终态及当前小结，缺少小结时返回 null。新键对 `IN_PROGRESS` 会话首次结束，需在事务内比较 `expectedRevision` 并原子写入终态、递增修订号、事实摘要、小结和重放记录。其他会话状态、或翻页先提交导致修订号不匹配时，返回 409 `SESSION_CONFLICT`，前端读取 API-013 再决定是否以新键提交。若结束先提交，随后的新翻页请求返回 409。成功重复结束和重放均沿用小结生命周期约定。

### DCL-026 小结事实与内容安全（2026-11-17 实现约束，待评审）

API-019 仍先提交含事实摘要的 PENDING 小结；API-013 读取最新状态，同键重放和新键重复结束的规则不变。依据 FR-09、AI-06、NFR-09，当前实现仅用服务端已读页记录组成事实摘要，不能从页面正文或模型输出推断答题数、情绪、投入程度或能力诊断。

模型只在固定的温和共读建议中选择编号，内部返回 `{"suggestionId":"NONE"}`、`REVISIT_PAGE` 或 `TALK_ABOUT_STORY`；该字段不加入公共 API。服务端校验 JSON 对象仅有 suggestionId、编号有效、有已读正文才可选择非 NONE 建议，组合后的 summary 不超过 500 字符。自由文本、额外字段、未知编号及模型失败均转 FAILED，保留原事实摘要，不发布模型原文，也不再次调用模型。READY 仅发布服务端事实与固定建议；activities 仍为空，FR-10/AC-09 的材料和步骤尚未实现。

这是一项限制输出范围的安全修复，不是通用自由文本事实核验器。外部字段、数据库结构与状态枚举不变；不改写既有 READY 历史记录。验证和后续范围见[小结安全检验](../testing/report-safety-checks.md)。

```mermaid
flowchart LR
    A[结束事务保存事实与 PENDING] --> B[事务外模型选择建议编号]
    B --> C{结构、编号与长度校验}
    C -->|通过| D[事实与固定建议组成 READY]
    C -->|失败| E[FAILED 保留原事实摘要]
```

### DCL-007 错误结构与前端反馈

JSON 错误统一使用 `{error: {code, message, retryable}, requestId}`。前端按状态和错误码分支，展示面向用户的提示。公共错误表中已有的错误语义继续适用。下面为首阶段映射建议。

| HTTP | 场景及错误码 | 前端处理 |
| --- | --- | --- |
| 401 | 受保护接口无有效凭证 `AUTH_REQUIRED`，API-002 凭证错误 `INVALID_CREDENTIALS` | 恢复认证或提示检查凭证 |
| 403 | 已认证但角色或儿童模式受限 `FORBIDDEN`，写操作校验失败 `CSRF_INVALID` | 提示权限受限或刷新认证上下文 |
| 404 | API-006/007/009/011/013/015/019/024 引用的资源不存在或不可访问 `RESOURCE_NOT_FOUND` | 提示资源不可用，媒体使用本地占位 |
| 409 | API-001 重复账号 `ACCOUNT_EXISTS`。API-015/019 状态或修订冲突 `SESSION_CONFLICT`，同键不同请求 `IDEMPOTENCY_CONFLICT`，同键执行中 `REQUEST_IN_PROGRESS` | 冲突时先读取会话。执行中按 `Retry-After` 原样重发 |
| 422 | 字段格式、枚举或必填项不符合约定 `VALIDATION_ERROR` | 提示检查表单或请求字段 |
| 429 | API-002 登录受限 `RATE_LIMITED` | 按 `Retry-After` 等待后再尝试 |
| 503 | 依赖暂不可用 `DEPENDENCY_UNAVAILABLE`，沿用 API-032 的错误码 | 提示稍后重试。读取可重试，已知未提交的写入可由用户重试 |
| 504 | 写入结果不明 `REQUEST_OUTCOME_UNKNOWN` | 查询会话，保留原请求标识，不自动创建新请求 |

上述场景中 `REQUEST_IN_PROGRESS`、`RATE_LIMITED` 和可重试的 503 读取响应使用 `retryable=true`，其余默认 false。API-032 健康检查继续返回现有的 503 和 `retryable=true`。API-015/019 的写入结果不明时返回 504 `REQUEST_OUTCOME_UNKNOWN`，由前端先查询会话状态。查不到私有资源和资源不属于当前账号使用相同的 404。

### DCL-008 档案与断线恢复

档案对象包含 `childProfileId`、`nickname`（可空）、`ageBand`（`AGE_3_4/AGE_4_5/AGE_5_6`）、`interests`（字符串数组，可为空）、`preferredMinutes`（1～30 整数）。建议昵称去除两端空白后 1～32 字符，创建省略时返回 null，修改时显式 null 清除昵称。兴趣最多 10 项，每项去除两端空白后 1～32 字符。创建省略兴趣时用空数组，修改传空数组清除兴趣。

会话对象包含 `readingSessionId`、`storyVersionId`、`currentPage`、`visitedPageNumbers`、`mode`、`status`、`revision`、`expiresAt`（UTC ISO 8601 字符串或 null）。断线恢复以 API-013 返回的固定版本、当前页、状态及修订号为准。首阶段开始和翻页的 `events` 示例使用 `interactionEventId`、`eventType`、`pageNumber`。家长可查看含 `readingSessionId`、`summary`、`activities`、`status` 的小结。儿童只接收授权范围内的必要交互和进度，API-013 及 API-019 的 `report` 均建议为 null，重放前仍校验调用者权限并按当前角色投影。客户端下一次新写入使用服务端修订号。网络失联或返回 `REQUEST_IN_PROGRESS` 时，可使用原键和原请求体查询式重发。收到明确的 504 后先读取 API-013，由用户根据会话状态决定下一步。

儿童在进行中的会话读取 API-013 时，`data.answerOptions` 列出当前页可以提交的作答文案。取当前会话、绘本版本和页面上最近一条正文非空且结果为 `SUCCESS` 或 `FALLBACK` 的 `QUESTION`。服务端可在该问题的最终响应 `data.answerOptions` 中保存已核验的字符串选项。旧问题省略该字段时提供“是”“不是”“不知道”。页面尚无可展示的问题时，沿用该页已审核的分支选项文案。返回空数组表示当前没有可提交的选项。

API-015 在同一写事务内重新计算允许的选项。儿童的 `ANSWER.inputText` 去除两端空白后须精确匹配其中一项，超出范围返回 `422 VALIDATION_ERROR`。家长在亲子共读时可提交 1～200 字符的文本。成功作答会记录事件并增加修订号，跳转剧情分支使用 API-018。

## 本期有限续写范围

用户确认本期纳入有限续写；范围限亲子模式、固定源快照、审核新版本和原子切换，整本及各阅读路径仍为4～6页。
确认日期：2026-11-04；范围：本期纳入验收；优先级：P1。
设计修订者：林鑫（102401115）；M05：业务后端与领域Agent沿用现有角色分工；不据此指定具体实现成员。
验收用例：TC-FR17-04 续写原子切换、TC-FR17-05 切换失败恢复；接口链 API-025 → API-026 → API-011。
普通主链路无需触发续写即可完成档案、选书、阅读、互动和结束记录；续写为本期有明确边界的P1流程。
用户范围确认已记录；组长确认及设计PR审核、合并仍待实际完成，不以本文代替正式审批。

## 小结、历史版本与知识修订

儿童模式按服务端时限结算。家长的请求仅处理本人家庭，儿童的请求仅处理当前授权。会话已开始且绘本仍可读时，到期记为 `FINISHED / TIME_LIMIT`，结束时间取授权截止时刻。在提交终态、事实和小结后才派发模型任务。待开始会话到期记为 `INTERRUPTED / GRANT_REVOKED`。若未终结会话的绘本已撤回，记为 `INTERRUPTED / CONTENT_WITHDRAWN`。这两种中断均不生成小结。API-013 为儿童返回绑定会话当前页的事件和 `report=null`，家长可查看本人会话的全部事件及小结。

首次结束阅读在同一事务保存会话终态、事实记录、PENDING小结和供同键重放的原响应。事务外按既有文本模型调用超时生成；仅当小结仍为PENDING时，成功置READY，确定失败置FAILED并保留事实摘要。
首版单工作进程启动或重启时，在应用就绪前将遗留的PENDING小结条件更新为FAILED，保留事实摘要并将activities置为空数组。恢复不调用模型，保留会话终态、事实记录及原结束响应。
API-013读取当前小结。READY返回已生成内容；PENDING或FAILED返回事实摘要及空活动数组。FALLBACK仅用于模型/互动结果，不是ReadingReport.status。
同键重放原结束响应，可能仍含PENDING；新键重复结束只返回既有终态及当前小结，不再次调用模型。本阶段不提供失败小结重新生成入口。

默认书架及推荐仅选择有权访问的当前PUBLISHED或APPROVED_PRIVATE版本。每个storybookId至多一个当前生效版本，不限制本人可复读的已审核私有历史版本数。
RETIRED退出当前默认列表；只有storybooks.source_type=ORIGINAL、owner_guardian_id等于当前家长账号且story_versions.reviewed_by_id和reviewed_at均非空的本人私有历史版本，才允许从scope=mine读取并创建新会话。
LIBRARY的RETIRED版本不允许新建会话；仍有权访问的既有有效会话可以继续读取。
儿童访问另要求有效且未到期的授权、同家庭归属和allowedStoryVersionIds包含确切版本ID；不能借历史入口扩大授权。
WITHDRAWN立即拒绝新建会话、既有会话内容、正文和媒体访问；scope=mine和历史记录不能绕过。所有内容入口与媒体入口使用同一版本权限规则。
API-028的发布操作仅适用于许可完备的LIBRARY版本，须由内容管理员执行。发布时在同一事务记录服务端鉴权得到的管理员账号ID为reviewed_by_id、服务器时间为reviewed_at，并将版本置为PUBLISHED。

知识版本与标识的含义见公共术语表。所有PATCH均插入新行及新ID，版本号由服务端原子分配递增。草稿内容修改和仅审核状态变更均遵循此规则。旧行内容、来源及历史event_evidence引用不变。

知识ageBand的取值见公共术语表。API-030必须提供该字段；API-031省略时沿用路径所指版本的值，显式传null时新版本适用全龄。API-029至API-031响应中的全龄知识返回ageBand: null。
taskTypes为知识适用标签数组，取值见公共术语表的知识适用标签。task_types_json使用相同的UPPER_SNAKE_CASE值，标签按精确值匹配。
领域知识检索仅使用APPROVED版本。按具体年龄段初筛时仅匹配该年龄段与全龄知识，不扩展到其他年龄段。候选还须符合主题及知识适用标签筛选，再按关键词相关度取有限片段；候选不足时沿用该任务的安全回退。
提交status=APPROVED须由有权内容管理员完成新内容与来源审核，服务端校验并记录reviewed_at，不将客户端状态值本身视为已批准。激活新APPROVED行与退休同itemKey旧APPROVED行在一个事务内完成；新DRAFT行不退休当前APPROVED版本。
API-031停用请求只提交status=RETIRED，混入其他字段返回422。路径必须指向该itemKey当前APPROVED版本；已存在但不再是当前APPROVED的ID返回409。服务端在同一事务内按目标ID和status=APPROVED条件更新为RETIRED，须恰好更新一行，再插入同itemKey的RETIRED新版本。新版本复制内容和来源，响应返回新ID。任一步失败则回滚；成功后该itemKey退出新检索，历史引用继续指向实际使用的版本。
API-030创建806/v1(APPROVED)，API-031以806为来源修订得到807/v2(APPROVED)，同itemKey=guided-observation；806转RETIRED，已有event_evidence.knowledge_item_id仍为806，新检索使用807。继续对807提交仅含status=RETIRED的请求后，807转RETIRED、返回808/v3(RETIRED)，该itemKey不再有批准版本，已有引用仍指向806。

## 语音偏好扩展草案

2026-11-11，林鑫 / G08，DCL-013；关联 FR-08/12。以下是待团队评审的增量建议，未冻结、未实现公开路由或档案存储，不改变上表和 v0.4 JSON 的既有接口。内部实现与模型目录见[交接说明](speech-preferences-handoff.md)。

- 候选 `GET /api/v1/voice/options`，接口编号待团队分配，仅已认证家长。服务端使用当前模型的 `get_speech_options(model)`，按既有 `data` / `requestId` 包装返回；不调用付费服务。`data.voices` 为 `{voiceId, displayName, description, category, languages, dialects, scenarios}` 数组，`data.speechRates` 为支持的语速枚举数组，`data.defaultSpeechRate` 为 `NORMAL`。旧模型不再提供可选项；未配置合成时业务层应返回不可用状态，不能把静态目录当作服务可用证明。
- API-006 候选新增 `speechPreferences` 对象，例如 `{"speechPreferences":{"voiceId":"wenhuaiqing_v3.1","speechRate":"SLOW"}}`；API-004 返回同结构。只允许本人家长修改，沿用安全 Cookie、写入防跨站请求与归属校验；他人档案按既有不可见规则返回 404，儿童授权不能写设置。省略整个对象表示不改；提供时两个字段均必填，null、未知字段、未知/跨模型声线和无效语速返回 422，整次修改不部分落库。
- `speechRate` 为 `SLOW` / `NORMAL` / `FAST`，含义是模型合成语速；`voiceId` 是受支持声线的供应商原值，按第三方命名例外保留。前端中文标签由界面映射，不向家长展示底层枚举。
- API-023 继续使用既有已授权事件/页面来源，由业务后端按会话对应档案读取偏好后调用内部网关；不在合成请求开放任意正文、模型名或供应商 URL。请求期间保存偏好变化不改动该请求已取得的快照，下一次合成使用新值；会话与资源权限仍在返回前复核。
- 档案持久化候选为 `child_profiles.voice_id`、`child_profiles.speech_rate`，与上述字段一一映射；约束、默认值和存量迁移由业务后端随数据库设计评审实现。本轮无建列或迁移。新模型默认温怀清 / NORMAL；已有不支持的偏好须明确迁移或提示重选，不能悄悄替换声线。

DCL-014（2026-11-11）目录元数据草案：category 取 MULTILINGUAL_DIALECT（多语种与方言）、CHINESE（精品中文）、ENGLISH（精品英文）、OTHER_SYSTEM（其他系统声线）、OTHER_LANGUAGE（其他语言），旧模型兼容项为 LEGACY。languages、dialects、scenarios 均是显示用中文字符串数组，不是供应商请求参数或适龄认证；空数组表示官方未明确，不代表支持所有语言。displayName 不保证唯一（不同语言可同名），选择、保存和列表键必须使用精确 voiceId，保留其大小写。新增信息只用于目录展示，档案仍只保存 voiceId / speechRate，不增加数据库列。

DCL-017（2026-11-12）范围调整：当前仅保留温怀清、叶知晴、龙牛牛、龙闪闪、龙杰力豆、龙泡泡六种声线，按此顺序返回；对应 voiceId 见交接说明。旧模型与其余声线不可选，原 DCL-014 全量目录仅为历史决策。已有不支持的偏好须重选，不能静默替换；字段结构不变。

DCL-020（2026-11-12）实现进展：API-022/023 与 API-024 音频分支已在本地实现，业务认证未接入时默认拒绝。此处声线选项及档案偏好扩展仍待评审和实现；格式转换与接口边界见[公开语音交接](voice-api-handoff.md)，未修改 v0.4 JSON。

DCL-021（2026-11-12，历史记录，依赖码及读取重试语义已由 DCL-022 更新）G08 错误码调整：同会话语音在途返回 409 `VOICE_REQUEST_IN_PROGRESS`，区别于 v0.4 JSON 中带幂等记录的业务 `REQUEST_IN_PROGRESS`；全局并发仍返回 429 `VOICE_BUSY`。当时资源不可见使用 `RESOURCE_NOT_FOUND`，数据库不可用采用 #28 早期候选名 `SERVICE_UNAVAILABLE`，均为 retryable=false。

DCL-022（2026-11-14）对齐 #28 合并提交 `16bb85c` 的依赖错误约定：API-022/023/024 音频实现统一使用 503 `DEPENDENCY_UNAVAILABLE`。媒体 GET 的依赖读取失败为 retryable=true，仅允许重试原资源读取并重新校验权限，不触发音频合成；识别/合成 POST 在调用前后遇到数据库故障均保持 retryable=false，不自动再次调用供应商。404 `RESOURCE_NOT_FOUND`、语音并发及其他专用码不变，不添加 Retry-After。当前范围不实现图片业务、认证或档案存储；完整错误边界见[公开语音交接](voice-api-handoff.md#语音错误码与重试边界dcl-021)。


DCL-023（2026-11-16）引入 PR #30 的 G07 首批实现（903695e）：共同应用现包含注册、家长登录/退出、档案列表/创建/修改和绘本列表/版本详情。API-022/023/024 音频分支默认复用其真实家长 Cookie/CSRF 校验；数据库就绪时匿名返回 401 AUTH_REQUIRED，写请求缺失或错误 CSRF 返回 403 CSRF_INVALID。读取/写入的依赖失败仍按 DCL-022 区分；新业务响应统一 requestId。不新增语音输入字段、数据库列或儿童认证入口，阅读会话创建、互动持久化、图片分派和偏好存储仍待业务实现。来源、兼容性及验证边界见[交接文档](voice-api-handoff.md#与最新-pr-30-的接入核对)。
