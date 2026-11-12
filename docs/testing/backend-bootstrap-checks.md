# 后端最小工程与文本模型适配验证

## 2026-11-11：G08 与主分支骨架整合

同日补充容器验证：使用 D 盘已有 WSL 2 Debian / Docker Engine 29.9.2 / Compose 5.6.1。TC-G08-006：干净源码副本构建镜像通过；TC-G08-007：容器内缺库 503、初始化后 200、响应追踪编号一致、非 root UID 10001、种子重复导入、重启持久化和 Docker healthy 全部通过。关联 FR-13 / NFR-08 / AC-11（局部）。依赖检查通过（pip 提示非 root 缓存目录不可写，不影响检查）；容器与网络已停止移除，独立测试数据卷与镜像保留。Windows `/mnt/d` 源目录直接构建因文件属性权限失败，Windows 端口转发未验证；详细限制见 [部署说明](../../infra/README.md)。真实供应商、语音和公网仍未验收。下文“容器构建运行未验收”为本日较早阶段状态，由本段更新。

基线：main `8c4172c847728644d86e5d5601ce2c93fa58212c`（PR #26）。负责角色：林鑫 / 后端2；执行与核验：Codex。变更原因：主分支已新增应用、数据库和前端骨架，需要将本地模型模块接入同一个后端。当前仍为本地未提交修改，不代表合并或正式验收。

- 保留 `app.main:app` 和 `create_app`，复用主分支健康路由；应用生命周期创建并关闭模型客户端。
- 健康检查按主分支规则检查数据库结构，缺失/缺表返回 503，结构就绪返回 200；模型配置允许留空。响应头与正文使用同一 requestId。
- 保留模型总期限、单次请求、安全错误和日志；未新增匿名模型/语音路由，未改数据库业务或接口契约。
- G08 部署初稿见 [infra](../../infra/README.md)。供应商未定；ASR/TTS（自动语音识别 / 文本转语音）未实现，真实模型效果、容器构建运行及公网部署均未验收。

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m unittest discover -s database/tests -v
.\.venv\Scripts\python.exe -m pip check
```

实际结果：后端 12 passed、41 subtests passed；数据库 4 项通过；依赖一致性检查通过。保留一个 Starlette TestClient / HTTPX 弃用提示，不影响本轮通过结果。

| 用例 / 关联依据 | 检查内容 | 结果 |
| --- | --- | --- |
| TC-G08-001 / API-032、FR-13 | 主分支 test_health_checks_database_readiness：缺库、空库、完整结构；不创建缺失库；追踪标识一致 | 通过 |
| TC-G08-002 / NFR-04/05 | test_no_business_routes_are_exposed：仅暴露健康业务路由 | 通过 |
| TC-G08-003 / IF-02、FR-12 | test_app_manages_model_gateway_lifecycle：随应用启动/关闭进入和退出网关上下文 | 通过 |
| TC-G08-004 / NFR-08、AC-11（局部） | 临时数据库初始化、种子导入及重复导入；真实 Uvicorn 本机 HTTP 健康响应、OpenAPI 路径和未实现语音路径 404 | 通过 |
| TC-G08-005 / NFR-04/05、AC-10（局部） | 实际进程访问日志不包含测试 query；未配置模型的终端脚本返回 MODEL_NOT_CONFIGURED、退出码 1 | 通过 |

实际进程检查使用临时 SQLite 与清空的 MODEL_* 环境变量，结束后停止服务并清理临时资源，没有调用收费服务。先前 9 个模型/通用错误测试仍全部通过；TC-BE-001 现在以有效临时数据库为前提。以下为 11 月 10 日初版记录，健康检查与启动语义已由本节和后端 README 更新。

## 2026-11-10：初版历史记录

| 缩写 | 中文名称（英文全称） |
| --- | --- |
| API / IF | 应用程序编程接口（Application Programming Interface）/ 接口分组（Interface） |
| FR / NFR | 功能需求（Functional Requirement）/ 非功能需求（Non-Functional Requirement） |
| AC / TC | 验收准则（Acceptance Criteria）/ 测试用例（Test Case） |
| HTTP / HTTPS | 超文本传输协议（Hypertext Transfer Protocol）/ 安全超文本传输协议（Hypertext Transfer Protocol Secure） |
| JSON / URL | JavaScript 对象表示法（JavaScript Object Notation）/ 统一资源定位符（Uniform Resource Locator） |
| UTF-8 | Unicode 转换格式（Unicode Transformation Format，8-bit） |
| ID / MiB | 标识符（Identifier）/ 二进制兆字节（Mebibyte） |
| SRS / AI | 软件需求规格说明书（Software Requirements Specification）/ 人工智能（Artificial Intelligence） |
| DEL / NaN | 删除控制字符（Delete）/ 非数值（Not a Number） |

日期：2026-11-10。开发起点：main 的 3c5835affe376119e6b4ccbe0ae581bb69304ae0；本地分支 feat/backend-model-bootstrap。本记录对应本轮工作区实现，不代表已经提交、合并或获得团队设计审批。

实现版本：0.1.0。负责角色：后端2承接模型适配，业务后端承接后续应用集成；本轮由 Codex 按用户要求实施和验证，不代替团队分工确认。

## 变更原因与范围

仓库尚无正式后端运行入口。本轮按[项目规范](../standards/知芽_项目设计与开发规范_精简版_v1.1.md)规定 3～5，增加可独立启动的 FastAPI 最小工程及内部文本模型适配，为后端与 Agent 后续接入提供基础。

| 需求或设计依据 | 本轮交付 | 边界 |
| --- | --- | --- |
| API-032；FR-13 / NFR-08 / AC-11 | 匿名健康检查、启动命令、统一 requestId | 只验证本机进程；未完成公网 HTTPS 或完整业务验收 |
| IF-02；FR-12 / AC-10 | 模型输入检查、依赖错误分类、超时与安全错误文案 | 使用模拟供应商，未执行真实模型效果或完整业务故障回退 |
| requestExecution.deadline / billing | 普通文本总预算不超过 20 秒，每次调用一次请求，禁止自动重试和重定向 | 不实现数据库幂等占位、恢复、事务或原创 60 秒流程 |
| NFR-04 / NFR-05；规范 5 | 模型配置留环境变量；日志不记录正文、query、密钥或上游异常原文 | 未实现用户身份、会话归属或儿童模式权限，不提供公开聊天接口 |

实现位于 backend/app；测试位于 backend/tests/test_backend.py。详细操作见[后端 README](../../backend/README.md)。现有接口契约、数据库、公共术语与设计图没有修改；MODEL_* 是内部诊断码，不作为新增冻结的业务接口。设计待决项 DCL-005～008 保持原状态。

## 验证环境与复现

- Windows，Python 3.12.14；依赖固定在 backend/requirements.txt。
- 直接依赖：FastAPI 0.162.2、Uvicorn 0.54.0、HTTPX 0.28.1、python-dotenv 1.2.4。
- 使用 unittest、FastAPI TestClient、HTTPX MockTransport 和虚构输入，测试不读取本地 .env、不访问真实模型。
- 执行者：Codex；子代理协助契约核对、测试及代码审查，主代理完成整合与真实本机进程检查。

从 backend 目录执行：

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
..\.venv\Scripts\python.exe -m pip check
```

## 离线测试结果

本次复查后共 9 个测试方法通过，参数化子场景通过。TC-BE-001～009 为本轮局部验证编号；下表同时关联既有需求，不代替其完整业务验收。测试方法名直接对应源文件和命令输出。

| 用例 / 关联依据 | 测试方法 | 检查内容 | 结果 |
| --- | --- | --- | --- |
| TC-BE-001 / API-032、FR-13 | test_unconfigured_health_matches_api_032 | 模型未配置仍可健康检查；响应精确匹配契约，追踪标识逐次生成并与响应头一致 | 通过 |
| TC-BE-002 / FR-12、NFR-04/05 | test_errors_use_contract_envelope_without_sensitive_details | 404、405、422、500 使用统一错误结构；校验输入、异常正文及假密钥不出现在响应/应用日志 | 通过 |
| TC-BE-003 / IF-02、FR-12 | test_validates_provider_url_and_timeout | HTTPS、无 URL 凭证/查询/fragment；拒绝非法域名、DEL、端口、NaN/无穷/超出 20 秒预算及非法 Unicode 模型名 | 通过 |
| TC-BE-004 / FR-12、AC-10 | test_invalid_or_unconfigured_calls_do_not_reach_provider | 配置缺失、空白/过长输入或非法 Unicode 输入/提示词在网络请求前失败 | 通过 |
| TC-BE-005 / IF-02、NFR-04/05 | test_sends_one_chat_completion_and_logs_metadata_only | 请求字段正确，指定未压缩响应；单次请求，日志仅含元数据 | 通过 |
| TC-BE-006 / FR-12、NFR-03/06 | test_total_deadline_cancels_provider_without_retry | 慢响应体读取也受总截止时间限制；不重试，超时关闭响应流，退出上下文关闭客户端传输 | 通过 |
| TC-BE-007 / FR-12、AC-10 | test_provider_failures_and_redirects_are_safe_single_attempts | 302 不跟随，401/429/502 和网络错误安全映射；每个场景仅一次请求 | 通过 |
| TC-BE-008 / IF-02、FR-12 | test_incomplete_or_invalid_model_responses_are_rejected | 畸形/极深 JSON、非法 Unicode、缺字段、空文本、截断、refusal、工具调用及超过 1 MiB 响应均拒绝 | 通过 |
| TC-BE-009 / FR-12、NFR-06 | test_compressed_response_is_rejected_before_reading | gzip/deflate/br/混合编码在读取和解压之前拒绝，关闭响应流，不自动重试 | 通过 |

发现并修复了配置 URL 被 HTTPX 拒绝时可能出现原始异常，以及极深 JSON 的 RecursionError 未被分类这两个边界问题，并补充回归场景。

2026-11-10 再次按规范复查：新增回归用例先复现非法 Unicode 输入异常、非法输出误判成功，以及压缩响应在体积检查前解压的问题；修复后 9 项测试全部通过。输入、系统提示、模型名与输出增加 UTF-8 检查；压缩响应在读取前拒绝，缓冲区在追加前检查容量。同时补齐缩写说明、第三方字段映射及用例编号。接口路径、业务数据模型与未决设计项没有变更。

当前 Starlette 的 TestClient 对 HTTPX 适配产生弃用提示；本轮全部测试通过，暂未因此引入第二套 HTTP 客户端依赖。后续升级依赖时复核其支持情况。

## 本机进程检查

另外实际启动 Uvicorn 工厂入口，使用 .env.example 的空模型配置及 --no-access-log，绑定 127.0.0.1 的临时端口，通过 HTTP 请求验证：

- GET /api/v1/health 返回 200，data.status=ok，requestId 与 X-Request-ID 一致。
- /openapi.json 仅声明 /api/v1/health 业务路径，没有添加未鉴权的模型接口。
- 不存在的路径返回 404 和约定错误结构；应用日志出现追踪记录，未出现请求 query 中的测试敏感值。
- 无模型配置时运行 smoke_model.py，返回 MODEL_NOT_CONFIGURED，退出码为 1，未发送真实模型请求。
- 检查结束后停止本轮启动的服务；未启动后台常驻服务、部署或工作流。
- pip check 无依赖冲突；Python 源码可编译。

## 尚未验证及后续接入

真实供应商地址、模型名与密钥留空，未验证供应商协议兼容、真实连通性、延迟、费用、知识增强效果或儿童内容安全。确定供应商后，先手动运行连通性脚本，再由业务后端与 Agent 在鉴权、幂等、结果校验和落库路径中接入。

本次验证不是 TC-FR12-01 的完整业务验收，也不代替系统设计第 9～10 章及 SRS 第 7 章要求的端到端、权限、AI 对照或上线验收。
