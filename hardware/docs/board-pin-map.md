# 4.3C 板级引脚与驱动

目标为 **Waveshare ESP32-S3-Touch-LCD-4.3C，SKU 33799，无外壳版**，ESP32-S3-WROOM-1-N16R8，800×480 RGB565，GT911。依据为厂商 **V1.1 原理图**第 1 页及同一提交的示例。板背丝印、PCB 修订和实物芯片标记由硬件负责人核对。本文的实板状态为 **NOT_TESTED**。

| 信号 | ESP32-S3 或板端映射 | H02 用法 |
| --- | --- | --- |
| RGB VSYNC / HSYNC / DE / PCLK | GPIO3 / GPIO46 / GPIO5 / GPIO7 | 16 MHz，负沿有效。H/V 的同步脉宽、后肩、前肩均为 4/8/8。 |
| RGB data0–4（B3–B7） | GPIO14、38、18、17、10 | RGB565，按此顺序送入 ESP-IDF RGB 驱动。 |
| RGB data5–10（G2–G7） | GPIO39、0、45、48、47、21 | 同上。 |
| RGB data11–15（R3–R7） | GPIO1、2、42、41、40 | 同上。 |
| I²C0 SDA / SCL | GPIO8 / GPIO9 | 400 kHz，使用板载上拉，同一总线接 IO 扩展器和触控。 |
| IO 扩展器 U10 | 7 位地址 `0x24`，方向 `0x02`，输出 `0x03` | H02 使用该板示例寄存器协议，仅 EXIO1/2 配置为输出。 |
| CTP_RST | EXIO1 | 低有效，板端复位 GT911。 |
| CTP_IRQ / GT911 INT | GPIO4 | 释放复位时拉低选择 `0x5D`，随后恢复输入。初始化在需要时探测备用地址 `0x14` 并核对产品 ID。正常运行轮询，不注册 IRQ。 |
| DISP / 背光电源使能 | EXIO2 | 高有效。初次成功刷新后点亮，初始化失败时尽力关断。 |
| BL_PWM | U10 的 EXIO_PWM | V1.1 原理图接背光反馈支路。H02 沿用示例的 DISP 开关，亮度调节与 PWM 设置留待实板核对。 |
| USB D− / D+ | GPIO19 / GPIO20 | 保留 H01 USB Serial/JTAG。 |

原理图 U10 的引脚表与厂商 CH32V003 板端口映射一致。锁定的示例资料仍有 CH422G 注释，托管 BSP 将其列为 CH32V003。H02 按该板的 `0x24` 设备与已核对的方向、输出寄存器工作。I²C 响应仅验证通信，实物料号和修订仍待确认。原理图将 EXIO3 标为 PA_CTRL，EXIO4 标为 SDCS。H02 的显示路径仅使用 EXIO1/2，后续外设应复用同一板级 IO 管理。

## 采用的版本与实现

| 路线 | 对照结果 |
| --- | --- |
| 官方 4.3C 示例路线（采用） | 锁定提交 `44e412f08c1e68c0da826bc5f51436f156a02eea` 的 `03_lcd`、`06_touch`、`12_lvgl_transplant`。沿用引脚、RGB 时序、GT911 寄存器与横屏轴向，使用 ESP-IDF v5.5.5 内置 RGB/I²C，以及经组件管理器固定的 LVGL 9.2.0。本地板级组件管理初始化和失败清理。 |
| 微雪托管 BSP 3.0.1 | 清单要求 ESP-IDF ≥5.3、LVGL 9.4.x 和 esp_lvgl_adapter 0.6 系列，并依赖 CH32V003、音频及 RTC 组件。H02 选择示例所用的 LVGL 9.2.0 并将依赖限定在显示与触控范围。 |

V1.1 的触摸与显示采用原生横屏 0°，无 XY 交换、无镜像。左上为原点，X 向右增至 799，Y 向下增至 479。板级层只上报单点，抬手显示最后一次有效坐标。无新数据保留末次状态，多点或异常样本取消当前点击，并等待有效零点报告确认后恢复接收按下。抬手报告丢失且持续无新数据仍可能保留按下，协议依据、回归和剩余风险见 [H02 定向复查](h02-review.md)。实际触摸方向与边缘余量见 [H02 实板验收](bringup.md#h02-实板验收)。

## 刷新与内存

- 一块 800×480 RGB565 扫描帧缓冲分配在 PSRAM，占 768,000 B。ESP-IDF 为 DMA 使用两个各 10 行的内部缓冲，共 32,000 B。
- LVGL 使用一块 20 行的内部局部绘制缓冲，占 32,000 B。flush 由 ESP-IDF 同步复制到扫描帧缓冲，复制完成后归还绘制缓冲。DMA 持续扫描同一帧，动态刷新时存在撕裂风险。
- LVGL 通过 `esp_timer_get_time()` 取得毫秒 tick，FreeRTOS 任务在递归互斥锁下处理定时器和触摸。其他任务操作 LVGL 须持同一把锁。初始化失败按依赖顺序释放显示、触摸、I²C 和 LVGL 资源，可按 RST 重试。
- 串口在初始化前后及每次测试按钮点击时记录 PSRAM、内部堆和内部 DMA 堆的当前可用量、历史最低量与最大连续块。以上为缓冲预算，实际余量以实板串口记录为准。

H06 可从 `app_main` 调用 `board_4_3c_init(create_ui)`，并在 `create_ui(lv_display_t *display)` 中创建首屏。该回调在 LVGL 任务启动前执行。其他任务通过 `board_4_3c_lock` / `board_4_3c_unlock` 短时访问 LVGL，读取触摸也使用同一把锁。面板、输入设备和任务在初始化成功后由板级组件持有。

参考来源：[V1.1 原理图](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/blob/44e412f08c1e68c0da826bc5f51436f156a02eea/hardware/schematics/ESP32-S3-Touch-LCD-4.3C-Schematics.pdf) · [官方 4.3C 示例](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/tree/44e412f08c1e68c0da826bc5f51436f156a02eea/examples/esp-idf) · [板级参考](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/blob/44e412f08c1e68c0da826bc5f51436f156a02eea/HARDWARE_REFERENCE.md) · [厂商组件说明](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/blob/44e412f08c1e68c0da826bc5f51436f156a02eea/docs/components.md) · [托管 BSP 3.0.1 清单](https://components-file.espressif.com/components/waveshare/esp32_s3_touch_lcd_4_3c.json)
