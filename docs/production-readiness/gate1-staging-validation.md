# PR #97 Gate 1 验收

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 提交与运行范围 | 已核对 | 既有 PR #97 分支。回归只使用隔离 SQLite、虚构绘本和合成儿童对话 |
| 多轮指代 | 本地定向回归通过 | 同版本、同会话、同次页面访问最多携带两条已展示的合格助手回复 |
| 独立云 Staging | BLOCKED | 待提供获批的测试地址、访问方式和验收账号 |
| 北京百炼 | 真实 API 已完成 | 两轮真实请求各 12 次，详见下表 |
| 真机、真实语音与账单 | BLOCKED | 待独立环境、供应商凭证和账单 |
| Gate 1、2、3 | BLOCKED | Gate 1 待上述证据。Gate 2/3 另外需要儿童数据、试运行及发布审批 |

## 北京真实模型运行

工具为 [run_gate1_live.ps1](../../infra/staging/run_gate1_live.ps1) 和 [gate1_live_probe.py](../../infra/staging/gate1_live_probe.py)。仅为获批的合成环境运行。PowerShell 使用隐藏输入获取同一测试业务空间的密钥，配置专属北京 API Host。保留仓库默认模型和现网配置。工具在隔离进程中创建空数据库，真实请求通过应用的 ModelGateway、既有 JSON mode、输出校验与 deadline，记录模型、请求编号、完整合成输入及消息、Prompt 和上下文指纹、供应商原始响应、usage、模型/API 耗时、显示文本、回执及降级。受限 JSONL 保存于工作树之外。请求完成后只将聚合结果和必要指纹写入报告。

| 分组 | 观察项 |
| --- | --- |
| A | 接住叶子小被子的想象 |
| B | 三轮想象延续，涵盖翅膀、月亮、云朵 |
| C | 区分绘本角色与孩子自己的想象 |
| D | 区分兔子和小熊及各自动作对象 |
| E | 回应对松鼠动机的推测并保留不确定性 |
| F | 两次陪看指令本地完成，恢复后才允许发送模型请求 |

计划 14 个回合，最少 12 次返回预期模型且具备可核验 ID 的真实完成。以回执和供应商响应同时确认完成及回退，逐轮复核完整性、归属、推测语气和跟随上下文。两轮分别核对供应商请求编号、模型、结束标志、回执和完整展示正文。相关结果见[浏览器验收](browser-e2e-report.md)、[语音联调](voice-integration-report.md)、[会话隔离](session-isolation-report.md)、[成本对账](cost-reconciliation.md)及[迁移验证](legacy-migration-verification.md)。

## 工程验证

源提交 `d1e92464d7e31684da369c32cf8453bd2bacd3a7` 的 CI 后端及数据库 873 通过、5 跳过、313 子测试通过，前端 467 通过、独立 Node 15 通过。隔离 Docker 实际构建启动，验证 Caddy HTTPS、健康检查、SQLite、合成 seed、授权与 off 回滚。[首次 CI](https://github.com/MumLauds/202610-zhiya/actions/runs/45975399150/job/140425747423)

修复版 `e2310b89c2bdf7406b47c200b96f10d3b8f9a664` 完整 CI 再次通过，后端及数据库 876 通过、5 跳过、313 子测试通过，前端 467 通过、独立 Node 15 通过。隔离 Docker smoke 通过输入授权撤回、关闭回退及已有会话版本固定检查。香港发布任务在 PR 阶段跳过。[修复版 CI](https://github.com/MumLauds/202610-zhiya/actions/runs/45978040980/job/140434638636)

## 两轮真实北京 Flash

真实调用使用合成数据、独立北京业务空间、本机隔离 ASGI 后端和冻结的运行时提交。每轮 14 个回合，12 个获得独立供应商请求 ID、预期模型和 HTTP 200。12 个生成均以 stop 结束。每轮两次明确陪看在本地完成。下表的后端耗时只统计有真实请求的 12 个回合。P50 为中位数，P95 使用 nearest-rank。

| 项目 | 第一轮 d1e9246 | 修复后 e2310b8 |
| --- | --- | --- |
| 真实调用 / 核验成功 | 12 / 12 | 12 / 12 |
| 整轮回退 | 0 | 0 |
| QUESTION_FORMAT 裁掉追问 | 1 | 0 |
| API P50 / P95 | 861.29 / 1863.87 ms | 901.68 / 2094.59 ms |
| 后端 P50 / P95 | 945.09 / 1978.22 ms | 1067.66 / 2251.92 ms |
| 本地陪看 | 2 | 2 |

| 案例 | 修复后人工检查 |
| --- | --- |
| A | 接住叶子小被子的想象，正确关联书里小兔的想象 |
| B | 三轮回应翅膀、月亮背面和云朵，实际状态均为 CONTINUE。首轮曾被模型误标 LISTEN、PAUSE |
| C | 回答书中小兔想象叶子像小被子，孩子先前也提过。首轮错说孩子没有提过 |
| D | 区分小兔拿黄色叶子和小熊戴蓝帽子。补测帽子颜色、松鼠放入的东西均正确 |
| E | 保留动机未写明的事实边界，未直接接住「有可能」这一互动偏好。首轮含追问降级，修复后完整显示 |
| F | 两次陪看无追问、无模型请求，恢复后生成回应。补测纸风车的位置与开始转动的状态均正确 |

仅在来源题中，把从当前已脱敏问题取得、且同次页面访问旧事件也出现过的短语作为来源线索。历史孩子原句未发送。合规的模型正文可以保留，模型生成的阅读控制动作统一转成普通回应。明确陪看与暂停仍由应用处理。以上人工复核只覆盖这套样本。更广的盲评仍待 Gate 2。

## 复跑

1. 使用干净的 PR 工作树安装 `backend/requirements-dev.txt` 和前端锁定依赖。独立云 Staging 的网络及权限配置见 [Staging 计划](staging-plan.md)。
2. 本地浏览器先执行 `npm --prefix frontend run build:public`。在项目根目录用已安装 Playwright Python、Chrome 和 OpenSSL 的环境执行 `python infra/staging/gate1_local_browser.py --openssl <openssl-path> --output <evidence-json>`。本地 HTTPS 使用一次性证书。仅本地合成服务使用测试证书例外。阅读 HTTP 客户端当前默认上限为 30 秒，Agent 模型调用截止为 3.5 秒。真实云上的慢响应与回退展示待测。
3. CI 隔离 Docker 使用 `bash infra/staging/smoke_ci.sh`。该脚本使用独立 Compose 工程和测试文件。
4. 真实北京 API 在 Windows PowerShell 执行 `run_gate1_live.ps1 -Python <python> -Evidence <checkout-outside-jsonl> -ApprovedSyntheticStaging`，按提示输入专属 API Host 和本地隐藏密钥。凭证无需写入文件。密钥不得进入命令行、仓库或共享证据。原始两轮 JSONL 私有保留，指纹在证据清单。
5. 冻结 v7.5 在候选 `89eb60335c9c2b344afca87bf91ea6d90361c6c7` 执行 `python -m pytest -q qa/p6/yaya_agent_v73`，复跑 58 通过、41 子测试通过。冻结版目录执行 `node --test test_browser.mjs test_coreading_browser.mjs`，启用本机 MOCK HTTP 和 jsdom 后 12 项通过。定向本地回归 45 通过。隔离 SQLite 初始化和 `python database/init_db.py --check` 均通过。
