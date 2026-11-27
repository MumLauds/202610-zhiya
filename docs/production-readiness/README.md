# 芽芽 Agent 2.0 生产准备度审查

- **审查日期** 2026-11-27
- **产品源码基线** `main@48844b2ee267bc539ed0580a358470beca13e449`
- **实验基线** v7.5 第二次候选 `89eb603`，后续配对取证 `1eb9fb4`
- **接入目标** 北京百炼 `qwen3.8-flash`。本轮完成审查和设计，交产品、QA、运维及数据负责人审批。

| 门禁 | 当前判断 | 审批需要的证据 |
|---|---|---|
| Gate 0 实验版本稳定 | **工程证据已达到，待签字** | 可复跑的 v7.5 冻结物、合成用例自动化与北京 API 记录。模型语义缺口单独追踪 |
| Gate 1 Staging Ready | **BLOCKED** | 正式业务适配、接口合同、角色隔离、取消与重放、独立环境以及真实浏览器联调未完成 |
| Gate 2 Controlled Pilot Ready | **BLOCKED** | 家长授权和删除闭环、儿童安全评估、供应商处理确认、数据地域审批、预算与回滚演练未完成 |
| Gate 3 Production Ready | **BLOCKED** | 关键事实质量、真实用户试运行、运行区间内的 SLO 证据与正式审批未完成 |

可以启动本地集成和独立 Staging 的建设。进入 Staging 验收前先签约定、补适配和隔离。当前禁止真实儿童数据进入新链路。

优先沿现有 FastAPI 阅读服务做模块化集成，第三方调用仍经 G08 网关。先以家长确认的共读入口为试点，保留合成夹具和旧版规则回应作为验证与降级基准。香港 ECS 与公网回执均核对到 `48844b2`，现有服务实际模型配置为 `qwen3.6-plus`。北京 Flash 属于 2.0 的接入目标。独立 Staging、源站防护、真实儿童数据与发布门禁见下文。

## 审批材料

| 问题 | 入口 |
|---|---|
| 已有哪些代码、运行和质量证据 | [现状盘点](current-state-inventory.md) · [v7.x 审查](seven-series-assessment.md) · [证据指纹](evidence-manifest.json) |
| 正式系统怎样接入 | [架构审查](architecture-assessment.md) · [数据流与 API](data-flow-and-api-contract.md) |
| 阻断项与后续版本优先级 | [差距和风险](gap-analysis.md) · [内部评分](production-readiness-scorecard.json) |
| 如何做 Staging 与验收 | [Staging 计划](staging-plan.md) · [生产验收计划](production-test-plan.md) |
| 真实数据与服务如何运行 | [隐私和儿童安全](privacy-and-data-governance.md) · [观测和成本](observability-and-cost.md) |
| 如何发布、停用和批准 | [发布和回滚](deployment-and-rollback.md) · [上线清单](go-live-checklist.md) · [决策记录](decision-log.md) |

审查使用本地代码、合成实测归档、正式主分支与公开发布资料。HTTP 200、合法结构、正确语义、端到端体验分别计证。实验包含一次 AI 辅助配对评分，评分规模和来源见 v7.x 审查。当前资料无需生产修改、真实儿童样本或新的供应商调用。

## 统一集成 PR 的审查口径

仓库已准备按会话绑定的迁移桥接、独立 Flash 客户端、桌面与移动回执和默认关闭的 Staging 配置。原评估 42.5/100 保留为历史对照。当前工程复评 50/100，申请人工复核。真实 Staging、儿童试运行、正式切换与旧版退役分别需要后续门禁。 [差距状态](gap-analysis.md) · [发布与退役](deployment-and-rollback.md)
