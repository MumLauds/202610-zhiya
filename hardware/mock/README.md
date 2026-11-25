# H05 硬件 DEMO / Mock 层

本目录只提供本地 DEMO 数据、标准库 HTTP Mock 和离线测试，不包含 LVGL 页面，也不替代真实后端。

## 文件

| 路径 | 用途 |
| --- | --- |
| `build_fixtures.py` | 从 4 个种子文件 + H03 封面清单 + H03 内页清单生成 5 个 fixtures |
| `server.py` | `127.0.0.1:8765` 的内存 Mock HTTP 服务 |
| `fixtures/` | 20 本原创绘本、104 页、40 个封面引用、104 个内页 PAGE 引用；全部 `demoOnly=true`，源状态 `PUBLISHED` |
| `../tests/mock/test_mock.py` | 临时目录 + 回环 HTTP 的离线测试（43 项） |
| `../docs/mock-contract.md` | 完整字段、接口、状态机、错误码和 H06 交接 |

## 运行

```powershell
Set-Location <仓库根目录>
python hardware/mock/build_fixtures.py
python hardware/mock/build_fixtures.py --check
python hardware/mock/server.py --port 8765
python -m unittest discover -s hardware/tests/mock -v
```

默认只监听 `127.0.0.1`。只有显式设置 `--host` 才允许受控局域网演示，不得暴露到公网。

## 边界

- 数据来自仓库种子和 H03 清单，原图/PAGE/种子只读；不编造正文或插画。
- 最新种子中 20 本原创绘本为 `PUBLISHED`，1 本《逃家小兔》为 `DRAFT` 联调草稿且不纳入 DEMO；`demoOnly=true` 仅表示本地演示，不是在线授权。
- `coverRef/imageRef=null`；104 页 `localPageAsset` 全部由 `(storyVersionId, pageNumber)` 从 H03 清单回填，`imageStatus=LOCAL_PAGE_ASSET_AVAILABLE`。
- `localPageAsset.decoderVerified=false`；本地 JPEG 存在不等于 ESP32-S3/LVGL 已能解码或已上屏。
- `localCover` 与 `localPageAsset` 的 `assetPath/devicePath` **不是** API-024 的 `resource_key`。
- 文本来源哈希使用 `sha256-utf8-lf-v1`（UTF-8，CRLF/CR 归一为 LF），覆盖 4 个种子、封面清单和内页清单；JPEG 按原始字节校验 SHA-256。
- `/mock/voice/*` 只是 H04 UI 状态演示；`tts_failed` 的重试复用已保存 ASK `interactionEventId` 回 `SYNTHESIZING`，返回的 `answerText` 取自该 ASK 的 `outputText`，不重新问答/录音。`GET` 会话会恢复 ASK 的 `outputText/strategyType/revision`。
- Mock 的成功不是真实后端联调成功；JPEG 真机解码、资源分区和 LVGL 显示仍由 H06 验证。
