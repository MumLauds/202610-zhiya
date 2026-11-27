# 旧版会话迁移验证

| 情形 | 期望与证据范围 |
| --- | --- |
| 已启动旧会话，随后获准试用 2.0 | 运行会话按原版本固定，旧状态和历史保留 |
| 允许名单中新建亲子会话 | GUARDIAN + PARENT_CHILD 新 Session 固定为 2.0 |
| 撤销当前授权或关闭 2.0 | 已固定 2.0 Session 使用 2.0 本地安全路径；停用后新 Session 进入旧链路 |
| 儿童独立阅读 | 继续使用目前经授权的短答案流程；上线计划需批准后另行完成该路径的验收 |
| 持久化和恢复 | 隔离 SQLite 验证版本字段和状态。真实云快照恢复、观察期、默认切换和旧版退役均待正式审批 |

自动化证据来自现有阅读流测试和 [Docker smoke](../../infra/staging/smoke_ci.sh)。真实 Staging 的切换与回滚、含试运行存量的恢复，以及各阶段的业务签字仍为 BLOCKED。切换会话规则和退役条件见[部署和回滚](deployment-and-rollback.md)。

## 免 ECS 补充验收

GitHub Actions 隔离 Docker 已实际将前后端切换到桥接提交 42bd04626979d3c93f36f8619e92072ca6fe654a 构建的镜像。读取容器镜像 ID 确认两项都发生改变，SQLite 设备号及 inode 保持一致，原 Caddy CA 校验通过。存量 2.0 会话继续阅读，新会话走旧链路。独立部署站的镜像级演练仍待执行。[CI 回执](https://github.com/MumLauds/202610-zhiya/actions/runs/45986156827/job/140462188944)
