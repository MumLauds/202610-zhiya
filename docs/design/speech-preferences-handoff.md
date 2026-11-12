# G08 声线选择与模型合成语速交接

版本：v0.5；日期：2026-11-12；负责人：林鑫（后端2）；基础版本：8c4172c，本地未提交。用户已确认模型直接合成快慢；语音路由和录音转换已实现，真实认证、档案偏好存储与界面仍待协作接入；当前状态见[公开语音交接](voice-api-handoff.md)。

| 缩写 | 中文名称（英文全称） |
| --- | --- |
| TTS / API | 语音合成（Text-to-Speech）/ 应用程序编程接口（Application Programming Interface） |
| JSON / ID | JavaScript 对象表示法（JavaScript Object Notation）/ 标识符（Identifier） |
| WAV / PCM | 波形音频文件格式（Waveform Audio File Format）/ 脉冲编码调制（Pulse-Code Modulation） |
| FR / NFR / AC | 功能需求（Functional Requirement）/ 非功能需求（Non-Functional Requirement）/ 验收准则（Acceptance Criteria） |

关联 FR-08/12、NFR-03/05/06、AC-07/10（局部）。决策见 [DCL-013](../standards/design-change-log.md#dcl-013g08-增加声线选择与模型合成语速)，验证见[声线与语速检查](../testing/speech-preferences-checks.md)。

## 已实现的内部能力

G08 提供静态可选项、按调用选择声线和语速、严格校验、百炼协议映射及本地联调命令。后续 DCL-020 已增加 FFmpeg、表单解析依赖及语音路由，未新增数据库字段；未接真实认证时拒绝付费请求。

当前模型 `qwen-audio-3.1-tts-flash` 仅保留以下 **6 种声线**，顺序与用户选择一致，默认温怀清。静态清单 [voice_catalog.json](../../backend/app/voice_catalog.json) 只存这六项；其他声线及旧模型三声线兼容已移除，不另存全量备份、不在运行时抓网页。元数据沿用 2026-11-11 核对的来源，选用范围于 2026-11-12 按用户要求调整（DCL-017）。

| voiceId（供应商原值） | 家长看到的名称 | 声音特点 | 用途摘要 |
| --- | --- | --- | --- |
| wenhuaiqing_v3.1 | 温怀清 | 清亮、柔和的女声 | 儿童故事 |
| yezhiqing_v3.1 | 叶知晴 | 轻快、自然的女声 | 儿童故事 |
| longniuniu_v3.1 | 龙牛牛 | 阳光男童声 | 儿童有声书 |
| longshanshan_v3.1 | 龙闪闪 | 戏剧化童声 | 儿童有声书 |
| longjielidou_v3.1 | 龙杰力豆 | 天真男童音 | 儿童陪伴 |
| longpaopao_v3.1 | 龙泡泡 | 卡通角色风格的女声 | 儿童陪伴 |

叶知晴不等于此前验证的叶清禾；原真实调用记录不能转移算作叶知晴已验证。上表为选型展示摘要，JSON 保留来源原始描述及场景，未明确的语言仍留空。2026-11-12 已另行生成这六种声线的同文 0.9 慢速样本，用户明确反馈“这六种声音都可以”；本轮样本听感已接受，其他文本和语速效果不据此推定，详见[真实验证与反馈](../testing/g08-live-delivery-checks.md)。

| speechRate | 家长看到的名称 | 适配层发送 input.rate |
| --- | --- | --- |
| SLOW | 慢速 | 0.9 |
| NORMAL | 正常 | 1.0 |
| FAST | 快速 | 1.2 |

合成结果本身具有不同语速，前端播放速率固定为 1。表中数字是供应商合成参数，不承诺每段时长精确按倍数缩放。调整设置只影响下一次合成，既有音频不会改变；前端切换后停止旧音频、忽略迟到结果，不能叠加播放器变速。人工听感与适龄选择仍需验收。

2026-11-12（DCL-015）：用户反馈 0.8 慢速大部分有颤音，换播放器后仍存在，按用户要求将 SLOW 调为 0.9。用户随后已确认本次温怀清 0.9 样本试听可接受；该反馈仅覆盖本次样本，不扩展为六种声线全部通过，也不代表已确定颤音根因。此前 0.8 的时长证据仅作为历史记录。命令行重新运行即可使用新映射，已运行服务需重启，已有 Docker 镜像需重新构建并创建容器。

```python
from app.speech_options import get_speech_options

# 传入当前 SpeechSettings.tts_model，不能把离线默认目录误当成部署能力。
options = get_speech_options(speech_settings.tts_model)
audio_bytes = await request.app.state.voice_gateway.synthesize(
    text=verified_text,
    voice_id=stored_voice_id,
    speech_rate=stored_speech_rate,
)
```

目录保留 category、languages、dialects、scenarios 字段供前端展示，字段语义见接口草案；当前只展示上述六项。未明确的语言留空，不猜测支持范围。

内部参数使用 snake_case，可选项 JSON 使用 camelCase。省略 `voice_id` 使用服务端 `TTS_VOICE`；省略 `speech_rate` 使用 `NORMAL`。每次调用只改变本次请求，不修改共享网关或环境配置。显式空声线、未知/跨模型声线返回 `TTS_INVALID_VOICE`；语速只接收三个精确字符串，其他值返回 `TTS_INVALID_RATE`，均为 422，不请求供应商。现有错误处理与资源上限见[接入说明](../../backend/bailian.md)。

## 环境迁移与复现

保留现有 `DASHSCOPE_API_KEY`，不重新生成密钥。仅修改本地配置中的三个非密钥字段：

```dotenv
# 把 {WorkspaceId} 换为百炼北京业务空间显示的实际标识。
TTS_API_URL=https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer
TTS_MODEL=qwen-audio-3.1-tts-flash
TTS_VOICE=wenhuaiqing_v3.1
```

仓库示例的 `TTS_API_URL` 留空，避免硬编码个人空间；未填时仅禁用合成，不影响已配置识别与文本。服务重启后读取配置；Docker 需重新创建容器，单纯 restart 不更新环境。真实调用共用 20 秒总期限，不自动重试。当前只支持北京下载域名。

从仓库根目录执行，输出目录须已存在，文件名须未使用：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend/smoke_voice.py options
.\.venv\Scripts\python.exe -X utf8 backend/smoke_voice.py tts D:/samples/new-slow.wav --voice wenhuaiqing_v3.1 --speech-rate SLOW
.\.venv\Scripts\python.exe -X utf8 backend/smoke_voice.py tts D:/samples/new-normal.wav --voice wenhuaiqing_v3.1 --speech-rate NORMAL
.\.venv\Scripts\python.exe -X utf8 backend/smoke_voice.py tts D:/samples/new-fast.wav --voice wenhuaiqing_v3.1 --speech-rate FAST
```

`options` 不加载 .env、不调用供应商，当前只列六种声线。合成命令从标准输入读测试文本，会产生供应商用量。相同文本比较三档，WAV 均以原采样率正常播放，不进行音频帧拉伸或采样率变速。

当前仅支持 qwen-audio-3.1-tts-flash。旧 qwen3-tts-flash、其 Cherry/Serena/Ethan 及本轮移除的其他声线均不可再选；旧模型配置在加载时拒绝，六项外的单次声线请求返回 TTS_INVALID_VOICE，不发送付费请求。若已有配置或档案使用移除项，须明确重选，不静默替换；本轮不修改个人 .env。历史全量目录与三声线验证仍留在变更/验证记录中，不代表当前可选集合。

## 前后端协作边界

公开字段、候选路径及保存语义统一记录在[接口契约的语音偏好草案](api-contract.md#语音偏好扩展草案)，本文件不另设一套公共结构。详细 JSON 示例、系统设计、数据库设计和相关图源须由团队一起评审更新后冻结；当前不修改既有 v0.4 JSON 或物理表结构。

| 交接方 | 待实现与验收 |
| --- | --- |
| 姚华俊 / 林鑫 | 姚华俊提供真实身份与防跨站请求校验、档案偏好持久化及默认值迁移；林鑫已实现只读归属/会话/版本复核与短期音频资源，接入后读取已保存偏好。不要把任意客户端正文直接转发合成。 |
| 前端 | 家长设置页按上述顺序展示六种声线单选和慢/正常/快三档，展示语言、描述和场景；保存失败保留原有效设置；儿童界面不能修改；播放速率为 1，切换时取消旧播放并忽略过期响应。 |
| 媒体/业务后端 | 每份音频绑定实际声线、语速、模型及内容版本。若以后使用缓存，键必须覆盖这些值与授权作用域，读取时仍复核权限；不共享私人故事音频，不暴露供应商签名链接。 |
| Agent 与测试 | 只传已授权、已核验正文；确认无漏读、适龄听感及失败时文字回退；测试跨家庭档案/媒体拒绝、保存恢复、快速切换与已移除声线拒绝。 |

不实现试听公网接口或自动预生成。固定短句样本仅用于本地验收，放在仓库外；上线试听的鉴权、限额、重复请求控制与媒体授权由业务模块协作后接入。

### 下一轮对接时须明确的分工

现有 v0.4 JSON 契约将 API-022/023 的 ownerRole 列为“模型与语音接入”，API-024 列为“业务后端与语音接入”。本文件的“业务后端承接鉴权、会话和媒体”不表示语音公开接口整体转交他人；林鑫现已实现 API-022/023、只读会话/版本授权和 API-024 音频资源；姚华俊接真实身份认证、偏好持久化及图片资源，统一路由归属与持久幂等仍须确认。按仓库实名岗位列出的步骤和待确认项见[本轮交接](voice-api-handoff.md#按仓库分工的具体交接)。

- 与 Agent 负责人确认 INITIAL 无用户问题时如何构造入参，以及 TIMEOUT/API_ERROR 如何转成最终 AgentResult；当前适配器拒绝空 user_input，不自动重试。资料不足和结果安全核验由 Agent 处理，不能把模型 success=true 当作内容通过。
- 与业务后端确认 API-022 的 file/readingSessionId/pageNumber/revision、API-023 的已核验事件或页面来源、返回前权限复核及 API-024 资源授权；合成不接受客户端任意正文。偏好字段、默认值与存量迁移仍按接口草案评审。
- 用户已指定浏览器录音格式转换由林鑫负责，当前路由/命令已支持 WebM、Ogg、MP4、MP3 和 WAV，统一输出 16 kHz 单声道 16 位 PCM；限制 4 MiB、120 秒。吴怡霏负责上传停止录音后完整的 Blob，内部网关不接原始 WebM。当前识别可靠性 reliable=false，亲子模式须确认/修正，儿童模式走重录或受限按钮，不能自动当作可信回答。
- 与前端确认六声线和三档设置、保存失败处理、播放器保持 1 倍速、切换后忽略旧结果；本次样本采用 SLOW，不改变产品默认 NORMAL。
- 与测试及集成负责人约定一条联调主链和失败用例，区分底层实调、人工试听与完整业务验收；代码仍在本地未提交，不能假定其他成员已可从 main 使用。

官方依据（2026-11-11 核对）：[Qwen-Audio 合成参数](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-http-api)、[各模型声线目录](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list)。

目录维护：仅维护这六个精确标识及其来源元数据；新增声线需另行确认。参数验证不等同于全部声线的真实合成或人工听感验收。此前全量目录结果见历史 DCL-014，当前范围见 DCL-017。
