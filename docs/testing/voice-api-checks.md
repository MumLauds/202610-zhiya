# G08 公开语音与录音转换检验

版本：v0.3；日期：2026-11-12；负责人：林鑫。初次验证基础提交 8c4172c，分支 feat/backend-model-bootstrap；后续提交和修改验证分节记录。对应 DCL-020/021、FR-08/12/13、NFR-04/05/06/08、AC-07/10/11（局部）。本记录不代替完整 G08 验收。

自动语音识别（Automatic Speech Recognition，ASR）、语音合成（Text-to-Speech，TTS）、脉冲编码调制（Pulse-Code Modulation，PCM）、波形音频文件格式（Waveform Audio File Format，WAV）。接口、限制、错误码和实名对接步骤统一见[交接文档](../design/voice-api-handoff.md)。

## 本轮验证范围

| 用例 | 内容与证据 |
| --- | --- |
| TC-VOICE-API-001 | API-022/023/024 音频路由已注册；默认业务认证未接入时全部返回 503，不进入付费适配；真实运行容器也验证此默认行为 |
| TC-VOICE-API-002 | 临时 SQLite + 测试专用身份：家长归属、儿童确切授权范围/到期/撤销、已审核私有版本、RETIRED 既有会话、WITHDRAWN 拒绝 |
| TC-VOICE-API-003 | multipart/JSON 严格字段校验、来源互斥、拒绝任意客户端正文、过大上传、错误格式、重复字段、旧页码/revision |
| TC-VOICE-API-004 | 转码/供应商前后重验；转换中进度变化不识别，合成中撤回/凭证失效不缓存，旧音频读取被拒绝 |
| TC-VOICE-API-005 | 32 MiB/300 秒媒体预算、容量淘汰、过期、同会话请求冲突、错误释放执行占位；取消处理协程的隔离测试 |
| TC-AUDIO-001 | 合规 WAV 快速路径，空/截断/超大/超长拒绝；输入不覆盖；CLI 离线转换不加载 .env、不调用网关 |
| TC-AUDIO-002 | 使用真实 Python 子进程模拟 FFmpeg：超时、取消、输出超限和畸形输出，均检查进程已退出；无原始内容回显 |
| TC-AUDIO-003 | Linux 容器内真实 FFmpeg：WebM/Opus、Ogg/Opus、MP3、分片 MP4/AAC、float32 WAV，规范化为 16 kHz/单声道/16 位 PCM，并检查时长 |
| TC-DOCKER-008 | 最终镜像源码摘要匹配、FFmpeg 可用、空密钥、默认认证拒绝、缺库/初始化、非 root、重复种子导入、重启持久化、依赖检查 |

测试使用虚构身份、临时数据库及模拟供应商；真实 FFmpeg 转码使用合成正弦波。没有读取个人 .env，没有新增付费模型/识别/合成调用。历史六声线的真实听感反馈独立保留在[先前验证](g08-live-delivery-checks.md)。

## 复核发现与修正

1. TimeoutError 属于 OSError，原异常捕获顺序将转换超时误映射为组件不可用；调整捕获顺序，子进程超时测试确认 504 AUDIO_CONVERSION_TIMEOUT。
2. Python wave 不识别合法 float32 WAV，原快路径直接拒绝；改为无法由快速检查识别的 WAV 交受限 FFmpeg 解码。规范 PCM 的空、截断和超长校验仍保留，增加真实 float32 转换用例。
3. 合成前及缓存命中时补充实时认证/快照复核；对外字段保持 camelCase，新增 Python Schema 属性按仓库规范使用 snake_case 和显式别名。

完成后由三个只读子智能体分别检查语音权限、转换清理及交接文档，再运行最终代码回归。文档复核中指出的 API-024 撤回疑点已对照实现与测试核实：音频分支确实阻断 WITHDRAWN，图片分支尚未实现；reliable=true 是契约示例，不是可伪造置信度的依据。

## DCL-020 最终运行记录（历史镜像）

- Windows：93 passed、5 skipped、191 subtests passed。五项跳过为本机未安装 FFmpeg 的真实格式转换，交由 Linux 镜像执行；pip check 通过。
- Linux 最终镜像：98 passed、191 subtests passed，无跳过；五种格式的真实 FFmpeg 转换全部通过。测试容器运行时使用 --network none；FFmpeg 版本为 7.1.5-0+deb13u1。
- 镜像：zhiya-check-1794470014-495-backend；ID：sha256:2c2881b04cc3465b88b4f0502c1ab4871d42cf9cc839cf130d6ef4d347961143。最终源码摘要与归档一致，基础容器/网络已移除，隔离测试卷和镜像保留。
- 唯一测试警告为 Starlette TestClient 的 httpx 弃用提示，不影响本次结果；本轮不为消除警告迁移框架依赖。

本机命令（仓库根目录，不调用供应商）：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m pip check
```

可重复基础容器检查（Linux 或 WSL 原生源码目录）：`sh infra/check-docker.sh`。本轮在现有 D 盘 Debian WSL Docker 中使用白名单源码归档构建，未将 .env、虚拟环境或业务数据库打入镜像。源码摘要、运行器和详细日志保存在仓库外的 `D:/软工/tmp/g08-voice-api-20261112/`；历史试听交付 ZIP 未重新打包，不能将旧 ZIP 当作包含本次接口实现。

真实 float32 WAV 用例最初使用 FFmpeg 管道输出，长度头尚未回填而被完整性检查拒绝；改为写入完整测试文件后再送入转换器，最终通过。未放宽生产代码去接受未知损坏或占位头上传。git diff --check 通过；当前文档相对链接已核对。

飞书 G08 已新增独立子记录“录音转换与公开语音接口本地实现及复核”（reczz28YgtX1FCxc），状态为已完成；备注明确此为局部交付、真实认证/图片/偏好/浏览器端到端待协作。未覆盖旧记录或将父任务标记为完成。

## 尚未验证及协作项

真实 Cookie/CSRF 登录接入、真实浏览器录音与断链、完整前后端流程、图片媒体、档案偏好保存/读取、适龄内容安全及生产部署仍待相关成员完成。取消测试当前为处理协程与子进程层验证；不能声称已通过真实浏览器断网验收。默认声线及 NORMAL 由服务端配置，六声线和三档内部能力不等于家长设置页已接通。当前缓存是单 worker 临时资源，不承诺跨进程、重启或过期后的付费幂等。

## 草稿 PR 提交前核对

2026-11-12：用户随后授权提交草稿 PR。核对远端 main 仍为 8c4172c、PR #27 仍为 aa13025；53 个 G08 相关文件纳入提交，个人 .env、凭据、数据库及试听音频排除。后端源码/测试/依赖与上述最终验证归档一致，本轮仅整理说明和配置注释。完整暂存检查发现 agent.py 原始 Schema 快照已有三处尾随空白，为保持与 #27 原文件一致予以保留；排除此文件后的暂存差异检查通过。之前 git diff --check 为当时已跟踪文件的检查，不包含尚未纳入 Git 的 Schema。

补充 PR #28 核对：最新 head 9158c8cf91f17b3e57ad44e07fc0f81df5dc6fa5，仍为文档 Draft。解析 JSON 确认仅新增 minimumBusinessReview，既有正式定义不变；阅读 15 个候选接口和 12 个配套场景，并与本轮媒体、错误/重试、恢复和偏好边界对照。对 5c6f080 与该 head 执行 git merge-tree --write-tree，无文本冲突，未切换、修改工作树或合并该 PR。差异见交接文档；这不是 G04/G08 运行联调验收，本轮仅追加文档，不改变已验证代码。

## DCL-021 错误码调整复核

2026-11-12；基于 cddbd1a 的修正，随 PR #29 更新交付，未合并。只调整语音并发码及资源不可见/业务数据库不可用的公共命名，未修改状态码、授权检查、重试行为或模型网关。

| 用例 | 内容与证据 |
| --- | --- |
| TC-VOICE-API-006 | ASR、TTS 分别同时发起两个同会话请求，首请求停留在模拟供应商内时，第二请求返回 409 VOICE_REQUEST_IN_PROGRESS、retryable=false、无 Retry-After；首请求成功，供应商总共仅调用一次，占位释放。全局满额仍为 429 VOICE_BUSY，未调用供应商 |
| TC-VOICE-API-007 | API-022 越权会话、API-023 不存在事件、API-024 无效资源及供应商调用后身份改变均返回 404 RESOURCE_NOT_FOUND；三条路由读库失败返回 503 SERVICE_UNAVAILABLE、retryable=false、无 Retry-After，不额外调用供应商 |

新增六个测试实例先在旧实现运行，5 failed、1 passed：失败均为预期的新旧错误码差异，全局 VOICE_BUSY 保持通过。修改后、安装 FFmpeg 前，全量执行 `.\.venv\Scripts\python.exe -X utf8 -m pytest backend/tests -q`：**99 passed、5 skipped、191 subtests passed**，保留既有 Starlette/httpx 弃用警告。跳过的五项当时为本机无 FFmpeg 的真实格式转换，随后按用户要求安装并复测，最终结果见下文。本轮没有重建或运行容器，上节历史镜像结果不能作为新源码的容器验证。

首轮全量运行有一项既有 `test_process_terminated_on_timeout_cancel_or_output_limit[timeout]` 失败：150 毫秒期限到达时测试的 processes 列表仍为空，其余 98 项通过。未修改该测试或转码实现，单独复跑为 1 passed，随后全量得到上述 99 passed；记录这一时序敏感现象，不隐去首次失败。

PR #27 的模型请求/结果 Schema 和网关代码不变，既有成功映射、API_ERROR/TIMEOUT 映射、单次调用及空输入不调用供应商测试随全量通过。新增的 INITIAL 指令样例和 API_ERROR 工作流分工见[调用说明](../../backend/README.md#对接-pr-27-的-agent-模型接口)，是待负责人确认的接入建议，不是已实现工作流降级。没有读取个人 .env 或新增付费调用；真实认证、图片媒体、档案偏好与端到端仍待协作。

**用户要求安装 FFmpeg 后的最终复测：** Windows 全量 **104 passed、191 subtests passed、0 skipped**，耗时 18.55 秒；唯一警告仍为既有 Starlette/httpx 弃用提示。WebM/Opus、Ogg/Opus、MP3、分片 MP4/AAC、float32 WAV 五项真实转换均通过，输出验证为 16 kHz、单声道、16 位 PCM WAV，并检查时长；输入为测试生成的正弦波。

- 安装来源：[FFmpeg 官方下载页列出的 Windows 构建](https://ffmpeg.org/download.html#build-windows) → [Gyan release essentials](https://www.gyan.dev/ffmpeg/builds/)，实际版本 9.0.2；ZIP 的 SHA256 与发布方校验文件一致：`60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba`。
- 本机安装目录：`D:/软工/tools/ffmpeg/ffmpeg-9.0.2-essentials_build/bin`；已追加到用户 86134 的 PATH，二进制未加入仓库。已打开的终端可用下面命令刷新本次进程 PATH。
- 安装账号首次复跑因无法访问原测试账号创建的 pytest 临时目录而产生 46 个 setup error（其余 58 passed），未修改目录权限或业务代码；改回原测试环境并显式添加 FFmpeg 到进程 PATH 后，得到上述最终全量通过结果。

```powershell
$env:Path = 'D:\软工\tools\ffmpeg\ffmpeg-9.0.2-essentials_build\bin;' + $env:Path
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/tests -q --tb=short
```

源码差异空白检查及本轮说明文档的相对文件链接检查通过。安装和测试没有修改个人 .env，也没有调用真实模型、识别或合成服务。

## P3 转码超时测试稳定性修正

2026-11-12，基于 768d0e9；处理 PR #29 的非阻断反馈，对应 TC-AUDIO-002。旧测试将整个转换期限缩为 150 毫秒，慢启动时尚未获得进程句柄就已超时，导致 processes 为空而断言失败。测试现使用真实 asyncio 超时上下文，先暂不设期限，待真实子进程句柄取得后通过 reschedule 在下一轮事件循环触发超时；继续验证 504 AUDIO_CONVERSION_TIMEOUT 和进程已退出。该用例验证已启动进程的超时回收，不再以启动耗时作为通过条件。

仅修改测试及本记录，生产转换代码、10 秒总期限、取消与回收逻辑均未改变。旧失败记录保留在上文，不改写为首次通过。

- 人为在子进程创建前额外等待 300 毫秒：旧测试稳定复现 processes 为空的失败；修正后在相同条件下通过，随后以 20 个独立 Python 测试进程连续复跑，20/20 通过。
- 使用已安装的 Windows FFmpeg 9.0.2 执行全量：104 passed、191 subtests passed、0 skipped（22.53 秒）；包含五格式真实转换、取消、输出超限及畸形输出，保留一条既有 Starlette/httpx 弃用提示。没有付费调用或重建容器。
- 常规复核命令：`.\.venv\Scripts\python.exe -X utf8 -m pytest backend/tests/test_audio_conversion.py -q`；全量命令同上。慢启动复现运行器保存在仓库外 `D:/软工/tmp/g08-voice-api-20261112/check_timeout_stability.py`，只用于注入启动延迟，不属于运行依赖。
