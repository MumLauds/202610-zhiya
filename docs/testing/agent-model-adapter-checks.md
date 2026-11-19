# G08 Agent 模型接口对接检查

日期：2026-11-12；负责人：林鑫（后端2）；基础提交：8c4172c，本地未提交。关联 FR-12、NFR-04/05/06、AC-10（局部）、IF-02；变更记录 DCL-016。PR 指 GitHub 合并请求（Pull Request）；API 指应用程序编程接口（Application Programming Interface）。

## 范围与依据

按 PR #27 的 aa13025 接口快照增加 ModelAdapter，复用 ModelGateway，并注入同一应用生命周期。[调用说明](../../backend/README.md#对接-pr-27-的-agent-模型接口)包含输入组织方式、错误映射及跨模块待确认事项。Schema 文件与该提交字节一致，未自行修改 Agent/知识结构，也未合并 PR。

## 离线结果

从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/tests -q
```

结果：55 项测试、574 个子用例通过，保留一条既有 Starlette/httpx 弃用提示。新增 7 项测试位于 backend/tests/test_model_adapter.py，均使用模拟传输或模拟对象，不读取 .env、不调用供应商。

| 用例 | 验证结果 |
| --- | --- |
| TC-MODEL-ADAPTER-001 | PR 请求模型可直接调用，返回 PR 结果模型；超过 200 字的上下文完整传入 user 消息，系统规则保持独立；仅一次请求 |
| TC-MODEL-ADAPTER-002 | HTTP 401/429/500、网络错误及畸形响应映射 API_ERROR；传输超时映射 TIMEOUT；失败 text 为空，不回传上游内容、不重试 |
| TC-MODEL-ADAPTER-003 | 总期限取消耗时调用，返回 TIMEOUT；仅一次请求 |
| TC-MODEL-ADAPTER-004 | 空密钥返回 API_ERROR，供应商请求次数为零 |
| TC-MODEL-ADAPTER-005 | 空问题、超长问题、非法 Unicode 上下文保留 INVALID_INPUT，不调用供应商 |
| TC-MODEL-ADAPTER-006 | 任务取消和程序异常正常向上传播，不返回伪成功或降级结果 |
| TC-MODEL-ADAPTER-007 | 应用暴露 model_adapter，并复用既有 model_gateway |

原有文本、语音和健康检查回归一并通过；旧网关未传上下文时的请求格式保持兼容。

## 未验收与交接

未新增付费调用；新上下文格式的真实模型效果、更新后容器及 Agent 端到端尚未验证。INITIAL 空问题策略、API_ERROR 最终降级、检索、安全校验、授权与幂等执行、事件保存和媒体访问仍由对应模块协作；不以本次适配测试替代这些验收。保持 20 秒以内总预算和无自动重试，不替调用方决定新的计费尝试。

## 飞书进展

已在 G08 下新增独立子记录“PR #27 模型接口适配与离线验证通过”（reczz28YdZSiK4LA），填写交付物、负责人备注、测试结果与未验收边界，核对状态为“已完成”并完成云端保存。原子记录及父任务进度保持不变；此完成状态仅指本次接口适配与离线验证。

后续容器验证：2026-11-12 已核对当前 Schema/适配器打包摘要，并在空密钥容器验证 API_ERROR 返回结构，见[复查记录](backend-review-checks.md)。真实上下文效果与 Agent 端到端仍未验收。

后续真实验证：同日完成三个固定案例各两次的无/有上下文对比，原始输出与效果判定见[独立交付验证](g08-live-delivery-checks.md)。有上下文组本次 3/3 符合检查点，无上下文组存在失败和待复核项；不替代完整 Agent 效果、安全或端到端验收。
