# 当前能力与证据盘点

## 证据口径

| 编号 | 证据和位置 | 可以确认 |
|---|---|---|
| E01 | GitHub `main@48844b2`，与本地审阅树一致 | 正式代码和仓库配置的当前基线。生产前后端镜像 revision 与公网回执已在 E07 核对。[主分支提交](https://github.com/MumLauds/202610-zhiya/commit/48844b2ee267bc539ed0580a358470beca13e449) |
| E02 | `yaya-qwen4-max-benchmark@1eb9fb4` 下 `qa/p6/yaya_agent_v73` | 实验源码、冻结清单、模拟测试及配对驱动。仓库主分支中的实验目录以自身内容为准 |
| E03 | v7.5 `live-r2-17695-29396` 与冻结证据 ZIP | 北京 Flash，6 个合成会话、14 条请求，0 次服务端回退。已读上下文与服务端展示结果可重放 |
| E04 | 配对 `live-direct-full-1eb9fb4-cc4c72`，批次 `031aaa96a5a842cda704d47f8860807a` | 同一冻结对话上下文，Flash/Max 各 14 条，28 条正式回包均送达。Max 另有 Schema 与一句完整回答提示。调用与浏览器延迟分属两类 |
| E05 | E04 的配对 AI 评分及备份映射 | 解盲后 Flash 6、Max 6、相当 2。自然度 Flash 4.71 对 4.36（14 组），想象陪伴 Max 4.60 对 3.80（5 组）。单份 AI 辅助评分 |
| E06 | G13 及独立 Qwen 决策实验 | 现有产品曾有分阶段集成验证。每份只适用于记录注明的构建、浏览器和场景。[G13 Round 1](../testing/g13/round1/README.md) · [正式应答评估](../testing/yaya-answer-evaluation.md) · [隔离决策实验](../testing/yaya-qwen-decision-pilot.md) |
| E07 | 香港发布源码、ECS Workbench、公开端点和安全组截图观察 | `48844b2` 两个镜像与公网回执一致，本机及跨 Cloudflare 的健康检查通过。源站安全组对公网开放 80/443、22/3389 和 ICMP，需专项复核。[发布与回滚](deployment-and-rollback.md) · [PR #96](https://github.com/MumLauds/202610-zhiya/pull/96) |
| E08 | 审查期间执行的本地自动化 | 正式树后端全量 812 通过/1 SHA-256 失败，确认是 Windows 换行转换，原始 Git 字节的定向复跑 5/5。前端全量首次 466 通过/1 超时，单 worker 全量复跑 467/467，移动会话 14/14，公开构建通过。实验 Python 78 项、浏览器模拟 11 通过/1 跳过。详见 [证据清单](evidence-manifest.json) |
| E09 | 公开法规和供应商条款 | 作为设计与法律确认输入。正式履约期限与当前账号处理配置仍需留档。[隐私与儿童安全](privacy-and-data-governance.md) |

逐文件 SHA-256、实验提交、运行批次及审查命令汇总在 [证据清单](evidence-manifest.json)。本地归档以目录名和文件名定位，由项目维护者按权限提供。评分映射、原始合成日志及密钥不进入文档提交。

正式树测试使用隔离临时数据、锁定依赖和替身。115 项定向后端通过。后端全量另外包含 313 个通过的子测试和一条 TestClient 弃用提示。唯一失败为历史语义夹具的字节校验。对照 Git blob 确认 v1/v2 两文件仅换行不同，隔离副本还原原始 LF 字节后 5 项通过。全量结果按首次记录保留。前端第一次全量只有书架的 same-filter refresh 用例超过 5 秒，独立文件 7/7 和后续全量复跑均沿用原超时。本轮具备公开构建和现网匿名探针证据，完整用户流程的真实浏览器验收仍待独立 Staging。

## 正式系统能力

| 面向 | 主分支已有实现 | 当前证据边界 |
|---|---|---|
| 阅读业务 | 创建、开始、翻页、分支、作答、家长提问、引导、结束、报告、读取和删除 | 已有 API 和测试。需用新 Agent 完整走一次真实页面 |
| 数据与授权 | SQLite，家长账号归属、儿童授权绑定绘本与会话、CSRF、版本和 revision，幂等事件 | 应继续复用。新 Agent 的消费范围、回滚隔离、滞留清理需验收 |
| 阅读应答 | 旧版规则回应，模型受控选候选。回答和提问分路，刷新恢复事件 | 生产链路代码与 v7.5 生成协议不同。PR #88 的历史验证明确使用测试替身。[PR #88](https://github.com/MumLauds/202610-zhiya/pull/88) |
| 显示与语音 | 桌面端和移动端文字、录音/ASR、TTS、等待、合规的状态恢复入口 | 当前端复用的回复接口仍缺 v7.5 对话状态字段。真机、HTTPS、2.0 联动缺证据 |
| 供应商 | 服务端环境配置、连接复用、HTTPS、有限时和响应体约束、0 传输重试 | 正式代码默认值与当前容器设置都是 `qwen3.6-plus`，运行端点 host 为 `dashscope.aliyuncs.com`，已确认凭证存在。未请求模型验证此次线上出站。2.0 目标固定北京 Flash |
| 小结 | 基于实际已读和已保存事件，后台任务及启动恢复，有本地摘要 | 保持原用途，新增 Agent 交互怎样供小结引用待合同签字 |

主分支定位 [FastAPI 装配](../../backend/app/main.py) · [阅读路由](../../backend/app/api/v1/reading_sessions.py) · [引导和提问](../../backend/app/api/v1/guidance.py) · [阅读服务](../../backend/app/services/reading_agent.py) · [小结服务](../../backend/app/services/reading_reports.py) · [数据库结构](../../database/schema.sql)。

## 统一集成的候选实现

API-011 和续读创建以同一事务保存 2.0 会话绑定。API-015、016、017 按会话归属分流。未绑定历史归属旧版。API-013 读取既有事件并为家长返回可选回执。北京 `qwen3.8-flash` 使用单独地址、密钥和预算。此前检查到的线上旧模型是 `qwen3.6-plus`。线上巡检证据为文首时点。 [服务](../../backend/app/services/yaya_agent.py) · [配置](../../backend/app/services/yaya_agent_config.py)
