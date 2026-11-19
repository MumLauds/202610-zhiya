# G04 接口与数据交接核对 v0.4

> 以下保留基线 `0e974ca` 的评审记录。当前结论见文末的[本轮遗留问题处理](#本轮遗留问题处理)。

状态：**待团队评审**。本记录整理可复核的现状和需要确认的问题，不修改 v0.4 契约，不代表设计已冻结或接口已实现。复核基线为团队仓库 main 的 `0e974ca`（包含 PR #15、#17 和移动端原型回退），复核日期为 2026-11-09；由姚华俊使用 Codex 辅助整理。

## 已核对的范围

- [接口目录](api-contract.md)和[接口示例](api-contract-v0.4.json)均为 v0.4，配套系统与数据库设计 v1.1；32 个接口的编号、方法、路径和权限一致。
- 共读主链可按 API-011 创建会话、API-015 开始和翻页、API-017 引导、API-016 提问、API-019 结束、API-013 查看会话与小结来讨论；示例是格式约定，不是运行结果。API-017 说明由前端按需发送 `INITIAL`，不能把读取会话或开始事件当成隐式提问。
- [种子数据](../../data/seed/)当前包含 21 本书、21 个版本、110 页和 20 条知识；其中 20 本、104 页为原创样例，另有 1 本、6 页的旧联调用例。绘本和版本 ID、分页所属版本、非空下一页引用已作静态核对。
- [知识库 v2.5](../../knowledge-base_v2.5/) 已统一目录与文档版本；20 条封面映射均指向该目录下存在的 PNG，20 条知识的来源链接和行号已对照对应条目核验。知识数据仍为 `DRAFT`，不能据此认定内容审核已完成。
- 移动端原型曾在 `4d84dda` 版本入库，其[历史 README](https://github.com/MumLauds/202610-zhiya/blob/4d84dda23e38c8d06fab67143c6acb2d186a5543/prototype/mobile-web/README.md)引用知识库 v2.0，原型使用独立的本地封面资源、浏览器本机记录和语音；它不是正式前后端联调结果。上游 `0e974ca` 已回退移除该原型目录，当前 main 无该 README。若前端后续恢复或另行维护原型，知识库版本与资源引用由前端单独同步。

## 需要共同确认的交接点

以下是评审问题，不表示已由任何一方批准。业务后端牵头与前端、林鑫及两位 Agent 成员确认；涉及素材或审核流程的问题还需相应负责人参加。

1. **书架与封面字段。** API-008/009 的响应示例包含版本 ID、书名和分页，但没有显式封面字段；v2.5 素材由 `cover_mapping.json` 按 `storybook_id` 映射到仓库路径，历史移动端原型使用独立的本地 `cover`、年龄段和主题。请确认书架列表和详情各需要哪些字段、封面如何变成可访问的受控媒体资源，以及 `storybookId` 与 `storyVersionId` 的使用边界。不要直接把仓库文件路径当作已部署的 URL。
2. **逐页资源与审核。** 104 页原创数据目前引用尚未入库的 `/assets/placeholder.png`；6 页旧联调用例的 `image_ref` 是画面描述。请确认导入时如何转换成符合 API-024 权限规则的资源键、缺图如何显示，以及实际素材和授权由谁确认。20 条知识均为 `DRAFT`，正式检索前需按审核流程处理。
3. **冲突与重放。** 通用 `requestExecution` 规定请求摘要涉及 `expectedRevision`，并让会话事件共用幂等范围；API-019 结束请求示例只有 `reason` 和 `clientEventId`。请确认结束请求是否也要求 `expectedRevision`、冲突时如何响应，并同步示例和实现。首次创建事件示例通常为 201，通用成功重放为 200；请前后端确认重放处理。
4. **错误处理。** 契约列出了通用 HTTP 状态和错误格式，部分接口约束给出了具体错误；其余接口引用通用错误表。请按登录失败、权限不足、参数错误、资源不存在、版本冲突及依赖不可用等场景确认接口级返回及页面处理，不把尚未商定的映射当作既定规范。
5. **档案、标识与状态。** 确认原型需要保存的档案字段与 API-004～007 一致；阅读会话使用的 `childProfileId`、`storyVersionId`、页码和 `revision` 由双方按同一约定维护。明确断线恢复时以 API-013 返回的服务端状态为准的交互方式。

## 交付前检查

- 记录上述问题的决定、确认人和日期；涉及契约变更时同时更新 Markdown、JSON 示例和相关设计资料。
- 用最终约定准备可复现的成功、权限拒绝、参数错误、重复请求和版本冲突用例，代码实现后再记录真实运行结果。
- 数据导入、真实媒体服务、前后端联调和团队确认尚未由本次静态核对完成。

## 本轮遗留问题处理

2026-11-09 在工作基线 `7fb10d7` 上修订。依据[项目设计与开发规范 v1.1](https://github.com/MumLauds/202610-zhiya/blob/4ee560cf577c34a5084a1f361adcc9d88baa84c8/docs/standards/%E7%9F%A5%E8%8A%BD_%E9%A1%B9%E7%9B%AE%E8%AE%BE%E8%AE%A1%E4%B8%8E%E5%BC%80%E5%8F%91%E8%A7%84%E8%8C%83_%E7%B2%BE%E7%AE%80%E7%89%88_v1.1.md)及[需求规格说明书 v0.2](../requirements/软件需求规格说明书.md)，涉及接口契约 v0.4、系统与数据库设计 v1.1、知识库 v2.5。以下为本轮修订，待团队评审。后端实现及用例执行待交付。

| 需求与用例 | 修改原因与处理 | 影响模块、数据与负责角色 | 实现后验收（待执行） |
| --- | --- | --- | --- |
| FR-11 · TC-FR11-01 | 20 个原创种子版本缺少发布所需的逐页素材和审核记录，改为 `DRAFT`。20 张已交付图片用作封面，发布前须配齐逐页素材并核对使用授权。 | M08 内容与知识管理，API-024/028、`story_versions`、`story_pages`。知识工程交接素材和授权，业务后端承接审核与发布。 | 未审核或缺图草稿拒绝发布，公开端不可见。管理员审核合格后，审核人、审核时间和发布状态在同一事务落库。 |
| AI-01、AI-07 · TC-AI01-01、TC-AI07-01 | 统一全龄、任务标签大小写和知识停用规则。知识检索限当前有效的 `APPROVED` 版本，匹配本年龄段或全龄；`taskTypes` 和 `task_types_json` 使用同一组大写标签。数据库设计表 4-19 补充显式停用的约束。 | M04 阅读智能体、M08 内容与知识管理，API-029～031、`knowledge_items`、`event_evidence`。领域智能体和知识工程核对检索及来源，业务后端承接版本事务。 | 验证同龄和全龄条目可命中，候选不足时触发安全回退。停用当前批准版后从新检索排除且历史引用可追溯；旧版本 ID 返回 `409`，停用请求混入其他字段返回 `422`。 |
| FR-09、FR-12 · TC-FR09-01、TC-FR12-01 | 进程中断会留下 `PENDING` 小结。首版单工作进程启动时把遗留小结置为 `FAILED`，保留事实摘要和原结束响应，沿用运行时超时规则。 | M03 阅读会话、M07 阅读反馈，API-013/019、`reading_reports`、`request_executions`。业务后端承接恢复和幂等，领域智能体承接正常生成。 | 正常生成成功后读取 `READY`；结束提交后中断并重启，读取 `FAILED`、原事实摘要和空活动数组。同键重放原响应，新键返回当前小结，模型调用次数不增加。 |

数据库时序图 5-1 描述一次正常阅读到结束的小结处理。应用启动恢复另由[接口契约](api-contract.md)定义。知识任务标签的具体值见[公共术语与枚举](../standards/glossary.md)。

### 静态核对结果

- 两份契约的 32 条接口编号、方法、路径与权限逐项一致。已检查全龄、知识停用和小结恢复的结构化示例。
- 21 个绘本版本和 20 条知识均为 `DRAFT`。核对了 110 页及后继引用、20 张封面 PNG 签名和 20 条知识来源的本地行号。后台原型的版本 ID 和状态、知识条目的状态、任务标签、正文和来源与种子一致。
- 数据库设计 PDF 共 39 页。逐页比对仅表 4-19 所在第 26 页的知识版本单元格有文字和画面差异。页签、书签和 152 个导航链接一致。

在仓库根目录可用 PowerShell 复核差异、草稿状态、标签集合和接口目录。`Compare-Object` 无输出表示两份接口目录的条目一致。

```powershell
git -c core.whitespace=cr-at-eol diff --check
$versions = Get-Content data/seed/story_versions.json -Raw -Encoding UTF8 | ConvertFrom-Json
$knowledge = Get-Content data/seed/knowledge_items.json -Raw -Encoding UTF8 | ConvertFrom-Json
$versions | Group-Object status
$knowledge | Group-Object status
$knowledge.task_types_json | Sort-Object -CaseSensitive -Unique
$endpoints = (Get-Content docs/design/api-contract-v0.4.json -Raw -Encoding UTF8 | ConvertFrom-Json).endpoints
$catalogue = Get-Content docs/design/api-contract.md -Encoding UTF8 | Where-Object { $_ -match '^\| API-\d+' }
$rows = $endpoints | ForEach-Object { "| $($_.interfaceId) | $($_.method) $($_.path) | $($_.permission) |" }
Compare-Object $catalogue $rows -CaseSensitive
```

书架封面字段、结束请求的修订号与重放约定、错误处理和断线恢复仍按原交接点确认。实现交付后再执行数据导入、媒体访问和接口验收。
