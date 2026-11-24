# G15 本地 HTTPS 部署预演

受测版本为 [`main@e5f32cd`](https://github.com/MumLauds/202610-zhiya/commit/e5f32cd9d645c75e03814af55ba5aa531442c8a6)。这次预演检查两个问题：浏览器能否在部署后的同源 HTTPS 入口完成一次共读；服务重启、新浏览器重新登录后，家长能否读回同一份记录和小结。

## 实测结果

2026 年 11 月 19 日，Chrome 154.0.8037.98 在本机 HTTPS 完成整条流程。最终一轮从新的独立 SQLite 卷开始，浏览器用例退出码为 0。[浏览器结果](browser-result.json)

| 操作 | 观察到的结果 |
| --- | --- |
| 登录与选档 | 从页面登录后创建两份合成孩子档案，主动从第二份切换到「预演小芽」。创建会话的请求使用选中的档案 `1`。会话 Cookie 带 `Secure` 和 `HttpOnly`。 |
| 开读与刷新 | 页面创建会话 `1` 并开始阅读《纸船停在哪里》。刷新后仍显示第 1 页和对应正文，API-013 返回 `IN_PROGRESS`、修订号 `1`、已读页 `[1]`。 |
| 录音确认 | 点击开始录音、停止并识别后，上传返回 200。页面将「小船停在哪里」显示为待确认转写；改成「纸船为什么停在绿叶旁？」后点击确认，在弹窗看到修改后的文字。该文字仅供本次确认。 |
| 结束与历史回看 | 页面完成阅读后，成长记录显示「阅读记录 #1 · 浏览 1 页 · 已完成」。历史卡没有详情入口，测试通过开读时记录的 `#reader/1` 回到原阅读页，界面显示 `READY` 小结「本次共读阅读了第 1 页。」。 |
| 重启与读回 | 关闭第一个 Chrome 后执行 Compose 整组重启。恢复健康后，新 Chrome 从空 Cookie 开始登录，在历史页及原阅读页读回相同记录。会话仍为 `FINISHED`、修订号 `2`，一条 `STARTED` 事件、小结正文及状态均保持一致。 |

本次运行的 Docker 镜像索引摘要如下，完整容器、数据卷和重启快照见[部署实测数据](deployment-result.json)。

| 镜像 | SHA-256 |
| --- | --- |
| 前端生产文件与 Caddy | `395a083a33eda10662c6e42c8ca814ce3a4b8bd68d977e6ff869080375b0b940` |
| 后端 FastAPI 与 FFmpeg | `7e52987d8476043ac3e7270326b8579910173c444712d1daa0d8cd131e0b1bfc` |

Docker Engine 29.9.2、Compose 5.6.1 记录了 `edge`、`backend` 和 `provider` 各自新的启动时间。重启前和新浏览器读回后，SQLite 都有 1 条 `FINISHED / USER_FINISH` 会话、1 条互动事件、2 条执行记录和 1 份 `READY` 小结，结束时间与小结更新时间逐毫秒相同。本地 ASR 收到 1 次经 FFmpeg 转换的 16 kHz、16 位、单声道 WAV，共 26,880 帧；小结服务收到 1 次针对第 1 页的请求。历史模拟服务仅记录成功请求，重启及再次读回期间未记录到新增的成功调用。

结束后已关闭预演容器并移除其网络，Windows 回环端口 `18443` 拒绝连接。隔离测试卷保留在本机，最终取证卷为 `g15-local-e5f32cd-20261119-verified_sqlite-data`。本轮未部署公网。

## 构建与运行边界

- 前端使用该提交的源码和锁文件，核对 `package.json` 和 `package-lock.json` 的 SHA-256 后复用本机对应的依赖安装，以 Node.js 24.22.3、Vite 7.3.8 重新构建 `frontend/dist`。静态文件封装进 Caddy 镜像。后端使用同一 Git 提交的归档和仓库的 `infra/Dockerfile` 构建，单 worker 运行 FastAPI。
- 浏览器访问 `https://127.0.0.1:18443`，网页和 `/api/v1` 走同一个 Caddy 入口。浏览器仅为本次临时证书公钥放行；本次证书不加入宿主系统信任库。后端和模拟供应商只接入隔离的容器网络，SQLite 在独立命名卷中。
- 测试账号、两个孩子档案和单页绘本《纸船停在哪里》均为合成数据。Chrome 的虚拟麦克风提供音源，浏览器的 `MediaRecorder`、上传和后端 FFmpeg 解码均走实际实现。服务端使用测试证书验证本地供应商 HTTPS：ASR 校验 WAV 格式后返回固定转写，小结模拟检查已读事实与绘本正文后返回 `suggestionId=NONE`。本轮没有 TTS 调用。

首轮 TLS 检查曾因按 IP 访问时没有 DNS SNI 而握手失败。为本地 Caddy 设置 `default_sni 127.0.0.1` 并重建前端镜像后，Windows 客户端用临时公开证书通过了 IP 地址与证书校验，协商 TLS 1.3。重启前后各经 HTTPS 逐个读回 24 个静态文件，每次共 4,678,012 字节，全部与生产构建逐字节一致。按相对路径排序，以 `路径<TAB>文件SHA-256<LF>` 的 UTF-8 字节计算的清单摘要为 `db121e16ea43f433b40f60c224938ccb54a562f765ef33f5ea3d51d96e0719f4`。

浏览器脚本在调试中修正了两处取证方式：按页面实际的 `status` 角色识别转写确认，再等阅读页的小结可见后才读取详情，以免连续导航取消上一次响应。最终记录使用新建测试卷完整复跑，之前两次调试的 ASR 或小结调用未计入最终结果。

## 命令行复跑

需要 Docker Compose、Git、OpenSSL、curl、flock（util-linux）、Python 3、Node.js、npm 和安装了 Python Playwright 的 Chrome。以下命令从包含本目录的仓库根目录运行。将首轮状态目录和项目名留给同一轮的后续命令。两个镜像按本轮的项目名打标签，均从 `G15_COMMIT` 的源码构建。

下方提交用于复现本轮归档。若改测 `02ba8d8` 或采用同一小结契约的后续版本，`verify_browser.py` 对空活动列表的历史断言会失败。有已读正文的新 `READY` 小结应包含活动名称、材料与步骤，复测还需核对重启前后这些字段一致。[小结契约](../../../design/api-contract.md)

```sh
export G15_COMMIT=e5f32cd9d645c75e03814af55ba5aa531442c8a6
export G15_PROJECT="zhiya-g15-$(date +%s)"
export G15_STATE="$(mktemp -d)"
g15=docs/testing/g15/local-https
python3 "$g15/prepare_frontend.py" "$G15_COMMIT" "$G15_STATE/frontend-build" && \
  sh "$g15/stack.sh" build && sh "$g15/stack.sh" up && sh "$g15/stack.sh" inspect
```

前端脚本从 Git 归档解出指定提交，在独立目录安装锁定依赖并运行 Vite。离线构建时追加 `--offline-cache /path/to/npm-cache`，路径可用 `npm config get cache` 查看。先在有网络的环境从受测提交的 `frontend` 执行一次 `npm ci --include=dev --cache /path/to/npm-cache`，为实际构建平台备齐包。脚本用 `npm ci --include=dev --offline --ignore-scripts` 从缓存安装，按锁文件校验包内容，并关闭依赖安装脚本的下载回退。缺包会使本次构建失败。旧参数 `--reuse-deps /path/to/frontend` 仍检查该目录的项目清单，并使用 npm 缓存执行离线安装。只有构建成功才写入提交号及逐文件 SHA-256，供 Docker 打包时校验。

Windows/WSL 分开运行时，在 Windows 用相同的 Python 命令准备前端，再在 WSL 设置 `G15_FRONTEND_BUILD` 指向该新目录的 WSL 路径。Docker 引擎中没有 Git 时，在 Windows 以 `git archive -o <new.tar> <完整受测提交> -- backend/app backend/requirements.txt backend/smoke_model.py backend/smoke_voice.py database data/seed knowledge-base_v2.5/图片 infra/Dockerfile` 创建源码包，将 tar 的 WSL 路径设为 `G15_SOURCE_TAR`。前端准备成功后，在部署终端执行上面的 `build && up && inspect`。

`build` 输出本次证书的公钥摘要。在另一个终端从仓库根目录运行以下命令，将 `PIN` 换成本次输出，结果写到一个新文件。

```sh
python docs/testing/g15/local-https/verify_browser.py --spki PIN --output g15-browser-result.json
```

浏览器脚本输出 `READY_FOR_RESTART` 后，在部署终端执行下列命令。`restart` 先采集数据库计数、已完成的会话、供应商日志及容器启动时间。健康检查返回 200 后，到浏览器终端按回车，在新 Chrome 中重新登录并读回历史。生成基线时逐个检查三个服务：旧实例须已在重启前采集时启动，新实例须在该次采集之后、读回后的采集结束前启动。

```sh
sh "$g15/stack.sh" restart && sh "$g15/stack.sh" inspect
```

等浏览器进程成功退出并写出 `g15-browser-result.json`，再采集读回后的快照并停止服务。将浏览器结果路径换成部署终端可读的实际路径。

```sh
sh "$g15/stack.sh" baseline ./g15-browser-result.json && sh "$g15/stack.sh" down
export G15_BASELINE="$G15_STATE/deployment-result.json"
```

`baseline` 比较两次采集到的阅读会话、事件、小结、执行记录和供应商请求，并检查新登录使库内登录会话数增加 1。成功后写出本轮独有的卷名、镜像引用及 ID、会话和毫秒时间戳。基线发布后，同一状态目录中的 `restart` 和 `baseline` 会在采集前退出，保留已归档的快照。保留该文件、数据卷、本轮 TLS 目录和镜像，可接着执行[备份恢复预演](../local-restore/README.md)。

每个新项目生成独立 TLS 证书。汇总时，用部署目录的证书核验本次传给 Chrome 的公钥摘要和 Chrome 实际访问的 HTTPS 证书指纹。新 Chrome 的登录响应带有服务器生成的 HTTP `Date`，汇总使用它核对登录发生在本轮后端重启与数据库终态采集之间。浏览器自己的 UTC 时间仅用于记录。

脚本还逐项核对浏览器与 SQLite 中的会话、已读页、小结及每条事件的编号、类型和页码。仓库中早期归档的浏览器报告与数据库快照保留原样；重新生成首轮基线时，使用本轮新采集的证书、登录响应和事件页码。
