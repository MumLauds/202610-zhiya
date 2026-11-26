# 芽芽 Agent 2.0 v6 实施计划

## 冻结 v5 基线
v5 交付包 SHA-256：`08afdc8e7773ff2d883185d0f1e6ee64d63563426e1a2f8853741fb23b01cd67`。保留原包和原始实测。审查链路：`Demo.start/turn` → `make_prompt` → `BailianProvider` → 协议 B → 截止时间/结构回退 → 网页记时＋可选留档。旧卡在服务端保存实际回复；纽扣卡使用服务器定义的固定历史和每次新会话，不能直接证明多轮或跨页泛化。已有两条北京实跑仅证明这一对的差别；页面标签错配不能算正式 LIVE 页面计时。

## 接口与数据边界（开始实现前确定）
- 独立本地入口，复用 v5 Provider、协议 B 的输出字段、验证、回退和限时；保留 v5 HTTP 与旧卡，新增 v6 路由。无生产 Agent、阅读 API、数据库或部署变更。
- `fixtures_v6.json`：`{synthetic_only:true,books:[{id,title,age,pages:[{id,text,facts:[...]}]}],cases:[{id,book_id,label,turns:[{page,child}]}]}`。开发案 12 条、每案 3–5 轮。讲解/评审 facts 与场景标签只留本地，绝不作为生成证据。真实只接固定合成原话，不提供可误用的自由文本出站。
- `GET /v6/catalog` 返回 mode/books/cases；`POST /v6/start {case_id}` 回 `{session,case_id,page_id,state}`；`POST /v6/page {session,page_id}` 显式翻到脚本下一轮指定页；`POST /v6/turn {session,number}`（从 0 起）按服务端固定输入推进。当前页变化清待答题和本页追问额度，不结束会话。扬弃旧页问题，不把历史草稿当原文。
- 上游 JSON 仍使用 `reply,next_action,followup_question?`；原动作只作结构投影，界面会话状态为 CONTINUE/LISTEN/PAUSE/END_TOPIC，不赋予模型翻页或结束阅读权限。若需要 PAUSE，用统一的向后兼容协议扩展，不依赖词表。历史限长（近 4 轮且标页，当前原文＋若已实际读过的相邻前页原文，不发整书/未来页），保留说话者和原文/交流的区别。
- 开发脚本能复跑和诊断，不作正式留出。冻结系统提示词、参数、脚本/原文与评分口径后，由独立评审编写并保管未见病例，一次性运行；失败原样记录，不写每案补丁。评审需两名真人，区分草稿、核验、服务回执和页面内容。
