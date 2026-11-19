# G15 本地备份恢复预演

本轮沿用[上次 HTTPS 部署预演](../local-https/README.md)的前后端镜像，应用版本为 [`e5f32cd`](https://github.com/MumLauds/202610-zhiya/commit/e5f32cd9d645c75e03814af55ba5aa531442c8a6)。两组预演分别验证原记录可恢复，以及家长能在恢复后的设备上通过正常入口读到图文。

| 场景 | 数据来源 | 核对方式 |
| --- | --- | --- |
| A：上次的会话 #1 | 上次保留的 `g15-local-e5f32cd-20261119-verified_sqlite-data`。其测试家庭的随机密码随上次浏览器脚本退出而释放。 | 只读备份原卷，恢复到新卷，比较会话、事件、小结和持久化时间戳，并与[上次归档的原始结果](../local-https/deployment-result.json)对照。 |
| B：独立账号及内页图 | 单独的数据卷、合成的家长和孩子档案、一页带纸船插画的测试绘本。测试进程暂存账号和密码，跨恢复过程使用。 | 源环境完成阅读。恢复后用全新 Chrome 重新登录，在成长记录和原阅读地址核对会话、小结及已读页；核对可见插图的 PNG 文件和解码后的像素。 |

版本化内页图需要随库恢复两个目录。`.zhiya.sqlite3.media_pins` 包含版本清单与固定的图片字节，`.zhiya.sqlite3.media_pins.bindings` 保存版本绑定校验文件。合成原图只用于源环境首次建立快照。SQLite 备份保存在本机专用卷，仓库归档测试脚本和脱敏结果。

## 2026-11-19 实测记录

A 组只读挂载上次的原卷，以 SQLite Backup API 创建备份，然后从备份卷恢复到空卷。恢复卷启动后，会话 #1 仍属于档案 1、绘本版本 11，状态为 `FINISHED / USER_FINISH`、修订号为 2。已读页为 `[1]`，库内有 1 条 `STARTED` 事件、2 条请求执行记录和 1 份 `READY` 小结「本次共读阅读了第 1 页。」。结束时间 `1795088215445` 和小结更新时间 `1795088215552` 与上次归档逐毫秒相同。数据库完整性检查通过，外键检查返回 0 行。

| A 组校验对象 | SHA-256 |
| --- | --- |
| 原卷的 SQLite 文件 | `d5408a1973497ccb9542e2dbc137c5195b92d7ffa2ca311960574fde9f86f19a` |
| 备份卷的 SQLite 文件 | `202716c54a9a3d21fec7015fd0a58445c17920cf3f7c9ae76c00a90c7911a89b` |
| 备份卷的 `manifest.json`（列出数据库及媒体文件的 SHA-256） | `ca0d678a6e4b3f835eb929198a9777e5b461e07a61b8abfcbe2dc3799507fcbc` |

SQLite Backup API 生成独立的数据库文件。恢复时逐文件比对的是备份卷与恢复卷启动前的字节。源卷是否发生变化则用备份前后的原卷摘要核对。

本轮保留的 Docker 卷如下。A、B 各自持有独立的数据库，会话编号只在各自的库内标识一条记录。

| 数据 | A 组 | B 组 |
| --- | --- | --- |
| 源卷 | `g15-local-e5f32cd-20261119-verified_sqlite-data` | `g15-rst-20261119-1-b-source-data` |
| 备份卷 | `g15-rst-20261119-1-a-backup` | `g15-rst-20261119-1-b-backup` |
| 新建恢复卷 | `g15-rst-20261119-1-a-restored-data` | `g15-rst-20261119-1-b-restored-data` |

B 组备份的 SQLite 文件 SHA-256 为 `54faa2702b737db9d763a9037eaaed098fe1d243389fad0b11b8e998e2053f7b`，清单文件为 `141f55ffcd1183e81cd2afea235f193ed8f4449d3fa4f1b0d332cce2b4b0cd84`。两组备份卷均再次只读挂载，用 `sha256sum` 独立复算了数据库和清单的摘要。

B 组恢复后，用空 Cookie 的 Chrome 154.0.8037.98 访问内页图返回 401。从家长登录页输入原测试账号后返回 201，成长记录显示「阅读记录 #1 · 浏览 1 页 · 已完成」。使用开读时保存的 `#reader/1` 打开阅读页，会话详情、已读页、事件和小结与恢复前一致。[浏览器实测数据](browser-result.json)

浏览器实际显示并解码了 128 × 80 的纸船插图，图片接口返回 200。恢复卷中没有用于生成快照的原图，默认绘本目录也没有这张图。读回的 PNG 为 30,868 字节，SHA-256 为 `abecc2668b8b0f9d544f44990c6d95948a102f1140e1f8bc9e1a875c2f3bf2bc`。浏览器解码的 RGBA 像素 SHA-256 为 `b2b5e749575b1fe44325218ac508ac7c466eafa3761ad15b34a89efb38562409`，两项均与恢复前及合成图夹具一致。

前端生产镜像、Caddy、后端、SQLite、登录和媒体读取均使用上次部署的实际实现。两个家庭使用合成档案与测试绘本，B 组插图在本地生成。历史模拟服务只记录成功请求，源环境记录了 1 次成功的小结调用，恢复后回看时记录 0 次。本轮未调用真实模型、ASR 或 TTS。[镜像、卷及数据库实测数据](restore-result.json)

演练结束后再次只读核对 A 组原卷，全部文件摘要与业务快照均与开始时相同。两组的源卷、备份卷和恢复卷共六个均已保留。三个 Compose 项目的容器和网络已关闭，Windows 访问本机端口 18443 返回连接被拒绝。整个过程仅使用本机 HTTPS。

## 命令行复跑

先按[本地 HTTPS 预演](../local-https/README.md)完成一次浏览器读回、采集最终基线并关闭服务。接着使用同一 Docker 引擎、该轮保留的 SQLite 卷和两个本地镜像。恢复命令和汇总脚本都从 `G15_BASELINE` 读取源卷、镜像及会话基线，每个镜像引用须与基线记录的实际镜像 ID 一致。复核 11 月 19 日保留的原卷时，可以将 `G15_BASELINE` 指向[历史部署基线](../local-https/deployment-result.json)；未设置此变量时也使用该文件。

在首轮的 Linux/WSL 部署终端从仓库根目录继续。环境需有 Python 3、Docker Compose、OpenSSL 和 curl。`G15_TEST_TLS` 指向含 `cert.pem` 和 `key.pem` 的本机测试目录，证书须在有效期内并可校验 `127.0.0.1` 和 `supplier`。如使用历史卷，可在新的私有目录按[部署脚本](../local-https/stack.sh)中的 OpenSSL 参数生成证书。密钥留在测试机上。

```sh
umask 077
export G15_RESTORE_RUN="g15-restore-$(date +%s)"
export G15_EVIDENCE="$(mktemp -d)"
export G15_BASELINE="$G15_STATE/deployment-result.json"
export G15_TEST_TLS="$G15_STATE/tls"
ops=docs/testing/g15/local-restore/restore.sh
```

若 Docker 宿主没有 Python 3，额外设置 `G15_BASELINE_READER` 为首轮构建的后端镜像引用或 ID。脚本会用该本地镜像在无网络容器内读取基线。最终汇总也可以用 Windows Python 运行，传入 Windows 可访问的 `--baseline`、`--browser` 和证据、结果文件路径。

**A 组。** 原卷须已停写。`backup-a` 仅以只读方式挂载它。`restore-a` 验证备份中每个文件的 SHA-256，再恢复到其他卷。服务只从恢复卷启动。所有比对输出均保存在 `G15_EVIDENCE`。

```sh
sh "$ops" backup-a && sh "$ops" restore-a && sh "$ops" start-a
sh "$ops" probe-a && sh "$ops" down-a && sh "$ops" original
```

汇总时会将 `a-backup.json` 的源记录、`a-restore.json` 的恢复记录、`probe-a.json` 的启动后记录与本次基线中的 `afterBrowserReadback.database` 比对。`original-after.json` 用于核对原卷的文件和业务数据。

**B 组。** 在同一部署终端运行 `sh "$ops" seed-b && sh "$ops" start-b-source`。另开浏览器终端，从仓库根目录运行下列命令。`PIN` 取站点公钥摘要（`openssl x509 -in "$G15_TEST_TLS/cert.pem" -pubkey -noout | openssl pkey -pubin -outform DER | openssl dgst -sha256 -binary | openssl base64 -A`）。两个终端需访问同一份 `G15_EVIDENCE` 目录。Windows 上运行浏览器脚本时，将下方两个路径替换为其可访问的对应 Windows 路径。浏览器读取夹具声明的 PNG 及 RGBA 摘要，并从同目录读取恢复服务启动后记录的目标卷名。

```sh
python -B docs/testing/g15/local-restore/verify_restored_browser.py --spki PIN --image-fixture "$G15_EVIDENCE/b-image.json" --output "$G15_EVIDENCE/browser-result.json"
```

等浏览器打印 `READY_FOR_RESTORE`，保持脚本进程运行。在部署终端记录源库、关闭源服务、备份并从新卷启动。如果 Docker 运行在 WSL 中，浏览器的两个阶段都需保持一个 WSL 终端运行，以便 Windows 可以访问回环端口。健康检查通过后，回到浏览器终端按回车，脚本会启动全新 Chrome 再登录。

```sh
sh "$ops" probe-b-source && sh "$ops" stop-b-source
sh "$ops" backup-b && sh "$ops" restore-b && sh "$ops" start-b
```

浏览器用例成功后，在部署终端停止服务并生成结果。停机后将再次只读检查 B 组的源卷和恢复卷。汇总要求源卷保持一次家长登录，恢复卷恰好多出本次登录，且读回前后的阅读、事件、小结数量一致。浏览器取证时间须落在该恢复实例的运行期间。源环境应只有一次成功的小结请求，恢复后任何供应商请求（包括失败请求）都会使汇总失败。应用数据和备份卷保留在本机 Docker 引擎。

```sh
sh "$ops" probe-b && sh "$ops" down-b && sh "$ops" original
python3 docs/testing/g15/local-restore/collect_restore.py "$G15_EVIDENCE" "$G15_RESTORE_RUN" "$G15_EVIDENCE/restore-result.json" --browser "$G15_EVIDENCE/browser-result.json"
```

审查修复后从新项目串跑两轮的[脱敏实测结果](../rerun-result.json)记录了首轮基线、第二轮实际使用的卷、重新登录前后的计数及内页图摘要。

在有 Git 和 Python Playwright 的 Linux 环境中，可从仓库根目录单独运行 9 项回归测试，覆盖构建失败、停写查询失败、错误卷、重复供应商请求和错误像素。

```sh
python3 -B -m unittest discover -s docs/testing/g15/local-https && \
  python3 -B -m unittest discover -s docs/testing/g15/local-restore
```
