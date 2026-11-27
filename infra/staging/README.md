# Yaya Agent 2.0 独立 Staging

Compose 项目固定为 `zhiya-agent-stg`。后端只接受 Compose 网络内的 Caddy 请求。HTTPS 默认绑定本机 8443，远程测试需经批准的私网访问或 SSH 转发。Caddy 私有 CA 只在专用测试客户端信任，私有 API 返回 `private, no-store`。SQLite 和 TLS 分属独立命名卷，日志使用容器本地轮换。

1. 在已获批的独立主机复制 `.env.example` 为本地 `.env`，限制读取权限。保留默认模式从空卷启动。容器只初始化 schema 和合成绘本《松鼠的纸风车》。用不同的合成监护人账号完成注册，再查取允许入组的账号 ID。
2. 获批北京百炼专用凭证和预算后，填写 `YAYA_AGENT2_API_URL`、`YAYA_AGENT2_API_KEY` 和独立的 `YAYA_AGENT2_LEDGER_SECRET`。以逗号分隔设置 `YAYA_AGENT2_ALLOWLIST` 的合成账号。只将已获独立出站许可的测试监护人和合成档案以 `guardian_id:child_profile_id` 成对加入 `YAYA_AGENT2_INPUT_ALLOWLIST`。改为 `allowlist` 后重启 backend。2.0 始终使用 `qwen3.8-flash`。
3. 从一名入组账号和一名未入组账号分别新建会话。核对回执、权限、数据库状态、预算和缓存头。用原会话演练关闭、重开和仅本地回退。按发布计划记录 Gate 1 回执。

```bash
docker compose --env-file infra/staging/.env -f infra/staging/compose.yaml config --quiet
docker compose --env-file infra/staging/.env -f infra/staging/compose.yaml up -d --build --wait
```

使用专用空卷和合成输入。线上账号、凭证、数据卷、备份和模型配置均不得用于此环境。详见 [Staging 计划](../../docs/production-readiness/staging-plan.md) 和 [发布与回滚](../../docs/production-readiness/deployment-and-rollback.md)。

语音默认关闭。真实语音验收时，在独立主机的受限环境文件注入 `STAGING_SPEECH_API_KEY`，使用获准调用 `qwen3-asr-flash` 与 `qwen-audio-3.1-tts-flash` 的独立测试 Key。Agent 文本模型继续使用 `YAYA_AGENT2_API_KEY`。在北京语音测试空间的专属 Host 下，将 `STAGING_ASR_API_URL` 设置为 `/compatible-mode/v1/chat/completions`，将 `STAGING_TTS_API_URL` 设置为 `/api/v1/services/audio/tts/SpeechSynthesizer`。两项均填写完整 HTTPS URL。使用成人朗读的虚构绘本测试句验证录音、转写、确认提交与播放。[百炼 ASR](https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference) · [百炼 TTS](https://help.aliyun.com/zh/model-studio/non-realtime-tts-user-guide)

在 CI 或有 Docker Compose 的本机可执行 `bash infra/staging/smoke_ci.sh`。测试使用另一个 Compose 项目和独立卷，从构建一直验证到关闭和版本隔离。其北京工作空间和密钥值是合成占位符，运行时检查无需供应商调用。
