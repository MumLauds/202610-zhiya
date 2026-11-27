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

拟用环境开关 `YAYA_AGENT2_ENABLED` 与服务端名单，默认 false。名称随 v7.6 合同确定。会话从开始固定 `agent_version` 与相应命名空间。新旧版本不读取对方的对话缓存。读取、报告、账单从业务事实继续供给。

1. 取事故编号、最近健康回执、当前/上一对镜像 ID、git SHA、数据库迁移版本及快照。守住环境和发布锁，暂停会让当前环境自动恢复 2.0 的发布入口。现场禁止导出环境密钥或完整 Compose 渲染。
2. 先把新 2.0 会话和新模型请求关闭，仍可使用旧版规则路径。对进行中的 2.0 会话冻结生成，用既有可恢复回应及已保存阅读事实继续。清掉旧执行 token 或静默丢弃迟到结果。用户要回到旧版引导时，从现有阅读位置创建新的旧版对话上下文，由家长确认，切换前保留原回执供 API-013。声轨也停止和清理。
3. 复核 API-013、读进度、旧版作答、完成、小结以及已发生的事件。关闭后应观察新增供应商调用和新 2.0 回执为零。网络异常和预算触顶同样可先走开关，监控记录原因。
4. 若开关无法恢复，在批准的 **Staging** 目录用本次记录的上代镜像 ID 和原数据卷重启应用。由运维确认前后端镜像和 Compose overlay 的精确命名。先运行健康检查，再验证匿名权限、授权会话、历史回执和关键增量写入。示例顺序见下节。香港线上如未来批准回滚，用仓库既有发布手册的 `pending/history-hold` 恢复，保留审计证据。
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
