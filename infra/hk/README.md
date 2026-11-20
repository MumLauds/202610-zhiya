# 香港站自动部署

PR 运行 `Validate release`。合入 main 后，香港 ECS 每三分钟检查主仓库，验证同一 SHA 的 push 或手动运行已通过测试，再在 ECS 上构建公开版前后端。GitHub 的 `Verify Hong Kong release` 从公网读取回执，复核版本、健康状态和匿名后台权限。整个流程依赖公开仓库、ECS 到 GitHub 和镜像源的出站网络，以及当前可访问的 HTTPS 域名。

## 首次安装

在香港 ECS 上从经审查的 Git 检出目录执行。需要 Bash、Git、Python 3、jq、curl、flock、Docker Engine 和带 `up --wait` 的 Compose v2。先用 `docker compose ls` 和 `docker ps` 核对现有项目、前后端和网关，用 `docker inspect` 的 Mounts 核对 SQLite 命名卷与现有 Caddyfile。确认两份应用镜像带有一致的完整 Git 修订号，宿主机和数据卷有足够空间存放新镜像、临时构建目录及完整数据库备份。

```sh
install -d -m 0750 /opt/zhiya-release
install -m 0750 infra/hk/deploy.sh /opt/zhiya-release/
install -m 0644 infra/hk/{gate.py,backup.py,receipt.py,Web.Dockerfile,receipt.yaml} /opt/zhiya-release/
install -m 0644 infra/hk/zhiya-deploy.{service,timer} /etc/systemd/system/
```

在 `/opt/zhiya-release/deploy.env` 写入经现场核对的参数，属主为 root，权限 0600。以下是本次香港站的项目和路径。

```ini
DEPLOY_DIR=/opt/zhiya-g15-hk-49d1e64-20261120/deployment
COMPOSE_PROJECT_NAME=zhiya-hk-49d1e64-20261120
ZHIYA_PROVIDER_ENV_FILE=/opt/zhiya-g15-hk-49d1e64-20261120/production-provider/production.env
ZHIYA_DOMAIN=zhiya.47-238-246-210.sslip.io
ZHIYA_GATEWAY_PORT=18080
```

主机文件和单元均由 root 管理。供应商配置由现有 Compose 注入后端。部署脚本只重建 backend 和 web，沿用原项目、数据卷和公网网关。首次合入前准备好服务，或安装后在 Actions 对当前 main 运行一次 `workflow_dispatch`。以后修改主机端发布程序时，从经审查的提交重新安装相关文件，更新单元后执行 daemon-reload。

```sh
systemctl daemon-reload
systemctl enable --now zhiya-deploy.timer
systemctl start zhiya-deploy.service
journalctl -u zhiya-deploy.service -n 60
```

## 验收与恢复

每次轮询先核对运行容器的状态、镜像 ID 和修订号，发生偏差时撤下公网回执。Actions 只接受匹配本次运行编号、尝试次数和提交号的回执。开始构建和切换前，ECS 均要求同一次主线工作流仍在运行且测试已通过。

备份前会在主机登记本次尝试。备份校验失败会清理本次副本；修复原因后请重跑全部作业或重新手动触发。切换后将备份中的账号、档案、会话和小结标识与当前库比对，同时核验归属和外键。遇到缺失或归属变化会阻止签发回执并触发应用回退。若期间发生用户主动删除或经批准的身份迁移，由负责人核对变更后再发起新尝试，保留用户最新数据。

- 访问 `https://zhiya.47-238-246-210.sslip.io/_deployment/current.json`，将 revision 与 Actions 的 main SHA 核对。backend、web 是运行容器的本地镜像 ID。previous 为上一版本，run 为 GitHub 运行编号和尝试次数。切换到其他域名时同时更新主机配置与工作流中的 SITE。
- 技术检查覆盖桌面和移动入口内容、健康接口、匿名书架和后台访问。首次业务验收另用家长账号登录，逐页核对 20 本原创，打开原档案、历史和小结，再用管理员账号验证后台。记录「还没懂」的实际引导、语音及小结服务模式。
- 每次切换前，在线 SQLite backup 会在原卷内创建权限受限的独立备份并检查完整性和外键。每次尝试的备份路径、前一对镜像 ID 和修订号保存在主机 `/opt/zhiya-release/releases/<sha>/attempt-<run>-<attempt>`。保留这些文件、相关镜像和独立的异机备份，定期按团队保留策略核对剩余空间。
- 切换失败会恢复上一对镜像并检查健康状态；进程中断则在下次启动时读取 pending 完成恢复。检查 journal 后可重新运行同一 SHA 的**全部作业**或手动触发新运行。同一 SHA 复用已记录的镜像 ID。应先在隔离副本上演练切换失败和服务中断，核对上一版本恢复且备份后新增的记录仍在。
- 数据库迁移必须兼容上一应用版本。若回退健康检查失败，应停止定时器，保存 pending 和当前数据库，由后端与部署负责人核对迁移和备份后的写入。整库恢复或重新导入种子须单独评估。
