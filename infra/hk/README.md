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
ZHIYA_DOMAIN=zhiyahuiben.xyz
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

### 维护窗口：先停轮询，再合并，最后恢复轮询

Actions 不会更新 ECS 上独立安装的 `/opt/zhiya-release/deploy.sh`。**不要先合并再停 timer**：旧脚本可能先接到新 main 的发布。PR 阶段只运行 `Validate release`；合并后的 main 会重新运行 `Validate release`，随后 `Verify Hong Kong release` 限时等待同一 SHA、同一 run/attempt 的公网回执。提前备好可信检出、权限、负责人和验收记录；窗口内协调其他人不再合并或手动启动 service；确认没有排队的手动作业，部署锁仍是安装时的最后一道保护。不要等 Verify 结束才恢复 timer，也不用为安装脚本启动 Docker、重启容器或重跑首次安装。

**1. 合并前：审查及 PR 校验通过，负责人准备立即合并时，才在 ECS 暂停 timer。** 只停 timer，不停 service。以下 Bash 检查只接受 service 已正常退出的 `inactive`；`Type=oneshot` 运行中常为 `activating`。

```bash
(
  set -eu
  sudo systemctl is-active --quiet zhiya-deploy.timer
  sudo systemctl stop zhiya-deploy.timer
  test "$(sudo systemctl show -p ActiveState --value zhiya-deploy.timer)" = inactive
  while :; do
    state=$(sudo systemctl show -p ActiveState --value zhiya-deploy.service)
    case "$state" in
      inactive) break ;;
      activating|active|deactivating|reloading) sleep 3 ;;
      *) echo "service 状态异常：$state；先查日志，不合并" >&2; exit 1 ;;
    esac
  done
)
```

不要强制终止正在发布的进程。此时记录下文的旧库 pack、keepalive 和临时目录基线，然后才合并。若决定取消、main 未变化且没有待处置的发布故障，立即恢复 timer 并确认 active；不要把暂停的 timer 留给无人负责的窗口。

**2. 合并后：从 GitHub 的本次 main push 记录取合并结果的完整 SHA**（不是 PR head）。在 ECS 的独立可信检出目录操作，先确认 `origin` 是主仓库，不要使用生产 `source.git`。设置 `APPROVED_SHA` 后执行下段；若 main 已又推进，停止并重新确认目标。首个验收版本应选无镜像记录的 SHA，恢复 timer 前记录是否命中缓存；已有镜像不阻碍安全升级宿主脚本，但该次复用不能算冷构建验收。

```bash
(
  set -eu
  : "${APPROVED_SHA:?请设置为本次合并到 main 的完整提交号}"
  git fetch --no-tags origin refs/heads/main
  test "$(git rev-parse --verify 'FETCH_HEAD^{commit}')" = "$APPROVED_SHA"
  root=/opt/zhiya-release
  if sudo test -s "$root/releases/$APPROVED_SHA/images"; then
    echo "镜像已缓存：正常复用，本次不计入冷构建验收"
  else
    echo "无非空镜像记录：发布后核对该 SHA 的真实构建与回执"
  fi
  tmp=$(mktemp)
  trap 'rm -f -- "$tmp"' EXIT
  git show "${APPROVED_SHA}:infra/hk/deploy.sh" > "$tmp"
  bash -n "$tmp"
  sudo flock -w 60 "$root/release.lock" sh -ec '
    dst=$(mktemp "$2/.deploy.sh.XXXXXXXX")
    trap '\''rm -f -- "$dst"'\'' EXIT
    install -m 0750 -- "$1" "$dst"
    cmp "$1" "$dst"
    mv -T -- "$dst" "$2/deploy.sh"
    cmp "$1" "$2/deploy.sh"
  ' sh "$tmp" "$root"
  sudo bash -n /opt/zhiya-release/deploy.sh
  sudo systemctl start zhiya-deploy.timer
  sudo systemctl is-active --quiet zhiya-deploy.timer
)
```

临时文件与目标在同一目录；只在持锁、语法检查和内容比对通过后原子替换，不直接覆盖正在运行的脚本。锁超时就等待后重试，不抢锁、不删锁。若 SHA、安装或比对检查失败，不要继续下一步，不要删除镜像记录来强制冷构建。**合并后的故障不能用“无条件恢复 timer”掩盖**：不要让旧脚本抢先发布新 main；负责人保持值守或明确交接，修复或重新批准方案，验证通过后立即恢复。不得安装未合并的候选脚本。

**3. 现场确认：上段在比对和语法检查成功后才恢复 timer。** 不需 `daemon-reload`，也不用手工启动 service。main 的校验完成后由原有门禁放行；维护窗口过长会使公网验证超时。如果超时，先确认安装与 timer，再为当前 main 重跑全部作业或触发新的手动运行；不能只重跑 Verify、跳过 Validate 或伪造回执。

```bash
sudo systemctl is-active zhiya-deploy.timer
sudo systemctl is-active zhiya-deploy.service
df -h /
docker ps
sudo journalctl -u zhiya-deploy.service -n 80 --no-pager
```

timer 应为 `active`。service 空闲时 `inactive` 是一次性服务的正常状态；运行中等待其自然完成，若为 `failed` 查 journal。以实际公网回执和工作流结果验收，而不是以 timer 为 active 判定发布成功。若 main 被后续提交取代，Verify 的 superseded 成功退出不代表原 SHA 已发布。

**Git 生命周期。** 旧版每次对同一长期裸库做深度为 1 的 fetch；浅取限制提交历史深度，不保证历次 fetch 的重叠对象只占一份磁盘。新版只在需要构建时，于发布根目录 `git.zhiya-XXXXXXXX` 中初始化空库，不取标签，按授权的完整 SHA 浅取一层；验证 `FETCH_HEAD^{commit}` 相等，再从该 SHA 归档既有构建目录到 `build.zhiya-XXXXXXXX`。解包成功即删除 Git 库，才开始构建后端和前端；构建结束删除源码。代价是每个未缓存版本重新下载，需要为下载、解包、Docker 缓存及 SQLite 备份分别预留空间，不能把“无长期 pack”当成磁盘不会再满。

**清理与旧库退役。** 两种临时目录共用构建阶段的 EXIT 清理；断电或 SIGKILL 无法执行 trap。后续轮询先持有 `release.lock` 并处理 `pending` / `history-hold`，再回收超过 24 小时、位于根目录第一层且名称完全匹配上述两个模板的目录（八个随机英数字符）。不跟随符号链接，不清理其他命名的目录。这两个命名空间仅供发布程序使用，不要把它们用作挂载点或在其中挂入其他业务目录。旧 `source.git` 退役方式是停止使用、原地保留；本次不迁移、不删除、不 repack，不改 `refs/keepalive`。若以后需移出另行备份和审批。保留当前及上一版本真实镜像、`releases/<sha>/images`、尝试记录、私有回执和原卷备份；回滚通过镜像 ID，而不是从 Git 重新构建。不要清除镜像或 SQLite 卷。

### 现场验收：两次不同 SHA 的真实构建

1. 验收第一笔必须是沿新 Git 获取路径的真实冷构建。选择两次经正常审查合并的不同 SHA，在各自构建前确认 `releases/<sha>/images` 不存在；保存 SHA、run/attempt 和对应 journal 的构建记录，发布后确认镜像记录已生成。仅复用镜像或命中已有回执不算；不要为验收删除缓存、创建无业务意义的发布或故意在生产触发回滚。
2. 在第 1 步停 timer、旧运行退出后记录下面的基线；每次真实发布完成后，在无部署进行时重复并逐次比较。旧库的 pack 文件清单（名称、字节数、修改时间）及 keepalive 引用必须不变；原本无旧库就应继续不存在。检查本次没有遗留 `git.zhiya-*`、`build.zhiya-*`，若有，先核对运行状态、基线和 journal，不手工删除。

```bash
sudo bash -euo pipefail -c '
  cd /opt/zhiya-release
  if test -d source.git; then
    find source.git/objects/pack -maxdepth 1 -type f -printf "%f %s %T@\n" | LC_ALL=C sort
    git --git-dir=source.git for-each-ref --format="%(refname) %(objectname)" refs/keepalive
  else
    echo "source.git 不存在"
  fi
  find . -mindepth 1 -maxdepth 1 -type d \( -name "git.zhiya-*" -o -name "build.zhiya-*" \) -print
'
```

3. 每次完成后，用该次 main Actions 给出的完整 `SHA` 和 `RUN`（运行编号-尝试次数）校验私有记录与**实际公网**回执，不用主机上的公开文件代替网络读取。用该次切换前记录的两个镜像 ID 验证上一版本仍在；保留原备份、历史校验与失败回滚流程，不更改 pending/history-hold。

```bash
(
  set -euo pipefail
  : "${SHA:?请设置本次 main 完整提交号}" "${RUN:?请设置本次 runID-attempt}"
  root=/opt/zhiya-release
  check='.revision == $sha and .run == $run'
  sudo jq -e --arg sha "$SHA" --arg run "$RUN" "$check" "$root/last-success.json"
  curl -fsS --max-time 10 "https://zhiyahuiben.xyz/_deployment/current.json?v=$RUN-$(date +%s)" |
    jq -e --arg sha "$SHA" --arg run "$RUN" "$check"
  sudo test -s "$root/releases/$SHA/images"
  attempt="$root/releases/$SHA/attempt-$RUN"
  old_api=$(sudo sed -n '2p' "$attempt")
  old_web=$(sudo sed -n '3p' "$attempt")
  sudo docker image inspect --format '{{.Id}}' "$old_api" "$old_web"
)
```

## 验收与恢复

ECS 将最近成功的回执保存在发布目录的私有文件 `last-success.json`。Docker 暂时不可读或运行镜像发生偏差时撤下公网副本；后续轮询确认两个容器均在运行、镜像 ID 和修订号均吻合后，原子恢复同一份公网回执。Actions 只接受匹配本次运行编号、尝试次数和提交号的回执。开始构建和切换前，ECS 均要求同一次主线工作流仍在运行且测试已通过。

备份前会在主机登记本次尝试。备份校验失败会清理本次副本；修复原因后可重跑全部作业或重新手动触发。切换后核对原有账号、档案、会话、互动事件和小结。已保存的问答、反馈、已完成小结正文和亲子活动均参与校验，备份时尚在生成的小结可以正常完成。

**历史数据核验失败时**，发布脚本回退应用镜像，撤下公网回执，并在 `/opt/zhiya-release/history-hold` 保留失败提交号、前一对镜像 ID 和运行标识。后续任何提交或重试都会暂停。中断恢复时也会用该次切换前的备份复核历史。先检查 journal；若仍有 `pending`，先完成应用回退。按 `history-hold` 的第一、四列定位 `releases/<sha>/attempt-<run>`，首行是原始备份在数据卷中的路径。由负责人对照此备份核对缺失记录及期间新增写入，修复数据并用 `backup.py verify <原始备份路径>` 在后端容器中复验。若差异来自已确认的用户主动删除或批准迁移，则记录具体差异及确认依据。处理完成后，在定时服务停用且当前发布进程已退出时，将 `history-hold` 移至私有事故记录目录留档，再启用定时器并发起新运行。

- 访问 `https://zhiyahuiben.xyz/_deployment/current.json`，将 revision 与 Actions 的 main SHA 核对。backend、web 是运行容器的本地镜像 ID。previous 为上一版本，run 为 GitHub 运行编号和尝试次数。切换到其他域名时同时更新主机配置与工作流中的 SITE。
- 技术检查覆盖桌面和移动入口内容、健康接口、匿名书架和后台访问。首次业务验收另用家长账号登录，逐页核对 20 本原创，打开原档案、历史和小结，再用管理员账号验证后台。记录「还没懂」的实际引导、语音及小结服务模式。
- 每次切换前，在线 SQLite backup 会在原卷内创建权限受限的独立备份并检查完整性和外键。每次尝试的备份路径、前一对镜像 ID 和修订号保存在主机 `/opt/zhiya-release/releases/<sha>/attempt-<run>-<attempt>`。保留这些文件、相关镜像和独立的异机备份，定期按团队保留策略核对剩余空间。
- 切换失败会恢复上一对镜像并检查健康状态；进程中断则在下次启动时读取 pending 完成恢复。检查 journal 后可重新运行同一 SHA 的**全部作业**或手动触发新运行。同一 SHA 复用已记录的镜像 ID。应先在隔离副本上演练切换失败和服务中断，核对上一版本恢复且备份后新增的记录仍在。
- 数据库迁移必须兼容上一应用版本。若回退健康检查失败，应停止定时器，保存 pending 和当前数据库，由后端与部署负责人核对迁移和备份后的写入。整库恢复或重新导入种子须单独评估。
