# H08 设备网络层

H08 是知芽可选扩展 E 的独立诊断固件，基于 main `48844b2ee267bc539ed0580a358470beca13e449`。诊断工程复用既有 H01 自检、H02 触摸页、4.3C BSP 和 4 MiB factory 分区。ESP-IDF 固定 v5.5.5，LVGL 固定 9.2.0。板级参考为微雪 `44e412f08c1e68c0da826bc5f51436f156a02eea`。正式设备业务联调状态为 **BLOCKED_BY_BACKEND**。

## 契约对照

- 核对仓库协作与开发规范、SRS、接口契约 v0.4 与 JSON、H00 决策、H01/H02 板测报告、H04 流程、H05 server/fixture、硬件详细设计与 11 月 27 日交接。代码核对覆盖 FastAPI 路由注册、认证、书架、阅读会话、引导、语音与受控媒体、数据库会话字段及移动端请求/恢复、桌面端阅读调用。
- API-008/009 的现有服务器版本对象可带 `pages`，H05 书架故意只发送轻量条目。翻页字段为 `page` 和 `pageSize`，实际响应没有总数。H08 可读两种结构，版本页数可从实际 `pages` 取得。显式的 `totalPages` 与详情数组长度须相符。H05 的 `localPageAsset.devicePath` 是本地资源索引，不进入 HTTP URL 构造。空媒体引用保留为空。
- API-024 已有受控图片和音频读取，媒体存在格式协商，后续不能假定远程资源都是 JPEG。API-011/013/015 保留固定版本、会话 revision、原 `clientEventId` 和写入请求体。当前认证仍以现有家长会话及获准儿童身份为主，设备凭证签发和范围校验由 H09/H10 决定。H08 正式模式在建立连接前拒绝。
- H05 的 504 注入在分发前返回。H08 可选测试包装器把已经创建并记录幂等键的成功响应替换为 504，主机测试通过会话查询和同键重试核对只新增一次。原 H05 测试和默认监听保持原样。

## Wi-Fi、时间和请求所有权

串口任务管理 Wi-Fi 命令和事件。使用 STA、默认 ESP event loop、默认 IPv4 DHCP 和 RAM 存储。NVS 初始化失败原样报告，保留分区内容。启动前检查已有 Wi-Fi 所有者，事件处理仅投递状态。关联和 DHCP 各设 12 秒限时，连续失败最多重连 4 次，延时依次为 0.5、1、2、4 秒，叠加 0–250 ms 抖动。获得 IP 后重置重连计数。断网或主动停止会清除在线及校时状态。主动停止由控制任务释放 Wi-Fi 驱动和 SNTP，并等待 STOP 事件后允许再次加入。

收到 IP 后通过 `esp_netif` SNTP 同步 `pool.ntp.org`。读取到同步信号且系统时间有效后才开始 TLS。实验室须允许该时钟源，时钟不可用返回 `CLOCK`。证书时间检查在本诊断配置显式开启。

HTTP 由单独 FreeRTOS 任务拥有。每次仅有一个活动请求，命令队列、回执队列各一槽。取消与结果绑定请求编号，下一请求等当前结果收尾后提交。DNS 使用一个常驻解析任务和一槽收发队列，丢弃已过期的解析编号。已有 DNS 查询可能在后台完成，下一 DNS 查询等待该任务空闲并继续遵守自己的截止时间。连接、读写、退避和取消共享同一绝对截止时间。TCP 建连的单次 SDK 调用上限为 2.5 秒，取消在该调用返回或 I/O 检查点生效。LVGL 没有网络调用和跨任务写控件。

实现选用 ESP-TLS 与 SDK 的 `http_parser`，以小块读取驱动 HTTP/1.x framing。DEMO 两个模式均限解析后的私有 IPv4，HTTP 由用户显式选用。HTTPS 的 SNI 与证书身份使用原 URL 主机，连接使用已解析 IP。默认信任 ESP-IDF CA bundle，测试镜像可通过专用参数选择一张本地公开 CA，仅用于 DEMO HTTPS。证书链、SAN/主机名和有效期均保留校验。HTTP 不跟随重定向，HTTPS 无明文回退。正式模式禁止测试 CA 和错误注入头。参考 [ESP-TLS v5.5.5](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32s3/api-reference/protocols/esp_tls.html) · [ESP-NETIF v5.5.5](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32s3/api-reference/network/esp_netif_programming.html)。

## 解析边界和错误语义

| 项目 | 当前限额与行为 |
| --- | --- |
| 请求 | URL 500 B，主机 127 B，路径与查询 383 B，JSON POST 2 KiB。DEMO 故障名限 48 个安全字符。 |
| 响应 | HTTP 头区 8 KiB，单个名称 47 B、值 255 B。Content-Type 95 B、请求 ID 80 B。SDK 识别 Content-Length、分块和关闭定界。截断或异常 framing 报错。 |
| JSON | 响应体 32 KiB，深度 12，词法节点 2,048，单容器最多 128 项。校验 UTF-8、数字词法、字段类型和重复键。业务整数为 0/正整数十进制并逐位核对 53 位安全范围，null 按字段语义处理。 |
| DTO | 书架最多 20 本，详情最多 32 页。标题 128 B、正文和简介 768 B、媒体键 256 B。(storybookId, storyVersionId, versionNumber, readingSessionId, pageNumber, revision) 各自保留字段语义。 |
| 流 | 媒体流使用调用方给定字节上限和分块消费回调。调用方负责处理半途失败，不自动重放已开始的流。 |

HTTP 状态和契约错误码同时保留。401、403、404、409 分开报告，409 进一步区分会话、幂等冲突。GET JSON 只对 DNS、连接、读失败或明确可重试的 503 再试一次，退避 180 ms 且纳入原截止时间。POST 不自动重试。开始发送后的断线、取消、错误代理响应、5xx 或显式结果未知保守返回 `REQUEST_OUTCOME_UNKNOWN`，保留起因和请求号。H11 用原请求体、键和修订号按正式契约查询恢复。

## 内存和集成交接

- H02 RGB 帧缓冲约 768,000 B PSRAM。H08 另分配 32,769 B PSRAM 响应缓冲，书架/详情 DTO 在 PSRAM 临时分配，Mbed TLS 使用外部内存配置。HTTP 与 DNS 任务栈预算分别为 12 KiB 和 4 KiB，仍占内部内存。JSON 树使用 cJSON 默认分配器，并受响应、深度及节点上限共同限制。
- 串口在请求提交、完成和 `status` 时打印 PSRAM、内部及 DMA 的 free/min/largest 和 HTTP 任务栈余量。min 是系统累计低水位。TLS 峰值、反复断网后的碎片及 H06/H07 同时运行的余量待实板测量。DMA 是内部堆子集。
- H08 新增独立诊断 CMake、配置和依赖锁，未产生共享启动文件改动。后续在 H06/H07 合并协调分支统一启动模式，再复用 `device_api`；每种模式单独构建和复验 H02。H06 可将书架/详情 DTO 投影到显示数据源。H09/H10 接入范围明确的凭证提供者；H11 负责恢复；H12 校验媒体类型和流式大小；H13 另加经批准的有界上传和语音流程。
