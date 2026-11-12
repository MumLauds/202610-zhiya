# 知芽

**基于领域知识增强的 AI 亲子绘本共读与阅读启蒙系统**

2026年软件工程实践课程团队项目，面向3～6岁幼儿及其家长，以家长陪伴下的绘本共读为主要使用场景。

## 当前状态

仓库包含需求与设计文档、接口契约、种子数据、交互原型及前后端与数据库骨架。当前工作区已接入 DeepSeek 内部文本模型与百炼语音适配模块，并通过真实短句接口联调；业务主链路、模型效果验收及正式部署待交付。原“知构”项目的代码与功能不作为本项目成果。

## 项目定位

以可独立公网使用的响应式Web软件为主体，以幼儿早期阅读领域智能体为技术重点。AI辅助家长选择绘本、开展互动和获得阅读反馈，不替代亲子陪伴。当前工作范围仅包含软件，暂不安排硬件开发。

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
- 模型与语音：通过云端API调用大语言模型、ASR和TTS；文本使用 DeepSeek 官方服务，语音使用阿里云百炼，两套密钥独立；[真实短句接口联调已通过](docs/testing/live-provider-checks.md)，业务端到端流程仍待验证。
- 部署：拟采用Linux云服务器、Docker Compose与HTTPS，尚未部署。

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

页面默认在 http://127.0.0.1:5173，通过 Vite 代理读取 API-032 的真实就绪状态。当前分支新增录音转换和语音接口，业务认证尚待接入，默认拒绝语音请求；其他业务接口与页面功能待后续开发。数据库默认位于 `database/instance/zhiya.sqlite3`，可在运行命令前设置环境变量 `ZHIYA_DB_PATH`。导入只在空种子表写入，已存在全部种子键时跳过，种子键缺失且表非空时退出。绘本版本和知识初始状态为 `DRAFT`。

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
- [知芽软件工程团队项目选题报告](docs/知芽-软件工程团队项目选题报告.docx)
- [软件需求规格说明书 v0.2（待团队评审）](docs/requirements/软件需求规格说明书.md)
- [项目设计与开发规范 v1.1（修订评审稿）](docs/standards/知芽_项目设计与开发规范_精简版_v1.1.md)
- [公共术语与枚举](docs/standards/glossary.md)
- [设计变更与冲突决策记录](docs/standards/design-change-log.md)
- [系统与数据库设计 v1.1](docs/design/team-lead-handoff-v1.1.md)
- [接口契约 v0.4](docs/design/api-contract.md)
- [种子数据与导入说明](data/seed/README.md)
- [知识库资源交付 v2.5](knowledge-base_v2.5/资源交付说明_v2.5.md)
- [交互原型入口](prototype/README.md)

选题报告与需求说明书保留原始内容；需求说明书v0.2仍为待团队评审草案。其中新增的儿童自主阅读、原创绘本和故事分支等属于拟议需求，不代表已经确认或实现。功能范围、优先级及验收指标以团队评审形成的需求基线为准。

## 主要目录

```text
202610-zhiya/
├── docs/
│   ├── 知芽-软件工程团队项目选题报告.docx
│   ├── requirements/     # 需求说明、图源与图片
│   └── design/           # 设计说明、接口契约与 UML 图源
├── data/seed/            # 种子数据与导入说明
├── knowledge-base_v2.5/  # 绘本、知识条目与封面
└── prototype/            # 原型入口与静态后台源码
```
