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

## 已有主机：升级 Git 获取方式

**仓库合并不等于主机脚本升级。** Actions 不会把 `infra/hk/deploy.sh` 写到 ECS；定时器执行独立安装的 `/opt/zhiya-release/deploy.sh`。只有负责人审查、批准并合并 PR 后，才从批准提交取出该文件，核对后在主机上持同一把锁原子替换。不要现场手改脚本、执行候选分支，或为此重启生产容器。仅换脚本不改 systemd 单元，也不需要重跑首次安装。以下从含该提交的受信任检出目录执行（需 GNU 工具）：

```sh
(
  set -eu
  : "${APPROVED_SHA:?请先设置为已批准并合并的完整提交号}"
  test "$(git rev-parse --verify "${APPROVED_SHA}^{commit}")" = "$APPROVED_SHA"
  tmp=$(mktemp)
  trap 'rm -f -- "$tmp"' EXIT
  git show "${APPROVED_SHA}:infra/hk/deploy.sh" > "$tmp"
  bash -n "$tmp"
  root=/opt/zhiya-release
  sudo flock -n "$root/release.lock" sh -ec '
    install -m 0750 -- "$1" "$2/deploy.sh.new"
    mv -T -- "$2/deploy.sh.new" "$2/deploy.sh"
  ' sh "$tmp" "$root"
  sudo cmp "$tmp" "$root/deploy.sh"
)
```

若锁被占用，不抢锁、不打断运行，稍后再安装。之后按原有授权流程观察下一次发布。同一 SHA 已有非空 `releases/<sha>/images` 时，不拉源码、不重建；仍逐一核对两份镜像 ID 与 revision 标签，不匹配就停止。

**Git 生命周期。** 旧版每次对同一长期裸库做深度为 1 的 fetch；浅取限制提交历史深度，不保证历次 fetch 的重叠对象只占一份磁盘。新版只在需要构建时，于发布根目录 `git.zhiya-XXXXXXXX` 中初始化空库，不取标签，按授权的完整 SHA 浅取一层；验证 `FETCH_HEAD^{commit}` 相等，再从该 SHA 归档既有构建目录到 `build.zhiya-XXXXXXXX`。解包成功即删除 Git 库，才开始构建后端和前端；构建结束删除源码。代价是每个未缓存版本重新下载，需要为下载、解包、Docker 缓存及 SQLite 备份分别预留空间，不能把“无长期 pack”当成磁盘不会再满。

**清理与旧库退役。** 两种临时目录共用构建阶段的 EXIT 清理；断电或 SIGKILL 无法执行 trap。后续轮询先持有 `release.lock` 并处理 `pending` / `history-hold`，再回收超过 24 小时、位于根目录第一层且名称完全匹配上述两个模板的目录（八个随机英数字符）。不跟随符号链接，不清理其他命名的目录。这两个命名空间仅供发布程序使用，不要把它们用作挂载点或在其中挂入其他业务目录。旧 `source.git` 退役方式是停止使用、原地保留；本次不迁移、不删除、不 repack，不改 `refs/keepalive`。若以后需移出另行备份和审批。保留当前及上一版本真实镜像、`releases/<sha>/images`、尝试记录、私有回执和原卷备份；回滚通过镜像 ID，而不是从 Git 重新构建。不要清除镜像或 SQLite 卷。

**只读验收。** 在空闲期记录以下输出，在至少两个不同 SHA 实际完成新构建后再比较（仅复用旧镜像不能验证下载路径）。若保留旧库，其 pack 清单与 keepalive 引用应不变；原先没有旧库则不应新建。正常退出不应留下本次构建目录。将两次的发布回执与对应的 SHA、run/attempt 核对，并确认上一个版本的镜像 ID 仍可 inspect。

```sh
# 在发布根目录，以有读取权限的账号执行
cd /opt/zhiya-release
if test -d source.git; then
  git --git-dir=source.git count-objects -vH
  find source.git/objects/pack -maxdepth 1 -type f -printf '%f %s %T@\n' | sort
  git --git-dir=source.git for-each-ref --format='%(refname) %(objectname)' refs/keepalive
fi
find . -mindepth 1 -maxdepth 1 -type d \
  -regextype posix-extended -regex '\./(git|build)\.zhiya-[A-Za-z0-9]{8}' -print
```

## 验收与恢复

ECS 将最近成功的回执保存在发布目录的私有文件 `last-success.json`。Docker 暂时不可读或运行镜像发生偏差时撤下公网副本；后续轮询确认两个容器均在运行、镜像 ID 和修订号均吻合后，原子恢复同一份公网回执。Actions 只接受匹配本次运行编号、尝试次数和提交号的回执。开始构建和切换前，ECS 均要求同一次主线工作流仍在运行且测试已通过。

备份前会在主机登记本次尝试。备份校验失败会清理本次副本；修复原因后可重跑全部作业或重新手动触发。切换后核对原有账号、档案、会话、互动事件和小结。已保存的问答、反馈、已完成小结正文和亲子活动均参与校验，备份时尚在生成的小结可以正常完成。

**历史数据核验失败时**，发布脚本回退应用镜像，撤下公网回执，并在 `/opt/zhiya-release/history-hold` 保留失败提交号、前一对镜像 ID 和运行标识。后续任何提交或重试都会暂停。中断恢复时也会用该次切换前的备份复核历史。先检查 journal；若仍有 `pending`，先完成应用回退。按 `history-hold` 的第一、四列定位 `releases/<sha>/attempt-<run>`，首行是原始备份在数据卷中的路径。由负责人对照此备份核对缺失记录及期间新增写入，修复数据并用 `backup.py verify <原始备份路径>` 在后端容器中复验。若差异来自已确认的用户主动删除或批准迁移，则记录具体差异及确认依据。处理完成后，在定时服务停用且当前发布进程已退出时，将 `history-hold` 移至私有事故记录目录留档，再启用定时器并发起新运行。

- 访问 `https://zhiya.47-238-246-210.sslip.io/_deployment/current.json`，将 revision 与 Actions 的 main SHA 核对。backend、web 是运行容器的本地镜像 ID。previous 为上一版本，run 为 GitHub 运行编号和尝试次数。切换到其他域名时同时更新主机配置与工作流中的 SITE。
- 技术检查覆盖桌面和移动入口内容、健康接口、匿名书架和后台访问。首次业务验收另用家长账号登录，逐页核对 20 本原创，打开原档案、历史和小结，再用管理员账号验证后台。记录「还没懂」的实际引导、语音及小结服务模式。
- 每次切换前，在线 SQLite backup 会在原卷内创建权限受限的独立备份并检查完整性和外键。每次尝试的备份路径、前一对镜像 ID 和修订号保存在主机 `/opt/zhiya-release/releases/<sha>/attempt-<run>-<attempt>`。保留这些文件、相关镜像和独立的异机备份，定期按团队保留策略核对剩余空间。
- 切换失败会恢复上一对镜像并检查健康状态；进程中断则在下次启动时读取 pending 完成恢复。检查 journal 后可重新运行同一 SHA 的**全部作业**或手动触发新运行。同一 SHA 复用已记录的镜像 ID。应先在隔离副本上演练切换失败和服务中断，核对上一版本恢复且备份后新增的记录仍在。
- 数据库迁移必须兼容上一应用版本。若回退健康检查失败，应停止定时器，保存 pending 和当前数据库，由后端与部署负责人核对迁移和备份后的写入。整库恢复或重新导入种子须单独评估。
