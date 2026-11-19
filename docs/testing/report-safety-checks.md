# 阅读小结安全与文档对齐检验

版本 v0.1；日期 2026-11-17；基线 `6ae0d50`，对应 DCL-026。本记录是离线修复验证，不代表完整 G08/G10/G13/G15 或 AI-06 验收。

## 修复与边界

旧实现只判断模型成功、非空及最多 500 字符。先新增回归用例，注入“正确回答十道问题，阅读能力低于同龄儿童。”，在旧实现中复现测试失败：接口和数据库将无依据内容当作 READY。

修复后，事实仅来自服务端已读页记录；模型根据已读正文选择 NONE、REVISIT_PAGE 或 TALK_ABOUT_STORY。服务端仅接受一个 suggestionId 字段，按固定文本组装温和、自愿的共读建议。页面正文和模型自由文本均不能直接进入 summary，不能生成儿童表现和能力诊断。输出不符合限制时为 FAILED，保留原事实摘要，activities 为空。模型成功返回 NONE 可生成仅含事实的 READY。

公共接口、数据库结构、权限、并发及幂等语义不变。已保存的历史 READY 不由本次修复追溯改写；本轮仅使用临时测试库，未改用户数据。固定建议不等于含材料和步骤的活动，FR-10/AC-09 仍未完成。后续如恢复自由文本小结，必须先提供可核实的事实对应关系及内容安全校验，不能仅放宽此处白名单。

## 用例与复现

| 编号 | 对应要求 | 覆盖 |
| --- | --- | --- |
| TC-REPORT-SAFETY-001 | FR-09、AI-06 | 三种合法选择只生成真实页码事实和固定建议；未读页、儿童投入程度不由模型补写 |
| TC-REPORT-SAFETY-002 | AI-06、NFR-09 | 虚构答题数/阅读页数/词汇掌握、能力诊断、危险建议、夹带字段、未知编号、错误类型、空值和超长输出均拒绝；数据库与 API 均保留事实摘要 |
| TC-REPORT-SAFETY-003 | FR-09 | 拒绝后同键重放原 PENDING 响应、新键读取当前 FAILED，不额外调用模型；复用已有并发、启动恢复及权限测试 |

从仓库根目录使用已安装 `backend/requirements-dev.txt` 的 Python 环境运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/tests/test_storybooks.py -q
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/tests -q
node --check prototype/admin-html/app.js
git diff --check
```

本次借用相邻旧工作区现有虚拟环境运行新工作区源码，不创建或修改 `.env`。结果如下：

- 旧实现复现：新增回归首例失败，确认为无依据文本被保存为 READY。
- 修复后定向检验：30 passed。
- 全量后端：160 passed、191 subtests passed，零跳过（130.90 秒）。仅有现有 Starlette/httpx 弃用提示，不影响测试通过，本轮未调整依赖。
- `node --check prototype/admin-html/app.js`、`git diff --check` 通过，契约 JSON 可解析。
- 自动比对原型面板 7 条接口的方法、路径和权限，全部与契约 Markdown 一致。

测试使用本地临时 SQLite 和注入适配器，没有真实供应商调用。

## 文档核对

根与后端 README 按已注册路由说明真实家长认证、会话创建/事件/结束及持久幂等；保留儿童认证、图片媒体、问答/引导和管理员接口的未实现边界。后台原型接口面板对照 API-017/019/027～031 校正方法、路径与权限，并注明契约权限不代表已实现的儿童支持。

未验证真实模型的结构遵循率、人工适龄效果、浏览器完整业务流程和线上部署。不将离线通过计作完整 AI 评测。
