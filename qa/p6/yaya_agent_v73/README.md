# 芽芽课堂演示 v7

这是独立合成演示，不修改正式知芽 Agent、API、数据库或 ECS。支持 Python 3.10+；安装 requirements.txt 后：
- 本地演示：`python demo.py --v7 --mock --port 18765`，打开 http://127.0.0.1:18765/v7。
- 自动化：`python -B -m unittest discover`；`node --test test_browser.mjs test_coreading_browser.mjs`。
- HTTP Mock 开发回归：`python -B run_v7.py --batch development --out runs-dev-mock-01`。新目录不可存在；将 development 换成 probes 可跑专项。Mock 不需要密钥。
- 真实运行单批：`python -B run_v7.py --live --prompt-key --batch development --out runs-dev-live-01 --base-url "<北京工作空间 URL>"`。密钥只在本机无回显输入。留出需另带 `--batch holdout --fixture <path> --fixture-sha256 <hash>`。
- 三批真实运行只输一次密钥：`python -B run_v7_suite.py --base-url "<北京工作空间 URL>" --fixture "<独立留出文件>" --fixture-sha256 e7e1850ad3ba0e83288f393ab39549f1607b65c52483318d3f52793b313e98cc --out runs-live-01`。需要估算费用时加 `--cny-per-million 输入单价 输出单价`，CLI 时间不冒充页面展示时间。
- 报告含实际发出的 messages、Qwen 原始响应、服务端回执；浏览器可导出各自计时文件。要合并页面计时，使用 `eval_v6.py report <v7-evidence.jsonl> <新报告目录> --page <录自同一批会话的 JSON>`。
- `report` 中的两份 HTML 分开交给甲乙。收回两份 JSON 后用 `python eval_v6.py merge <blind-key-private.json> <甲.json> <乙.json> <汇总.json>`，不要把映射表发给评审。
- 设计与已知不足见 docs 中的三份 v7 文档。以下 v6/v5 用法保留供复现；v7 要显式传入 `--v7`。旧包原样保留。

# 芽芽 Agent 2.0 v6 · 多轮共读实验

v6 是独立、仅合成数据的课堂原型。保留包中的旧版演示页和模型连接，不修改知芽生产 Agent、正式 API、数据库或部署。三本书、12 条开发对话不是正式泛化留出集；芽芽台词不在脚本里。

## 本机启动

在解压后的 `yaya-class-demo` 目录运行（Windows PowerShell）：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe demo.py --mock --port 18765 --record-dir run-v6-mock-01
```

打开 `http://127.0.0.1:18765/v6`，选择一条合成对话。按页面提示逐轮提交；需要翻页时手动点翻页。Mock 只验界面和上下文，不评模型能力。已有虚拟环境不必重建。macOS/Linux 的解释器改用 `.venv/bin/python`。记录目录必须是新目录。

结束 Mock 后，真实实验建议使用**不同端口和新标签页**，避免旧标签保留 Mock 模式。先提供已有的北京工作空间 `DASHSCOPE_BASE_URL`，以及本机项目的 `DASHSCOPE_API_KEY`；变量缺失时不联网。只有 Key 没预配时，在启动命令末尾加 `--prompt-key`，在本机无回显提示里填入，切勿在聊天、命令行参数或文件里写 Key。

```powershell
.\.venv\Scripts\python.exe demo.py --port 18766 --record-dir run-v6-live-01
```

打开 `http://127.0.0.1:18766/v6`，确认页面标为 LIVE；只用虚构书页和合成儿童表达。看到回退并不代表没有真实的上游草稿：保存原始服务器记录和页面导出，不自行修改模式、补造缺失耗时或重标失败。

同一批次可用本地 HTTP 回放器自动跑完开发集（不是浏览器计时）。启动带 `--record-dir` 的服务器后，在另一窗口执行：

```powershell
.\.venv\Scripts\python.exe replay_v6.py --port 18765 --mode MOCK --record-dir run-v6-mock-01
```

真实运行只在已有凭据且确认消耗后切换成 `--mode LIVE`，将端口和目录改成对应 LIVE 服务，批量模式通常会产生 **51 次**生成请求。用 `--case dev-v6-01` 可先测一条。运行时不与浏览器同时提交，真实浏览器时延应单独按可辨的会话采集。模型调用失败或超时不静默重试；日志没有浏览器数据时，报告中的页面指标保持缺失。

离线汇总（不用再调用模型）：

```powershell
.\.venv\Scripts\python.exe eval_v6.py report run-v6-mock-01/v6-evidence.jsonl report-v6-mock
```

真实批次改为对应的 LIVE 日志，可用每个会话一次 `--page <页面导出.json>` 匹配真实页面回执；加 `--blind` 才会产生两份独立的真人评分表。只运行接口回放时不要加伪造的页面导出。评分、费用单价和独立留出集的复跑见下方评测说明。

独立留出不替换源码中的 12 个开发案例。评审确认一个外部文件**全为合成数据**并固定哈希后，以 `--v6-fixtures <独立.json> --v6-fixtures-sha256 <事先核对的SHA-256>` 启动同一入口，使用新目录。普通汇总程序不认证样本是否独立编写、不把工具输出当泛化认证。正式评审前不要把留出文件放进调试素材或公开仓库。

- [实施计划](docs/yaya-agent-2.0-v6-plan.md)
- [设计](docs/yaya-agent-2.0-v6-design.md)
- [测试报告](docs/yaya-agent-2.0-v6-test-report.md)
- [离线评测和双人盲评](docs/v6-evaluation.md)
- [冻结校验清单](v6-run-manifest.json)

---

## v5 兼容入口说明

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

## v7.1 独立演示

本包使用原有 `--v7` URL 和入口参数，实际代码版本以 `v71-run-manifest.json` 为准。只有虚构绘本及固定合成台词可用于测试。解压后进入目录：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -v
node --test test_browser.mjs test_coreading_browser.mjs
python demo.py --v7 --mock --port 18765
```

打开 http://127.0.0.1:18765/v7 。Mock 仅验证交互和记录。真跑前，将证据包中的 `holdout-v71-frozen.json` 单独解压到工作目录外，**不要用旧 25 轮集，不要提前打开审阅答案**。用本地终端运行（脚本无回显读取密钥，不从聊天取用密钥）：

```powershell
python run_v7_suite.py --base-url "https://<你的WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1" --fixture "完整路径\holdout-v71-frozen.json" --fixture-sha256 8e168a8811b11aa462bbbe65c09429f3cc2c0cb53b881b1095eddf81bf527a8a --out "..\runs-live-v71-001"
```

三批正式执行：开发 12 段 / 51 轮、专项 6 段 / 16 轮、新留出 6 段 / 19 轮，预检另计。实际请求以记录为准，空输入不调用。不要复用已有输出目录。浏览器 LIVE 测试与批跑 API 统计分开，优先记录森林星星归属、兔子寄信是否送达。当前未取得 v7.1 LIVE 数据。
