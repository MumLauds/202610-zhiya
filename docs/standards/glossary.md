# 知芽公共术语与枚举

本文按项目设计与开发规范第 3.8 节统一本轮交接涉及的公共术语、标识语义与枚举。条目取自当前设计和接口约定，随设计稿评审。[项目设计与开发规范 v1.2](知芽_项目设计与开发规范_精简版_v1.2.md)

接口字段及路径见[接口契约](../design/api-contract.md)。物理字段和关系约束见[数据库设计说明书](../design/妈妈夸我说的队_数据库设计说明书.pdf)。

## 公共术语与标识

| 术语 | 对外标识或字段 | 含义 |
| --- | --- | --- |
| 绘本 `Storybook` | `storybookId` | 一本绘本跨版本的稳定标识。 |
| 绘本版本 `StoryVersion` | `storyVersionId` | 具体图文版本的标识。阅读会话、页面、分支和授权以该版本为准。 |
| 阅读会话 `ReadingSession` | `readingSessionId` | 一次阅读的标识，绑定阅读档案和绘本版本。 |
| 阅读小结 `ReadingReport` | `readingSessionId` | 由关联会话定位，每个会话至多一份。 |
| 知识版本 `KnowledgeItem` | `knowledgeItemId` | 一条具体知识版本的标识。`itemKey` 关联同一知识条目的各版本。 |
| 互动引用依据 `EventEvidence` | `interactionEventId` 与 `knowledgeItemId` | 记录互动实际使用的知识版本，历史引用保留该版本标识。 |
| 展示版本号 | `versionNumber` | 在同一本绘本或同一知识条目内区分版本。 |

资源标识对外使用 JavaScript 对象表示法（JavaScript Object Notation，JSON）正整数，范围为 `1` 至 `9007199254740991`。

## 年龄段

| `ageBand` 值 | 含义 |
| --- | --- |
| `AGE_3_4` | 3～4 岁。 |
| `AGE_4_5` | 4～5 岁。 |
| `AGE_5_6` | 5～6 岁。 |
| `null` | 知识条目适用于系统覆盖的所有年龄段。 |

幼儿阅读档案使用具体年龄段。知识管理接口中 `null` 的提交和返回规则见[接口契约](../design/api-contract.md)。

## 知识适用标签

`taskTypes` 是知识适用标签数组。数据库的 `task_types_json` 使用相同的值。标签值采用 `UPPER_SNAKE_CASE`，按精确值匹配。

| 标签 | 含义 |
| --- | --- |
| `GUIDANCE` | 阅读引导方法及引导所需的年龄发展特点。 |
| `RETRIEVAL` | 知识标签和检索规则。 |
| `SAFETY` | 儿童安全和互动内容的安全规则。 |

标签分组依据[种子数据](../../data/seed/knowledge_items.json)和[知识库知识条目 v2.5](../../knowledge-base_v2.5/知识库知识条目_v2.5.md)。

## 内容来源与状态

| 对象或字段 | 枚举值 | 含义 |
| --- | --- | --- |
| 绘本来源 `sourceType` | `LIBRARY` | 由内容管理员维护的库内绘本，符合许可和审核条件后可发布。 |
| 绘本来源 `sourceType` | `ORIGINAL` | 家长账号名下的原创绘本，私有批准由所属家长完成。 |
| `StoryVersion.status` | `DRAFT` | 草稿。 |
| `StoryVersion.status` | `PENDING_REVIEW` | 等待审核。 |
| `StoryVersion.status` | `CHECK_FAILED` | 内容检查未通过。 |
| `StoryVersion.status` | `PUBLISHED` | 经内容管理员审核发布的库内版本。 |
| `StoryVersion.status` | `APPROVED_PRIVATE` | 经所属家长批准的私有版本。 |
| `StoryVersion.status` | `RETIRED` | 退出当前默认列表，历史访问按来源和授权校验。 |
| `StoryVersion.status` | `WITHDRAWN` | 已撤回，停止版本内容和媒体访问。 |
| `ReadingSession.endReason` | `TIME_LIMIT` | 已开始的儿童阅读达到授权截止时刻，正常完成。 |
| `ReadingSession.endReason` | `GRANT_REVOKED` | 儿童会话失去阅读授权而中断，包括尚未开始就达到授权期限。到期儿童凭证仍可供所属家长验证密码解锁。 |
| `ReadingSession.endReason` | `CONTENT_WITHDRAWN` | 未终结的阅读因其固定版本被撤回而中断。 |
| `KnowledgeItem.status` | `DRAFT` | 知识草稿，供管理审核。 |
| `KnowledgeItem.status` | `APPROVED` | 当前可用于领域知识检索的审核版本。同一 `itemKey` 至多一版。 |
| `KnowledgeItem.status` | `RETIRED` | 停止参与新检索，保留历史引用。 |
| `ReadingReport.status` | `PENDING` | 已保留阅读事实，等待小结生成结果。 |
| `ReadingReport.status` | `READY` | 小结生成成功。 |
| `ReadingReport.status` | `FAILED` | 小结生成失败或启动恢复收尾，返回事实摘要和空活动数组。 |

具体状态流转、访问权限和错误处理见[接口契约](../design/api-contract.md)及[系统设计说明书](../design/妈妈夸我说的队_系统设计说明书.pdf)。
