# G13 第四轮：分支阅读与小结

同一家长和孩子分两次阅读《纸船停在哪里》，从第 1 页分别前往两个结局。本轮检查刷新后看到的正文，以及结束后每条会话的历史和小结。

本轮涉及 FR-17 的受控分支和 FR-09 的阅读记录。家长、孩子、绘本和两条标为 `APPROVED` 的分支均为隔离库中的测试数据。开读、刷新、完成阅读和打开成长记录走真实桌面页面。当前阅读页没有分支选择按钮，脚本从绘本详情读取分支 ID，在同一登录浏览器里携带 CSRF 令牌调用 API-018。FastAPI、SQLite 和小结生成流程均在本机运行，外部模型 HTTP 响应由 `httpx.MockTransport` 提供固定的建议编号。正式绘本审核、真实模型效果和分支按钮交互仍待验收。

## 原测结果

2026 年 11 月 19 日，原测应用为 [`main@0fd880e`](https://github.com/MumLauds/202610-zhiya/commit/0fd880e421b533529cf800a22f71863eb8dbc47f)。在 Chrome 154.0.8037.98 中，家长使用同一孩子档案连续完成两次亲子共读。刷新后的阅读页显示各自的目标页和正文。[原测数据](result.json)

| 会话 | 从第 1 页选择 | 刷新后可见的正文 | 完成后的小结事实 |
| --- | --- | --- | --- |
| 1 | 到芦苇旁 | 第 2 页「白鹭在芦苇旁接住了纸船。」 | 本次共读阅读了第 1、2 页。 |
| 2 | 去石桥边 | 第 3 页「青蛙在石桥边收好了纸船。」 | 本次共读阅读了第 1、3 页。 |

- **重放。** 两次分支选择首次均返回 201，使用原请求键和请求体重放均返回 200，响应内容与首次相同。重放后两条会话的修订号均为 2，SQLite 中各有一条开读事件和一条分支事件，会话执行记录也保持两条。
- **结束和小结。** 从页面点击完成后，API-019 均返回 200。两条会话均以 `USER_FINISH` 结束，修订号为 3。小结各自在 `PENDING` 后变为 `READY`，刷新仍可见。两次模型请求分别只包含第 1、2 页和第 1、3 页的正文。SQLite 中每个会话恰有一份小结。
- **成长记录。** 第二次结束后，页面同时显示阅读记录 #1 和 #2，两张卡片都标为「浏览 2 页 · 已完成」。再用同一登录身份分别请求两个会话的详情，两条路径和小结均与各自完成时一致。[浏览器和数据库断言](branch_flow.py)

前端生产构建通过。第四轮浏览器用例 1 项通过，后端分支、结束、历史与小结相关回归测试 29 项通过。

## 主干复测

2026 年 11 月 20 日（北京时间），在 [`main@5a0d0da`](https://github.com/MumLauds/202610-zhiya/commit/5a0d0da0a87cb6e5d99d5e8e526c723671377bf4) 上重新构建前端，用 Chrome 154.0.8037.98 和当前提交的 FastAPI 跑同一脚本。两次均使用独立于正式数据的本地 SQLite、测试家庭及绘本，以及固定返回 `REVISIT_PAGE` 的模拟模型；没有调用外部语音服务。分支选择由同一个登录浏览器调用 API-018。[复测数据](retest.json)

刷新后两个会话分别停在第 2、3 页，各自的已读路径为 `[1, 2]` 和 `[1, 3]`。两次分支请求均首次返回 201，重放返回 200，重放后修订号均为 2，事件和执行记录各 2 条。两个会话完成后的修订号均为 3，每个会话各生成一份 `READY` 小结，使用的正文与所读分支一致。浏览器用例 1 项、阅读页分支相关单测 2 项、相关后端回归 29 项均通过。

## 复跑环境

在仓库根目录使用 Python 3.12 测试虚拟环境、项目支持的 Node.js、Google Chrome、Git 和可在命令行使用的 OpenSSL。脚本复用主分支上的[回环 HTTPS 环境和测试绘本夹具](../round2/verify.py)。

```sh
python -m pip install -r backend/requirements-dev.txt playwright==1.65.2
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend test -- src/pages/ReaderPage.test.js -t 'branch'
python -m pytest -q -s docs/testing/g13/round4/branch_flow.py
python -m pytest -q backend/tests/test_interactions.py backend/tests/test_storybooks.py -k "branch or report or completion or finish or history"
```

浏览器用例在临时目录创建独立数据库和 HTTPS 证书。成功后，终端打印本次生成的 `result.json` 路径。
