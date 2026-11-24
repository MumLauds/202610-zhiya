# H05 硬件 DEMO / Mock 层

本目录只提供本地 DEMO 数据、标准库 HTTP Mock 和离线测试，不包含 LVGL 页面，也不替代真实后端。

## 文件

| 路径 | 用途 |
| --- | --- |
| `build_fixtures.py` | 从 4 个种子文件 + H03 `assets-manifest.json` 生成 5 个 fixtures |
| `server.py` | `127.0.0.1:8765` 的内存 Mock HTTP 服务 |
| `fixtures/` | 20 本原创绘本、104 页、40 个 H03 封面引用；全部 `demoOnly=true`，源状态 `PUBLISHED` |
| `../tests/mock/test_mock.py` | 临时目录 + 回环 HTTP 的离线测试（35 项） |
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

- 数据来自仓库种子和 H03 清单，原图/种子只读；不编造正文或插画。
- 最新种子中 20 本原创绘本为 `PUBLISHED`，1 本《逃家小兔》为 `DRAFT` 联调草稿且不纳入 DEMO；`demoOnly=true` 仅表示本地演示，不是在线授权。
- `coverRef/imageRef=null`；每页 `localPageAsset=null` 是后续独立 H03 内页任务的预留接入位，当前不填路径/哈希。
- `localCover` 的 `assetPath/devicePath` **不是** API-024 的 `resource_key`。
- 文本来源哈希使用 `sha256-utf8-lf-v1`（UTF-8，CRLF/CR 归一为 LF），保证 Windows 工作树和 Git LF 干净检出都能通过 `--check`。
- `/mock/voice/*` 只是 H04 UI 状态演示；`tts_failed` 的重试复用已保存 ASK `interactionEventId` 回 `SYNTHESIZING`，不重新问答/录音。
- Mock 的成功不是真实后端联调成功；JPEG 真机解码、资源分区和 LVGL 显示仍由 H06 验证。
