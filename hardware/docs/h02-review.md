# PR #39 H02 定向复查

复查日期为 2026-11-17，原始 HEAD 为 `fe014daf9b067188fc9be76f4963ed3284394bf3`。复查范围为 GT911、LVGL、RGB565 刷新和缓冲内存。PR 保持 Draft。复查当日尚未上板。2026-11-24 已通过基础显示、GT911、LVGL 交互及初始化堆快照，结果和待补项目见[实板验收报告](reports/h01-h02-bringup-report-20261124.md)。

## GT911 协议与局部修复

核对 Goodix 编程指南的 3.2、3.3、5 节，以及锁定微雪提交中的 `12_lvgl_transplant/components/touch/gt911.c`。状态寄存器 `0x814E` 的 bit7 表示数据就绪，读取就绪报告后写零确认。无就绪数据时保留末次样本。`0x8057/0x8058` 的坐标变化阈值可能抑制静止坐标上报，因此单纯等待超时无法可靠判定抬手。Espressif GT911 驱动也采用读取状态、坐标、确认报告的顺序，其缓存取点接口会消费缓存，不能直接把该接口每次返回零点等同于硬件抬手。[Goodix 编程指南](https://www.crystalfontz.com/controllers/GOODIX/GT911ProgrammingGuide/478/) · [微雪锁定源码](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/blob/44e412f08c1e68c0da826bc5f51436f156a02eea/examples/esp-idf/12_lvgl_transplant/components/touch/gt911.c) · [Espressif GT911 驱动](https://github.com/espressif/esp-bsp/blob/master/components/lcd_touch/esp_lcd_touch_gt911/esp_lcd_touch_gt911.c)

确认原代码在故障取消后立即接受同一手指的后续单点报告，抬手时可生成一次误点击。局部修复增加等待释放状态，并拒绝异常大面积、触摸键、HotKnot 接近 ID 32 和按住期间的跟踪 ID 跳变。坐标仍限制在 X 0～799、Y 0～479，继续使用原横屏映射。

| 输入报告或故障 | 对 LVGL 的处理 |
| --- | --- |
| 就绪单点，读取及确认成功 | 接受坐标并上报 PRESSED，持续按住保持同一接触。 |
| 无新数据，bit7 为零 | 返回内部状态 `ESP_ERR_NOT_FINISHED`，保留末次状态和坐标，不读取陈旧坐标、不确认报告。 |
| 就绪零点，确认成功 | 正常接触转 RELEASED，由 LVGL 按原点击规则处理。故障后的等待状态在此解除。 |
| 状态/坐标读取或报告确认失败 | 调用 `lv_indev_reset` 取消当前接触，上报 RELEASED，进入等待释放状态。 |
| 多点、异常点数、越界、大面积、触摸键、接近报告或跟踪 ID 跳变 | 已就绪报告完成确认后取消接触，进入等待释放状态。 |
| 等待释放期间的单点或无新数据 | 保持 RELEASED，直到收到并成功确认有效零点报告。同一接触恢复通信后不会重新触发点击。 |

若抬手报告丢失且后续 I²C 一直成功但无新数据，当前实现仍可能保留 PRESSED。协议允许静止触点无新数据，暂时无法区分这两种情况。若已经进入等待释放状态，丢失的抬手报告会使下一次完整触摸也被忽略，待其零点报告确认后恢复。控制器固件、实际阈值和释放报告行为需实板记录，状态为 **NOT_TESTED**。

## LVGL、锁和释放顺序

- 使用锁定的 LVGL 9.2.0。源码确认 `lv_indev_reset` 会取消活动对象的按下状态，并在输入回调返回后处理 reset 标志，故障释放不会作为正常抬手点击。主机回归使用该版本真实的输入及按钮事件处理。[LVGL 输入实现](https://github.com/lvgl/lvgl/blob/v9.2.0/src/indev/lv_indev.c)
- `esp_timer_get_time()` 提供毫秒 tick。LVGL 任务优先级为 4，栈为 6,144 B，在同一把 FreeRTOS 递归互斥锁下执行 `lv_timer_handler()`，每轮休眠 10～20 ms。生成配置 `CONFIG_LV_DEF_REFR_PERIOD=33`，输入定时器使用该 33 ms 默认周期。I²C 每次超时为 20 ms，一轮触摸读取最多涉及状态、坐标、确认三次事务，故障时会延迟 UI 和锁的获取，实际延迟需实板测量。
- `create_ui` 在任务启动前运行，`xTaskCreate` 是最后一个可失败初始化步骤。失败路径依次删除输入设备及其定时器、显示及对象、LVGL 剩余定时器，再释放绘制缓冲、板端资源及互斥锁。RGB 驱动先停止扫描及 DMA，再释放扫描/弹跳缓冲。现有成功生命周期由板级组件持续持有资源。按现有调用约定审查未发现死锁或释放后访问路径。运行期成功退出流程尚未提供。[ESP-IDF RGB 驱动](https://github.com/espressif/esp-idf/blob/v5.5.5/components/esp_lcd/rgb/esp_lcd_panel_rgb.c)
- 其他任务必须先成功获取 `board_4_3c_lock`，再调用 LVGL 或读取触摸，并成对解锁。初始化由 `app_main` 单次调用。递归锁允许 LVGL 回调调用背光接口。调度、内存分配失败及外设释放的真实时序为 **NOT_TESTED**。

## RGB565 局部刷新

`esp_lcd_panel_draw_bitmap` 同步将局部像素复制到扫描帧缓冲。LVGL 区域的闭区间终点通过 `+1` 转成驱动的开区间终点，RGB565 行步长对齐为 1，奇数宽度区域也按紧密行排列。复制返回后调用 `lv_display_flush_ready` 归还绘制缓冲。

原有 `flush_status` 保存错误，首次错误输出 `RGB flush failed`，后续成功刷新保留该错误记录。启动刷新失败会走初始化清理，运行期失败继续归还缓冲。主机测试确认重复失败仅记录首次错误，且成功调用后错误状态仍保留。失败区域可能保留旧像素，直到后续重绘覆盖。驱动调用成功也无法证明扫描、色序和总线时序正确。当前单扫描帧缓冲的撕裂和故障后画面一致性为 **NOT_TESTED**。

## 内存核算

| 用途 | 实际配置与字节数 |
| --- | --- |
| PSRAM 扫描帧缓冲 | `num_fbs=1`，`fb_in_psram=true`，800 × 480 × 2 = **768,000 B**。 |
| 内部 DMA 弹跳缓冲 | `bounce_buffer_size_px=800*10`，ESP-IDF 分配两块 INTERNAL/DMA/8BIT 缓冲，各 16,000 B，共 **32,000 B**。 |
| LVGL 绘制缓冲 | 一块 800 × 20 × 2 = **32,000 B**，64 B 对齐，INTERNAL/8BIT 分配，由 CPU 复制到扫描帧缓冲。 |
| LVGL 内置对象内存池 | 生成配置 `CONFIG_LV_MEM_SIZE_KILOBYTES=64`，**65,536 B** 静态池，已计入 `.bss`。 |
| LVGL 任务栈 | **6,144 B**，另计 FreeRTOS TCB、锁、RGB DMA 描述符及驱动对象。 |

三类像素缓冲合计 **832,000 B**，其中 PSRAM 768,000 B、内部堆 64,000 B。静态池已包含在本次链接的 `.bss` 68,408 B 中，不能再次当作初始化堆分配相加。链接 `.data` 为 13,588 B。分配器管理和对齐开销、其他任务及驱动开销也会消耗内存。PSRAM、内部堆和 DMA 堆的初始化快照已在实板记录，为 **PASS**。连续操作后的 free、min、largest 仍为 **NOT_TESTED**。

## 自动化验证

2026-11-17 在 Windows PowerShell 使用 ESP-IDF v5.5.5，SDK 提交 `b774170ff46c393eeb5e495ea37936038d3f4f4f`、Xtensa GCC 14.2.0、CMake 3.30.2、Ninja 1.12.1 执行下列命令。

```powershell
# 在已激活 ESP-IDF v5.5.5 的终端，从仓库根目录执行
Set-Location .\hardware\firmware
idf.py --version
idf.py set-target esp32s3
if ($LASTEXITCODE -ne 0) { throw 'set-target failed' }
idf.py build
if ($LASTEXITCODE -ne 0) { throw 'build failed' }
idf.py size
```

`set-target` 日志确认执行 `fullclean`，随后重新配置。`set-target`、`build`、`size` 的实际退出码均为 **0**。构建完成 1,203 步，应用 `.bin` 为 **462,944 B**（`0x71060`），4 MiB 应用分区余 **3,731,360 B**，分区容量检查通过。`idf.py size` 的未补齐镜像大小为 462,825 B。编译日志未出现编译器 warning/error。

新增回归使用主机 GCC 编译锁定 LVGL 9.2.0，并从生产文件提取原函数，替换 I²C 和面板写入接口进行故障注入。测试覆盖正常按下/持续按住/移动/抬手、4 秒无新报告、三处读取/确认故障、释放确认失败、多点/大面积/触摸键、越界、跟踪 ID、屏幕右下边界及刷新错误锁存。主机执行不覆盖 ESP32 调度、真实 I²C、电气和 DMA 时序。

```powershell
# 从仓库根目录执行，需要 PATH 中有主机 GCC
python .\hardware\tests\board\test_touch.py --cc gcc
```

若主机编译器未加入 PATH，可通过 `--cc` 传入其绝对路径。本次实际使用 `D:/programmming/Dev-Cpp/MinGW64/bin/gcc.exe`。修复后全部用例通过，脚本退出码为 **0**。通过 `--revision fe014daf9b067188fc9be76f4963ed3284394bf3` 对照原始生产函数时，故障后同一触点恢复并抬手导致 `clicks == 0` 断言失败，脚本退出码为 **1**，复现原始误点击。实板补测清单见 [bringup](bringup.md#h02-实板验收)。
