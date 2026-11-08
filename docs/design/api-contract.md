# 知芽接口契约

版本：v0.4；配套系统、数据库设计v1.1；状态：待团队评审。

服务端Cookie使用Secure/HttpOnly/SameSite=Lax，写入验证CSRF。进入儿童模式时同事务吊销当前设备家长auth_sessions、签发child_grants及版本范围并绑定会话。退出须重新验证账号密码，撤销儿童授权并签发全新家长凭证；旧家长凭证不恢复。

资源ID为JSON正整数；storyVersionId为具体版本，storybookId为稳定本体。详细请求响应、错误及幂等规则见同目录api-contract-v0.4.json。

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

首次结束阅读在同一事务保存会话终态、事实记录和PENDING小结；事务外生成成功后置READY，确定失败置FAILED并保留事实摘要。
API-013读取当前小结；READY返回已生成内容，FAILED只返回事实摘要及空活动数组。FALLBACK仅用于模型/互动结果，不是ReadingReport.status。
同键重放原结束响应，可能仍含PENDING；新键重复结束只返回既有终态及当前小结，不再次调用模型。本阶段不提供失败小结重新生成入口。

默认书架及推荐仅选择有权访问的当前PUBLISHED或APPROVED_PRIVATE版本。每个storybookId至多一个当前生效版本，不限制本人可复读的已审核私有历史版本数。
RETIRED退出当前默认列表；只有storybooks.source_type=ORIGINAL、owner_guardian_id等于当前家长账号且story_versions.reviewed_by_id和reviewed_at均非空的本人私有历史版本，才允许从scope=mine读取并创建新会话。
LIBRARY的RETIRED版本不允许新建会话；仍有权访问的既有有效会话可以继续读取。
儿童访问另要求有效且未到期的授权、同家庭归属和allowedStoryVersionIds包含确切版本ID；不能借历史入口扩大授权。
WITHDRAWN立即拒绝新建会话、既有会话内容、正文和媒体访问；scope=mine和历史记录不能绕过。所有内容入口与媒体入口使用同一版本权限规则。

knowledgeItemId对应具体knowledge_items行；同一itemKey由versionNumber区分版本。所有PATCH均插入新行、新ID，版本号由服务端原子分配递增；包含草稿内容修改和仅审核状态变更。旧行内容、来源及历史event_evidence引用不变。
提交status=APPROVED须由有权内容管理员完成新内容与来源审核，服务端校验并记录reviewed_at，不将客户端状态值本身视为已批准。激活新APPROVED行与退休同itemKey旧APPROVED行在一个事务内完成；新DRAFT行不退休当前APPROVED版本。
API-030创建806/v1(APPROVED)，API-031以806为来源修订得到807/v2(APPROVED)，同itemKey=guided-observation；806转RETIRED，已有event_evidence.knowledge_item_id仍为806，新检索使用807。
