# 知芽 P6 黑盒验收测试报告

- 开始时间：2026-11-18T13:22:03Z
- 结束时间：2026-11-18T13:22:06Z
- 被测提交：ead1debaaf1eb5eb14e1a79503371667d790c4c1
- 被测地址：http://127.0.0.1:8000
- 测试环境：local

## 结果汇总

| 状态 | 数量 |
| --- | ---: |
| PASS | 31 |
| FAIL | 0 |
| BLOCKED | 6 |
| SKIP | 0 |
| WARN | 4 |

合计 41 项。

## 用例明细

| 用例 | 验收项 | 标题 | 状态 | 耗时/ms | 说明 |
| --- | --- | --- | --- | ---: | --- |
| TC-P6-ENV-001 | NFR-02 | 测试环境与协议检查 | PASS | 0 | scheme=http, profile=local |
| TC-P6-API-001 | NFR-08 | 健康检查与 requestId | PASS | 7 | status=ok, 7ms |
| TC-P6-API-002 | FR-12 | 未知路由错误结构 | PASS | 7 | 404 NOT_FOUND，envelope 完整 |
| TC-P6-API-003 | FR-12 | 方法不允许错误结构 | PASS | 2 | 405 METHOD_NOT_ALLOWED |
| TC-P6-AUTH-001 | AC-01 | 注册新账号 | PASS | 0 | account=p6a1118212204dcae，201 且返回 accountId |
| TC-P6-AUTH-002 | AC-01 | 重复账号注册被拒绝 | PASS | 0 | 409 ACCOUNT_EXISTS |
| TC-P6-AUTH-003 | AC-01 | 错误密码登录被拒绝 | PASS | 0 | 401 INVALID_CREDENTIALS |
| TC-P6-AUTH-004 | AC-01 | 登录成功与 Cookie 安全属性 | PASS | 0 | account=p6a1118212204dcae |
| TC-P6-AUTH-005 | AC-01 | 匿名访问受保护接口 | PASS | 3 | 401 AUTH_REQUIRED |
| TC-P6-AUTH-006 | AC-01 | 退出后旧会话失效 | PASS | 280 | 退出 204，旧会话失效 |
| TC-P6-PROF-001 | AC-02 | 创建并读取幼儿档案 | PASS | 21 | childProfileId=2 |
| TC-P6-PROF-002 | AC-02 | 修改档案并持久化 | PASS | 14 | preferredMinutes=15, interests=[动物] |
| TC-P6-PROF-003 | AC-02 | 档案字段校验 | PASS | 2 | 0 分钟与未知字段均 422 |
| TC-P6-PROF-004 | NFR-04 | 跨账号档案隔离 | PASS | 5 | 跨账号修改/删除均 404 |
| TC-P6-BOOK-001 | AC-03 | 绘本列表至少 8 本 | PASS | 4 | 可用绘本 12 本，选用 逃家小兔（v1） |
| TC-P6-BOOK-002 | AC-03 | 绘本筛选与分页边界 | PASS | 5 | 无匹配为空、pageSize>50 为 422、匿名为 401 |
| TC-P6-BOOK-003 | AC-04 | 绘本详情与越界访问 | PASS | 5 | 6 页，缺图字段为 null，越界 ID 404 |
| TC-P6-REC-001 | AC-03 | 绘本推荐与理由 | PASS | 8 | 10 条推荐，均含故事版本与理由 |
| TC-P6-SESS-001 | AC-08 | 创建阅读会话与 CSRF | PASS | 16 | readingSessionId=1, status=CREATED, revision=0 |
| TC-P6-SESS-002 | AC-08 | 开始阅读、幂等与冲突 | PASS | 33 | STARTED=201、重放=200、同键不同体=409、过期 revision=409 |
| TC-P6-SESS-003 | AC-04 | 翻页与进度持久化 | PASS | 17 | currentPage=2, revision=2 |
| TC-P6-SESS-004 | NFR-04 | 跨账号会话隔离 | PASS | 5 | 跨账号读取/列表均不可见 |
| TC-P6-BRANCH-001 | AC-14 | 受控故事分支选择 | WARN | 51 | branchId=1, currentPage=2, revision=2。仅验证一条审核分支及其重放、过期修订冲突。同一决策点两种不同后续、节点上限及无环约束尚未验收。 |
| TC-P6-SESS-005 | AC-08 | 结束阅读与小结 | PASS | 12 | status=PENDING, activities=0（模型未配置或生成失败，记警告） |
| TC-P6-SESS-006 | AC-08 | 阅读历史可查询 | PASS | 14 | 历史列表与详情均可查询，状态 FINISHED |
| TC-P6-SESS-007 | AC-10 | 结束后禁止继续写入 | PASS | 11 | 结束后新事件 409 |
| TC-P6-SESS-008 | FR-14 | 删除本人阅读记录 | PASS | 27 | 删除 3 后 404 |
| TC-P6-CHILD-001 | AC-12 | 儿童模式授权与家长解锁 | PASS | 616 | 儿童可读受限内容、不可改设置、错误密码 401、解锁后旧凭证失效 |
| TC-P6-VOICE-001 | AC-07 | 语音接口鉴权 | PASS | 31 | 匿名 401、缺 CSRF 403 |
| TC-P6-VOICE-002 | AC-07 | 语音格式与未配置回退 | PASS | 9 | TTS 未配置 503；错误类型 415/422；缺字段 422 |
| TC-P6-NFR-001 | NFR-03 | 接口响应时间抽测 | PASS | 41 | health p95=3ms, list p95=4ms（阈值 3000.0ms） |
| TC-P6-SEC-001 | NFR-04 | 安全响应头检查 | WARN | 2 | 未设置 x-content-type-options, x-frame-options, content-security-policy, referrer-policy（安全响应头不作为 P6 阻塞项，记录为警告） |
| TC-P6-API-004 | NFR-07 | 接口契约覆盖情况 | WARN | 15 | 契约路径 27 个，已实现 19 个；未实现 8 个 |
| TC-P6-FE-001 | NFR-01 | 前端页面可访问 | PASS | 18 | HTTP 200, 491 bytes |
| TC-P6-PUB-001 | AC-11 | 公网 HTTPS 与证书 | BLOCKED | 0 | 本轮为本地 profile；公网验收需 --profile public 与真实域名 |
| TC-P6-PUB-002 | AC-11 | HTTP 强制跳转 HTTPS | BLOCKED | 0 | 本轮为本地 profile |
| TC-P6-AGENT-001 | AC-05 | 情境提问 API-016 与适龄问题 | BLOCKED | 0 | API-016 尚未实现（契约路径缺失） |
| TC-P6-AGENT-002 | AC-06 | 自适应引导 API-017 两种分支 | BLOCKED | 0 | API-017 尚未实现（契约路径缺失） |
| TC-P6-AGENT-003 | AC-13 | 原创故事生成与审核 AC-13/15 | BLOCKED | 0 | API-025/026 尚未实现 |
| TC-P6-ACT-001 | AC-09 | 亲子活动材料与步骤 AC-09 | BLOCKED | 0 | 当前 activities 固定为空数组 |
| TC-P6-REPORT-001 | FR-09 | 小结生成状态 | WARN | 0 | report.status=PENDING；未配置文本模型时按 FAILED 保留事实摘要，属预期降级 |

## 验收项汇总

| 验收项 | 通过 | 失败 | 阻塞 | 跳过 | 警告 |
| --- | ---: | ---: | ---: | ---: | ---: |
| AC-01 | 6 | 0 | 0 | 0 | 0 |
| AC-02 | 3 | 0 | 0 | 0 | 0 |
| AC-03 | 3 | 0 | 0 | 0 | 0 |
| AC-04 | 2 | 0 | 0 | 0 | 0 |
| AC-05 | 0 | 0 | 1 | 0 | 0 |
| AC-06 | 0 | 0 | 1 | 0 | 0 |
| AC-07 | 2 | 0 | 0 | 0 | 0 |
| AC-08 | 4 | 0 | 0 | 0 | 0 |
| AC-09 | 0 | 0 | 1 | 0 | 0 |
| AC-10 | 1 | 0 | 0 | 0 | 0 |
| AC-11 | 0 | 0 | 2 | 0 | 0 |
| AC-12 | 1 | 0 | 0 | 0 | 0 |
| AC-13 | 0 | 0 | 1 | 0 | 0 |
| AC-14 | 0 | 0 | 0 | 0 | 1 |
| FR-09 | 0 | 0 | 0 | 0 | 1 |
| FR-12 | 2 | 0 | 0 | 0 | 0 |
| FR-14 | 1 | 0 | 0 | 0 | 0 |
| NFR-01 | 1 | 0 | 0 | 0 | 0 |
| NFR-02 | 1 | 0 | 0 | 0 | 0 |
| NFR-03 | 1 | 0 | 0 | 0 | 0 |
| NFR-04 | 2 | 0 | 0 | 0 | 1 |
| NFR-07 | 0 | 0 | 0 | 0 | 1 |
| NFR-08 | 1 | 0 | 0 | 0 | 0 |
