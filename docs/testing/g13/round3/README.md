# G13 第三轮联调：儿童自主阅读

第三轮首次在 [`a09f9b6`](https://github.com/MumLauds/202610-zhiya/commit/a09f9b669762d3d957be1db8ff70a17e01bc9692) 验证，随附数据取自 #52 合入后在 [`main@0fd880e`](https://github.com/MumLauds/202610-zhiya/commit/0fd880e421b533529cf800a22f71863eb8dbc47f) 的复跑。本轮验证家长授权后，儿童在同一会话中取得问题、选择作答和交回设备的流程。对应 FR-06、FR-07 和 FR-16，覆盖 AC-12 的授权、受限选项及家长解锁环节。

## 实测结果

| 步骤 | 入口 | 本轮观察 |
| --- | --- | --- |
| 创建会话并交给孩子 | 真实页面 | 家长登录、创建档案、从书架开读，在会话尚为 `CREATED` 时点击“设置并进入儿童模式”和“开启儿童模式”。授权成功后，桌面显示儿童交接页，家长导航隐藏。 |
| 开始并出题 | 同一浏览器的 API-015、API-017，随后刷新交接页 | `STARTED`、`INITIAL` 依次写入。刷新触发的 API-013 读回首题及“是／不是／不知道”三个选项，儿童读取的小结为 `null`。 |
| 选择答案与重放 | 同源 API-015 | 自由文本“其它”被拒绝（422 `VALIDATION_ERROR`）。从刷新的选项中选“是”后，服务端返回 201。立即重放和会话推进后再次重放均返回 200，响应业务数据与首次一致。SQLite 只保存了一条 `ANSWER`，答案为“是”。 |
| 已理解、跳过与恢复 | 同源 API-017，随后刷新交接页 | `UNDERSTOOD` 生成了另一道问题；`SKIPPED` 返回 `FALLBACK / SKIP`、正文为空。两次刷新均返回 200。最后一次仍可读取最近一道可展示问题的选项。整条链路模型模拟调用 2 次。 |
| 家长接回设备 | 真实页面 | 家长在交接页输入凭据并点击“退出儿童模式”，返回账号页。读取家长档案列表由儿童身份的 403 变为家长身份的 200；原活动会话转为 `INTERRUPTED`，原因为 `GRANT_REVOKED`。 |

服务端按顺序保存了 `STARTED → QUESTION → ANSWER → FEEDBACK(UNDERSTOOD) → FEEDBACK(SKIPPED)`，五个事件对应修订号 1 至 5。两次作答重放均返回最初的事件 3、修订号 3。解锁后，SQLite 联表查询确认本会话绑定授权的 `revoked_at` 已写入，会话修订号升至 6。作答正文通过数据库核对；刷新请求用于核对事件和可用选项。[脱敏实测数据](result.json) · [复跑脚本](child_flow.py)

## 运行边界

使用 Chrome 和 Vue 生产构建，在回环 HTTPS 环境运行 FastAPI、真实会话鉴权、CSRF 校验、知识检索和 SQLite。测试账号、儿童档案、绘本与知识为隔离夹具，模型供应商的 HTTP 响应由本地模拟。桌面端已有儿童授权、设备交接和家长解锁页面。出题、选项作答与反馈经该浏览器的同源请求提交；手机端或硬件端的实际儿童按钮交互需另行验证。

## 复跑

从仓库根目录运行。需要 Python 3.12、项目支持的 Node.js、桌面 Google Chrome、Git，以及可在命令行运行的 OpenSSL。脚本复用主分支随附的[本地 HTTPS 环境与测试夹具](../round2/verify.py)。在已激活的测试虚拟环境中执行以下命令。

```sh
python -m pip install -r backend/requirements-dev.txt playwright==1.65.2
npm --prefix frontend ci
npm --prefix frontend run build
python -m pytest -q -s docs/testing/g13/round3/child_flow.py
```

脚本会打印本次脱敏结果文件的路径。既有的儿童模式与交互接口用例也可单独复跑：`python -m pytest -q backend/tests/test_child_mode.py backend/tests/test_interactions.py -k child`。本轮该筛选命中 31 项，全部通过。
