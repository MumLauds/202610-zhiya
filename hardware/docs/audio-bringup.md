# H07 构建、烧录与实板验收

## Windows 构建与烧录

在已安装 ESP-IDF v5.5.5 的知芽本地工作区中执行。首次使用的 H07 worktree 应在 `D:\zhiya-hardware-workspace\worktrees\zhiya-h07`。

```powershell
Set-Location D:\zhiya-hardware-workspace
.\tools\Build-ZhiyaFirmware.ps1 -Hardware h07
# 也可在同一分支复核当前 H02 默认模式
.\tools\Build-ZhiyaFirmware.ps1 -Hardware h07 -H02Baseline
```

或在已激活的 ESP-IDF PowerShell 中构建。每次在工作区 `temp` 下选择新的绝对构建地址。

```powershell
Set-Location D:\zhiya-hardware-workspace\worktrees\zhiya-h07\hardware\firmware
idf.py --version
$scratch = Join-Path 'D:\zhiya-hardware-workspace\temp' ('h07-board-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$build = Join-Path $scratch 'build'
$config = Join-Path $scratch 'sdkconfig'
idf.py -B $build -D "SDKCONFIG=$config" -D IDF_TARGET=esp32s3 -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.h07' build
if ($LASTEXITCODE -ne 0) { throw 'H07 build failed' }
Get-CimInstance Win32_SerialPort | Select-Object DeviceID, Name
$port = Read-Host '输入已核实的板卡 USB Serial/JTAG COM 口'
idf.py -B $build -D "SDKCONFIG=$config" -D IDF_TARGET=esp32s3 -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.h07' -p $port flash monitor
```

若通过本地脚本构建，使用该次成功输出的 cache 目录指派 `$build`，取其父目录下的 `sdkconfig` 作为 `$config`。串口日志保存到工作区 `logs`。开始前确认板卡为 SKU 33799，记录 PCB 修订、SPK 连接、提交 SHA、镜像大小和 COM 口。退出监视器使用 Ctrl+]。

## 实板验收

以下各项为 **NOT_TESTED**。按顺序操作，记录实物现象、串口日志和被测版本。使用非敏感测试语音。

| 编号 | 操作与预期 | 状态 |
| --- | --- | --- |
| A01 | 上电启动，H01 自检通过，H07 页面能触控，状态从初始化进入 Ready。无自动播放。 | NOT_TESTED |
| A02 | 点「测试音」，低音量短鸣后自动结束。检查无明显启动/关闭爆音。 | NOT_TESTED |
| A03 | 两次测试音之间调节音量，确认相对音量变化。静音后播放应无输出，点恢复后正常。 | NOT_TESTED |
| A04 | 录制约 5 秒，状态、样本数、峰值/RMS 随采集更新。分别靠近 MIC1、MIC2 轻声测试并记录有效来源。 | NOT_TESTED |
| A05 | 点回放，确认听到同一段实际录音。再次播放确认录音仍可用。 | NOT_TESTED |
| A06 | 连续录至自动停止，时长不越 15 秒，样本数不超过 240,000。 | NOT_TESTED |
| A07 | 录音不足 0.5 秒即停止，提示过短、回放禁用。 | NOT_TESTED |
| A08 | 录音取消后内容清除。录音回放中取消能够停止，已有有效录音仍可重放。 | NOT_TESTED |
| A09 | 快速点击互斥控制，核对无重复任务、重启或同时录放。 | NOT_TESTED |
| A10 | 连续 20 轮录音、回放、清除，检查按钮恢复、无卡死。 | NOT_TESTED |
| A11 | 录放期间检查背光、画面更新和异常花屏。 | NOT_TESTED |
| A12 | 录放期间操作滑块、取消和停止，确认触摸仍响应，按需要回归 H02 四角触控。 | NOT_TESTED |
| A13 | 比对初次分配后的各轮 PSRAM、内部及 DMA 堆 free/min/largest。复用缓冲造成的起始下降需与持续下降区分。 | NOT_TESTED |
| A14 | 按 RST，再断电重启各一次，H07 能重新进入 Ready，录音不会跨重启保留。 | NOT_TESTED |

若启动停在 Audio error，先记录 `audio_hw` 和 `board_audio` 中错误阶段及错误码，再点重试。探测失败优先核对板卡修订、GPIO8/9 共享总线和 codec 7 位地址。录音有时长但为恒定或全零时，先观察不同 MIC 和音源位置，再核查 RX 时钟、slot 和 ADC 增益。安静输入不等同于硬件故障。无声或爆音时降低音量并停止，核对 SPK、EXIO3 和 DAC 状态。遇到 LCD/触摸异常先保存同一轮音频及 H02 日志，再用默认 H02 模式对照。

## 主机回归

```powershell
Set-Location D:\zhiya-hardware-workspace\worktrees\zhiya-h07
python hardware/tests/audio/test_audio.py --cc 'D:\programmming\Dev-Cpp\MinGW64\bin\gcc.exe'
python hardware/tests/board/test_touch.py --cc 'D:\programmming\Dev-Cpp\MinGW64\bin\gcc.exe'
```

主机用模拟 I/O 驱动生产状态转换、样本和扩展器掩码逻辑。真实任务调度、板载麦克风、功放、DMA 以及屏幕共存结果以 A01–A14 为准。

## 软件验证记录

2026-11-25，ESP-IDF v5.5.5、ESP32-S3、独立构建目录。H02 默认模式与 H07 音频模式的构建退出码均为 **0**，镜像校验和、验证哈希及 4 MiB 应用分区容量检查通过。

| 模式 | 应用镜像大小 | 工作区相对构建路径 |
| --- | ---: | --- |
| H02 默认界面 | 463,232 B | `temp/h07-20261125-003611-1743/build` |
| H07 音频界面 | 507,088 B（比默认模式大 43,856 B） | `temp/h07-20261125-003611-1504/build` |

H07 主机测试和 H02 板级回归退出码均为 **0**。H02 共 13 项，覆盖触摸和刷新；H07 覆盖 WAV 解析、录音边界、实际采样回放、模拟故障、功放时序和共享资源所有权。日志位于工作区的 `logs/h07-audio-host.log`、`logs/h07-h02-touch.log` 和两份 `logs/h07-migration-build-20261125-003611-*.log.build`。H07 实板仍待验收。
