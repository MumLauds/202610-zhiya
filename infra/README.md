# G08 后端部署初稿

| 缩写 | 中文名称（英文全称） |
| --- | --- |
| API | 应用程序编程接口（Application Programming Interface） |
| HTTP / HTTPS | 超文本传输协议（Hypertext Transfer Protocol）/ 安全超文本传输协议（Hypertext Transfer Protocol Secure） |
| FR / NFR / AC | 功能需求（Functional Requirement）/ 非功能需求（Non-Functional Requirement）/ 验收准则（Acceptance Criteria） |
| UID / GID | 用户标识（User Identifier）/ 用户组标识（Group Identifier） |
| WSL | 适用于 Linux 的 Windows 子系统（Windows Subsystem for Linux） |
| JSON | JavaScript 对象表示法（JavaScript Object Notation） |

G08 为本组模型与语音接入任务编号；SQLite 是本项目使用的文件数据库。

变更负责人：林鑫；日期：2026-11-11。状态：已通过本地 Linux 容器基础验证，尚未生产部署验收。Windows 未安装 Docker Desktop；本轮使用已有的 D 盘 Debian WSL 2 内 Docker Engine 29.9.2 / Compose 5.6.1，未重复安装 Desktop。

验证项目 `zhiya-g08-check-20261111`：镜像构建、缺库 503、初始化后 200、种子导入及重复导入、UID 10001、重启数据保留、Docker healthy 与依赖检查通过。15 张表、21 本书、21 个版本、110 页、20 条知识。验证后停止并移除了本次容器和网络，保留独立测试卷与镜像；未调用真实模型。Windows 主机端口转发不计为本轮通过项。

环境限制：直接从 `/mnt/d` 构建时，Windows 测试缓存的扩展属性读取返回 permission denied；增加缓存忽略项仍未消除该环境问题。本轮将明确列出的后端源码、依赖、数据库脚本、种子 JSON 与 infra 文件复制到 WSL 原生文件系统后构建成功。遇到相同问题应使用 WSL 内的干净源码目录，不复制 .env、缓存、数据库或虚拟环境。不能把该结果写成 Windows 原目录直接构建已通过。

同日切换为 DeepSeek 文本与百炼语音后，独立项目 `zhiya-mixed-check-20261111` 重新构建、初始化、空密钥启动、容器内默认配置核对与健康响应 200 通过；测试容器及网络已移除，独立卷与镜像保留。详见[文本与语音验证记录](../docs/testing/bailian-checks.md)，不代表真实供应商联调通过。

2026-11-12 补充：镜像新增 FFmpeg，包含录音转换及语音公开路由；未接业务认证时默认返回 503。当前源码的重建与转码检查见[最新检验](../docs/testing/voice-api-checks.md)，接口和单 worker 边界见[交接说明](../docs/design/voice-api-handoff.md)。以下早期验证记录按其日期保留。

## 运行

最新结果：2026-11-12 已重建包含模型适配器、六声线、0.9 慢速及本轮语音边界修复的镜像，完成空密钥启动、源码一致、非 root、初始化与持久化验收，见[复查记录](../docs/testing/backend-review-checks.md)。此前 2026-11-11 的真实容器调用与 Windows 健康访问见[历史容器记录](../docs/testing/docker-checks.md)，不作为本轮新模型真实联调证据。

后续实调：同日已用上述镜像完成六种声线各一次慢速合成及六次文本上下文对比，源码摘要一致、非 root，测试容器已移除；见[独立交付验证](../docs/testing/g08-live-delivery-checks.md)。新合成模型的容器连通性已验证，主观听感及业务端到端仍待验收。

在仓库根目录使用 Docker Engine 与 Compose v2，依次执行：

```sh
docker compose -f infra/compose.yaml build
docker compose -f infra/compose.yaml run --rm backend python database/init_db.py
docker compose -f infra/compose.yaml up -d
curl http://127.0.0.1:8000/api/v1/health
```

初始化是显式的一次性操作，启动服务不会自动建库或导入数据。空模型环境变量允许启动；数据库未初始化时健康接口返回 503，初始化后预期返回 200。仅在需要开发用草稿种子时手动执行：

```sh
docker compose -f infra/compose.yaml run --rm backend python database/import_seed.py
docker compose -f infra/compose.yaml run --rm backend python database/init_db.py --check
```

种子不是生产内容，不自动导入生产数据库。SQLite 文件保存在 Compose 命名卷 `sqlite-data`，容器使用 UID/GID 10001。首次创建卷时由镜像目录继承写权限；外部已有卷需自行核对权限。单 worker 运行，关闭 Uvicorn access log；应用自己的请求元数据日志仍保留。

```sh
docker compose -f infra/compose.yaml logs --tail=100 backend
docker compose -f infra/compose.yaml stop
docker compose -f infra/compose.yaml down
```

`stop` 和不带 `-v` 的 `down` 均保留数据。不要使用 `down -v`，它会删除数据库卷；生产迁移、备份与恢复演练尚未覆盖。

2026-11-11 的声线与模型语速版本已重建并通过空密钥容器验收，见[声线语速验证](../docs/testing/speech-preferences-checks.md)。2026-11-12 的模型适配器与六声线镜像已重新通过空密钥验收，见复查记录。该轮新模型真实调用在 Windows 执行；下文三服务容器实调为旧 qwen3-tts-flash 的历史结果，不能据此宣称新模型已经在容器真实调用。

## 配置与边界

文本已选择 DeepSeek 官方服务（deepseek-flash），`MODEL_API_KEY` 仅用于文本；语音保留百炼北京地域，`DASHSCOPE_API_KEY` 仅用于语音。两者默认空、互不回退，通过运行环境注入，密钥不得提交仓库或写进镜像。对应密钥为空时不调用该供应商；文本与语音字段见 backend/.env.example。Windows 本机及最新容器均已通过真实短句联调，见[本机记录](../docs/testing/live-provider-checks.md)和[容器记录](../docs/testing/docker-checks.md)。构建上下文采用 Dockerfile 专用忽略文件，仅放行后端源码、联调命令、锁定依赖、数据库脚本及种子 JSON，排除环境文件、密钥、虚拟环境和 Git。

合成当前使用 qwen-audio-3.1-tts-flash，需设置包含本人北京业务空间标识的 TTS_API_URL；未填时只禁用合成。默认声线 wenhuaiqing_v3.1。配置迁移、可选声线和 SLOW/NORMAL/FAST 合成参数见[交接说明](../docs/design/speech-preferences-handoff.md)。Compose 显式空 TTS_MODEL / TTS_VOICE 保持禁用，不用默认值补回。

追踪 FR-13 / NFR-08 / AC-11：本初稿仅提供相关后端运行、配置注入与健康检查的部署基础；具体条款验收仍需结合需求基线、真实供应商联调和容器运行证据，不据此宣称这些条款完成。当前仅绑定本机 `127.0.0.1:8000`，不包含前端、反向代理、HTTPS、生产访问控制或可用性保证。

可选 MODEL_TIMEOUT_SECONDS（默认 20，最大 20 秒）与 MODEL_MAX_TOKENS（默认 512）也从运行环境注入。Compose 不自动读取 backend/.env；需要该文件时在每条命令的 `compose` 后添加 `--env-file backend/.env`。不要把包含真实密钥的配置展开输出粘贴到任务表或日志中。

## 可重复容器验收

在 Linux 或 WSL 的干净源码目录运行，无需宿主 Python：

```sh
sh infra/check-docker.sh
```

脚本清空本工程供应商配置、指定空环境文件，创建独立项目与随机回环端口；构建后验证缺库 503、显式初始化后 200、镜像源码一致、非 root、三项缺密钥错误、种子重复导入、重启持久化及 Docker healthy。它不调用付费服务，不读取 backend/.env。退出时移除本次容器和网络，保留输出中标明的测试数据卷与镜像。异常退出后的资源可按输出的项目名核对；不自动删除数据卷。

脚本的端口行为依据 [Compose run 官方说明](https://docs.docker.com/reference/cli/docker/compose/run/)；运行时变量优先级见 [Compose 环境文件说明](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)。Windows `/mnt/d` 的扩展属性限制仍按上文处理，本轮没有验证 Windows 原目录直接构建。

## 容器内真实联调

镜像包含现有两个显式联调命令。以下命令在仓库根目录执行，使用已有 backend/.env；会产生供应商调用。初始化与启动均使用同一项目配置，首次运行先执行 build。

```sh
docker compose --env-file backend/.env -f infra/compose.yaml build
docker compose --env-file backend/.env -f infra/compose.yaml run --rm backend python database/init_db.py
docker compose --env-file backend/.env -f infra/compose.yaml up -d --wait
docker compose --env-file backend/.env -f infra/compose.yaml exec backend python backend/smoke_model.py
docker compose --env-file backend/.env -f infra/compose.yaml exec backend python backend/smoke_voice.py tts /tmp/zhiya-check.wav --voice wenhuaiqing_v3.1 --speech-rate SLOW
docker compose --env-file backend/.env -f infra/compose.yaml cp backend:/tmp/zhiya-check.wav /absolute/path/outside-repo/zhiya-check.wav
```

文本与合成命令交互输入无个人信息的短句，合成输出文件必须尚不存在。复制出的音频是本地验收产物，应移至仓库外，不提交。识别可先把获授权的成人录音复制到容器临时目录：

```sh
docker compose --env-file backend/.env -f infra/compose.yaml cp /absolute/path/adult-16k.wav backend:/tmp/adult-16k.wav
docker compose --env-file backend/.env -f infra/compose.yaml exec backend python backend/smoke_voice.py asr /tmp/adult-16k.wav --format wav
```

内部识别 WAV 须为 16 kHz 单声道 16 位 PCM；合成默认 24 kHz，当前 asr 命令会先经转换器规范化，也支持 --format webm/ogg/mp4/mp3，详见[接入说明](../backend/bailian.md)。测试完移除测试容器可清除容器临时音频，外部原文件保留由本人管理。修改声线或密钥后使用 `up -d --force-recreate` 重新注入环境变量，仅 `restart` 不更新容器环境。健康接口只验证应用与数据库，不代表供应商可用；供应商调用仍用以上显式命令检查。
