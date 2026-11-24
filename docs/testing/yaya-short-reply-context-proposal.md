# 短答承接后的上下文摘要：待评审设计

## 现状与范围

现有共读服务会跳过本地承接轮，不把承接文案或孩子原话当作模型上下文。这避免了泄露，但后续 Qwen 不知道芽芽刚刚接过一次话。本设计只在测试里验证一个候选投影，不修改生产的摘要生成、API-015/API-017、数据库或 Qwen 接入状态。

## 候选投影

每次调用至多保留**当前访问中最近两条**可用回合，按事件顺序输出。仅允许四个字段：

| 字段 | 封闭取值 | 含义 |
| --- | --- | --- |
| `questionIntent` | `OBSERVATION`、`PLOT`、`CAUSAL`、`EMOTION`、`IMAGINATION`、`LIFE_CONNECTION`、`UNKNOWN` | 已展示问题的意图，不传题目全文 |
| `answerRelation` | `SUPPORTED`、`PARTIAL`、`CONFLICT`、`UNCERTAIN`、`IMAGINATIVE`、`UNKNOWN` | 本地判定的回答关系；证据不足时不得报支持、部分支持或冲突 |
| `locallyAcknowledged` | JSON 布尔值 | 服务端已展示或保存本地承接；仅承接不代表答对 |
| `evidenceVerified` | JSON 布尔值 | 有依据支持本地关系判定；不传证据 ID 或正文，反向证据也不代表答对 |

例如：在观察问题后，孩子说出一个未核实的角色，未来可只传：

```json
{"questionIntent":"OBSERVATION","answerRelation":"UNKNOWN","locallyAcknowledged":true,"evidenceVerified":false}
```

不要从承接句式反推 `locallyAcknowledged`，也不要从名词在正文出现推断 `evidenceVerified`。若在未来接入，标记应来自可信的本地决策结果；无法可靠恢复时宁可省略该条。不能靠这个标记推断已回答焦点、翻页或新增追问。

## 边界

- 在服务端鉴权后，用会话、绘本版本、页码和页面到达事件过滤。离开再返回同页时，上一轮访问不会进入摘要。原始标识只做本地过滤，不发送。
- 只从已完成、可展示的回合取值；丢弃被安全拦截的回合，按持久化事件去重，不用同键重放扩充摘要。
- 从封闭枚举和布尔值显式构造对象，拒绝额外字段和字符串冒充布尔值。原答、转录、承接文案和隐私信息不进入数据对象、日志或模型请求。
- 这是未来给 Qwen 的有限状态提示。只提示「已经接过话」，无法复原孩子说了哪个词。既有事实核验和安全降级不能依赖或由这个摘要替代。

`backend/tests/test_short_reply_context_contract.py` 只验证设计约束，未接入阅读器或实际调用模型。历史存储方式保持不变。
