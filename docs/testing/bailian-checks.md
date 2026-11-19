# DeepSeek 文本与百炼语音适配验证

日期：2026-11-11；版本：本地未提交工作区，基础提交 8c4172c；负责角色：林鑫 / 后端2；执行：Codex，两个子智能体参与实现和交叉复查。

| 缩写 | 中文名称（英文全称） |
| --- | --- |
| API / IF | 应用程序编程接口（Application Programming Interface）/ 接口分组（Interface） |
| ASR / TTS | 自动语音识别（Automatic Speech Recognition）/ 语音合成（Text-to-Speech） |
| FR / NFR | 功能需求（Functional Requirement）/ 非功能需求（Non-Functional Requirement） |
| AC / TC | 验收准则（Acceptance Criteria）/ 测试用例（Test Case） |
| CLI | 命令行接口（Command-Line Interface） |
| HTTPS / URL | 安全超文本传输协议（Hypertext Transfer Protocol Secure）/ 统一资源定位符（Uniform Resource Locator） |
| WAV | 波形音频文件格式（Waveform Audio File Format） |
| WSL | 适用于 Linux 的 Windows 子系统（Windows Subsystem for Linux） |
| DCL | 设计变更记录（Design Change Log） |

变更依据 [DCL-009～011](../standards/design-change-log.md)，使用方式见[接入说明](../../backend/bailian.md)。本文件保留既有语音用例编号，文本协议已按第 010 项更新为 DeepSeek 官方服务；第 011 项修复真实合成音频的占位头。

## 已执行检查

Windows / Python 3.12.14，从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/tests -q
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s database/tests -q
.\.venv\Scripts\python.exe -m pip check
```

真实联调修复后的最新回归：后端 **35 passed，102 subtests passed**。数据库 4 项及依赖一致性检查在上一轮供应商切换时通过，本次未修改数据库或依赖。既有 Starlette TestClient / HTTPX 弃用提示仍存在。模拟服务使用 MockTransport 与虚构密钥/文本/音频，不加载真实密钥、不调用收费接口。CLI 测试禁止加载本地 .env；保存测试仅使用临时目录。真实调用证据与回归分开记录，见[真实联调记录](live-provider-checks.md)。

此前复查先用回归测试复现，再修复三处边界问题：环境 URL 在校验前被去除空白、损坏 WAV 块导致未捕获 RuntimeError、ASR 接受非 assistant 或附带拒绝/工具调用的消息。同步补齐正式文档缩写表。本轮先确认新协议与默认配置测试在旧实现上失败，再切换文本协议和密钥来源；语音逻辑不变，不变更公开契约。

| 用例 | 测试文件 / 方法 | 需求关联与实际结果 |
| --- | --- | --- |
| TC-BL-001 | test_speech_config.py 的 5 个测试 | FR-12、NFR-05：DeepSeek 文本与百炼语音默认配置、两套密钥隔离、空配置、环境 URL 空白拒绝、非法参数及凭证不进入 repr 通过 |
| TC-BL-002 | test_backend.py 的模型测试 | IF-02、FR-12：文本非思考请求、期限、无重试、输出校验与安全日志通过；旧测试保留 |
| TC-BL-003 | test_voice.py::test_success_payload_and_download_has_no_credentials | FR-08、NFR-05：识别数据格式、reliable=false、合成输入、HTTPS 下载且无 Bearer 通过 |
| TC-BL-004 | test_unconfigured_and_invalid_inputs_do_not_request | FR-12：缺配置、非法音频/文字在网络前失败，通过 |
| TC-BL-005 | test_status_mapping_no_retry、test_invalid_recognition、test_non_transcription_messages_are_rejected | FR-08/12、AC-10（局部）：上游失败、安全错误、无效转写、错误角色/拒绝/工具调用与不重复请求，通过 |
| TC-BL-006 | test_untrusted_urls_never_download | NFR-04/05：恶意主机、凭证、控制字符等下载地址拒绝，通过 |
| TC-BL-007 | test_bounded_and_uncompressed_responses、test_bad_tts_output_and_audio、test_malformed_wav_chunk_returns_safe_errors | NFR-06：响应大小、压缩、畸形数据与不完整 WAV 拒绝；损坏块在识别前拒绝且不联网，合成下载映射为安全错误，通过 |
| TC-BL-008 | test_timeout_and_cancel_cover_download、test_download_stream_closed_on_cancel_and_deadline | NFR-03/06、AC-10（局部）：总期限包含下载、取消透传及响应流关闭，通过 |
| TC-BL-009 | test_smoke_voice.py 的 3 个测试；test_health.py 生命周期测试 | NFR-05/06：CLI 不覆盖文件、不修改原录音、无密钥不产出文件，文本与语音客户端随应用关闭，通过 |
| TC-BL-010 | Docker 实际构建与空密钥启动 | FR-13、NFR-08（局部）：新镜像构建、初始化、healthy、健康响应 200、三项配置均无密钥时不可调用，通过 |

| 本轮新增追踪 | 测试文件 / 方法 | 需求关联与实际结果 |
| --- | --- | --- |
| TC-MP-001 | test_backend.py::test_sends_one_chat_completion_and_logs_metadata_only；test_speech_config.py::test_defaults_and_separate_provider_keys | IF-02、FR-12：DeepSeek 默认地址与模型、thinking.type=disabled、非流式请求、无百炼文本参数，通过 |
| TC-MP-002 | test_voice.py::test_mixed_providers_use_separate_credentials_and_protocols、test_speech_key_alone_does_not_send_text_request | NFR-05、FR-08/12：同一模拟流程中的文本、识别、合成各用正确主机与密钥，音频下载无鉴权头；仅有语音密钥时文本不联网，通过 |
| TC-MP-003 | Compose 容器默认配置与空密钥启动 | FR-13、NFR-08（局部）：实际构建、初始化、DeepSeek/百炼默认值、空密钥不可调用、healthy 和健康响应 200，通过 |
| TC-LIVE-004 | test_voice.py::test_bailian_placeholder_wav_is_normalized_without_changing_pcm、test_placeholder_repair_rejects_other_damage_and_asr_input | FR-12、NFR-06：只修正已知供应商占位头、帧不变、长度正确；未知头、半帧、空数据拒绝，上传仍严格校验，通过 |

语音测试方法均位于 test_voice.py 的 VoiceTests；配置测试位于 test_speech_config.py 的 unittest 类。原健康、统一错误和数据库测试也通过，本轮未实现额外业务路由。

## 容器与验收边界

使用 D 盘已有 Debian WSL 2，Docker Engine 29.9.2 / Compose 5.6.1；独立项目 zhiya-bailian-check-20261111，从明确列出的干净源码副本构建。沿用[部署说明](../../infra/README.md)记录的 Windows 扩展属性限制，不声称原 Windows 目录直接构建或 Windows 端口转发已通过。测试后删除本次容器与网络，保留独立镜像和数据卷。

上述为较早的百炼接入阶段结果。本次切换后另以独立项目 `zhiya-mixed-check-20261111` 从不含 .env 的白名单源码副本重建镜像；清空供应商环境变量，Compose 配置检查、15 张表初始化、healthy、健康响应 200 及响应追踪编号一致通过。容器内核对文本默认地址为 DeepSeek、模型 deepseek-flash，语音模型仍为 qwen3-asr-flash / qwen3-tts-flash；三项能力均因缺密钥不可调用。测试后移除本次容器与网络，保留独立镜像和数据卷；未调用供应商。Windows 原目录直接构建和端口转发仍未验证。

上述容器结果对应音频占位头修复前的代码，本次修复后未重新构建。实际供应商短句调用随后已执行并通过，详见[独立记录](live-provider-checks.md)；多样本识别准确率、模型适龄效果、人工听感与费用仍无验收结论。浏览器 WebM 转码、会话鉴权、返回前再次校验、可靠性回退、受控音频资源与安全审核由后续业务联调验证。API-022～024 和完整 AC-07 不能标记已完成。北京地域以外的下载域名也未接入。

后续进展：最新镜像已重建并完成基础验收、真实三服务调用和 Windows 健康接口访问，见[容器验收记录](docker-checks.md)；上段保留较早阶段结果。
