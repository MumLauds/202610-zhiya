# 发布与回滚

## 当前 ECS 核查

| 项目 | 证据 | 现场待确认 |
|---|---|---|
| GitHub 和发布门禁 | 当前远端 main、前后端镜像 revision 和公网回执均为 `48844b2`，回执 run/attempt 为 `45550056951-1`。部署 timer 为 active，oneshot 空闲 inactive，上次 Result=success。宿主安装的 `deploy.sh` 指纹匹配历史 `4297f3cf13`，与当前 main 的脚本字节不同 | R13 需受控同步宿主脚本并证明冷构建与切换/恢复。镜像到位不代表宿主脚本已升级。[发布运行记录](https://github.com/MumLauds/202610-zhiya/actions/runs/45550056951) |
| Ubuntu ECS / Docker Compose / Caddy / Uvicorn | Workbench 显示 Ubuntu 22.04.5、Compose v5.6.1，控制台为 2 vCPU / 4 GiB。根分区 79%，余 8.2 GiB。backend healthy，web、gateway 均在运行。网关容器 Caddy v2.11.7，宿主 Caddy unit inactive。仓库后端镜像配置单 worker Uvicorn | 磁盘、图片缓存和原卷备份的可用空间窗口待验，维持容量告警。[香港手册](../../infra/hk/README.md) · [镜像](../../infra/Dockerfile) |
| Cloudflare、DNS、TLS、缓存 | 公共 DNS 为 Cloudflare。本机直连 TLS 健康为 ok，发布回执 200，匿名后台 401。通过 Cloudflare 的公开健康接口返回 HTTP/2 200，`CF-Cache-Status: DYNAMIC`，公开回执同 SHA/run。浏览器确认健康正文 ok 与回执。早先工具网络超时 | 单次健康响应只证明这一端点的缓存状态，真实私有阅读与媒体的 CDN BYPASS/no-store 待独立 Staging 验收。 [公开回执](https://zhiyahuiben.xyz/_deployment/current.json) · [公开健康](https://zhiyahuiben.xyz/api/v1/health) |
| 源站与管理面 | 网关绑定 80/443，18080 侦听于 loopback。香港安全组 IPv4 入方向允许公网 80/443 及 SSH 22、RDP 3389、ICMP，Caddyfile 未匹配常见 IP 过滤指令 | 未从外部直连源站测试，宿主防火墙与 Cloudflare 控制台完整策略待复核。关闭 R12 后才允许真实儿童 Pilot |
| 日志、保护与模型 | 已有 systemd、pending 与 history-hold 保护。实际容器模型配置 `qwen3.6-plus`，host 为 `dashscope.aliyuncs.com`，仅确认凭证存在 | 运行日志采样、备份恢复及真实 2.0 模型出站未测。北京 `qwen3.8-flash` 仍是拟接入目标 |

后续核验用只读账号查看 Cloudflare DNS/TLS/缓存与源站 ACL。检查专用管理入口、网络防火墙、备份卷与恢复。API 私有响应的 `private, no-store`/CDN BYPASS、健康和回执缓存行为分开记录。多网络点重复 GET，保留日期、状态、SHA、证书和缓存头。 [香港发布工作流](../../.github/workflows/deploy-hk.yml) · [PR #96](https://github.com/MumLauds/202610-zhiya/pull/96)

## 分阶段发布

| 阶段 | 开启方法与检查 | 退出 |
|---|---|---|
| 内部 | Local/Staging 合成数据，默认关闭，按测试账号显式启用 | 功能开关关闭，清理隔离测试数据 |
| 白名单 | Gate 1 后仅内部测试、独立 Staging 与专用凭证 | 验证监控、预算和回执后进入 Gate 2 审批 |
| 受控家庭 | Gate 2 签字，监护人授权，批准人数/周期/费额，指定值班 | 安全与数据问题立即停止。正常结束时依数据治理删除 |
| 扩量 | Pilot 通过后小批量扩大。每步对比回退/延迟、账单和反馈并审批 | 有越权、错误角色归因、预算透支、恢复异常就停止扩量。按下文关闭 2.0 |
| 正式 | Gate 3 审批、告警和回滚就绪 | 旧版保留已批准观察期作为回退路径 |

## Staging 退回步骤（待演练）

使用服务端 `YAYA_AGENT2_MODE=off|allowlist|default`，部署默认值为 `off`。Staging 仅允许 `off` 或经批准的 `allowlist`。Session 创建事务固定 Agent 版本。读取、报告、账单继续使用原有业务事实。

1. 取事故编号、最近健康回执、当前/上一对镜像 ID、git SHA、数据库迁移版本及快照。守住环境和发布锁，暂停会让当前环境自动恢复 2.0 的发布入口。现场禁止导出环境密钥或完整 Compose 渲染。
2. 先把新 2.0 会话和新模型请求关闭，新 Session 暂时使用旧版。进行中的 2.0 Session 保持原归属，关闭远程生成，继续使用 2.0 本地安全回应和已有回执。等待在途请求结束，确认并发或修订号冲突的结果已丢弃。家长也可以结束该会话，从书页位置开启独立的旧版 Session。保留原回执供 API-013 读取。
3. 复核 API-013、读进度、旧版作答、完成、小结以及已发生的事件。在途请求结束后，确认 2.0 新会话和 2.0 出站调用为零，已有 2.0 会话的本地回执仍可读。网络异常和预算触顶同样可先走开关，监控记录原因。
4. 若开关无法恢复，在批准的 **Staging** 目录用登记过的支持版本绑定的上一镜像和原数据卷重启应用。存在活跃 2.0 Session 时，回滚目标必须识别版本绑定。由运维确认前后端镜像和 Compose overlay 的精确命名。先运行健康检查，再验证匿名权限、授权会话、历史回执和关键增量写入。示例顺序见下节。香港线上如未来批准回滚，用仓库既有发布手册的 `pending/history-hold` 恢复，保留审计证据。
5. 数据迁移须先读兼容再引入写入，上一版能读当前 schema 才直接回应用。如有不兼容或完整性错误，保持只读/停写，保存当前库、备份及差异。由 DB 负责人在隔离副本验证恢复、外键和备份后增量，批准一个经过比对的恢复点再切流量。整库覆盖需要单独审批。无论结果都核对用户删除是否会被备份恢复撤销。
6. 只有事故修复、审批、回执核验和容量数据通过，才解除冻结。重新启用采用新的发布记录和审批动作。回滚证据包含时间、执行者、镜像对、卷、回执、恢复和用户通知。

```bash
# 仅供获批的 Staging 演练。路径和镜像由发布回执提供。
set -eu
test "$STG_PROJECT" = zhiya-agent-stg
: "${STG_ENV:?}" "${STG_COMPOSE:?}" "${STG_PREVIOUS_IMAGES:?}" "${STG_ORIGIN:?}"
docker compose -p "$STG_PROJECT" --env-file "$STG_ENV" -f "$STG_COMPOSE" -f "$STG_PREVIOUS_IMAGES" config --quiet
docker compose -p "$STG_PROJECT" --env-file "$STG_ENV" -f "$STG_COMPOSE" -f "$STG_PREVIOUS_IMAGES" up -d --no-build --pull never --wait backend web
curl -fsS --max-time 10 "$STG_ORIGIN/api/v1/health"
```

旧镜像 overlay 必须固定 digest 且引用现有卷。后续检查须按 [上线清单](go-live-checklist.md) 完成。通用 `infra/compose.yaml` 仅有 backend，不能单独套用上述 Staging web 命令。


## Legacy Agent Migration & Retirement Plan

| 时点 | 新 Session | 已有 Session 与回执 |
|---|---|---|
| 迁移桥接和 Gate 1 | 默认进入旧版。经批准的独立 Staging 仅向合成账号开放 2.0。 | 兼容读取加表前的数据和回执。无 2.0 绑定的 Session 沿旧版继续。2.0 绑定与创建 Session 同事务提交。 |
| Gate 2 受控试运行 | 仅已批准名单内、获得独立授权的家庭可启动 2.0。 | 试运行绑定按 Session 生效。调整名单只影响新建和续读生成的新 Session。 |
| Gate 1、2、3 全数通过且发行审批后 | 运营记录批准人、切换时刻、镜像 SHA、数据库快照和回滚镜像。将发布配置设置为 `YAYA_AGENT2_MODE=default` 和 `YAYA_AGENT2_RELEASE_APPROVED=true`。旧版此时停止接收所有新 Session。 | 在途和存量旧版 Session 继续按原版本完成。续读如创建新 Session，传递书籍版本、已读路径和页码等阅读事实，新对话由 2.0 独立开始。历史事件、回执和阅读报告仍可查询。 |
| 观察期内的应急回滚 | 经批准将配置改回 `off`，新 Session 临时回到旧版。 | 保留 2.0 绑定，使用本地安全回应。仍活跃的 2.0 Session 必须运行在支持绑定的桥接版本上。存量旧 Session 始终使用旧上下文。 |
| 停止旧模型和退役 | 新建链路统一为 2.0。 | 旧会话历史继续可读。未正常完成的旧会话按审批的规则结束，家长可从原页开启独立 2.0 会话。 |

切换前，使用迁移前数据库的隔离副本验证加表前后兼容、外键、会话状态和恢复。线上 Compose 来自 ECS 宿主机的发布目录。获批后由运维单独审查并将 2.0 的环境变量传入 backend，先用 `off` 验证旧链路及版本绑定兼容，再依签字记录切换。现行模型环境变量保持原值。用过期或撤销授权、跨账号读写、重复请求键、并发翻页、进程中断、超时及紧急 `off` 覆盖版本边界。使用同一个 Session ID 检查关前关后的 Agent 路由，核对双方上下文和回执。Gate 记录需要附实际验证证据及签字。

建议在正式切换时审批至少 14 个完整自然日的观察窗口、负责人和主动关闭旧会话的用户通知方案。观察期需从最后一次恢复旧版接入后重算。退役前核对旧版新建数、活跃旧 Session、在途旧请求与旧模型出站。如旧 Session 仍活跃，继续排空或遵照审批方案通知家长并结束旧会话。活跃旧 Session 和旧请求均为零、观察期通过且上线负责人已签字后，关闭**旧版共读专用**模型调用，并连续验证七天无旧版出站或新建。保留由阅读报告等其他能力使用的共享模型、ASR、TTS 网关与各自配置。

旧版删除另建经过批准的退役变更。验收需证明旧专用入口和路由分支、旧 Prompt 及其专用组装和调用代码、临时名单和旧版回滚开关均已清理。保留 API-013、015、016、017 的稳定契约及存量回执的读取兼容。回滚手册须改为回退到已验证的 2.0 版本，由产品、后端、QA 和运维联合确认。退役完成后，正式共读只走 Yaya Agent 2.0。

## PR #97 Gate 1 本轮证据

CI 已对隔离样例执行输入授权撤回与 off 模式两阶段演练。撤回保持已固定会话的 2.0 版本并阻止供应商调用。off 模式下，新会话回到旧链路，存量 2.0 本地安全续读。独立云回滚、默认切换和旧版退役仍需审核。见[迁移验证](legacy-migration-verification.md)。
