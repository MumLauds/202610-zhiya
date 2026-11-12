# G05 Agent 降级与异常处理契约

本文档冻结 Agent 模块在遇到异常时的返回规范，所有返回值必须严格符合 `backend/app/schemas/agent.py` 中的 `AgentResult` 定义。

## 1. 超时（Timeout）
- **触发条件**：模型调用超过 20 秒。
- **处理逻辑**：模型适配器捕获超时，返回 `success=False, error_code="TIMEOUT"`。引导工作流不得直接报错，需返回预设的兜底问句。
- **返回结果**：
  - `status`: "FALLBACK"
  - `strategyType`: "TIMEOUT_FALLBACK"
  - `outputText`: "我们先看看画面上有什么吧？" (根据当前年龄段/上下文返回预设安全语句)

## 2. 无可用知识（No Knowledge）
- **触发条件**：知识检索模块返回 `has_results=False`。
- **处理逻辑**：引导工作流不强行让大模型生成，直接使用通用问题模板。
- **返回结果**：
  - `status`: "FALLBACK"
  - `strategyType`: "NO_KNOWLEDGE_FALLBACK"
  - `outputText`: "这个故事里，你最喜欢谁呀？"

## 3. 触发安全规则（Blocked）
- **触发条件**：用户输入命中 `KN-SAFE` 系列知识规则，或模型输出违规。
- **处理逻辑**：中断大模型调用，直接返回安全拦截话术。
- **返回结果**：
  - `status`: "BLOCKED"
  - `strategyType`: "SAFETY_BLOCK"
  - `outputText`: "这个问题我们请爸爸妈妈来一起说好吗？"

## 4. 主动跳过（Skip）
- **触发条件**：前端发送 `feedback="SKIPPED"`。
- **处理逻辑**：不调用检索模块，不调用大模型。
- **返回结果**：
  - `status`: "FALLBACK"
  - `strategyType`: "SKIP"
  - `outputText`: ""