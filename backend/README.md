# 知芽后端最小工程

## 缩写与编号

| 缩写 | 中文名称（英文全称） |
| --- | --- |
| API / IF | 应用程序编程接口（Application Programming Interface）/ 接口分组（Interface） |
| FR / NFR | 功能需求（Functional Requirement）/ 非功能需求（Non-Functional Requirement） |
| AC | 验收准则（Acceptance Criteria） |
| ASR / TTS | 自动语音识别（Automatic Speech Recognition）/ 语音合成（Text-to-Speech） |
| HTTP / HTTPS | 超文本传输协议（Hypertext Transfer Protocol）/ 安全超文本传输协议（Hypertext Transfer Protocol Secure） |
| JSON | JavaScript 对象表示法（JavaScript Object Notation） |
| URL / ID | 统一资源定位符（Uniform Resource Locator）/ 标识符（Identifier） |
| UTF-8 | Unicode 转换格式（Unicode Transformation Format，8-bit） |
| MiB | 二进制兆字节（Mebibyte），1 MiB = 1,048,576 字节 |

本候选版的 FastAPI 已注册 v0.4 契约列出的 32 项操作。家长可登录、选择孩子档案和绘本、开始共读，也可启用儿童限时授权并解锁。API-016 保存主动提问与回答，API-017 生成阅读引导，API-018 保存分支选择，API-019 结束阅读并生成小结。API-022/023 提供转写与合成，API-024 提供受控图片与音频。API-025 支持生成原创及续写草稿，API-026 由家长审核本人的草稿。[G13 联调记录](../docs/integration.md) · [语音接口交接](../docs/design/voice-api-handoff.md)

管理员可通过 API-027/028 维护资料库绘本，通过 API-029～031 管理带来源的知识版本。档案声线与语速偏好存储仍待完成。[接口契约](../docs/design/api-contract.md) · [候选版实测与演示步骤](../docs/testing/p6/课程候选版演示.md)

接口以[当前契约](../docs/design/api-contract.md)及其 JSON 示例为准；[验证记录](../docs/testing/backend-bootstrap-checks.md)说明本轮实际验证范围。

## 本地启动

使用 Python 3.12。以下命令从仓库根目录开始，在 PowerShell 中执行；无需激活虚拟环境。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe database/init_db.py
cd backend
# 只在首次配置时复制，保留已存在的本地配置。
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
..\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --env-file .env --no-access-log
```

Linux/macOS 对应使用 python3.12 创建环境、.venv/bin/python 安装；进入 backend 后使用 ../.venv/bin/python 启动同一 Uvicorn 命令。首次将 .env.example 复制为 .env，已有文件不要覆盖。

访问 http://127.0.0.1:8000/api/v1/health，返回形如：

```json
{"data":{"status":"ok"},"requestId":"服务端生成的追踪标识"}
```

X-Request-ID 响应头与响应体一致。/docs 可查看健康、账号、档案、绘本、阅读会话及语音/音频路由。默认应用使用家长会话或儿童授权进行认证。数据库就绪时，未登录返回 401 AUTH_REQUIRED，缺失或错误 CSRF 的语音写请求返回 403 CSRF_INVALID。显式注入 UnconfiguredVoiceAuthenticator 时仍返回 503 VOICE_AUTH_NOT_CONFIGURED。默认绑定本机、单工作进程。容器包含 FFmpeg；Windows 转换非标准录音需要 PATH 中可用的 FFmpeg。

家长联调：先 POST `/api/v1/auth/registrations` 注册，再 POST `/api/v1/auth/sessions` 登录，两者传 `{account,password}`。客户端保留登录返回的 Cookie，将 `zhiya_csrf` 值放入写请求的 `X-CSRF-Token`；`zhiya_session` 为 HttpOnly，不自行读取或构造。Cookie 有 Secure 属性，应使用 HTTPS（本地需 HTTPS 代理或证书），上面的 HTTP 启动命令仅适合健康检查等无凭据请求。跨域浏览器联调尚未配置 CORS。创建本人档案并取得有权读取的已审核绘本版本后，可 POST `/api/v1/reading-sessions`，传 `{childProfileId,storyVersionId,mode:"PARENT_CHILD"}`；再向 `/{session_id}/events` 提交 STARTED 事件。API-022/023 使用返回的真实会话、当前页和最新 revision 或允许的已保存事件，具体字段见接口契约。会话生命周期的离线联调见 `backend/tests/test_storybooks.py`；`backend/tests/test_voice_auth_integration.py` 验证真实家长认证与模拟供应商，其中会话由测试准备。儿童模式联调在会话开始前调用 API-020 切换设备身份。恢复家长权限时，通过 API-021 提交所属家长凭证和当前 CSRF 令牌。到期、解锁和多设备退出见 `backend/tests/test_child_mode.py`。完整浏览器及真实供应商组合流程仍须联调。

阅读小结（FR-09、AI-06、NFR-09）：事实摘要仅取服务端记录的已读页；模型根据已读正文选择固定建议编号，不直接发布模型自由文本。非法结构、额外字段、未知建议、模型失败或超长结果均置 FAILED，保留事实摘要和空 activities，不重试模型。READY 由事实摘要与固定的自愿共读建议组成，不推断答题数、投入程度或儿童能力。生成成功且有已读正文时，activities 包含一项与已读页关联的活动，含名称、材料和步骤。无已读正文时活动为空。接口说明见[阅读小结契约](../docs/design/api-contract.md)，早期事实摘要的验证见[小结安全检验](../docs/testing/report-safety-checks.md)。

健康检查沿用主分支的数据库就绪规则：数据库缺失、不可读或缺表时返回 503 / DEPENDENCY_UNAVAILABLE（retryable 为 true），不会自动创建数据库；结构就绪时返回 200。它不探测供应商，也不代表业务链路或种子内容已通过验收。三个模型配置均留空时可启动；真实调用会在发送请求之前报告 MODEL_NOT_CONFIGURED。无效的非空 URL、超时或令牌配置会阻止启动，以便及时发现配置错误。

保留 app.main:app 供主分支原有启动命令使用，app.main:create_app --factory 支持测试注入；两者采用相同路由与生命周期。数据库路径由 ZHIYA_DB_PATH 指定（默认 database/instance/zhiya.sqlite3），相对路径以启动时工作目录解析，跨目录启动建议用绝对路径。开发种子导入见根目录 README，不在服务启动时自动导入。

## 模型环境变量

用户于 2026-11-11 将文本改为 DeepSeek 官方服务，语音继续使用百炼。文本默认 deepseek-flash，默认发送 thinking.type=disabled；识别使用北京地域 Qwen-ASR，合成使用支持模型语速的 Qwen-Audio-3.1-TTS-Flash。新合成接口须填写本人业务空间地址，声线与语速配置见[交接说明](../docs/design/speech-preferences-handoff.md)。详见[文本与语音接入说明](bailian.md)。两个供应商的密钥分别配置；切换语音地域须同步核对密钥、端点与音频下载白名单。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| MODEL_API_URL | https://api.deepseek.com/chat/completions | 完整 HTTPS 请求地址，包含 /chat/completions 路径；不自动拼接或去除空白。禁止 URL 用户名、密码、查询参数、fragment、空白和控制字符。 |
| DASHSCOPE_API_KEY | 空 | 用于百炼语音和可选的 AI 插画；不得提交或写入日志。 |
| MODEL_API_KEY | 空 | DeepSeek 官方文本凭证；空时禁用文本调用，绝不回退到百炼密钥。 |
| MODEL_NAME | deepseek-flash | DeepSeek 实际支持的模型标识，必须能编码为 UTF-8。 |
| MODEL_TIMEOUT_SECONDS | 20 | 请求及响应读取的总预算，必须大于 0 且不超过 20 秒。 |
| MODEL_SEND_THINKING | true | 默认发送 `thinking.type=disabled`。兼容端点明确拒绝该字段时设为 `false`。 |
| MODEL_MAX_TOKENS | 512 | 最大输出令牌数，整数 1～4096；供应商需支持 max_tokens。 |

.env 由 Uvicorn 的 --env-file 或本地连通性脚本加载，已有进程环境变量优先；Settings.from_env 本身只读取进程环境。仓库已有规则忽略 .env，保留 .env.example。配置值不出现在健康检查或 OpenAPI 中。此适配器关闭环境代理读取，避免无意将凭证交给本机代理；如运行环境必须经代理，应另行明确配置与信任边界。

## 工坊 AI 插画（可选）

保留 `IMAGE_API_URL` 为空时，工坊可以继续使用既有的授权模板。启用时填入百炼业务空间的完整 HTTPS 文生图同步接口，使用与该地域匹配的 `DASHSCOPE_API_KEY`。默认模型是 `qwen-image-2.1-turbo`，可通过 `IMAGE_MODEL` 换成同一请求格式的千问图像模型。使用 Compose 或现有部署文件时也要给后端容器传入这两个图像变量。[示例配置](.env.example) · [百炼文生图说明](https://www.alibabacloud.com/help/en/model-studio/text-to-image)

家长在工坊勾选「使用 AI 生成专属插画」才会调用文生图。每个新增页调用一次，调用受百炼额度和计费约束。续写沿用已读页的冻结配图。生成结果保存在数据库同卷的媒体存储，只有本人家长可在审核前预览。请逐页核对画面后再审核，生成失败可在工坊取消该选项并改用模板。

## 调用内部文本模型

后续业务路由在通过身份、归属、会话状态和幂等检查后，可以调用 request.app.state.model_gateway；其连接池由应用启动和退出时统一管理。

```python
output_text = await request.app.state.model_gateway.generate(
    input_text="故事里的小狐狸为什么把雨伞借给朋友？",
    system_prompt=trusted_system_prompt,
)
```

input_text 去除首尾空白后为 1～200 字符，与契约中的用户文本边界一致。输入与输出均检查 UTF-8 可编码性，拒绝孤立代理码点。system_prompt 由受信任的 Agent 模块提供，承载当前页上下文、已审核的检索依据和适龄规则；不能把客户端提交的任意文本当成系统指令。

按项目规范 3.3 的第三方协议例外，供应商字段保持其原有拼写，仅在适配层转换，不改写为知芽业务字段：

| 内部值 | 供应商协议字段 / 返回处理 |
| --- | --- |
| Settings.model_name | 请求 model |
| system_prompt / input_text | 请求 messages 中 system / user 消息的 content |
| Settings.model_max_tokens | 请求 max_tokens；stream 固定为 false |
| Settings.model_send_thinking | 默认为 true 时发送 `thinking.type=disabled`，设为 false 时省略该字段。 |
| 响应 choices[0].message.content | 校验后作为字符串返回；本地脚本使用知芽 camelCase 字段 outputText，不透传供应商完整对象 |

适配器仅返回完整的 assistant 文本，不创建业务事件、不生成知识引用、不执行工具。调用方仍需完成领域结果校验和安全回退，写入最终事实记录后才向用户展示。校验 finish_reason、文本结构或供应商 refusal 并不能证明内容适龄或安全。

如果只想在配置好后手动检查连通性，从 backend 目录运行：

```powershell
..\.venv\Scripts\python.exe -X utf8 smoke_model.py
```

脚本从终端读取一条测试问题并发起一次真实请求，成功时只向当前终端打印结果；失败返回非零退出码。它不会自动运行，也没有公开 HTTP 聊天接口。2026-11-11 用户自行填写凭证后，三个内部适配的真实短句调用已通过，详情见[真实联调记录](../docs/testing/live-provider-checks.md)。

## 对接 PR #27 的 Agent 模型接口

2026-11-12，DCL-016：`app.schemas.agent` 最初沿用 [PR #27](https://github.com/MumLauds/202610-zhiya/pull/27) 提交 `aa13025bd145f7be20166142d470b9b6efa4f2df` 的原始文件快照。2026-11-16 引入 #30 及其基线，已包含 #27/#28 的主分支合并历史，Schema 保持同一份，不另维护同名模型；具体协作约定见[交接文档](../docs/design/voice-api-handoff.md)。

`request.app.state.model_adapter` 接收该 Schema 的 `ModelGenerateRequest`，返回 `ModelGenerateResult`，复用已有 `model_gateway` 的连接池、20 秒以内总期限和单次请求规则。经授权的业务调用方可使用：

```python
from app.schemas.agent import ModelGenerateRequest

result = await request.app.state.model_adapter.generate(ModelGenerateRequest(
    system_prompt=trusted_system_prompt,
    context_text=verified_page_and_knowledge_context,
    user_input=confirmed_user_input,
))
```

| 输入或结果 | 本轮映射 |
| --- | --- |
| system_prompt | 受信任的系统规则，单独作为供应商 system 消息 |
| context_text | 服务端提供的已授权页面/检索资料，放在 user 消息的参考上下文区；不提升为系统指令，不计入用户问题 200 字限制 |
| user_input | 去除首尾空白后 1～200 字；与上下文分区传入 user 消息 |
| 成功 | success=true、text 为有效模型文本、error_code=null |
| MODEL_TIMEOUT | success=false、text=""、error_code=TIMEOUT |
| 其他 MODEL_* 失败 | success=false、text=""、error_code=API_ERROR；包含未配置、鉴权、限流、网络、上游状态及响应无效 |
| INVALID_INPUT、程序错误或任务取消 | 保持异常传播，不伪装成供应商失败或成功，不自动重试 |

此分区只约定输入组织方式，不构成提示注入防护或内容安全验收。调用方负责资料授权、审核、上下文预算及生成结果校验；适配器不截断或丢弃上下文，不记录其内容。省略 context_text 的旧网关调用保持原请求格式与返回行为。

本节模型适配只完成接口桥接：不生成 AgentResult、兜底问句或知识引用，不执行检索或保存事件。SKIPPED 沿用业务层短路，不应调用此适配器。验证见[模型接口对接检查](../docs/testing/agent-model-adapter-checks.md)。

**INITIAL 调用建议（待 Agent 工作流负责人确认）：** 没有儿童输入时，由工作流根据已授权当前页构造非空生成指令，作为 user_input；这是工作流指令，不应保存成儿童原话。例如：

```python
result = await request.app.state.model_adapter.generate(ModelGenerateRequest(
    system_prompt=trusted_system_prompt,
    context_text="当前页：小兔子把雨伞借给了淋雨的小熊。",  # 示例；实际使用已授权页面与审核资料
    user_input="请围绕当前页小兔子帮助小熊的情节，提出一个适合幼儿回答的简短问题。",
))
```

示例成功结果为 `{"success": true, "text": "小兔子把雨伞借给了谁？", "error_code": null}`，仅用于说明结构，不代表真实模型输出或适龄验收。ModelGenerateRequest 没有任务类型，适配器无法从空串判断 INITIAL；空白/空 user_input 继续抛出 `INVALID_INPUT`（422），不调用供应商，不自动补默认话。非空指令仍遵守 1～200 字符限制。

**API_ERROR 调用约定：** 供应商鉴权、限流、网络、响应异常或模型未配置时，适配器返回 `{"success": false, "text": "", "error_code": "API_ERROR"}`，符合 #27 Schema；超时返回同结构的 `TIMEOUT`。API-017 对 `TIMEOUT` 保存安全回退，对 `API_ERROR` 返回 503 `DEPENDENCY_UNAVAILABLE` 并记录可重放的失败，保留原修订号。真实供应商失败至多调用一次，未配置及非法输入不发出请求。

上述结构和调用次数已有 `test_model_adapter.py` 中的成功映射、失败映射、未配置与非法输入测试覆盖；测试使用模拟供应商。API-017 的正式 HTTP 入口另有离线测试，验证 INITIAL 指令、非超时错误重放和儿童可选回答。

## 超时、错误与日志

- 使用外层总期限与 HTTPX 分阶段超时共同约束普通文本请求。流式传输仅用于限制 HTTP 响应读取，协议请求明确 stream=false；追加缓冲区前检查累计响应是否超过 1 MiB。
- 请求指定 Accept-Encoding: identity，仅接受无 Content-Encoding 或明确 identity 的未压缩响应。压缩响应在读取前拒绝，避免在检查响应体大小前解压数据；供应商或中间代理须支持该约定。
- 每次 generate 最多发起一次请求；禁用连接重试、业务重试和重定向。超时、网络错误、畸形响应、被截断或拒绝的答案均不自动重发。
- 这不等于业务幂等已实现。调用方仍须按契约先取得 request_executions 的执行权；相同业务键重复调用 generate 仍会再次请求供应商。本模块不依赖供应商幂等请求头。
- ServiceError 只含固定错误码与安全文案，不返回供应商正文、异常字符串或凭证。MODEL_* 是本模块内部诊断码，尚不是团队新增冻结的业务错误码；业务接入时再确定端点映射和回退。

| 内部结果 | 默认 HTTP 映射 | 含义 |
| --- | --- | --- |
| INVALID_INPUT | 422 | 输入为空、过长、缺少系统提示词或不是有效 UTF-8；不调用供应商 |
| MODEL_NOT_CONFIGURED | 503 | 缺少 URL、密钥或模型名；不调用供应商 |
| MODEL_AUTH_FAILED / MODEL_RATE_LIMITED | 503 | 供应商鉴权失败或限制调用；不把它误报为终端用户登录失败 |
| MODEL_UNAVAILABLE / MODEL_PROVIDER_ERROR | 503 | 网络失败、供应商服务错误、重定向或其他非 200 响应 |
| MODEL_INVALID_RESPONSE | 503 | 无效 JSON/Unicode、缺字段、空文本、截断、拒绝、工具调用、压缩或超大响应 |
| MODEL_TIMEOUT | 504 | 总期限或传输阶段超时，无法据此判断供应商是否已执行或计费 |

所有这些错误的 retryable 均为 false，避免页面据此自动创建第二次调用。业务层可在明确状态与确认策略后决定新的操作。

HTTP 错误沿用契约 envelope：error.code、error.message、error.retryable、requestId；框架校验与未处理异常也返回固定文案。当前新增的本地错误码不改变已有契约条目。

默认日志只记录服务端追踪标识、路由模板、状态/结果码和耗时；不记录请求正文、query、提示词、回答、凭证或上游错误正文。启动命令使用 --no-access-log，避免 Uvicorn 的独立访问日志记录原始 query。模型日志使用单次 call_id，只标识这次外部尝试，不作为业务幂等键。不得开启 HTTPX/httpcore 的调试日志来收集真实输入或请求头。

## 离线验证

从 backend 目录运行：

```powershell
..\.venv\Scripts\python.exe -m pytest tests -q
```

测试使用 pytest 运行主分支测试及 unittest 用例，使用 TestClient、MockTransport、临时数据库和虚构测试数据，不读取 .env、不访问真实模型。依赖版本及初版验证限制见[骨架检查记录](../docs/testing/backend-bootstrap-checks.md)，当前回归见[文本与语音检查](../docs/testing/bailian-checks.md)。容器基础验证见 [infra](../infra/README.md)，真实供应商短句联调另行记录。

API-017 的回归使用已开始的会话、临时 SQLite 和模拟模型。从项目根目录运行 `python -m pytest -q backend/tests/test_interactions.py -k guidance`，可验证儿童答题与刷新、连续两轮理解反馈、等待首题时跳过、跨页和到期重放、供应商失败、输出安全与证据保存。引导会复用 G09 的主题和年龄筛选。[G05 引导约定](../docs/design/agent-degradation.md)

## 设计与实现参考

采用 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/) 管理客户端生命周期；使用 [HTTPX transport](https://www.python-httpx.org/advanced/transports/) 显式关闭重试并进行离线模拟。HTTPX 的[超时说明](https://www.python-httpx.org/advanced/timeouts/)描述的是各传输阶段，因此另外设置总期限。文本请求/响应使用 [DeepSeek Chat Completions 协议](https://api-docs.deepseek.com/api/create-chat-completion/)，语音继续使用百炼协议。

旧实验仅用于比较环境配置和错误分类做法。本轮没有移入其业务工程、部署配置、真实凭证、自动重试或较长超时规则，也不将旧实验的验证记录算作知芽成果。
