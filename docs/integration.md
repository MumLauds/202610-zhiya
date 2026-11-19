# G13 系统联调记录

以下记录分别保留受测代码版本。首轮验证基础共读，随后复测 Firefox 录音上传，第二轮在同一次阅读会话中验证自适应引导。

| 范围 | 受测提交 | 结果与证据 |
| --- | --- | --- |
| 基础共读、历史与小结（首轮） | [`6e04a14`](https://github.com/MumLauds/202610-zhiya/commit/6e04a14292c801f72e14247dfb73b89642d0de85) | Chrome 完成登录、建档、开读、录音确认、结束和重新登录后读回小结。Firefox 录音上传返回 422，改走手动输入后也读回了小结。[首轮记录](testing/g13/round1/README.md) |
| Firefox 录音修复（FR-08、AC-07 的录音确认环节） | [`ce8b2a2`](https://github.com/MumLauds/202610-zhiya/commit/ce8b2a25b8253550e3e51cd494a18f2e3ec70854) 复现，[`9be5439`](https://github.com/MumLauds/202610-zhiya/commit/9be54399bf41ca2f09eb1680456a009ffdd1f169) 复测 | 修复前，Firefox 的 Ogg 音频以 WebM 上传；修复后 Firefox 用 Ogg、Chrome 用 WebM，均返回 200，解码为 16 kHz 单声道，转写可以编辑并确认。实测提交与 [#51](https://github.com/MumLauds/202610-zhiya/pull/51) 合入后的 `a09f9b6` 代码树一致。[双浏览器实测数据](testing/g13/voice-51.json) |
| 首题、未理解反馈和刷新恢复（第二轮，FR-06、FR-07） | [`a09f9b6`](https://github.com/MumLauds/202610-zhiya/commit/a09f9b669762d3d957be1db8ff70a17e01bc9692) | 同一会话内新增两条引导事件，刷新后可按序读回；三次原请求重放均未增加写入或模型调用。[第二轮实测数据](testing/g13/round2/result.json) |

首轮和 #51 均使用真实浏览器、本机 HTTPS、FastAPI、SQLite 和 FFmpeg。浏览器录音来自虚拟麦克风，ASR 供应商响应由本地模拟。首轮的 TTS 和小结模型也使用本地模拟，绘本、家长和孩子档案均为测试样例。首轮报告中的“阻塞”描述的是当时的提交。

## 第二轮实测

2026 年 11 月 19 日，Chrome 154.0.8037.98 登录家长账号、创建档案，从书架打开《纸船停在哪里》并开始阅读。在同一会话（ID `1`）的第 1 页，用浏览器同源请求调用 API-017，携带当前登录凭据和 CSRF 令牌。首次请求使用新的 `clientEventId`，每次重放都复用对应的请求键和完整请求体。

| 操作 | 请求修订号 | HTTP | 返回事件 ID / 修订号 | 库内会话修订号 | 累计引导事件 / 模型调用 |
| --- | ---: | ---: | --- | ---: | --- |
| 提交 `INITIAL` | 1 | 201 | 2 / 2 | 2 | 1 / 1 |
| 重放 `INITIAL` | 1 | 200 | 2 / 2 | 2 | 1 / 1 |
| 提交 `NOT_UNDERSTOOD` | 2 | 201 | 3 / 3 | 3 | 2 / 2 |
| 重放 `NOT_UNDERSTOOD` | 2 | 200 | 3 / 3 | 3 | 2 / 2 |
| 会话推进后再次重放最初的 `INITIAL` | 1 | 200 | 2 / 2 | 3 | 2 / 2 |

三次重放返回的 `data` 均与各自首次响应相等。刷新阅读页后，页面发出的 API-013 GET 返回 200。会话仍为 `1`、修订号为 `3`。开始阅读的 `STARTED` 事件之后，依次读回首题 `QUESTION`（`CONTEXT_QUESTION`）与未理解反馈 `FEEDBACK`（`CONCRETE_RECALL`）；正文、策略、事件 ID 及反馈值与首次响应逐项核对成功。

独立查询 SQLite 得到 3 条互动事件（含 `STARTED`）、2 条成功的引导执行记录和 2 条知识引用。两条引导事件都关联测试知识 `81`，第二次模型请求的上下文包含首次问题。测试绘本和知识在隔离库中设为可读、可检索，模拟供应商从实际请求的候选问句中选择第一句。[验证脚本](testing/g13/round2/verify.py) · [HTTP、刷新与数据库对账](testing/g13/round2/result.json)

第二轮覆盖 API-017 的首次提问和未理解反馈，以及 API-013 的刷新恢复。阅读页反馈按钮交互、API-016 主动提问到回答播放、正式绘本和真实模型效果仍待联调；G13 全链路验收尚未完成。

## 第二轮的复跑方法

在仓库根目录使用项目支持的 Python 3.12、Node.js 和已安装的 Google Chrome。Git 和 OpenSSL 需可在命令行运行。在已激活的测试虚拟环境中执行以下命令。

```sh
python -m pip install -r backend/requirements-dev.txt playwright==1.65.2
npm --prefix frontend ci
npm --prefix frontend run build
python -m pytest -q -s docs/testing/g13/round2/verify.py
```

脚本在临时目录建立独立 SQLite 库和回环 HTTPS 服务，Chrome 只为本轮生成的证书公钥放行。测试账号、档案、绘本和知识条目都来自合成数据。脚本走真实的认证、CSRF、知识检索、模型网关与数据库路径，对外部模型的 HTTP 响应使用确定性模拟。成功后，终端会打印本次 `result.json` 的路径。

同一基线还执行了 `python -m pytest -q backend/tests/test_interactions.py`，47 项测试通过。
