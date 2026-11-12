# G08 容器验收记录

2026-11-12 最新空密钥镜像验收见[工作区复查记录](backend-review-checks.md)，覆盖模型适配器、六声线及语音格式修复。以下 2026-11-11 结果保留为历史证据。

日期：2026-11-11；基础版本：8c4172c，本地未提交工作区；负责人：林鑫 / 后端2；执行：Codex。范围为 G08 独立模型与语音适配、部署基础；未提交合并请求，不代表生产上线或完整业务验收。

| 缩写 | 中文名称（英文全称） |
| --- | --- |
| API / HTTP / HTTPS | 应用程序编程接口（Application Programming Interface）/ 超文本传输协议（Hypertext Transfer Protocol）/ 安全超文本传输协议（Hypertext Transfer Protocol Secure） |
| ASR / TTS | 自动语音识别（Automatic Speech Recognition）/ 语音合成（Text-to-Speech） |
| WAV / PCM | 波形音频文件格式（Waveform Audio File Format）/ 脉冲编码调制（Pulse-Code Modulation） |
| WSL / UID | 适用于 Linux 的 Windows 子系统（Windows Subsystem for Linux）/ 用户标识（User Identifier） |
| FR / NFR / AC / TC | 功能需求（Functional Requirement）/ 非功能需求（Non-Functional Requirement）/ 验收准则（Acceptance Criteria）/ 测试用例（Test Case） |
| JSON / URL | JavaScript 对象表示法（JavaScript Object Notation）/ 统一资源定位符（Uniform Resource Locator） |
| MP3 | MPEG-1 音频第三层编码（Moving Picture Experts Group-1 Audio Layer III） |

最新声线与模型语速版本的容器重建及本机真实合成结果另见[声线语速检查](speech-preferences-checks.md)；本页保留前一阶段的原始验收证据。

## 环境与修改

使用 D 盘已有 Debian WSL 2，Docker Engine 29.9.2、Compose 5.6.1；Python 3.12-slim 容器。未安装新的 Docker Desktop 或宿主 Python。Windows 源码经 27 个明确白名单文件打包至 WSL 原生目录 `/root/zhiya-docker-latest-20261111`；不含 .env、数据库、录音或虚拟环境。保留既有 `/mnt/d` 构建限制，未宣称 Windows 原目录直接构建通过。

镜像新增现有 smoke_model.py、smoke_voice.py 两个命令，供部署人员显式联调；不新增公开路由或第三方依赖。新增 [check-docker.sh](../../infra/check-docker.sh) 将容器验收留在仓库，可重复执行，不依赖宿主 Python；Git 属性限定该目录 shell 脚本使用 Linux 换行，避免 Windows 检出后无法运行。镜像内所有后端源码与白名单源文件逐一核对摘要一致，包含 DCL-011 的 WAV 修复。

## 验收结果

| 用例 | 需求关联 | 实际结果 |
| --- | --- | --- |
| TC-DOCKER-001 | FR-13、NFR-08（局部） | 最新镜像构建、源码摘要一致、容器以 UID 10001 运行，通过 |
| TC-DOCKER-002 | API-032、NFR-06 | 缺库返回 503 且不创建文件；显式初始化后返回 200；响应追踪编号与响应头一致，通过 |
| TC-DOCKER-003 | FR-08/12、NFR-05/06 | 空密钥启动；文本、合成命令及识别内部入口分别返回 NOT_CONFIGURED；合成不留下输出文件，通过 |
| TC-DOCKER-004 | FR-13、NFR-08（局部） | 15 张表，种子导入两次不重复；重启后数据保留，Docker healthy，通过 |
| TC-DOCKER-005 | FR-08/12、NFR-03（局部） | 运行环境注入用户配置，容器内真实文本、合成及识别全部成功，见下表 |
| TC-DOCKER-006 | API-032、FR-13（局部） | Windows PowerShell 访问 127.0.0.1:8000/api/v1/health，200 且追踪编号一致，通过 |
| TC-DOCKER-007 | NFR-05、AC-10（局部） | 应用目录无环境文件；镜像配置无供应商密钥；服务日志不含密钥、签名链接或本次样本文本，通过 |

空密钥项目：`zhiya-check-1794406023-504`，随机回环端口 32768。数据库最终为 21 本书、21 个版本、110 页、20 条知识。容器内依赖检查通过；第一次检查仅出现非 root 的 pip 缓存目录提示，验收命令已显式禁用缓存，不影响运行。

真实调用项目：`zhiya-container-live-20261111`，使用已有被忽略的 backend/.env 运行时注入，未把密钥复制至构建目录或镜像。仅以下一次短句流程，无自动重试：

| 服务 | HTTP 结果 | 耗时 |
| --- | --- | --- |
| DeepSeek deepseek-flash 文本 | POST 200 | 1326 毫秒 |
| 百炼 qwen3-tts-flash 合成与下载 | POST 200、GET 200 | 1790 毫秒 |
| 百炼 qwen3-asr-flash 识别 | POST 200 | 547 毫秒 |

样本为“小狐狸把雨伞借给了朋友。”，文本输出及识别结果均与原句一致。合成音频 119084 字节、24 kHz 单声道 16 位 PCM、2.48 秒。临时测试工具转换为 16 kHz 后送识别；生产适配器没有新增自动转码。reliable 仍为 false，不以一次合成样本证明真实录音准确率。本轮新增文本、合成、识别各 1 次，下载 1 次，实际费用未查询。

产物保留在仓库外 `D:/软工/tmp/bailian-smoke/zhiya-container-live-20261111/`：result.json、synthesis.wav、recognition-input.wav。临时驱动为 `D:/软工/tmp/zhiya-container-live-check.py`；仓库中长期使用的复现命令见[部署说明](../../infra/README.md)。两个测试项目的容器及网络均在验证后移除，数据卷与镜像保留。

本轮后端回归：35 项测试、102 个子用例通过，保留已有 Starlette / HTTPX 弃用提示；本机及容器依赖检查均通过。未改数据库结构和应用业务代码。

## G08 交付与待协作项

| 项目 | 当前边界 / 下一步 |
| --- | --- |
| 文本、识别、合成适配与错误处理 | 已提供内部入口、独立密钥配置、有限超时、无自动重试、离线回归和本机/容器真实连通性证据 |
| 部署基础与可复现验收 | 最新镜像、初始化、健康检查、重启持久化、容器联调命令已验证；可交业务后端与 Agent 接入 |
| API-022～024 | 与业务后端对接鉴权、会话调用前后复核、执行权、授权文本与受控媒体；暂未实现公开路由 |
| Agent 与内容安全 | Agent 提供可信上下文、结果校验和适龄安全策略；不得将内部模型入口直接公开 |
| 浏览器录音 | 与前端确定录音格式与转码责任；当前接受合规 WAV 或 MP3，WebM 未接入 |
| 效果验收 | 需要获授权的成人模拟录音、多样本与人工听感评价；不能把合成样本回环当成儿童识别准确率 |
| 上线 | HTTPS、部署域名、访问控制、生产备份恢复和端到端验收仍待团队完成；当前不标记完整 G08 / AC-07 完成 |

## 飞书同步状态

此前因浏览器连接超时未同步；2026-11-11 在本次声线语速任务中恢复后，已新增 G08 独立子记录 reczz28YPcNl6fM1 并核对保存，原有记录未改写。该条补记的历史内容如下：

- 标题：最新 Docker 镜像与容器内真实三服务联调通过
- 交付物：infra/check-docker.sh、容器联调命令、docs/testing/docker-checks.md、DCL-012
- 备注：2026-11-11，负责人林鑫。最新镜像完成缺库 503 / 初始化后 200、非 root、源码一致、缺密钥安全失败、重复种子导入、重启数据保留与健康检查；容器内 DeepSeek 文本 1326 毫秒、百炼合成 1790 毫秒、识别 547 毫秒，各一次成功，Windows 健康接口 200。35 项回归与 102 个子用例通过。密钥不入镜像，测试容器及网络已移除，卷与镜像保留。业务鉴权、会话、媒体授权、浏览器录音及效果验收仍待协作，不代表完整 G08 完成，未提交 PR。
