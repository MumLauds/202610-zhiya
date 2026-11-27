# H06 构建与实板验收

当前板上状态为 **NOT_TESTED**。先保存正在使用的固件路径，确认板卡 SKU 33799 和 USB Serial/JTAG COM 口。H06 烧录只写入引导、分区表及应用，本轮沿用原分区。

## Windows 构建

在 ESP-IDF v5.5.5 终端从 H06 的固件目录执行。生成目录须为新的绝对路径。

```powershell
Set-Location 'D:\zhiya-hardware-workspace\worktrees\zhiya-h06\hardware\firmware'
idf.py --version
python -m pip install -r ..\tools\requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'H03 image validation dependency install failed' }
$scratch = Join-Path 'D:\zhiya-hardware-workspace\temp' ('h06-' + [Guid]::NewGuid().ToString('N'))
$build = Join-Path $scratch 'build'
$config = Join-Path $scratch 'sdkconfig'
idf.py -B $build -D "SDKCONFIG=$config" -D IDF_TARGET=esp32s3 -D ZHIYA_H06=ON -D ZHIYA_H06_BOOKS=3 build
if ($LASTEXITCODE -ne 0) { throw 'H06 build failed' }
```

可指定 `-D ZHIYA_H06_BOOKS=1` 仅打包《小狐狸的雨伞》。同样使用新目录并设置 `-D ZHIYA_H06=OFF` 可复验 H02。使用现有本地构建入口时，也可运行 `Build-ZhiyaFirmware.ps1 -Hardware h06`，追加 `-Books 1` 或 `-H02Baseline` 选择另一模式。

## 本轮构建记录

2026-11-27 在 PR #98 的复查版本使用 ESP-IDF v5.5.5 和 H03 锁定的 Pillow 13.0.0。三种模式各从空目录构建。表中路径相对于 `D:\zhiya-hardware-workspace\temp\h06-pr98-r2-mwypm05y`，每个目录内的构建目录为 `build`，配置文件为同级 `sdkconfig`。

| 模式 | 目录 | `idf.py build` 退出码 | 应用镜像 `zhiya_hardware.bin` |
| --- | --- | ---: | ---: |
| H02 基线 | `h02` | 0 | 462,976 B |
| H06 单本 | `h06-single` | 0 | 744,288 B |
| H06 三本 | `h06-three` | 0 | 1,140,688 B |

三本版占 4 MiB factory 分区约 27%。上表为主机构建记录，ROM 解码、显示、触摸和运行期内存仍待实板验收。

## 烧录与监视

```powershell
[System.IO.Ports.SerialPort]::GetPortNames()
$port = Read-Host '输入已核实的 USB Serial/JTAG COM 口'
idf.py -B $build -D "SDKCONFIG=$config" -D IDF_TARGET=esp32s3 -D ZHIYA_H06=ON -D ZHIYA_H06_BOOKS=3 -p $port flash monitor
```

首次实板验证可选单本预构建产物。在普通 PowerShell 中执行，插板后核对新出现的串口名称。

```powershell
Set-Location 'D:\zhiya-hardware-workspace\temp\h06-pr98-r2-mwypm05y\h06-single\build'
[System.IO.Ports.SerialPort]::GetPortNames()
$port = Read-Host '输入已核实的 USB Serial/JTAG COM 口'
$python = 'D:\zhiya-h01-idf-tools\python_env\idf5.5_py3.13_env\Scripts\python.exe'
& $python -m esptool --chip esp32s3 -p $port -b 460800 --before default_reset --after hard_reset write_flash '@flash_args'
if ($LASTEXITCODE -ne 0) { throw 'H06 flash failed' }
```

验收书架滑动分页时，将构建目录改为 `D:\zhiya-hardware-workspace\temp\h06-pr98-r2-mwypm05y\h06-three\build`，在同一目录运行相同烧录命令。

保持一台终端占用串口，监视器按 Ctrl+] 退出。保存提交号、镜像大小、板卡修订、COM 口和串口日志，有条件时记录屏幕照片或视频。

## 逐项实板验收

| 项目 | 操作和预期 | 结果 |
| --- | --- | --- |
| 启动 | 冷启动应完成 H01 自检并点亮屏幕，显示欢迎页和 DEMO。点进入本地演示，首页可进书架。 | NOT_TESTED |
| 单本闭环 | 打开《小狐狸的雨伞》详情，开始阅读，比对第 1–6 页的真实图文；在末页只能回退。向左/右滑动插画对照前后翻页。 | NOT_TESTED |
| 书架封面及分页 | 烧录三本版。点击封面应进对应详情，返回保持分页。横滑内容区到第二组应只翻一页，滑动结束应停在书架，首末页控件禁用。 | NOT_TESTED |
| 混合输入 | 书架与阅读页分别快速交替点箭头和横滑。成功翻页后 250 ms 内应无第二次翻页。一次超过 500 ms 的慢滑应无翻页或详情跳转。超过冷却后再操作应正常。 | NOT_TESTED |
| 结束及再读 | 点击结束进入确认，取消回原页。再次确认显示实际读过页数，点「再读一遍」应进入第 1 页并生成不同的本地会话号。回书架保留之前的分组。 | NOT_TESTED |
| 字体与色彩 | 核对中文和标点、400×300 插画比例及色序，正文换行完整。三本真实素材未触发超长正文分段，超长样例由主机 LVGL 回归覆盖，板上该分支仍待专用测试素材。 | NOT_TESTED |
| 异常及快速操作 | 快速交替翻页并退出，检查旧 JPEG 不覆盖新页。缺图或解码失败时还有原页文字和图片占位。 | NOT_TESTED |
| 稳定性 | 建议 20 轮、连续 15 分钟。比较每轮结束的 PSRAM、内部堆和 DMA 堆 free/min/largest，检查无持续下降。核对冷启动/RST 重启。 | NOT_TESTED |
| 离线边界 | 无网络完成闭环，DEMO 持续可见，设备绑定、朗读和提问入口禁用。 | NOT_TESTED |

串口 `reader_ui` 给出本地会话、版本、页码及从创建页面到图片交给 LVGL 的毫秒数。`reader_media` 给出 JPEG 尺寸、字节数和解码微秒，发生错误时记录 ROM 返回码。屏幕实际刷新、换页体验、ROM 解码色序和真实堆余量由实板记录。

## 主机回归

从仓库根目录运行，先构建固件以下载 LVGL 托管组件。使用装有 H03 依赖的 Python 和已有主机 GCC，并让 `--cc` 指向 GCC。阅读 UI 测试用 LVGL 9.2.0 和模拟触摸事件覆盖冷却边界、封面与滑动、再读、17 页真实正文及超过八行的续段。

```powershell
python hardware/tools/validate_assets.py --json
python hardware/tools/validate_page_assets.py --json
python hardware/mock/build_fixtures.py --check
python -m unittest discover -s hardware/tests/mock -v
$cc = 'D:\programmming\Dev-Cpp\MinGW64\bin\gcc.exe'
python hardware/tests/reader/test_reader.py --cc $cc
python hardware/tests/board/test_touch.py --cc $cc
```
