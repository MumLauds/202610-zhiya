# 知芽硬件端

知芽的独立可选扩展。固件启动后保留 H01 的串口自检，随后进入 H02 的彩屏与触控测试界面，显示颜色、按钮计数和实时触摸坐标。

目标板为 Waveshare **ESP32-S3-Touch-LCD-4.3C，SKU 33799，无外壳版**。官方仓库确认模块为 ESP32-S3-WROOM-1-N16R8，配备 16 MiB Flash 和 8 MiB Octal PSRAM，板载 800×480 RGB 屏幕及 GT911。见[产品资料](https://docs.waveshare.com/ESP32-S3-Touch-LCD-4.3C)和[微雪 ESP-IDF 指南](https://docs.waveshare.com/ESP32-S3-Touch-LCD-4.3C/ESP-IDF)。

## 构建环境与依赖

使用 Windows 11、VS Code、Espressif ESP-IDF 扩展和 **ESP-IDF v5.5.5**，目标芯片为 `esp32s3`。C 工程通过 ESP-IDF 自带的 FreeRTOS 启动 `app_main()`。工具链、Python 环境、CMake、Ninja 和烧录工具使用 Espressif 安装的版本。H01 的固件组件全部随所锁定的 ESP-IDF 提供，第三方组件依赖集合为空。

厂商参考源码固定在 [Waveshare 提交 `44e412f08c1e68c0da826bc5f51436f156a02eea`](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/tree/44e412f08c1e68c0da826bc5f51436f156a02eea)。官方[构建矩阵](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/blob/44e412f08c1e68c0da826bc5f51436f156a02eea/config/ci.json)固定 ESP-IDF v5.5.5 和 v6.0.2。H01 选用 [Espressif v5.5.5](https://github.com/espressif/esp-idf/releases/tag/v5.5.5)（`b774170ff46c393eeb5e495ea37936038d3f4f4f`），下表四个示例均通过了[该提交的微雪 v5.5.5 CI 构建](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/actions/runs/31859483912)。Flash 与 PSRAM 的模式、频率和 Flash 容量来自 [4.3C LVGL 示例配置](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/blob/44e412f08c1e68c0da826bc5f51436f156a02eea/examples/esp-idf/12_lvgl_transplant/sdkconfig.defaults)。

| 官方示例 | 该提交的依赖与版本 |
| --- | --- |
| [`03_lcd`](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/tree/44e412f08c1e68c0da826bc5f51436f156a02eea/examples/esp-idf/03_lcd)、[`06_touch`](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/tree/44e412f08c1e68c0da826bc5f51436f156a02eea/examples/esp-idf/06_touch) | 清单要求 ESP-IDF ≥5.1.0。RGB LCD、GT911 驱动和所用板级代码随示例提交固定。 |
| [`12_lvgl_transplant`](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/tree/44e412f08c1e68c0da826bc5f51436f156a02eea/examples/esp-idf/12_lvgl_transplant) | 清单要求 ESP-IDF ≥5.3.0，采用随源码提供的 LVGL 9.2.0，包含本地 LVGL 移植代码。该清单也声明下行的三个音频相关依赖。 |
| [`11_speaker_microphone`](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/tree/44e412f08c1e68c0da826bc5f51436f156a02eea/examples/esp-idf/11_speaker_microphone) | 清单要求 ESP-IDF ≥5.3.0，`esp_codec_dev` 为 `~1.6.2`，`chmorgan/esp-audio-player` 为 `~1.1.0`，`chmorgan/esp-file-iterator` 为 `1.0.0`。带 `~` 的值是版本范围。 |

微雪另行发布的托管 BSP `waveshare/esp32_s3_touch_lcd_4_3c` 3.0.1 使用 LVGL 9.4 和 `esp_lvgl_adapter`，属于 H02 可评估的集成路径。若后续引入托管组件，用 ESP-IDF Component Manager 在 `idf_component.yml` 中声明版本，并提交解析生成的 `dependencies.lock`。本地板级组件放入 `firmware/components/`，复用厂商代码时记录提交、来源和许可证。见[微雪组件说明](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/blob/44e412f08c1e68c0da826bc5f51436f156a02eea/docs/components.md)。

## 目录与构建

| 路径 | 用途 |
| --- | --- |
| `firmware/CMakeLists.txt` | ESP-IDF 工程入口，校验目标及版本 |
| `firmware/main/` | 启动日志和 N16R8 容量自检 |
| `firmware/sdkconfig.defaults` | N16R8、USB Serial/JTAG 和自定义分区的构建基线 |
| `firmware/partitions.csv` | NVS、PHY 数据和 4 MiB 工厂应用分区 |
| `firmware/components/board_4_3c/` | 4.3C RGB LCD、IO 扩展器、GT911 和 LVGL 板级接口 |
| `firmware/dependencies.lock` | ESP-IDF Component Manager 解析的 LVGL 9.2.0 及校验摘要 |
| `docs/bringup.md` | Windows 11 安装、构建、烧录、串口验收与排查 |
| `docs/board-pin-map.md` | 引脚、时序、驱动来源、内存预算及实板状态 |

从仓库根目录进入已激活 v5.5.5 的 PowerShell，首次构建执行：

```powershell
Set-Location .\hardware\firmware
idf.py --version
idf.py set-target esp32s3
idf.py build
```

确认工具链版本、烧录与复位验收见 [Windows 11 上板指南](docs/bringup.md)。

## Flash 布局

ESP-IDF 分区表位于 `0x8000`。NVS 从 `0x9000` 开始，占 24 KiB，供后续小型配置。PHY 数据预留 `0xF000–0xFFFF`（4 KiB）。工厂应用从 `0x10000` 开始，占 4 MiB。其余从 `0x410000` 到 16 MiB Flash 末尾的空间保留，待 OTA 和离线资源方案确定后再分配。

## 任务状态与项目边界

- H00 已提交[硬件设计变更申请](docs/h00-hardware-change-request.md)和[接口决策登记](docs/h00-interface-decisions.md)供团队评审，涵盖选型风险、验收条件和后端依赖。

- H01 已在 Windows 上使用 ESP-IDF v5.5.5 验证 `idf.py set-target esp32s3` 和 `idf.py build`，生成应用、引导程序和分区表。已通过实板烧录、USB 串口启动及 Flash/PSRAM 自检。此阶段的设备行为限于串口诊断。
- H02 采用官方示例的板级配置与 LVGL 9.2.0，已在 Windows 11 / ESP-IDF v5.5.5 上构建。板级接口及初始化顺序见[引脚表](docs/board-pin-map.md)，实板 LCD、背光、GT911 与基础 LVGL 交互通过。修复版已通过触摸、独立 RST 和断电重启复测，板卡型号已核对。PCB 修订号、长期稳定性及真实故障恢复待验证。见 [H01/H02 实板验收报告](docs/reports/h01-h02-bringup-report-20261124.md)。
- H05 提供本地 DEMO 数据与 Mock HTTP：`python hardware/mock/build_fixtures.py` 生成 20 本 / 104 页 / 40 个 H03 封面引用（源状态 `PUBLISHED`，全部 `demoOnly=true`；1 本 DRAFT 联调绘本排除）；`python hardware/mock/server.py` 默认在 `127.0.0.1:8765` 运行，`python -m unittest discover -s hardware/tests/mock -v` 运行 37 项离线测试。问答成功的 ASK 事件会保存 `outputText/strategyType/revision`，可由 `GET` 会话恢复；TTS 失败重试复用该 ASK 的实际回答。它不调用真实后端或云端 AI，不替代设备认证，也不负责 LVGL 页面。字段、错误码、`sha256-utf8-lf-v1` 来源哈希和 H06 交接见[`docs/mock-contract.md`](docs/mock-contract.md)。
- [SRS v0.3](../docs/requirements/软件需求规格说明书.md) 记录 4.3C 彩色触摸方案，保留 HW-01～HW-05 的可选扩展级别，修订依据见 [DCL-025](../docs/standards/design-change-log.md)。SRS 修订及后续设备认证、授权和受控媒体访问待团队评审。现有接口以[接口契约 v0.4](../docs/design/api-contract.md)和[对应 JSON](../docs/design/api-contract-v0.4.json)为基准。

H02 的“知芽”标题使用 Noto Sans SC 600 的两个字形，36 px / 4 bpp，已用 `lv_font_conv 1.5.3` 转为固件内置数据。构建只编译生成的 C 文件。字形来源为 [Google Fonts Noto Sans SC](https://fonts.google.com/noto/specimen/Noto+Sans+SC)，许可证为 [SIL OFL 1.1](firmware/main/OFL-NotoSansSC.txt)。
