# DeepSeek 与百炼真实接口联调记录

日期：2026-11-11；基础版本：8c4172c，本地未提交工作区；负责角色：林鑫 / 后端2；执行：Codex。用户自行填写密钥并授权真实联调、直接修复问题，未授权提交合并请求。

| 缩写 | 中文名称（英文全称） |
| --- | --- |
| API | 应用程序编程接口（Application Programming Interface） |
| HTTP / HTTPS | 超文本传输协议（Hypertext Transfer Protocol）/ 安全超文本传输协议（Hypertext Transfer Protocol Secure） |
| ASR / TTS | 自动语音识别（Automatic Speech Recognition）/ 语音合成（Text-to-Speech） |
| PCM / WAV | 脉冲编码调制（Pulse-Code Modulation）/ 波形音频文件格式（Waveform Audio File Format） |
| RIFF | 资源交换文件格式（Resource Interchange File Format） |
| URL / JSON | 统一资源定位符（Uniform Resource Locator）/ JavaScript 对象表示法（JavaScript Object Notation） |
| FR / NFR / AC / TC | 功能需求（Functional Requirement）/ 非功能需求（Non-Functional Requirement）/ 验收准则（Acceptance Criteria）/ 测试用例（Test Case） |
| kHz / MiB | 千赫兹（Kilohertz）/ 二进制兆字节（Mebibyte） |
| DCL | 设计变更记录（Design Change Log） |

## 实测范围与结果

Windows / Python 3.12.14 直接调用工程的 ModelGateway 与 VoiceGateway，使用 backend/.env，未输出密钥、供应商错误正文或签名 URL。调用前只检查配置完整性、供应商主机和模型名匹配情况；两个密钥不同，所有配置检查通过。未上传用户或儿童录音，使用固定测试句子“小狐狸把雨伞借给了朋友。”。

| 用例 / 需求关联 | 实际操作 | 结果 |
| --- | --- | --- |
| TC-LIVE-001 / IF-02、FR-12 | DeepSeek deepseek-flash，非思考、非流式，请求简短复述测试句子 | HTTP 200，返回“小狐狸把雨伞借给了朋友。”；耗时 1053 毫秒 |
| TC-LIVE-002 / FR-08/12、NFR-03/06 | 百炼 qwen3-tts-flash / Cherry 合成同一句子，下载并校验音频 | 修复后 POST/GET 均 200；耗时 1143 毫秒（含下载）；107564 字节，24 kHz、单声道、16 位 PCM，2.24 秒 |
| TC-LIVE-003 / FR-08、AC-07（局部） | 将本次合成音频在测试准备阶段转换为 16 kHz 单声道 PCM WAV，再调用 qwen3-asr-flash | HTTP 200，耗时 542 毫秒；转写为“小狐狸把雨伞借给了朋友。”，与原句完全一致；reliable 仍为 false |
| TC-LIVE-004 / FR-12、NFR-06、AC-10（局部） | 占位音频头修复及拒绝异常输入回归 | 先复现失败再修复；新增 2 个测试方法，验证音频帧不变、实际长度正确、未知长度/格式/半帧/空音频拒绝，上传路径不启用修复 |

这些耗时是单次短句样本的墙钟时间，不是性能分位数或稳定性承诺。没有做人工听感、儿童发音、噪声、多语言、长文本、限流、余额不足或付费失败的真实验收。合成内容被识别回原句不能代替音色质量评审。

## 实测发现与修复

最初合成请求和下载均成功，但本地返回 TTS_INVALID_RESPONSE。再次定向合成并仅保存测试音频，确认供应商 WAV 头中的 RIFF 长度为 0x7FFFFFBF、data 长度为 0x7FFFFF9B；实际文件为 96044 字节、音频帧 96000 字节（2 秒）。占位头使标准库得到巨大的声明帧数，与实际数据不符。

修复位于 backend/app/voice_gateway.py 的 _normalize_tts_wav：仅对实测的完整 44 字节占位头（24 kHz、单声道、16 位 PCM）且非空、帧完整的下载结果，回填 RIFF/data 实际长度。音频帧原样保留，之后仍经过既有 WAV 校验。不进行通用“截断文件修复”，不放宽用户上传格式，不修改域名白名单、10 MiB 下载限制、总期限或禁止重试的规则；没有增加运行时依赖。见 [DCL-011](../standards/design-change-log.md)。

总计执行文本请求 1 次、合成请求 3 次（首次、诊断、修复后验证）、音频下载 3 次、识别请求 1 次。两次前置合成虽在本地校验失败，供应商请求均已成功；不能据此认为不计费。没有自动重试，实际费用未查询。

## 本地证据与复现

证据保留在仓库之外，不提交合成音频或运行报告：

- D:/软工/tmp/bailian-smoke/live-20261111-fe25e62d/result.json：文本成功和首次合成本地校验失败。
- 同目录 provider-audio.wav、audio-diagnosis.json：第二次定向合成的占位头样本及安全元数据。
- D:/软工/tmp/bailian-smoke/live-20261111-0027848f/result.json、synthesis.wav、recognition-input.wav：修复后的合成与识别结果。

可从仓库根目录使用已有入口重新执行；命令会再次产生真实调用，文件名应换为尚不存在的路径：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend/smoke_model.py
.\.venv\Scripts\python.exe -X utf8 backend/smoke_voice.py tts D:/samples/new-answer.wav
.\.venv\Scripts\python.exe -X utf8 backend/smoke_voice.py asr D:/samples/adult-16k.wav --format wav
```

文本和合成均输入上述测试句子。合成默认输出 24 kHz，不能直接把它作为当前仅接受 16 kHz 的识别 WAV 输入；本轮仅在临时测试工具中使用 Python 3.12 标准库 audioop 做采样率转换，没有给业务适配器增加自动转码能力。已有 16 kHz 合规录音或 MP3 可按[接入说明](../../backend/bailian.md)测试。

## 回归与交付边界

修复后执行 `python -X utf8 -m pytest backend/tests -q`：35 项测试、102 个子用例通过，保留既有 Starlette / HTTPX 弃用提示；完整模拟覆盖见[验证记录](bailian-checks.md)。本次未重新构建 Docker，上一轮容器验证对应占位头修复前的代码。

本轮证明三个内部适配可以真实调用，修复了实际服务兼容问题；API-022～024 的鉴权、会话复核、内容安全、受控媒体、浏览器端到端和完整 AC-07 仍未完成。未修改数据库或公开契约，未提交合并请求。

后续进展：同日已在最新镜像中完成容器基础与真实三服务验收，并验证 Windows 健康接口访问，见[容器验收记录](docker-checks.md)。以上“未重建”描述保留本轮历史边界。
