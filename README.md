# 知芽

**基于领域知识增强的 AI 亲子绘本共读与阅读启蒙系统**

2026年软件工程实践课程团队项目，面向3～6岁幼儿及其家长，以家长陪伴下的绘本共读为主要使用场景。

## 当前状态

家长已能登录、选择孩子档案、开读绘本、翻页、录音确认、结束阅读并读回小结。G13 记录了这些操作以及儿童授权到期、分支阅读的本地联调结果。G15 已用测试家庭完成本机 HTTPS 部署、重启读回和备份恢复预演。正式绘本与真实模型的组合验收、公网部署仍待完成。[G13 联调记录](docs/integration.md) · [G15 部署预演](docs/testing/g15/local-https/README.md) · [G15 恢复预演](docs/testing/g15/local-restore/README.md)

原“知构”项目的代码与功能不作为本项目成果。

## 项目定位

以可独立公网使用的响应式Web软件为主体，以幼儿早期阅读领域智能体为技术重点。AI辅助家长选择绘本、开展互动和获得阅读反馈，不替代亲子陪伴。可选硬件扩展采用 Waveshare ESP32-S3-Touch-LCD-4.3C（SKU 33799，无外壳版），作为独立样机开发与验收，软件核心流程可独立运行。

## 规划功能

以下均为计划需求，具体边界与验收标准由团队在需求分析中确认：

1. 家长账号与幼儿阅读档案管理。
2. 根据年龄、兴趣与主题推荐绘本并说明理由。
3. 绘本图文展示、翻页与语音朗读。
4. 结合当前情节生成适龄互动问题。
5. 根据孩子回答或家长反馈调整阅读引导。
6. 录音、语音识别、故事问答与语音播放。
7. 阅读记录与本次活动小结。
8. 亲子延伸活动推荐。
9. 绘本与领域知识内容管理。

## 初步技术方案

- 前端：Vue 3、Vite，移动端优先的响应式Web。
- 后端：Python、FastAPI。
- 数据库：SQLite起步，后续按需要评估PostgreSQL。
- 智能体：领域知识检索增强生成（RAG）、专业提示词、工具调用与工作流。
- 模型与语音：文本使用 DeepSeek，ASR 和 TTS 使用阿里云百炼。两套密钥独立，真实供应商已通过短句联调。G13 和 G15 各轮使用的模拟依赖见对应记录。[短句联调记录](docs/testing/live-provider-checks.md)
- 部署：本机预演使用 Docker Compose 和同源 HTTPS，公网环境待验收。

## 本地启动

使用 Python 3.12（SQLite 3.38+）和 Node.js 20.19+（20.x）或 22.12+。在仓库根目录启动后端：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe database/init_db.py
.\.venv\Scripts\python.exe database/import_seed.py
if (-not (Test-Path -LiteralPath backend/.env)) { Copy-Item -LiteralPath backend/.env.example -Destination backend/.env }
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000 --env-file backend/.env --no-access-log
```

在另一个终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

页面默认在 http://127.0.0.1:5173，通过 Vite 代理读取 API-032 的就绪状态。带凭据的浏览器联调使用 HTTPS，因为认证 Cookie 带有 Secure 属性。儿童独立阅读支持限时授权和家长密码解锁。后端已接入自适应引导、分支选择、绘本草稿生成与家长审批。主动问答（API-016）和管理员绘本、知识管理（API-027～031）仍待实现。[后端说明](backend/README.md)

数据库默认位于 `database/instance/zhiya.sqlite3`，可在运行命令前设置 `ZHIYA_DB_PATH`。空种子表会导入数据，种子键齐全时会按规则回填符合条件的旧草稿配图，表非空且种子键不完整时退出。首次导入后，家长书架可读 20 本团队原创课程绘本；联调用《逃家小兔》和 20 条知识保留为 `DRAFT`。[种子导入说明](data/seed/README.md)

在根目录检查数据库及运行测试：

```powershell
.\.venv\Scripts\python.exe database/init_db.py --check
.\.venv\Scripts\python.exe -m unittest discover -s database/tests -v
.\.venv\Scripts\python.exe -m pytest backend/tests -q
npm --prefix frontend run build
```

## 项目资料

- [后端与模型接入说明](backend/README.md)
- [G08 部署初稿](infra/README.md)
- [G08 最新容器验收与交接边界](docs/testing/docker-checks.md)
- [G08 公开语音接口、录音转换与实名分工](docs/design/voice-api-handoff.md)
- [公开语音与转码最终检验](docs/testing/voice-api-checks.md)
- [G08 声线与模型合成语速交接](docs/design/speech-preferences-handoff.md)
- [声线语速实测与最新容器检查](docs/testing/speech-preferences-checks.md)
- [六声线试听与独立交付验证](docs/testing/g08-live-delivery-checks.md)
- [G13 系统联调记录与复测步骤](docs/integration.md)
- [知芽软件工程团队项目选题报告（立项版本）](docs/知芽-软件工程团队项目选题报告.docx)
- [软件需求规格说明书 v0.3（待团队评审）](docs/requirements/软件需求规格说明书.md)
- [项目设计与开发规范 v1.2（修订评审稿）](docs/standards/知芽_项目设计与开发规范_精简版_v1.2.md)
- [公共术语与枚举](docs/standards/glossary.md)
- [设计变更与冲突决策记录](docs/standards/design-change-log.md)
- [系统与数据库设计 v1.1](docs/design/team-lead-handoff-v1.1.md)
- [接口契约 v0.4](docs/design/api-contract.md)
- [种子数据与导入说明](data/seed/README.md)
- [知识库资源交付 v2.5](knowledge-base_v2.5/资源交付说明_v2.5.md)
- [交互原型入口](prototype/README.md)
- [4.3C 硬件工程与验收说明](hardware/README.md)
- [P6 软件测试文档](docs/testing/p6/P6-测试文档.md)
- [P6 验收演示脚本](docs/testing/p6/P6-演示脚本.md)
- [P6 测试与验收程序](qa/p6/README.md)

选题报告保留立项方案。需求说明书 v0.3 记录当前 4.3C 硬件方案，仍待团队评审。功能范围、优先级及验收指标以团队评审形成的需求基线为准。

## 主要目录

```text
202610-zhiya/
├── docs/
│   ├── 知芽-软件工程团队项目选题报告.docx
│   ├── requirements/     # 需求说明、图源与图片
│   └── design/           # 设计说明、接口契约与 UML 图源
├── data/seed/            # 种子数据与导入说明
├── hardware/             # 4.3C 可选硬件工程与上板说明
├── knowledge-base_v2.5/  # 绘本、知识条目与封面
└── prototype/            # 原型入口与静态后台源码
```
