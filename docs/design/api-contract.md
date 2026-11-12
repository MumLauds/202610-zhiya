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
