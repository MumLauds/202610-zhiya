# 知芽接口契约

版本：v0.4；配套系统、数据库设计v1.1；状态：待团队评审。

服务端Cookie使用Secure/HttpOnly/SameSite=Lax，写入验证CSRF。进入儿童模式时同事务吊销当前设备家长auth_sessions、签发child_grants及版本范围并绑定会话。退出须重新验证账号密码，撤销儿童授权并签发全新家长凭证；旧家长凭证不恢复。

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
| API-003 退出 | 无请求体 | 204 |
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

## 本期有限续写范围

用户确认本期纳入有限续写；范围限亲子模式、固定源快照、审核新版本和原子切换，整本及各阅读路径仍为4～6页。
确认日期：2026-11-04；范围：本期纳入验收；优先级：P1。
设计修订者：林鑫（102401115）；M05：业务后端与领域Agent沿用现有角色分工；不据此指定具体实现成员。
验收用例：TC-FR17-04 续写原子切换、TC-FR17-05 切换失败恢复；接口链 API-025 → API-026 → API-011。
普通主链路无需触发续写即可完成档案、选书、阅读、互动和结束记录；续写为本期有明确边界的P1流程。
用户范围确认已记录；组长确认及设计PR审核、合并仍待实际完成，不以本文代替正式审批。

## 小结、历史版本与知识修订

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
