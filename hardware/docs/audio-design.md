# H07 板载音频设计

H07 为 Waveshare ESP32-S3-Touch-LCD-4.3C（SKU 33799）提供主动录音、短测试音、PCM/WAV 播放及录音回放。H01 启动自检与 H02 显示、触摸继续工作。固件默认使用 H02 页面，H07 页面通过构建配置开启。

## 板级资源

| 资源 | 参考配置 |
| --- | --- |
| I²C0 | SDA GPIO8、SCL GPIO9、400 kHz，与 GT911 和板载 IO 扩展器共用 |
| ES7210 | 7 位地址 `0x40`，驱动常量 `0x80`；输入使能 MIC1/MIC2，I2S0 命名的板上音频网络由 ESP **I2S1** 驱动 |
| ES8311 | 7 位地址 `0x18`，驱动常量 `0x30`；DAC 输出至 NS4150B，再到 SPK |
| I²S | ESP 主机，MCLK/BCLK/WS = GPIO6/44/16，TX 到 ES8311 = GPIO15，RX 来自 ES7210 = GPIO43 |
| 物理格式 | Philips I²S，16 kHz、两路 16-bit slot，MCLK 4.096 MHz，BCLK 512 kHz |
| 功放 | 扩展器 `0x24` 的 EXIO3 高有效，方向寄存器 `0x02`，输出寄存器 `0x03` |

PA_CTRL 的选位来自 V1.1 原理图和锁定音频示例中的 `IO_EXTENSION_IO_3`，交叉核对了托管 BSP 的同一位。微雪产品在线表格曾标为 EXIO4；V1.1 原理图中 EXIO4 属于 SDCS。本轮保持 LCD 背光 EXIO2、触摸复位 EXIO1、SDCS EXIO4 和 USB GPIO19/20 的用途。实际 PCB 修订、功放极性及物理 MIC 到 slot 的对应关系仍待实板核对。固件按 slot 0 生成单声道，右 slot 不并入录音。[锁定微雪源码](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/tree/44e412f08c1e68c0da826bc5f51436f156a02eea/examples/esp-idf/11_speaker_microphone) · [V1.1 原理图](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C/blob/44e412f08c1e68c0da826bc5f51436f156a02eea/hardware/schematics/ESP32-S3-Touch-LCD-4.3C-Schematics.pdf) · [产品页](https://docs.waveshare.com/ESP32-S3-Touch-LCD-4.3C)

组件锁定 `espressif/esp_codec_dev` 1.6.2，使用其 Apache-2.0 ES7210/ES8311 实现，两个驱动的时钟表均包含 16 kHz / 4.096 MHz。代码通过自有短超时适配器使用 ESP-IDF 新 I²C 接口，转换组件的 8 位地址为硬件 7 位地址。适配器保留首个寄存器传输错误，即便组件个别方法返回成功。依赖的其他 codec 不参与 H07 构建。两款字体子集来自 Noto Sans SC，许可随 `OFL-NotoSansSC.txt` 保留，H07 字库生成参数在生成文件头部。[Codec 组件](https://components.espressif.com/components/espressif/esp_codec_dev/versions/1.6.2/readme) · [Noto Sans SC](https://fonts.google.com/noto/specimen/Noto+Sans+SC)

## 所有权与时序

- `board_4_3c` 持有 I²C0 总线和扩展器。音频借用总线，只注册及删除两个 codec 设备。扩展器方向及电平都由同一个互斥锁保护，写成功才更新缓存。功放关闭时先写低再设为输出，失败时返回错误并尝试静音及关功放。回到已知状态时重写完整缓存对应的输出寄存器；对总线失联不声称已关闭实物功放。
- 音频工作任务独占硬件及录音缓冲，使用 6,144 B 栈、优先级 4 和深度 8 的命令队列。状态快照及提交门控用短临界互斥锁。UI 每 100 ms 读取快照，提交后即返回。音频任务不持有 LVGL 锁。板级操作可能按 LVGL 锁 → 扩展器锁 → I²C 驱动锁进入，音频路径不反向获取 LVGL 锁。
- I²S 单次 I/O 最长等待 40 ms，块长 160 帧。通过 I²S1 的 TX 侧产生共用时钟。录音期间 TX 只发静音，功放及 DAC 输出保持关闭，RX 独占有效音频数据。播放只启动 TX，RX 不采集。停止或故障先静音、关功放，然后关闭 RX/codec/TX，再按需删除自身资源；共享总线仍服务触摸。
- 播放前将 DMA 描述符预填静音，恢复 DAC 格式和增益后，再使能功放和解除静音。PCM 有 10 ms 首尾淡化并限幅度比例，默认 20%，上限 40%，DAC 设置为 −18 dB。应用完成判断等待四块尾随静音通过 TX DMA 发送队列。因此正常完成基于 DMA 进展，取消则丢弃剩余播放。百分比不能证明实际扬声器声压。

## 业务音频和接口

输入逻辑格式为 16,000 Hz、单声道、PCM16 little-endian。ADC 双 slot 中提取 slot 0；回放把同一样本复制至 TX 双 slot。与现有后端 WAV 归一化格式一致，H07 只做内存内的本地处理。

调用 `board_audio_init` 前先确认显示初始化完成，初始化和释放由生命周期所有者串行执行，释放前停止客户端提交和状态读取。操作返回 `ESP_OK` 表示队列接受，状态和运行结果通过 `board_audio_status` 查询。异步播放的源缓冲在 `pending` 清除且状态离开 `PLAYING` 前须保持可读。PCM 长度以样本计，WAV 输入以字节计。WAV 只接受同规格未压缩的 RIFF PCM，编码工具根据样本数生成标准 44 字节头。

状态依次为初始化、空闲、录音/播放、已录音或可重试错误。同一时间只接受一个录放操作。有效录音 8,000–240,000 样本，达到 15 秒或样本上限自动停止。开始新录音时清除旧有效录音；取消、过短和采集失败不保留本次内容；播放结束、取消和失败保留已有有效录音。静音会将软件采样和 DAC 输出同时置于静音。队列满时返回超时，冲突操作返回状态错误。

## 内存预算

| 项目 | 理论上限或开销 |
| --- | ---: |
| 有效录音 | 480,000 B PSRAM，首次录音分配，复用至组件释放 |
| I²S 双向 DMA 采样缓冲 | 2 × 4 × 160 × 2 × 2 = 5,120 B 内部 DMA，另计描述符 |
| 任务栈、内含立体声工作块 | 6,144 B，工作块 640 B |
| 其他 | 队列、锁、驱动对象、LVGL 控件与临时启动静音块 |

H02 实板基线来自 [2026-11-24 归档](reports/h01-h02-bringup-report-20261124.md)。内部 DMA 堆是内部堆子集。H07 的稳定性、余量和 LCD/I²S 并发带宽，通过 [实板验收](audio-bringup.md)记录。清除录音后缓冲复用造成的首次保留分配是预期行为；组件释放时才归还缓冲。麦克风仅在用户触发录音时读取，无持久化和上传。
