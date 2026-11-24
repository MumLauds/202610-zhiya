# 知芽 H01/H02 实板调试与验收报告

- 团队：妈妈夸我说的队
- 项目：知芽——幼儿绘本学习智能体
- 硬件负责人：黄锦培
- 测试日期：2026 年 11 月 24 日
- 版本：v1.1

## 1. 调试目标

本阶段完成知芽硬件端从工程初始化到实板显示、触摸交互的基础验证。H01 主要检查开发环境、固件烧录、芯片启动及 Flash、PSRAM；H02 主要验证 4.3C 板级显示和触摸驱动、LVGL 基础界面及初始化内存余量。通过分阶段验证，为后续绘本阅读、音频和联网功能建立可用的硬件基础。

## 2. 测试环境与版本

| 项目 | 配置 |
| --- | --- |
| 开发板 | Waveshare ESP32-S3-Touch-LCD-4.3C，SKU 33799，无外壳版（选型记录）；实物型号已核对，PCB 修订号未确认 |
| 芯片 | ESP32-S3，revision v0.2，双核，运行频率 160 MHz |
| Flash / PSRAM | 16 MiB / 8 MiB Octal PSRAM |
| 显示与触摸 | 800×480 RGB565 LCD，GT911 电容触摸 |
| 开发环境 | Windows 11、VS Code、PowerShell |
| SDK / 图形库 | ESP-IDF v5.5.5 / LVGL 9.2.0（H02） |
| 烧录工具 | esptool v4.13.0，460800 baud |
| 串口监视 | esp-idf-monitor 1.11.0，COM5，USB Serial/JTAG，115200 baud |
| H01 基线 | `feat/hw-scaffold`，`9c27a7c`；实板验证版本在本地增加了临时心跳日志 |
| H02 基线 | `feat/hw-bsp-4-3c`，`d48086a`（迁移交接记录及 PR #39）；归档前本地工作树干净，HEAD 与 PR #39 一致。烧录时未独立留存 Git 状态 |

工程位于 `D:\zhiya-hardware-workspace\worktrees\`，使用已有 `Build-ZhiyaFirmware.ps1` 构建，不复用迁移前的 CMake 缓存。

## 3. H01 工程初始化与实板验证

### 3.1 构建及烧录过程

先核对 H01 工作树、ESP-IDF 路径及 Python 环境，确认 SDK 版本为 v5.5.5。使用统一构建脚本完成固件编译，随后通过 USB Type-C 数据线连接开发板。更换数据线后，插拔对比确认新增串口 COM5。

通过 esptool 的 `flash_id` 命令检测到 ESP32-S3、16MB Flash 和 8MB 嵌入式 PSRAM 特征。首次烧录完成后，Bootloader、应用程序和分区表均通过 Hash 校验。

首次构建目录：`temp\h01-20261124-220950-2354\build`，应用镜像 172336 B。增加临时心跳日志后重新构建，目录为 `temp\h01-20261124-224138-7043\build`，应用镜像 172464 B；两次构建均退出 0，烧录均通过数据校验。

### 3.2 调试问题及处理

| 问题 | 排查与处理 | 结果 |
| --- | --- | --- |
| 原数据线连接后屏幕亮，但无新增 COM 口 | 对比插拔前后端口；更换支持数据传输的线材 | 新增 COM5，芯片识别和烧录正常。原线具体故障原因未进一步检测 |
| VS Code 扩展部分配置命令不可用 | 复用已安装的 ESP-IDF SDK、Python 环境及 PowerShell 构建脚本 | 无须重新安装整套环境，H01/H02 均编译成功 |
| 初次烧录后没有收到应用日志，串口监视器出现写入超时和重连提示 | 确认 COM5 存在，检查实际 `sdkconfig` 中 `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`，使用 Python 只读串口验证连接；在 H01 `app_main()` 加入 2 秒心跳循环后重新编译烧录 | 成功捕获完整自检结果和持续心跳。初始现象可能与启动日志时机及 USB 重连相关，未单独确定根因 |

心跳日志仅用于诊断，保留原有 Flash 和 PSRAM 自检逻辑，使用 FreeRTOS `vTaskDelay(pdMS_TO_TICKS(2000))`，避免忙等待。无限循环会改变 app_main 的生命周期。临时改动保留在本地工作树并另存补丁，正式固件不包含该循环。

### 3.3 实际运行结果

```text
I (518) zhiya_boot: Flash: 16777216 bytes (physical)
I (518) zhiya_boot: PSRAM initialized: 8388608 bytes
I (518) zhiya_boot: H01 startup checks passed
I (518) zhiya_boot: H01 heartbeat: Flash=OK, PSRAM=OK
I (2518) zhiya_boot: H01 heartbeat: Flash=OK, PSRAM=OK
I (4518) zhiya_boot: H01 heartbeat: Flash=OK, PSRAM=OK
I (16518) zhiya_boot: H01 heartbeat: Flash=OK, PSRAM=OK
```

以上为实际日志节选。完整已提供的心跳记录从 518ms 至 16518ms，每隔 2000ms 输出一次。Flash 容量与 PSRAM 初始化结果符合 N16R8 配置，H01 启动自检及本次约 16 秒的心跳观测通过。

## 4. H02 显示与触摸适配（修复前）

### 4.1 板级驱动与固件构建

依据 PR #39 的板级设计，H02 使用 `board_4_3c` 组件，实现 RGB565 LCD、背光控制、GT911 单点触摸轮询及 LVGL 刷新与互斥。测试界面包含知芽标题、五色色块、触摸坐标和点击计数按钮。

本次实板构建完成 1203 步，退出码 0，应用镜像为 462944 B（0x71060，约 452.1 KiB），占 4 MiB 应用分区约 11.04%，余 3731360 B。该空间为应用分区余量，并非资源分区或运行内存余量。

H02 构建目录：`temp\h02-20261124-224647-9084\build`。Bootloader、分区表与应用镜像均成功烧录并通过 Hash 校验。

![H02 实板显示照片](images/h02-display-test.jpg)

*图 1 H02 实板测试界面（2026-11-24）*

### 4.2 功能测试

| 测试项目 | 测试操作及结果 | 状态 |
| --- | --- | --- |
| 背光及颜色 | 界面成功显示，五色色块、标题、布局可辨；照片无明显整体错位 | PASS（现场观察） |
| 四角触摸 | 依次触摸四角，检查坐标方向及响应 | PASS（现场观察；具体坐标值待补录） |
| 按钮事件 | 连续点击 Touch test 20 次，检查计数 | PASS（现场观察） |
| 长按及抬手 | 按住按钮至少 5 秒后松开，检查 DOWN/UP 与计数 | PASS（现场观察） |
| 多指与恢复 | 双指触摸、释放后恢复单点操作 | PASS（现场观察）；建议补录先松一指的细分场景 |
| 连续拖动 | 横向、纵向及画圈连续操作约 1 分钟 | PASS（现场观察） |
| 系统启动 | 捕获 H01 自检及 H02 初始化日志 | PASS |
| RST 与冷启动 | 分别按 RST、完全断电后上电，检查计数归零和交互恢复 | NOT_TESTED；现有记录为烧录后的启动 |
| 堆内存 | 对比初始化前后的 PSRAM、内部 RAM、DMA 堆信息 | PASS（初始化快照）；连续操作后的堆统计为 NOT_TESTED |

测试中未报告明显触摸失灵、重复计数或界面卡死。单次照片不能排除动态扫描撕裂；长期稳定性、异常 I2C/刷新故障的真机恢复尚未验证。

### 4.3 启动日志分析

完整启动日志显示 Flash 实际使用 QIO、80MHz，PSRAM 为 8MB、80MHz，SPI SRAM 内存测试通过。GT911 于启动日志 864ms 时成功识别，H02 于 994ms 时完成初始化。关键日志如下：

```text
I (117) esp_psram: Found 8MB PSRAM device
I (550) esp_psram: SPI SRAM memory test OK
I (564) zhiya_boot: Flash: 16777216 bytes (physical)
I (564) zhiya_boot: PSRAM initialized: 8388608 bytes
I (564) zhiya_boot: H01 startup checks passed
I (564) zhiya_boot: Starting H02 display and touch
I (864) board_4_3c_panel: GT911 detected at 0x5D; native landscape
I (994) board_4_3c: H02 initialized: 800x480 RGB565, backlight on, LVGL 9.2.0
I (994) main_task: Returned from app_main()
```

`Returned from app_main()` 表示初始化主函数返回，不表示整个 FreeRTOS 系统退出。已创建的显示与触摸任务继续运行，无须为此重复烧录。

### 4.4 初始化内存统计

单位：字节（B）。

| 内存类型 | 初始化前 free | 初始化后 free | 初始化后 min | 初始化后 largest | free 减少量 |
| --- | ---: | ---: | ---: | ---: | ---: |
| PSRAM | 8386308 | 7616752 | 7616752 | 7602176 | 769556 |
| Internal | 312515 | 240763 | 208732 | 163840 | 71752 |
| Internal DMA | 304727 | 232975 | 200944 | 163840 | 71752 |

H02 初始化后 PSRAM 剩余约 7.26 MiB，内部堆剩余约 235.1 KiB，内部 DMA 堆剩余约 227.5 KiB。PSRAM free 较初始化前减少约 751.5 KiB，与 768000 B 的 RGB565 帧缓冲规模相符。内部 DMA 属于内部内存中的具备 DMA 能力的部分，两项不可相加计算。

目前初始化内存余量良好，但连续点击后的最小堆、最大连续块和内存变化尚无实测记录，不能据此证明无内存泄漏。后续 JPEG 解码、音频及 TLS 联网应分别测量峰值。

### 4.5 警告与遗留问题

启动出现 I2C 上拉电阻检查提示：

```text
W (564) i2c.master: Please check pull-up resistances whether be connected properly. Otherwise unexpected behavior would happen. For more detailed information, please read docs
```

GT911 探测及现有人工触摸测试正常，当前不进行额外焊接或盲目调整 I2C 参数。记录提示，若后续出现通信失败、异常抬手或触摸失灵，再按原理图和驱动日志排查。

## 5. H02 修复版实板回归（2026-11-24）

本节记录 feat/hw-bsp-4-3c 上的测试提交 `2f5c98f14a7f5d88b6ed5a30b15bb55ba9998af9`。第 4 节及图 1 保留修复前固件的结果。

| 项目 | 修复版记录 |
| --- | --- |
| 环境 | ESP-IDF v5.5.5，LVGL 9.2.0，COM5 USB Serial/JTAG |
| 构建 | 1203/1203，退出码 0 |
| 应用镜像 | 修复前 462,944 B，修复后 462,976 B，4 MiB 应用分区剩余 3,731,328 B（约 89%） |
| 构建目录 | `D:\zhiya-hardware-workspace\temp\h02-20261124-233659-2553\build` |
| 构建日志 | `D:\zhiya-hardware-workspace\logs\h02-migration-build-20261124-233659-2553.log.build` |
| 烧录 | Bootloader、应用和分区表均完成写入及 Hash 校验 |

### 5.1 主机回归

在 `hardware/firmware` 执行：

```powershell
python ..\tests\board\test_touch.py --cc "D:\programmming\Dev-Cpp\MinGW64\bin\gcc.exe"
```

13 项均通过，最后一行为套件汇总。该测试使用 LVGL 9.2.0 和模拟 I²C。

```text
PASS idle/no-new-data does not read or acknowledge coordinates
PASS press/hold/move/no-new-data/release produces one click
PASS idle status timeout/no-new-data recovery preserves first click
PASS status-read failure cancels until acknowledged release
PASS coordinate-read failure cancels until acknowledged release
PASS idle report ACK failure still requires release across a status timeout
PASS coordinate ACK failure cancels until acknowledged release
PASS release ACK failure does not synthesize a click
PASS multi-point/invalid count/palm/key reports cancel
PASS out-of-bounds coordinates cancel
PASS contact-ID discontinuity/proximity report cancel
PASS inclusive screen edge maps without overflow
PASS flush failure latches/logs once and returns the LVGL buffer
PASS LVGL 9.2.0 event regression (mocked I2C, host execution)
```

### 5.2 启动和初始化内存

```text
I (550) esp_psram: SPI SRAM memory test OK
I (559) app_init: Compile time: Nov 24 2026 23:38:00
I (559) app_init: ELF file SHA256: 97c88875d...
I (564) zhiya_boot: Flash: 16777216 bytes (physical)
I (564) zhiya_boot: PSRAM initialized: 8388608 bytes
I (564) zhiya_boot: H01 startup checks passed
I (564) zhiya_boot: Starting H02 display and touch
I (864) board_4_3c_panel: GT911 detected at 0x5D; native landscape
I (994) board_4_3c: H02 initialized: 800x480 RGB565, backlight on, LVGL 9.2.0
I (994) board_4_3c: PSRAM heap bytes: free=7616752 min=7616752 largest=7602176
I (994) board_4_3c: internal heap bytes: free=240763 min=208732 largest=163840
I (994) board_4_3c: internal DMA heap bytes: free=232975 min=200944 largest=163840
I (994) main_task: Returned from app_main()
```

三类堆的初始化快照与修复前一致。启动仍出现 I²C 上拉检查警告，本轮操作未观察到触摸异常。以上为烧录后启动日志节选。独立 RST 和冷启动记录来自现场观察。

### 5.3 人工实板复测

| 项目 | 现场结果 | 状态 |
| --- | --- | --- |
| 板卡型号 | 背面丝印为 ESP32-S3-Touch-LCD-4.3C、800×480 Pixels，厂商 Waveshare，模组 ESP32-S3-WROOM-1。具体 PCB 修订号未确认 | PASS（型号） |
| 坐标与点击 | 四角和中心响应正常。连续点击按钮 20 次计数正确，点击非按钮区域不计数。未记录具体坐标值 | PASS |
| 长按与多指 | 按住按钮 5 秒再松开计数正常。双指接触后先松一指、再全松均未误计数，随后单指点击恢复 | PASS |
| 空闲与滑动 | 静置 30 秒后首击正常。连续滑动约 1 分钟，未见明显卡死、花屏或触摸失灵 | PASS |
| 独立 RST | 界面自动恢复，计数归零，随后 3 次点击正常 | PASS |
| 冷启动 | 完全断电 5 秒后重新上电，界面自动恢复，计数归零，随后 3 次点击正常 | PASS |

![H02 开发板背面及硬件接口实物图](images/h02-board-back.jpg)

*H02 开发板背面及硬件接口实物图*

正常空闲后的首击由实板验证，空闲 I²C 超时恢复后的首击由主机故障注入验证。真实 I²C 故障恢复尚待实板验证。

## 6. 验收结论与后续待测项

H01 的启动自检和 H02 修复版的基础板级验收通过。H02 的显示、触摸、RST 和断电重启已完成实板复测。以下项目留待集成阶段验证。

| 项目 | 后续记录或验证 | 状态 |
| --- | --- | --- |
| PCB 修订号 | 取得明确的实物修订标识。厂商 V1.1 原理图是设计参考 | NOT_TESTED |
| 连续操作后的堆与长期稳定性 | 在相同负载下多轮采集三类堆的 free、min、largest，记录持续运行、分配失败和异常复位 | NOT_TESTED |
| 真实 I²C 和刷新故障恢复 | 记录 GT911 超时或释放报告丢失、RGB 刷新失败以及后续触摸和重绘能否恢复 | NOT_TESTED |
| 复杂画面撕裂 | 在集成界面高刷新负载下观察并录像 | NOT_TESTED |

GT911 丢失释放报告且无新数据时可能保持 PRESSED，等待释放期间丢失零点报告也可能暂时屏蔽下一次触摸。RGB 刷新失败可能保留旧画面。上述风险仍保留在 [H02 定向复查](../h02-review.md)。

## 7. 构建产物

下列 SHA-256 在归档时从本地构建产物计算，用于识别文件。烧录工具报告的写入 Hash 校验是独立证据。构建日志位于本地硬件工作区的 logs 目录。串口证据见上文的现场摘录。

| 应用镜像 | 大小（B） | SHA-256 | 本地构建日志 |
| --- | ---: | --- | --- |
| H01 基线 | 172336 | 8fa352154744173eb401fd02400071bfa3ec68029a85960faa167e61cbec981f | h01-migration-build-20261124-220950-2354.log.build |
| H01 心跳诊断 | 172464 | 7dc028689a93078ddc40d32a8f624302988f1e4133b56df3205ab80774856458 | h01-migration-build-20261124-224138-7043.log.build |
| H02 修复前 | 462944 | 9d30db0c0c78c39b011574a5c720922cba3954feef59dfb409073a08ea440dd9 | h02-migration-build-20261124-224647-9084.log.build |
| H02 修复版 | 462976 | 8b720a4963643809039708277ea0eb5392b52d7380ce779b381c0d164e1f6b4c | h02-migration-build-20261124-233659-2553.log.build |

相关记录见 [H00/H01 PR #34](https://github.com/MumLauds/202610-zhiya/pull/34)、[H02 PR #39](https://github.com/MumLauds/202610-zhiya/pull/39)、[上板指南](../bringup.md)及[板级引脚表](../board-pin-map.md)。
