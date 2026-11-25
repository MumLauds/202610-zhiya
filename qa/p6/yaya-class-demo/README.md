# 芽芽共读 v5 · 独立课程演示入口

基于 v4，仅修正《井沿的纽扣》当页证据的来源。两个选项、预设对话、系统提示词、模型调用、回退、计时及历史回放均不变。

只供课程答辩，使用虚构绘本和预写合成话语，没有自由文本入口；浏览器只发送卡片、会话、轮次或选项 ID。后端通过冻结夹具合成请求，包含当页证据、服务器记录的已显示回复与实际追问，以及本轮合成儿童话语。不会读取生产数据库、接管生产 Agent、安装模型或启用 GPU。不对外开端口。标为「Qwen 现场生成」的内容是课程演示草稿，未通过儿童场景的事实核验，不能拿作生产配置。

## 在本地运行（不调用百炼）

解压，进入包含 `demo.py` 的 `yaya-class-demo` 目录。Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe demo.py --mock
```

macOS / Linux：用 `python3 -m venv .venv` 建环境，然后用 `.venv/bin/python` 运行后两条。若已经有包含 `requirements.txt` 所列依赖的环境，直接运行 `python demo.py --mock` 即可。打开 `http://127.0.0.1:8765/`。如果本机端口已占用，可加 `--port 18765`，同时换成对应浏览器地址。

选择《井沿的纽扣》，先看「当页原文」和「演示预设」。点击孩子想说的一句话，收到回复后点「换个说法再试」，再选另一句。每次进入选择点都创建新会话，回到相同预设；只提交所选 ID，服务端把它换成白名单中的合成原话。初始展示和换个说法都不会发模型请求。计时导出仍保留各轮记录，《井沿的纽扣》的记录另带 `choice_id`。

## 本地验证与后续真人评估

- 自动化：使用同一个 Python 环境执行 `python -m unittest discover -v`；有 Node.js 时可执行 `node --test test_browser.mjs`。两条选项均经真实本地 HTTP 和替身生成器验证；前端按钮、切换和计时分类用模拟 DOM/请求验证。Mock 回复是占位句，只证明交互，不代表 Qwen 如何回答。
- 两条路径调用同一生成入口，系统提示词、绘本证据、预设历史和其余上下文字段完全一致，只有“本轮孩子说”不同。模型请求不带选择 ID、分支标签、标准答案或期望动作。
- 将来需要验证真实质量时，可在自己的本地终端设置 `DASHSCOPE_BASE_URL` 为北京工作空间的 OpenAI 兼容地址，然后运行 `python demo.py --prompt-key` 并只在无回显提示输入实验 Key。人工判断：想象时允许延伸或倾听，不把飞上天讲成原书事实；核实时直接说明本页写的是木纽扣躺在井沿，没有写它变船。不用匹配固定台词。此交付未调用真实模型，未部署 ECS，也未测真实五秒延迟。

## 实验与计时口径

- 唯一模型 Qwen3.8-Flash，协议 B；传输沿用第五轮北京设置（非流式、关闭思考与历史思考、JSON object、温度 0.6、max_completion_tokens 512、SDK timeout 60 秒、无重试无转址）。提示词只补书页回查与一轮最多问一次。没有按案例或关键词预选模型回复。
- 单个上游调用最多等待 4.1 秒后在服务端提交回退；底层请求仍可能完成或计费，但迟到模型文本不会回写，且前一个上游仍占用时不发第二个。浏览器另在 4.7 秒停止等待，显示本地回退并结束本卡，重新开始才能继续，避免服务器与页面历史分歧。硬件卡顿、浏览器绘制或传输仍可能使完整显示超过 5 秒。
- 正文里出现中英文问号，或追问字段出现多段问号时，演示标记为 `QUESTION_FORMAT`，显示备用回复，不把被拒草稿写进后续历史。这个检查只针对标点能识别的问句，可能挡住带问号的原话引用，也不能识别所有没有问号的句子；不视为语义或绘本事实核验。
- 让标签页保持前台。按钮按下才开始计时，追加完整回复（含追问）后使用两次动画帧保守观测绘制完成。页面分开计数：五秒内模型正文、五秒内回退、超过五秒。切换至后台的样本另列并排除。用页面按钮下载当前标签页的计时 JSON。
- 计时 JSON 新增 `fallback_reason` 和 `server_ms`。`BROWSER_DEADLINE` 只说明浏览器没在时限内收到完整响应，不能据此认定百炼超时；这类样本没有可观测的后端耗时，`server_ms` 为 `null`。收到服务端返回时，`DEMO_DEADLINE` 指演示后端的等待时限，`TIMEOUT` 指模型请求超时，`API_ERROR` 和 `INVALID_DRAFT` 分别指接口错误和草稿格式检查未通过。`server_ms` 是演示服务处理本轮的耗时，不是模型 API 耗时。服务端发现客户端已断开时只写一行摘要，不输出草稿、请求或密钥。
- 「第五轮历史回放」中的片段来自已验档的北京批次，不会重跑 API。分别显示实际待答问题、合成孩子原话、书证、模型原始 JSON 草稿的字段、实验最终回执和保存的回退文本。优先展示「先不问我」与「真的写了吗」两段。
- 本包包含本地 Mock 和自动化测试；真实 ECS 到浏览器的耗时必须用启动后的 LIVE 页面实测，不用 Mock 或旧 API 计时代替。
- 有 Node.js 时可运行 `node --test test_browser.mjs`，检查前端回退文案、导出字段与计时分类。它模拟请求结果，无需在 ECS 安装 Node.js，也不产生真实页面耗时数据。

## v5｜在本地实测并留证据（需要已配置实验环境）

向模型发送的 `本页证据.p1.e1` 只有「一粒木纽扣安静地躺在井沿。」。「本页没有描述木纽扣变成小船。」仍单独显示在讲解区，也保存在本地实验元数据中，但不进入模型的 messages。第五轮封存回放只是历史记录，从不参与 v5 的生成。LIVE 正文依旧明确标为未经独立事实核验的合成草稿，系统回退单列原因。

1. 先由现有实验环境注入 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`（北京工作空间的 HTTPS OpenAI 兼容地址）。不把 Key 写入命令、源码、压缩包或日志。缺任一变量，LIVE 启动在网络调用之前退出。先运行一次连通性检查（只发独立的合成 JSON 指令，无重试，不进入任何分支）：
   ```powershell
   @'
   import json
   from provider import BailianProvider, Settings, PRIMARY_MODEL, message_digest

   cfg = Settings.from_env()
   if cfg.region != "cn-beijing" or cfg.timeout_s != 60:
       raise SystemExit("Use the existing Beijing workspace and 60-second API timeout.")
   messages = [
       {"role": "system", "content": "Synthetic connectivity check. Return a JSON object only."},
       {"role": "user", "content": 'Return the JSON object {"ok": true}.'},
   ]
   with BailianProvider(cfg, allow_message={message_digest(messages)}.__contains__) as p:
       r = p.complete(messages, PRIMARY_MODEL)
   print(r["status"], r["http_status"], r["error_type"])
   if r["status"] != "RETURNED" or json.loads(r["text"]) != {"ok": True}:
       raise SystemExit(1)
   '@ | .\.venv\Scripts\python.exe -
   ```
   macOS/Linux 可把同一段 Python 放在本地文件中执行。连通检查失败就停止，不把失败或 Mock 当作真实结果。通过后用全新的记录目录启动：
   ```powershell
   .\.venv\Scripts\python.exe demo.py --record-dir run-live-01
   ```
   有 Python 依赖的 Linux/macOS 可用 `.venv/bin/python`；测试留档工具而不联网时加 `--mock`，并换独立目录（如 `run-mock-01`）。
2. 在本机浏览器打开打印的地址，保持标签页在前台。进《井沿的纽扣》，选第一句一次，等页面结果，点「换个说法再试」，再选第二句一次。若先看到超时备用回复，等现有上游请求结束再测另一条，否则会得到 `UPSTREAM_BUSY`、没有新的 API 调用。记录文件 `evidence.jsonl` 的 `upstream` 事件表示该请求已取得上游结果（也可能是 API 错误）；迟到的草稿依然留档，不影响页面。
3. 点「下载本次页面计时 JSON」，保存到项目目录，然后可结束本地演示。在同目录运行：
   ```powershell
   .\.venv\Scripts\python.exe evidence.py run-live-01/evidence.jsonl yaya-page-timings-live.json run-live-01/paired-report.json
   ```
   保留两份原始文件，不用汇总覆盖。报告用会话 ID 合并，只认可同一批次的页面记录，分开列上游响应、服务器回执、最终页面、Token 与耗时；缺页面样本则为 null。不保存 HTTP 认证头、错误响应体、无法安全保留的响应或密钥。检查 `paired_context_and_parameters_match` 和两支的 `request_id`、上游状态。检查每支 `provider_response.request_body.messages` 的当页证据只有原文，且两套 messages 只差 `本轮孩子说`；分支 ID 只应出现在实验记录的元数据中；`UPSTREAM_BUSY` 或页面有回退不等于取得真实模型草稿。
4. 用《测试与人工核验.md》的六个维度检查两条真实草稿，再对照实际页面回执。程序不会把模拟、结构检查或未经独立核验的结果标为通过。

本次交付环境缺少上述两个变量，连通性检查与真实双分支均未调用 API；未获取真实 Token、API 或页面耗时，也未部署 ECS。
