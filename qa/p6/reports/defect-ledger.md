# 知芽 P6 缺陷跟踪表

- 缺陷总数：12
- 等级分布：P0 0，P1 5，P2 7
- 状态分布：待修复 12
- 关联自动验收：acceptance-latest.json

> 缺陷编号 DEF-P6-xxx 为本轮测试建立；P0 = 阻断核心流程/严重越权，P1 = 重要功能失效但有回退，P2 = 文案、兼容性或工程细节。“待修复”表示本轮已确认但尚无修复版本，不等于已验收通过。

## 缺陷清单

| 编号 | 等级 | 标题 | 验收项 | 状态 | 负责人 | 关联用例 |
| --- | --- | --- | --- | --- | --- | --- |
| DEF-P6-001 | P1 | 种子绘本与知识全部为 DRAFT，未审核发布时公开书架为空 | AC-03, AC-05 | 待修复 | 张楠（知识工程）/ 姚华俊（后端1） | TC-P6-BOOK-001(PASS)、TC-P6-AGENT-001(BLOCKED) |
| DEF-P6-002 | P1 | 情境提问 API-016 与自适应引导 API-017 尚未实现 | AC-05, AC-06 | 待修复 | 张智文（Agent 知识）/ 姚华俊（后端1） | TC-P6-API-004(WARN)、TC-P6-AGENT-001(BLOCKED)、TC-P6-AGENT-002(BLOCKED) |
| DEF-P6-003 | P1 | 原创故事生成与审核接口未实现 | AC-13, AC-15 | 待修复 | 陈烨婷（Agent 工程）/ 姚华俊（后端1） | TC-P6-AGENT-003(BLOCKED) |
| DEF-P6-004 | P1 | 阅读小结 activities 固定为空数组，AC-09 未完成 | AC-09 | 待修复 | 陈烨婷（Agent 工程） | TC-P6-ACT-001(BLOCKED)、TC-P6-REPORT-001(WARN) |
| DEF-P6-006 | P1 | 尚未提供公网 HTTPS 地址与演示账号 | AC-11, NFR-08 | 待修复 | 林鑫（后端2/部署）/ 刘俊延（验收） | TC-P6-PUB-001(BLOCKED)、TC-P6-PUB-002(BLOCKED) |
| DEF-P6-005 | P2 | 浏览器端到端自动化未建立 | AC-04, AC-07, AC-11, AC-12, NFR-01, NFR-02 | 待修复 | 张陈雨菲（前端1）/ 吴怡霏（前端2） | TC-P6-FE-001(PASS) |
| DEF-P6-008 | P2 | 图片媒体解析未实现，coverRef/imageRef 恒为 null | AC-03, AC-04 | 待修复 | 姚华俊（后端1）/ 林鑫（后端2） | TC-P6-BOOK-003(PASS) |
| DEF-P6-009 | P2 | 声线/语速偏好未持久化，语音选项接口未实现 | FR-08 | 待修复 | 林鑫（后端2） | TC-P6-VOICE-002(PASS) |
| DEF-P6-010 | P2 | 安全响应头未设置 | NFR-04 | 待修复 | 林鑫（后端2/部署） | TC-P6-SEC-001(WARN) |
| DEF-P6-011 | P2 | 未配置 CORS，跨域浏览器联调受限 | NFR-02 | 待修复 | 林鑫（后端2/部署） | TC-P6-FE-001(PASS) |
| DEF-P6-012 | P2 | 缺少 CI 测试门禁与覆盖率报告 | NFR-07 | 待修复 | 刘俊延（测试）/ 姚华俊（后端1） | - |
| DEF-P6-013 | P2 | 种子数据没有已审核故事分支，AC-14 演示需临时插入测试分支 | AC-14 | 待修复 | 张楠（知识工程）/ 陈烨婷（Agent 工程） | TC-P6-BRANCH-001(WARN) |

## 缺陷详情

### DEF-P6-001 种子绘本与知识全部为 DRAFT，未审核发布时公开书架为空

- 等级 / 类别：P1 / 数据/内容
- 关联验收项：AC-03, AC-05
- 状态 / 负责人：待修复 / 张楠（知识工程）/ 姚华俊（后端1）
- 发现日期 / 环境：2026-11-18 / 本地 SQLite，init_db.py + import_seed.py
- 复现步骤：初始化并导入种子后启动后端，注册登录，GET /api/v1/storybooks?pageSize=50。
- 预期结果：演示环境至少有 8 本已发布绘本可供选择。
- 实际结果：返回 items=[]；需运行 qa/p6/prepare_demo_data.py --yes 审核发布后才有 12 本。
- 证据：data/seed/story_versions.json 全部 DRAFT；TC-P6-BOOK-001 在准备数据前不可通过
- 修复版本：待定

### DEF-P6-002 情境提问 API-016 与自适应引导 API-017 尚未实现

- 等级 / 类别：P1 / 功能/接口
- 关联验收项：AC-05, AC-06
- 状态 / 负责人：待修复 / 张智文（Agent 知识）/ 姚华俊（后端1）
- 发现日期 / 环境：2026-11-18 / main 分支 eb91092
- 复现步骤：GET /openapi.json，检查 paths；或请求对应路径返回 404。
- 预期结果：按接口契约 v0.4 提供 API-016/017，返回 AgentResult 与来源。
- 实际结果：契约 27 条路径中已实现 17 条，上述两条缺失。
- 证据：TC-P6-API-004 missingPaths；TC-P6-AGENT-001/002 BLOCKED
- 修复版本：待定

### DEF-P6-003 原创故事生成与审核接口未实现

- 等级 / 类别：P1 / 功能/接口
- 关联验收项：AC-13, AC-15
- 状态 / 负责人：待修复 / 陈烨婷（Agent 工程）/ 姚华俊（后端1）
- 发现日期 / 环境：2026-11-18 / main 分支 eb91092
- 复现步骤：GET /openapi.json 检查对应路径；请求 /api/v1/story-generations 返回 404。
- 预期结果：按 SRS FR-15～FR-18 与接口契约提供生成、审核、分支接口。
- 实际结果：接口不存在；模型层只有 G09 内部 ContentDraft，没有业务路由。
- 证据：TC-P6-API-004 missingPaths；TC-P6-AGENT-003/004 BLOCKED
- 修复版本：待定

### DEF-P6-004 阅读小结 activities 固定为空数组，AC-09 未完成

- 等级 / 类别：P1 / 功能
- 关联验收项：AC-09
- 状态 / 负责人：待修复 / 陈烨婷（Agent 工程）
- 发现日期 / 环境：2026-11-18 / 本地，结束阅读后 GET API-013
- 复现步骤：完成一次阅读后读取 API-013 的 report.activities。
- 预期结果：至少 1 项与故事相关的亲子活动，含材料和步骤。
- 实际结果：activities=[]；report 状态 READY/PENDING/FAILED 均如此。
- 证据：TC-P6-ACT-001 BLOCKED；TC-P6-REPORT-001 WARN
- 修复版本：待定

### DEF-P6-006 尚未提供公网 HTTPS 地址与演示账号

- 等级 / 类别：P1 / 部署
- 关联验收项：AC-11, NFR-08
- 状态 / 负责人：待修复 / 林鑫（后端2/部署）/ 刘俊延（验收）
- 发现日期 / 环境：2026-11-18 / README、infra/README.md
- 复现步骤：检查 README 当前状态与 infra/README.md。
- 预期结果：提供 HTTPS 公网入口、演示账号、初始化数据和操作说明。
- 实际结果：无公网 URL；P6 公网验收只能留待部署后执行。
- 证据：README“尚未部署”；TC-P6-PUB-001/002 BLOCKED
- 修复版本：待定

### DEF-P6-005 浏览器端到端自动化未建立

- 等级 / 类别：P2 / 前端/测试
- 关联验收项：AC-04, AC-07, AC-11, AC-12, NFR-01, NFR-02
- 状态 / 负责人：待修复 / 张陈雨菲（前端1）/ 吴怡霏（前端2）
- 发现日期 / 环境：2026-11-18 / frontend/src/App.vue
- 复现步骤：npm test、npm run build 通过；run_acceptance.py --frontend-url 仅检查首页 200 与挂载点。
- 预期结果：用真实浏览器完成登录→选书→共读→语音→儿童模式并留存截图/录屏。
- 实际结果：页面可达与 71 项组件/Store 测试通过；浏览器 E2E 未自动化。
- 证据：TC-P6-FE-001 PASS；frontend vitest 71 passed
- 修复版本：待定

### DEF-P6-008 图片媒体解析未实现，coverRef/imageRef 恒为 null

- 等级 / 类别：P2 / 功能/内容
- 关联验收项：AC-03, AC-04
- 状态 / 负责人：待修复 / 姚华俊（后端1）/ 林鑫（后端2）
- 发现日期 / 环境：2026-11-18 / GET API-008/009
- 复现步骤：GET /api/v1/story-versions/1，检查 coverRef 与 pages[].imageRef。
- 预期结果：有素材时返回版本绑定的受控媒体键，并经 API-024 鉴权读取。
- 实际结果：全部为 null。
- 证据：TC-P6-BOOK-003 证据 coverRef=null, imageRef=null
- 修复版本：待定

### DEF-P6-009 声线/语速偏好未持久化，语音选项接口未实现

- 等级 / 类别：P2 / 功能/接口
- 关联验收项：FR-08
- 状态 / 负责人：待修复 / 林鑫（后端2）
- 发现日期 / 环境：2026-11-18 / API-023 与档案接口
- 复现步骤：检查 API-004/006 字段与 API-023 实现；无 speechPreferences。
- 预期结果：家长可选择并持久化声线与语速，儿童授权不能修改。
- 实际结果：未实现；六声线只能在脚本/容器中验证。
- 证据：docs/design/speech-preferences-handoff.md；TC-P6-VOICE-002
- 修复版本：待定

### DEF-P6-010 安全响应头未设置

- 等级 / 类别：P2 / 安全
- 关联验收项：NFR-04
- 状态 / 负责人：待修复 / 林鑫（后端2/部署）
- 发现日期 / 环境：2026-11-18 / 本地 uvicorn 响应头
- 复现步骤：curl -I /api/v1/health，检查响应头。
- 预期结果：至少设置 nosniff、DENY/SAMEORIGIN 与基础 CSP/Referrer-Policy。
- 实际结果：四项均缺失；Server 头暴露 uvicorn。
- 证据：TC-P6-SEC-001 WARN
- 修复版本：待定

### DEF-P6-011 未配置 CORS，跨域浏览器联调受限

- 等级 / 类别：P2 / 接口/部署
- 关联验收项：NFR-02
- 状态 / 负责人：待修复 / 林鑫（后端2/部署）
- 发现日期 / 环境：2026-11-18 / backend/app/main.py
- 复现步骤：从不同源调用 API 并检查 Access-Control-Allow-Origin。
- 预期结果：生产按明确白名单配置 CORS，开发用 Vite 代理。
- 实际结果：无 CORS 配置。
- 证据：backend/app/main.py 无 CORSMiddleware
- 修复版本：待定

### DEF-P6-012 缺少 CI 测试门禁与覆盖率报告

- 等级 / 类别：P2 / 工程/可维护性
- 关联验收项：NFR-07
- 状态 / 负责人：待修复 / 刘俊延（测试）/ 姚华俊（后端1）
- 发现日期 / 环境：2026-11-18 / GitHub 仓库
- 复现步骤：检查 .github/workflows；不存在。
- 预期结果：PR 触发 pytest、数据库测试与前端 build，失败阻止合并。
- 实际结果：仅本地命令；P6 验收脚本也需人工运行。
- 证据：仓库无 .github/workflows
- 修复版本：待定

### DEF-P6-013 种子数据没有已审核故事分支，AC-14 演示需临时插入测试分支

- 等级 / 类别：P2 / 数据/内容
- 关联验收项：AC-14
- 状态 / 负责人：待修复 / 张楠（知识工程）/ 陈烨婷（Agent 工程）
- 发现日期 / 环境：2026-11-18 / 本地 SQLite，data/seed 无 story_branches 文件
- 复现步骤：初始化导入种子后查询 story_branches 为空；GET 绘本详情的 branches 为 []。
- 预期结果：至少一本演示绘本含 1～2 个已审核分支，分支后续情节不同且连贯。
- 实际结果：需测试脚本插入临时分支后才能跑 TC-P6-BRANCH-001。
- 证据：prepare_demo_data.py ensure_branch；TC-P6-BRANCH-001 PASS
- 修复版本：待定
