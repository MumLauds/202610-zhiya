# P6 测试与验收程序

本目录是知芽 P6 的测试程序与证据生成入口，覆盖：

- 黑盒接口验收：账号、档案、绘本、推荐、阅读会话、分支、儿童模式、语音鉴权、异常与性能；
- 领域模型对比：24 个案例，通用模型 vs 知芽领域条件，支持两位评分人独立打分；
- 缺陷跟踪：人工确认的缺陷 + 自动失败用例，输出 Markdown / CSV；
- 公网验收：HTTPS、证书、Cookie 属性、安全响应头、契约覆盖与演示账号全流程；
- 演示脚本：`docs/testing/p6/P6-演示脚本.md`。

## 快速开始（本地）

```bash
# 1. 在仓库根目录创建虚拟环境并安装后端依赖
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt

# 2. 一键：准备演示数据 → 起服务 → 跑验收 → 生成模型对比模板与缺陷表
.venv/Scripts/python.exe qa/p6/run_p6.py --start-local --db qa/p6/instance/zhiya.sqlite3
```

首次运行会创建数据库并导入 20 本已发布的课程原创，默认给其中 12 本准备演示分支、审核发布 20 条演示知识、创建演示账号
`p6demo / Zhiya-demo-2026`。数据库默认位于 `qa/p6/instance/zhiya.sqlite3`。脚本只发布与仓库种子逐项一致的候选绘本、页面和知识，
也可用 `--db` 指定到仓库外，避免提交数据库文件。一键入口会继续完成自己上次中断的准备事务。对没有准备标记、仅有种子、尚未创建演示账号的库，请先确认用途，再执行 `python qa/p6/prepare_demo_data.py --db <数据库路径> --yes`。

准备命令输出的 `publishedCount` 和 `storyVersionIds` 只统计本轮由草稿转为已发布的绘本；`demoBranchesCreated` 统计本轮实际新增的演示分支。书架的已发布总数见 `check.publishedVersions`。

## 分步执行

```bash
# 准备数据（会修改数据库，必须显式 --yes）
python qa/p6/prepare_demo_data.py --db qa/p6/instance/zhiya.sqlite3 --yes

# 启动后端（另开终端）
ZHIYA_DB_PATH=qa/p6/instance/zhiya.sqlite3 \
  .venv/Scripts/python.exe -m uvicorn app.main:create_app --factory \
  --app-dir backend --host 127.0.0.1 --port 8765 --no-access-log

# 黑盒验收（本地 HTTP 需 --allow-http 才能回传 Secure Cookie）
# 本地未配置 TTS 时加 --voice-expect-unconfigured 断言 503；不调用付费服务
python qa/p6/run_acceptance.py --base-url http://127.0.0.1:8765 \
  --profile local --allow-http --voice-expect-unconfigured

# 模型对比：默认 dry-run，不产生调用
python qa/p6/run_model_compare.py
python qa/p6/summarize_model_compare.py

# 缺陷表
python qa/p6/track_defects.py
```

只汇总人工缺陷时，运行 `python qa/p6/track_defects.py --manual-only`。

## 公网验收

```bash
python qa/p6/run_acceptance.py \
  --base-url https://your-domain.example \
  --http-url http://your-domain.example \
  --profile public \
  --account p6demo \
  --frontend-url https://your-domain.example
```

已有账号的密码可通过环境变量 `P6_ACCEPTANCE_PASSWORD` 传入。外部部署可用 `--tested-commit` 记录实际版本。公网模式要求启用证书校验的 HTTPS；脚本会检查证书链、HTTP 入口是否跳转到被测 HTTPS 服务的主机和端口、HSTS、Cookie 的
`Secure/HttpOnly/SameSite` 属性、演示账号主流程与响应时间。`--frontend-url`
用于检查前端页面是否可访问；未提供时前端验收记为 BLOCKED，不会写成通过。前端代码另有 71 项 Vitest，可通过 `npm --prefix frontend test` 执行。

## 模型对比

`model_cases.json` 含 24 个案例（EVAL-01～08 的扩展集），覆盖 3～6 岁三个年龄段、
事实问答、跨页未知、未理解引导、安全边界、原创故事与分支连贯性。

真实执行需要：

```bash
export MODEL_API_KEY='...'       # 不要写进仓库
python qa/p6/run_model_compare.py --live --db qa/p6/instance/zhiya.sqlite3
python qa/p6/summarize_model_compare.py
```

默认条件为 `baseline,domain`（通用模型 vs 领域条件）。如需消融实验，加
`--conditions baseline,page_only,domain`。每次调用都会计费，脚本默认最多 48 次，
超过需显式调高 `--max-calls`。`--limit N` 取前 N 例，N 必须是正整数。条件列表中的每项都须有效且唯一。结果写入 `reports/model-compare/results-*.jsonl`，
首次汇总会为成功且非空的输出生成独立评分表，文件名以 `scoring-` 开头并包含运行标识。两位评分人填写该表后，用同一个 `--results <结果 JSONL 路径>` 参数再次汇总。换运行或输出发生变化时使用新表，原有人工评分和备注保存在原表中。通用与领域两种条件都有本轮成功输出，且两位评分人填完全部维度后，才计算配对胜平负。dry-run 生成的 `scoring.csv` 供预览评分格式。

计划、运行元数据和每条结果都记录同一个 SHA-256 指纹，覆盖完整案例、条件和评分维度。旧结果缺少指纹时可导出原始内容供查阅，重新运行后即可评分。汇总遇到残缺的评分表时会保留原文件，并打印补齐后的 `-recovered.csv` 路径，请在该副本继续评分。

仅生成计划且没有结果文件时，执行和汇总均返回 0。实际运行或汇总没有可评分输出时返回 1，原始结果保留供排查。非法调用范围或计划与结果不匹配时返回 2。

## 产物

| 路径 | 内容 |
| --- | --- |
| `reports/acceptance-latest.json` | 最近一次验收的机器可读结果 |
| `reports/acceptance-latest.md` | 用例明细与验收项汇总 |
| `reports/acceptance-*.xml` | JUnit 格式，可接入 CI |
| `reports/model-compare/` | 评测计划、评分模板、原始输出与汇总 |
| `reports/defect-ledger.md`、`reports/defect-ledger.csv` | 最近发布的缺陷台账。并发复测时，从 Markdown 末尾的两个链接获取同一轮的 Markdown 和 CSV 归档。 |
| `docs/testing/p6/P6-测试文档.md` | 提交用测试文档 |

## 边界与注意

- `run_p6.py --outdir <目录>` 将黑盒报告、缺陷台账和模型子目录写入同一轮输出。多轮共用目录时，先读 `acceptance-latest.json` 中的 `meta.runId`，再按 `acceptance-<runId>.json/.md/.xml` 获取同轮的三份归档。
- 导出 Word 时，先在运行报告脚本的同一虚拟环境执行 `python -m pip install -r qa/p6/requirements-docs.txt`。仅导出 Markdown 可跳过此依赖。
- `python qa/p6/build_test_doc.py --acceptance <JSON>` 在该 JSON 旁的 `retests/` 下生成本轮 Markdown 和 Word 报告，文件名包含输入指纹，日期和提交取自运行记录。可用 `--markdown <新路径>`、`--docx <新路径>` 指定输出，或用 `--no-docx` 仅生成 Markdown。11 月 18 日交付的文档、演示脚本及 PDF 保留原版。
- 黑盒脚本不导入后端代码，不读取 `.env`，不接触真实密钥。
- 一键入口和演示默认跳过已鉴权的有效合成请求。明确确认被测服务禁用 TTS 后，传 `--voice-expect-unconfigured` 验证 503；真实语音使用 `--voice-live`（演示为 `--voice`），遇 503 不能判为真实合成通过。
- 一键启动会核对本次进程的健康响应，并记录被测 Git 提交。本地服务、数据库输入或验收脚本有未提交变动时，提交号附加 `-dirty`。端口被占用时，可用 `--port` 选择其他本地端口。仅 `--text-model-live` 允许它启动的后端用真实文本模型生成阅读小结。已有外部服务沿用部署方的模型配置。
- 登录遇到 429 时，独立检查继续执行，依赖登录的用例逐条记录为阻塞。退出码 1 表示有用例失败，2 表示环境或核心流程受阻。0 表示核心流程已执行且无失败，仍需查看报告中的专项阻塞和警告。
- 指定 `--story-version-id` 时按版本号读取详情，版本不可用会中止该绘本的验收或演示。准备演示数据时，已有同名账号须与所需角色一致。
- 结束阅读后，验收会在 `--timeout` 秒内读取会话详情，记录小结终态，并核对已就绪活动的材料和步骤。
- 儿童模式 API-020 会吊销当前设备的家长会话，脚本为此使用独立客户端；手工演示时
  请先用另一台设备/另一个浏览器保留家长入口。
- 新库导入后即可阅读 20 本课程原创。旧库保留原有审核状态。

- Windows 系统代理可能接管本机 POST 请求并返回 422；测试程序默认 `trust_env=False`。如确需代理，设置 `ZHIYA_USE_ENV_PROXY=1`。

## 处理 2026-11-18 的归档会话

原始验收报告来自测试人员本机的临时 SQLite 库。两份修订归档只记录 Cookie 属性。旧提交中的会话有效期为登录后 24 小时。在生成报告的机器上可执行下列命令，向原数据库撤销该条测试会话；已过期或已撤销时不会更新任何行。命令只输出状态。

```bash
git show f195a60:qa/p6/reports/acceptance-latest.json | python qa/p6/revoke_archived_session.py --db <原验收数据库路径>
```
