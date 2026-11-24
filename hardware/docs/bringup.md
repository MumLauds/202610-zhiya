# Windows 11 首次构建与上板

## 安装 ESP-IDF

1. 安装 Git for Windows、VS Code 和 Espressif 官方扩展。在 PowerShell 可通过 `code --install-extension espressif.esp-idf-extension` 安装该扩展。
2. 在 VS Code 命令面板运行 **ESP-IDF: Open ESP-IDF Installation Manager**（若扩展未提供该入口，从 Espressif 的安装文档下载 EIM）。在 EIM 中选 **ESP-IDF v5.5.5**，安装 ESP32-S3 的工具链。选择只含 ASCII 且无空格的安装和项目路径，例如 `C:\Espressif` 和 `D:\zhiya-h01`。
3. 在仓库根目录用 `code .\hardware\firmware` 打开固件目录。在命令面板选择 **ESP-IDF: Select Current ESP-IDF Version**，选刚安装的 v5.5.5，然后运行 **ESP-IDF: Open ESP-IDF Terminal**。下面的命令使用 PowerShell，并假定终端在含 `partitions.csv` 的固件目录。也可从 EIM 打开 v5.5.5 的环境终端后进入同一目录。

参见 [ESP-IDF v5.5.5 Windows 安装](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32s3/get-started/windows-setup.html)和 [VS Code 扩展安装与版本选择](https://docs.espressif.com/projects/vscode-esp-idf-extension/en/latest/installation.html)。

## 编译

```powershell
idf.py --version
idf.py set-target esp32s3
idf.py build
```

版本应为 `ESP-IDF v5.5.5`，构建成功后应生成 `build\zhiya_hardware.bin`、`build\bootloader\bootloader.bin` 和 `build\partition_table\partition-table.bin`。普通增量编译只需运行 `idf.py build`。重新运行 `idf.py set-target esp32s3` 会清理构建目录并从默认值生成项目配置，原 `sdkconfig` 会移到 `sdkconfig.old`。

## USB 烧录与串口日志

1. 用可传输数据的 USB Type-C 线给 4.3C 供电并连接电脑。首次烧录前保存[微雪 4.3C 出厂固件及恢复步骤](https://docs.waveshare.com/ESP32-S3-Touch-LCD-4.3C/FactoryFirmWare)。烧录会写入引导程序、分区表和应用，可覆盖板上的出厂程序。
2. 在设备管理器查看「端口（COM 和 LPT）」，对比插拔前后的端口。也可用 PowerShell 列出当前可用的 COM 号。
3. 关闭已占用该端口的串口工具，执行下列命令，并在提示处输入板卡实际的 COM 号。

```powershell
[System.IO.Ports.SerialPort]::GetPortNames()
$boardPort = Read-Host '输入开发板 COM 口，例如 COM5'
idf.py -p $boardPort flash monitor
```

单独查看串口日志时，在同一终端执行 `idf.py -p $boardPort monitor`。进入监视器后短按板上 **RST**，检查一轮完整的启动日志。按 **Ctrl+]** 退出。重连后如果 COM 号变化，重新列出端口并更新 `$boardPort`。

### 手动进入 BOOT 下载模式

按住 **BOOT**，短按并松开 **RST**，再松开 **BOOT**。若端口仍未出现，也可在按住 **BOOT** 时重新插入 USB 数据线，板卡上电后松开 **BOOT**。重新查找 COM 号并重试烧录。写入成功后若仍停留在下载模式，松开 BOOT 再按一次 RST。

参见[微雪烧录说明](https://docs.waveshare.com/ESP32-S3-Touch-LCD-4.3C/Firmware-Flashing)和 [Espressif USB Serial/JTAG 控制台说明](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-guides/usb-serial-jtag-console.html)。

## H01 启动自检

2026-11-24 实板烧录、COM5 启动、Flash 16 MiB 与 PSRAM 8 MiB 自检为 **PASS**。独立 RST 和断电重启为 **NOT_TESTED**。见 [H01/H02 实板验收报告](reports/h01-h02-bringup-report-20261124.md)。

复位后保存完整串口输出，并记录板卡丝印或修订、固件提交号及 ESP-IDF 版本。H01 的预期日志内容如下，时间戳和芯片修订以实物为准：

```text
Zhiya hardware boot (H01)
ESP-IDF: v5.5.5
Chip: ESP32-S3, 2 cores, revision v...
Reset reason: ... (esp_reset_reason_t)
Flash: 16777216 bytes (physical)
PSRAM initialized: 8388608 bytes
H01 startup checks passed
```

重复断电上电及 RST 复位，确认自检可稳定通过。记录任何 `Unexpected reset reason`、容量异常或 `H01 startup checks failed`；异常复位编号对应 ESP-IDF 的 `esp_reset_reason_t`。若故障在 `app_main()` 之前发生，保留能捕获的 ESP-IDF 启动日志和监视器输出。

## H02 实板验收

**2026-11-24 修复版（`2f5c98f`）：**板卡型号、显示、GT911、初始化堆快照、触摸复测、独立 RST 和冷启动为 **PASS**。PCB 修订号、连续操作后堆数据、长期稳定性、真实 I²C 故障恢复和复杂画面撕裂为 **NOT_TESTED**。见[实板验收报告](reports/h01-h02-bringup-report-20261124.md)。 确认无外壳版 SKU 33799 并记录 PCB 丝印，按[板级引脚表](board-pin-map.md)核对板卡修订。对已设置目标芯片的固件目录，在 ESP-IDF PowerShell 执行：

```powershell
idf.py build
if ($LASTEXITCODE -ne 0) { throw 'Firmware build failed' }
[System.IO.Ports.SerialPort]::GetPortNames()
$boardPort = Read-Host '输入 4.3C 实际 COM 口'
idf.py -p $boardPort flash monitor
```

| 检查项 | 操作和应观察到的结果 |
| --- | --- |
| LCD 上电与背光 | 冷启动或按 RST。H01 自检通过后应出现 `GT911 detected`、`H02 initialized: 800x480 RGB565, backlight on, LVGL 9.2.0`。背光点亮并显示“知芽”标题。 |
| 色彩和显示边界 | 核对红、绿、蓝、白、黑色块的顺序，检查外框四条边、文字及画面是否完整。记下错色、闪屏、偏移和明显的撕裂。 |
| 单点和四角坐标 | 逐点触摸四角内侧及中心。以左上为原点，右上接近 (799,0)，左下接近 (0,479)，右下接近 (799,479)。以可触及内边缘为准，记录偏差。按下显示 DOWN，抬手显示 UP 并保留末次坐标。 |
| 横屏方向 | 横向从左向右拖动，X 持续增大。纵向从上向下拖动，Y 持续增大。记录轴互换、反向或点击偏移。 |
| 持续按住与异常接触 | 在按钮上静止按住至少 5 秒，再拖动和抬手。正常抬手只计一次，静止时不得闪为 UP。两指同时按按钮、先松一指、再全部松开，整个异常接触不计点击，完全抬手后的新单点点击应恢复。保存 `GT911 read failed` 日志，记录是否出现卡住 DOWN 或重复计数。 |
| 连续操作及复位 | 点击 `Touch test` 20 次，每次按下并松开，应每次只增加一计数，点击色块不增加计数。连续拖动 1 分钟，检查界面是否卡住、错位或重启。至少复测一次 RST 和一次断电重启。计数从 0 恢复，启动结果应一致。 |
| PSRAM 和余量 | 保存初始化前后及最后一次点击的 `PSRAM`、`internal`、`internal DMA` 堆日志。比较 free、min 和 largest，记录重复同样操作后是否持续下降，检查分配失败。 |
| 异常与恢复 | 保存首条 `H02 board initialization failed` 及其前面的模块错误。IO 扩展器或 GT911 探测失败时核对板型、供电和连线，不更换其他型号的引脚定义。记录后按 RST 重试，再以断电冷启动复查。若官方 `03_lcd` 或 `06_touch` 同样失败，先定位板卡或供电。 |

提交记录时附板卡照片或丝印、固件提交、完整串口日志、四角坐标、颜色/边界照片与复位结果。H06 开始接入 UI 前需确认上述项目，并用真实资源评估图片解码、字体及 LVGL 绘制所需的堆余量。

2026-11-17 的协议、锁与资源顺序审查、干净构建和主机故障注入回归见 [H02 定向复查](h02-review.md)。已有触点的通信故障或异常报告会触发取消并等待有效零点报告。空闲时单纯的状态读取超时不会新设释放等待。若释放报告丢失，后续触摸可能被忽略至下一次完整抬手。出现 `RGB flush failed` 时保存日志与画面，检查后续重绘是否覆盖旧区域，再用 RST 和断电重启复查。实际报告丢失、刷新失败、长期堆余量及调度时序仍为 **NOT_TESTED**。

## 常见错误

| 现象 | 排查步骤 |
| --- | --- |
| `idf.py` 找不到或版本不符 | 用扩展选择 v5.5.5 后重新打开 ESP-IDF Terminal，运行 `idf.py --version`。 |
| CMake 或交叉编译器找不到 | 用 EIM 修复 v5.5.5 工具安装，确认选了 ESP32-S3 并新开终端。 |
| 无 COM 口、`Failed to connect` | 检查 USB 数据线和直连端口，用 BOOT/RST 进入下载模式后重新查 COM 号。在设备管理器核对 USB Serial/JTAG 枚举。 |
| `Access is denied` | 关闭旧的 Monitor 或其他串口工具。拔插后 COM 号可能变化。 |
| 烧录不稳定 | 使用短数据线、直连 USB 和稳定供电，关闭其他占用端口的程序，可试 `idf.py -p $boardPort -b 115200 flash`。 |
| 目标或 Flash/PSRAM 设置异常 | 核对板卡型号、`sdkconfig.defaults` 和生成的 `sdkconfig`。运行 `idf.py set-target esp32s3` 后重新构建、烧录并保存日志。 |
| 写入成功但没有应用日志 | 确认选中板载 USB 对应的 COM 口，启动 Monitor 后短按 RST，检查生成配置中的 `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`。 |
| 循环复位或出现异常复位日志 | 保存首轮错误和回溯。出现掉电复位时先换用稳定的 USB 供电；出现 PSRAM 错误时核对板卡和 N16R8 配置。 |
