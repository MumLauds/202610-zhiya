# G08 DeepSeek 文本与百炼语音接入

日期：2026-11-11；负责角色：林鑫 / 后端2；执行：Codex。依据用户最新选型，文本改用 DeepSeek 官方服务，语音识别与合成保留百炼；原文本选型由变更记录第 010 项更新。密钥不入库，修改尚未提交；模拟测试不等于真实服务联调或团队正式验收。

| 缩写 | 中文名称（英文全称） |
| --- | --- |
| API | 应用程序编程接口（Application Programming Interface） |
| HTTP / HTTPS | 超文本传输协议（Hypertext Transfer Protocol）/ 安全超文本传输协议（Hypertext Transfer Protocol Secure） |
| ASR / TTS | 自动语音识别（Automatic Speech Recognition）/ 语音合成（Text-to-Speech） |
| PCM / WAV | 脉冲编码调制（Pulse-Code Modulation）/ 波形音频文件格式（Waveform Audio File Format） |
| MP3 | MPEG-1 音频第三层编码（Moving Picture Experts Group-1 Audio Layer III） |
| UTF-8 / JSON | Unicode 转换格式（Unicode Transformation Format，8-bit）/ JavaScript 对象表示法（JavaScript Object Notation） |
| URL / OSS | 统一资源定位符（Uniform Resource Locator）/ 对象存储服务（Object Storage Service） |
| SDK | 软件开发工具包（Software Development Kit） |
| FR / NFR | 功能需求（Functional Requirement）/ 非功能需求（Non-Functional Requirement） |
| AC / TC | 验收准则（Acceptance Criteria）/ 测试用例（Test Case） |
| MiB / kHz | 二进制兆字节（Mebibyte），1,048,576 字节 / 千赫兹（Kilohertz） |

## 配置与本地联调

从 backend/.env.example 首次复制到 backend/.env，已有文件不要覆盖。在本地把 DeepSeek 官方密钥填入 `MODEL_API_KEY`，把百炼北京地域密钥填入 `DASHSCOPE_API_KEY`。两个字段独立，文本密钥缺失时不再回退到百炼密钥。不要把密钥贴入聊天、任务表或 Git。已有系统环境优先于 .env。各项默认值见示例文件：文本 deepseek-flash，识别 qwen3-asr-flash，合成 qwen-audio-3.1-tts-flash / wenhuaiqing_v3.1；合成地址默认留空，须填百炼北京业务空间的完整 SpeechSynthesizer 地址。采用普通单次请求，文本固定 thinking.type=disabled；文本、语音总预算各自默认 20 秒（语音含合成下载）。

已有百炼文本配置需同步迁移：MODEL_API_URL 改为 `https://api.deepseek.com/chat/completions`，MODEL_NAME 改为 `deepseek-flash`；MODEL_API_KEY 若以前填写过百炼密钥，须由本人换成 DeepSeek 官方密钥。本次声线与模型语速扩展还需迁移 TTS_API_URL、TTS_MODEL、TTS_VOICE，密钥保持原值，详见[声线与语速交接](../docs/design/speech-preferences-handoff.md)。直接在终端导出 DEEPSEEK_API_KEY 不会被当前工程读取，应使用上述 MODEL_API_KEY 字段。当前文本参数固定适配 DeepSeek，不能仅改地址就宣称兼容其他供应商。

三个服务 URL 均原样校验，不自动删除首尾空白；包含空白或控制字符时报配置错误。显式空字符串表示未配置，纯空白字符串属于无效配置。

在仓库根目录执行以下显式真实联调命令；仅使用成年人模拟录音或获授权素材。命令会产生供应商调用，离线测试不调用供应商。

```powershell
.\.venv\Scripts\python.exe -X utf8 backend/smoke_model.py
.\.venv\Scripts\python.exe -X utf8 backend/smoke_voice.py asr D:/samples/adult.wav --format wav
.\.venv\Scripts\python.exe -X utf8 backend/smoke_voice.py asr D:/samples/adult.mp3 --format mp3
.\.venv\Scripts\python.exe -X utf8 backend/smoke_voice.py tts D:/samples/new-answer.wav
```

示例路径须替换为实际文件；合成命令从终端读取测试文字，目标目录必须存在，脚本会在调用供应商前检查目录，已有文件不会覆盖；保存成功返回 outputPath 完整路径。识别仅在内存读取当前音频，不复制或删除调用者原文件；合成命令仅把结果保存到指定的新文件。服务器适配层不持久化原始录音或合成文件。合成下载仅接受完整 24 kHz、单声道、16 位 PCM WAV；其他格式返回 TTS_INVALID_RESPONSE，不转码、不自动重试。

识别初版接受 WAV、MP3，输入最多 4 MiB；WAV 限单声道、16 kHz、16-bit PCM。浏览器录音常见 WebM 需调用方转为支持格式；本轮不引入转码程序。合成输入去除首尾空白后 1～600 字符。超过边界直接报错，不自动切片追加付费调用。

## 供业务后端调用

```python
transcription = await request.app.state.voice_gateway.transcribe(
    audio=audio_bytes, media_type="audio/wav",
)
audio_bytes = await request.app.state.voice_gateway.synthesize(
    text=verified_text, voice_id="wenhuaiqing_v3.1", speech_rate="SLOW",
)
```

同一应用生命周期管理文本与语音客户端。调用前由业务模块校验身份、阅读会话、页面、revision、执行权与输入授权，返回前再次核对会话。TTS 只能传入从有权访问的已核验事件或绘本页取得的文本。媒体模块须按 API-024 存储并授权交付结果；不能把本地命令当成公开接口。

ASR 返回内部 Transcription（text、reliable），非空转写仅表明供应商返回了文本；本接口没有可用的置信度证据，reliable 固定为 false。亲子模式须确认/修正，儿童模式须走受限按钮或重录回退，不自动当成可信回答。空、畸形、非 assistant 消息、携带拒绝或工具调用、超过 200 字的转写失败，不截断后继续生成。

2026-11-12（DCL-020）：API-022/023 路由和 API-024 音频分支已实现，复用现有 SQLite 做只读归属、版本、授权与进度检查，并提供短期内存媒体。真实认证由姚华俊注入，未接入时默认 503；图片分支和业务端到端未完成。详见[接口交接](../docs/design/voice-api-handoff.md)。

## 协议映射与边界

| 内部操作 | 第三方固定字段（保留原拼写） | 本项目处理 |
| --- | --- | --- |
| generate | messages、max_tokens、thinking.type | DeepSeek 非思考文本，返回完整字符串；不发送 enable_thinking |
| transcribe | input_audio.data、asr_options | 内存编码音频；只提取转写，不伪造置信度 |
| synthesize | input.text、voice、format、sample_rate、rate | Qwen-Audio 使用 WAV / 24000 Hz，SLOW/NORMAL/FAST 映射 0.9/1.0/1.2，生成后受限下载字节；不透传签名链接 |

当前只支持 qwen-audio-3.1-tts-flash，声线只保留温怀清、叶知晴、龙牛牛、龙闪闪、龙杰力豆、龙泡泡；完整列表、标识和前后端边界见[交接说明](../docs/design/speech-preferences-handoff.md)。原全量目录及旧模型兼容已移除，语言与场景元数据不透传供应商；模型直接合成不同语速，不改变音频帧或播放器倍速。六种声线已另行生成同文 SLOW=0.9 样本，用户已接受本次听感，见[六声线证据](../docs/testing/g08-live-delivery-checks.md)；其他文本及语速未据此验收。历史真实结果见[声线语速验证](../docs/testing/speech-preferences-checks.md)。

TTS 下载仅允许北京地域指定 OSS 主机，HTTP 链接升级为 HTTPS；拒绝其他主机、凭证、片段、非标准端口和重定向。下载请求不携带 API 密钥；不记录签名 URL、音频、转写或正文。换地域须单独核对下载域名，不能放开任意 URL。JSON 响应限 1 MiB，音频限 10 MiB，拒绝压缩响应；这些是内部资源限制，不代表已经冻结对外上传契约。

百炼真实合成可能返回带占位长度的 24 kHz 单声道、16 位 PCM WAV。适配层只识别本次实测的固定占位头，在完整下载、帧对齐后回填实际长度，再进行 WAV 校验；不修改音频帧，不修复未知损坏格式，也不对用户上传执行该处理。合成输出维持 24 kHz；公开识别路由及 smoke_voice.py asr 现在会先规范化为 16 kHz 单声道 16 位 PCM；支持 WebM、Ogg、MP4、MP3 和 WAV，超过 4 MiB 或 120 秒整段拒绝。内部 VoiceGateway 仍只接收规范音频。离线 convert 命令不读取 .env、不请求供应商，详见接口交接。

所有网络调用均无自动重试。识别一次 POST；合成一次 POST 与一次受限 GET，共用总期限。固定错误分类包含未配置、鉴权、限流、不可用、非法响应和超时，不暴露上游错误正文。取消请求向上传播并关闭响应流；模型结果、内容安全与幂等仍由业务层处理。

## 依据与验证

依据 FR-08、FR-12、NFR-03/05/06、AC-07/10、接口 API-022～024。采用已有 HTTPX 与标准库，不新增 SDK。官方依据：[DeepSeek 文本协议及思考开关](https://api-docs.deepseek.com/api/create-chat-completion/)、[DeepSeek 模型与接入地址](https://api-docs.deepseek.com/)、[百炼 ASR](https://help.aliyun.com/en/model-studio/qwen-asr-api-reference)、[百炼 Qwen-Audio TTS](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-http-api)。读取日期 2026-11-11。

离线验证：仓库根目录执行 `.\.venv\Scripts\python.exe -m pytest backend/tests -q`。具体结果与需求追踪见[文本与语音验证记录](../docs/testing/bailian-checks.md)。2026-11-11 用户自行填写凭证后，文本、合成与识别的真实短句调用通过，见[真实联调记录](../docs/testing/live-provider-checks.md)。模型适龄效果、人工听感及浏览器端到端流程仍待验收，不能据此标记完整 AC-07 已通过。

最新容器也已验证三项真实调用及 Windows 健康接口访问，镜像包含上述两个联调命令，见[容器操作说明](../infra/README.md)和[容器验收及 G08 交接边界](../docs/testing/docker-checks.md)。
