# H06 构建与实板验收

当前板上状态为 **NOT_TESTED**。先保存正在使用的固件路径，确认板卡 SKU 33799 和 USB Serial/JTAG COM 口。H06 烧录只写入引导、分区表及应用，本轮沿用原分区。

## Windows 构建

在 ESP-IDF v5.5.5 终端从 H06 的固件目录执行。生成目录须为新的绝对路径。

```powershell
Set-Location 'D:\zhiya-hardware-workspace\worktrees\zhiya-h06\hardware\firmware'
idf.py --version
$scratch = Join-Path 'D:\zhiya-hardware-workspace\temp' ('h06-' + [Guid]::NewGuid().ToString('N'))
$build = Join-Path $scratch 'build'
$config = Join-Path $scratch 'sdkconfig'
idf.py -B $build -D "SDKCONFIG=$config" -D IDF_TARGET=esp32s3 -D ZHIYA_H06=ON -D ZHIYA_H06_BOOKS=3 build
if ($LASTEXITCODE -ne 0) { throw 'H06 build failed' }
```

可指定 `-D ZHIYA_H06_BOOKS=1` 仅打包《小狐狸的雨伞》。同样使用新目录并设置 `-D ZHIYA_H06=OFF` 可复验 H02。使用现有本地构建入口时，也可运行 `Build-ZhiyaFirmware.ps1 -Hardware h06`，追加 `-Books 1` 或 `-H02Baseline` 选择另一模式。

## 本轮构建记录

2026-11-27 在 `feat/hw-reader-ui` 的本地改动上使用 ESP-IDF v5.5.5。三种模式各用独立缓存和配置，初次从空目录开始，交付镜像在最后一轮复编生成。表中路径相对于 `D:\zhiya-hardware-workspace\temp`，每个目录内的构建目录为 `build`，配置文件为同级 `sdkconfig`。

| 模式 | 目录 | `idf.py build` 退出码 | 应用镜像 `zhiya_hardware.bin` |
| --- | --- | ---: | ---: |
| H02 基线 | `h06-20261127-152914-7591-4f2f553e` | 0 | 462,976 B |
| H06 单本 | `h06-20261127-152928-9738-50d8a2bb` | 0 | 743,248 B |
| H06 三本 | `h06-20261127-153347-3413-cd91140e` | 0 | 1,139,664 B |

三本版占 4 MiB factory 分区约 27%。上表为主机构建记录，ROM 解码、显示、触摸和运行期内存仍待实板验收。

## 烧录与监视

```powershell
[System.IO.Ports.SerialPort]::GetPortNames()
$port = Read-Host '输入已核实的 USB Serial/JTAG COM 口'
idf.py -B $build -D "SDKCONFIG=$config" -D IDF_TARGET=esp32s3 -D ZHIYA_H06=ON -D ZHIYA_H06_BOOKS=3 -p $port flash monitor
```

首次实板验证可选单本预构建产物。在普通 PowerShell 中执行，插板后核对新出现的串口名称。

```powershell
Set-Location 'D:\zhiya-hardware-workspace\temp\h06-20261127-152928-9738-50d8a2bb\build'
[System.IO.Ports.SerialPort]::GetPortNames()
$port = Read-Host '输入已核实的 USB Serial/JTAG COM 口'
$python = 'D:\zhiya-h01-idf-tools\python_env\idf5.5_py3.13_env\Scripts\python.exe'
& $python -m esptool --chip esp32s3 -p $port -b 460800 --before default_reset --after hard_reset write_flash '@flash_args'
if ($LASTEXITCODE -ne 0) { throw 'H06 flash failed' }
```

保持一台终端占用串口，监视器按 Ctrl+] 退出。保存提交号、镜像大小、板卡修订、COM 口和串口日志，有条件时记录屏幕照片或视频。

## 逐项实板验收

| 项目 | 操作和预期 | 结果 |
| --- | --- | --- |
| 启动 | 冷启动应完成 H01 自检并点亮屏幕，显示欢迎页和 DEMO。点进入本地演示，首页可进书架。 | NOT_TESTED |
| 单本闭环 | 打开《小狐狸的雨伞》详情，开始阅读，比对第 1–6 页的真实图文；在末页只能回退。向左/右滑动插画对照前后翻页。 | NOT_TESTED |
| 详情及分页 | 回书架翻到第二组，打开其他书再开始，核对页码、标题、封面和正文。首末页控件禁用。 | NOT_TESTED |
| 结束及重置 | 点击结束进入确认，取消回原页；再次确认显示实际读过页数；返回书架后重新开始应从第 1 页进入。 | NOT_TESTED |
| 字体与色彩 | 核对中文和标点、400×300 插画比例及色序，正文区域完整。 | NOT_TESTED |
| 异常及快速操作 | 快速交替翻页并退出，检查旧 JPEG 不覆盖新页。缺图或解码失败时还有原页文字和图片占位。 | NOT_TESTED |
| 稳定性 | 建议 20 轮、连续 15 分钟。比较每轮结束的 PSRAM、内部堆和 DMA 堆 free/min/largest，检查无持续下降。核对冷启动/RST 重启。 | NOT_TESTED |
| 离线边界 | 无网络完成闭环，DEMO 持续可见，设备绑定、朗读和提问入口禁用。 | NOT_TESTED |

串口 `reader_ui` 给出本地会话、版本、页码及从创建页面到图片交给 LVGL 的毫秒数。`reader_media` 给出 JPEG 尺寸、字节数和解码微秒，发生错误时记录 ROM 返回码。屏幕实际刷新、换页体验、ROM 解码色序和真实堆余量由实板记录。

## 主机回归

从仓库根目录运行，先构建固件以下载 LVGL 托管组件。使用已有主机 GCC，并让 `--cc` 指向它。

```powershell
python hardware/tools/validate_assets.py --json
python hardware/tools/validate_page_assets.py --json
python hardware/mock/build_fixtures.py --check
python -m unittest discover -s hardware/tests/mock -v
$cc = 'D:\programmming\Dev-Cpp\MinGW64\bin\gcc.exe'
python hardware/tests/reader/test_reader.py --cc $cc
python hardware/tests/board/test_touch.py --cc $cc
```
