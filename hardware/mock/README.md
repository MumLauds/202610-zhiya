# H05 硬件 DEMO / Mock 层

本目录只提供本地 DEMO 数据、标准库 HTTP Mock 和离线测试，不包含 LVGL 页面，也不替代真实后端。

## 文件

| 路径 | 用途 |
| --- | --- |
| `build_fixtures.py` | 从 4 个种子文件 + H03 `assets-manifest.json` 生成 5 个 fixtures |
| `server.py` | `127.0.0.1:8765` 的内存 Mock HTTP 服务 |
| `fixtures/` | 20 本原创绘本、104 页、40 个 H03 封面引用；全部 `demoOnly=true` |
| `../tests/mock/test_mock.py` | 临时目录 + 回环 HTTP 的离线测试 |
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
- 20 本原创绘本的版本状态保留 `DRAFT`；`coverRef/imageRef=null`。
- `localCover` 的 `assetPath/devicePath` **不是** API-024 的 `resource_key`。
- `/mock/voice/*` 只是 H04 UI 状态演示；不调用 ASR/TTS/LLM，`audioUrl=null`。
- Mock 的成功不是真实后端联调成功；JPEG 真机解码、资源分区和 LVGL 显示仍由 H06 验证。
