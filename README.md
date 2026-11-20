# 知芽

**基于领域知识增强的 AI 亲子绘本共读与阅读启蒙系统**

2026 年软件工程实践课程项目，面向 3～6 岁幼儿及其家长。家长可以为孩子选择绘本，一起翻页、听朗读、围绕当前画面提问，读完后查看本次共读的小结与延伸活动。

## 在线体验

| 入口 | 使用方式 |
| --- | --- |
| [知芽主站](https://zhiya.47-238-246-210.sslip.io/) | 注册或登录家长账号，创建儿童档案，浏览绘本、开始阅读或查看成长记录。内容管理员使用自己的账号登录工作台。 |
| [手机亲子共读](https://zhiya.47-238-246-210.sslip.io/mobile.html) | 用手机浏览器打开。家长登录后会回到移动首页；只有一个儿童档案时自动选中，多个档案可按昵称选择。点绘本封面即可开始共读。 |

首个正式版本 **v0.1.0** 对应提交 `5c15440`。该版本已通过主线测试和香港站公网部署验收。线上站点随 `main` 更新，可在部署回执中核对当前提交号 `revision` 和 Actions 运行号 `run`。[版本说明](https://github.com/MumLauds/202610-zhiya/releases/tag/v0.1.0) · [本版验收记录](https://github.com/MumLauds/202610-zhiya/actions/runs/44756429704) · [当前部署回执](https://zhiya.47-238-246-210.sslip.io/_deployment/current.json)

## 主要功能

- **家长与儿童档案**　按年龄、兴趣和主题选书，查看推荐理由、历史阅读和成长记录。家长可以开启限时儿童阅读，之后用家长密码解锁。
- **绘本共读**　支持图文翻页、语音朗读、主动问答和分支选择。阅读引导结合当前页和孩子的回答调整，结束会话后可查看小结与亲子延伸活动。
- **家庭创作与内容管理**　家长可以生成原创或续写草稿并审核自己的作品。内容管理员维护资料库绘本、绘本版本和带来源的领域知识。
- **课程绘本**　新库首次导入后，书架上有 20 本团队原创绘本，共 104 页，供非商业课程学习与演示。《逃家小兔》及 20 条知识种子保留为草稿。[种子与许可说明](data/seed/README.md)

软件可独立运行。可选硬件使用 Waveshare ESP32-S3-Touch-LCD-4.3C（SKU 33799，无外壳版），按独立样机验收。课程交付范围沿用选题报告，原“知构”项目的代码与功能不计入本项目成果。[硬件工程说明](hardware/README.md)

## 技术与部署

- **应用**　桌面端和移动端使用 Vue 3、Vite；FastAPI 提供账号、档案、绘本、阅读会话和受控媒体接口，业务数据保存在 SQLite 中。[前端说明](frontend/README.md) · [后端说明](backend/README.md)
- **模型与语音**　阅读引导结合领域知识检索与 DeepSeek 文本模型，语音识别和合成使用阿里云百炼。两套服务分别配置密钥。供应商短句实测和各轮联调的依赖配置记录在相应测试文档中。[供应商联调记录](docs/testing/live-provider-checks.md) · [系统联调记录](docs/integration.md)
- **自动部署**　面向 `main` 的 PR 运行测试和公开版构建。合并后，香港 ECS 定时检查已通过验证的主线提交，备份 SQLite、切换应用镜像并生成回执。GitHub Actions 从公网核对版本、健康接口和匿名后台访问权限。[部署与恢复说明](infra/hk/README.md) · [工作流](.github/workflows/deploy-hk.yml)

## 本地启动

以下命令使用 Windows PowerShell、Python 3.12（SQLite 3.38+）和 Node.js 22.12+（22.x）。先在仓库根目录初始化数据库、导入种子并启动后端。

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

默认开发地址为 `http://127.0.0.1:5173/`，Vite 将 `/api` 代理到本机 8000 端口。移动端开发入口为 `/mobile.html`，默认显示演示内容。要在本机使用与公网一致的移动首页，把上述 `npm run dev` 换成 `npm run dev -- --mode public`。涉及登录 Cookie、手机麦克风和同源 HTTPS 的联调，可按本机预演步骤启动环境。[HTTPS 部署预演](docs/testing/g15/local-https/README.md)

数据库默认位于 `database/instance/zhiya.sqlite3`。需要独立数据库时，在初始化、导入和启动后端前将 `ZHIYA_DB_PATH` 设为同一绝对路径。调用真实模型和语音服务前，按示例填写本机 `backend/.env`。[配置示例](backend/.env.example)

## 测试与构建

完成依赖安装后，在仓库根目录运行以下检查。前端检查依次运行 Vitest、移动端 Node.js 测试和公开站点构建。只需重新打包时可运行 `npm --prefix frontend run build:public`。公开版产物位于 `frontend/dist/`，包含主站和移动端入口，两端书架均读取后端。

```powershell
.\.venv\Scripts\python.exe database/init_db.py --check
.\.venv\Scripts\python.exe -m pytest -q -c backend/pytest.ini backend/tests database/tests
npm --prefix frontend run check
```

课程演示中的手机实测、业务流程验收及数据库冷恢复各有单独记录。[课程候选版演示与验收步骤](docs/testing/p6/课程候选版演示.md) · [G15 恢复预演](docs/testing/g15/local-restore/README.md)

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
├── frontend/            # Vue 主站（src/）和手机共读入口（mobile/）
├── backend/             # FastAPI、阅读服务与后端测试
├── database/            # SQLite 结构、初始化与种子导入
├── infra/               # 容器、本地部署及香港站发布脚本
├── qa/                  # 课程验收程序
├── docs/
│   ├── 知芽-软件工程团队项目选题报告.docx
│   ├── requirements/     # 需求说明、图源与图片
│   └── design/           # 设计说明、接口契约与 UML 图源
├── data/seed/            # 种子数据与导入说明
├── hardware/             # 4.3C 可选硬件工程与上板说明
├── knowledge-base_v2.5/  # 绘本、知识条目与封面
└── prototype/            # 原型入口与静态后台源码
```
