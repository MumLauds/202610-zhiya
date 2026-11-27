# H06 离线绘本阅读

H06 运行于 Waveshare 4.3C SKU 33799，复用 ESP-IDF v5.5.5、LVGL 9.2.0 和已验板的 H01/H02。软件基线为 `48844b2ee267bc539ed0580a358470beca13e449`。H06 默认随开发包嵌入三本绘本。H02 仍是未指定模式的默认构建，H07 独立开发。

## 数据和界面

- `pack_reader_demo.py` 从 H05 书架、详情和 DEMO 会话 fixture 读取书目与文案，按 `(storybookId, storyVersionId)` 校验版本。DEMO fixture 指向书架首项《小狐狸的雨伞》，另含《青蛙的音乐会》和《晚安小熊》。三本分别为 6、6、5 页。书架两本一组。详情的简介只有来源提供时才显示。
- H03 清单与 fixture 按书目、版本、用途及页码交叉匹配，校验字节数、SHA-256 和仓库内路径。打包复用 H03 的 `jpeg_info`，解码并验证实际尺寸、三分量 RGB JPEG、SOF0 Baseline 和非渐进编码。有效输入生成只读 C 目录和压缩 JPEG 包。资源路径只在构建阶段访问。DEMO 保留课程素材许可边界，详情图和书架缩略图与阅读正文来自各自的记录。
- 界面沿用 H04 的暖米色、森林绿、纸白和鼠尾草色，采用 800×480 原生 LVGL。图框 456×342，400×300 正文图居中。280 px 正文卡留出 20 px 左右内边距，24 px Noto Sans SC 以 36 px 行高完整换行，常规正文最多 8 行，短文垂直居中。超过 8 行时正文卡内以 6 行为一段，通过「上一段」「下一段」完整阅读，不滚动、不省略，不推进故事页码。
- H04 书架原设计每页四本，H06 DEMO 每页两本，保留大封面和详情按钮，三本模式可覆盖分页。点卡片或封面进详情，内容区左右滑动或点箭头分页，返回时保留书架页码。按钮和滑动在成功翻页后共用 250 ms 冷却，手势须在 500 ms 内达到横向 ≥60 px、纵向 ≤40 px。结束小结可对同一本书开启新会话，从第 1 页再读。绑定、设置、朗读和提问以禁用入口展示。

## 状态与所有权

- `reader_core` 的目录、版本、正文和媒体 DTO 不依赖 LVGL，也不依赖 JSON。按显式后继页码前进，按路径栈回退，另记去重访问集合以生成本地小结。ID 使用 64 位，覆盖公共契约的最大整数。会话存于 RAM，重启回到欢迎页。重复结束幂等。保留本地状态类型中的 `INTERRUPTED` 给后续恢复边界，H06 的用户流程只有确认结束。
- UI 事件只投递一项操作，由 LVGL 定时器处理页切换。媒体请求为单槽覆盖队列，结果为单槽有界队列。独立任务串行解码，任务不调用 LVGL。界面代号过期的结果直接释放。换页先删控件并丢弃旧图片缓存，再释放 PSRAM；加载失败保留正文和图片占位。整机运行期只有一个媒体任务，H02 模式不启动它。
- 采用 ESP32-S3 ROM 的 TJpgDec Baseline JPEG 解码入口，避免重复引入第三方解码库。按 JPEG 区块的边界将 ROM RGB888 转为 LVGL 本机字节序 RGB565，解码前匹配预期尺寸。每张最大 240,000 B。显示中、排队结果和解码中各至多一批，每批为一张详情/正文或两张缩略图，像素缓冲保守上界 720,000 B PSRAM。解码工作区 8 KiB，任务栈 4 KiB。保留 H02 的 768,000 B PSRAM RGB 扫描缓冲、32,000 B 内部 DMA 弹跳缓冲和 32,000 B LVGL 绘制缓冲。DMA 堆为内部堆子集。留出的 H07 480,000 B 录音与未来 TLS 内存尚无组合实测。[Espressif TJpgDec 文档](https://components.espressif.com/components/espressif/esp_jpeg/versions/1.3.1/readme)
- 单本 JPEG 包约 231 kB，三本约 626 kB。完整 20 本的封面和正文已有 3,757,769 B 压缩素材，再加既有固件与字体无法放入当前 4 MiB factory 分区。H06 维持现有分区和 Flash 写入范围。

## 中文字体复现

字体为 Noto Sans SC 500，24 px、2 bpp，ASCII 与三本绘本和界面实际使用的 Unicode 字符。复用 `firmware/main/OFL-NotoSansSC.txt`。从仓库根目录生成 `glyphs.txt` 后，用 Google Fonts 的 Noto Sans SC 500 文本子集（ASCII 32–126 与 `glyphs.txt` 合并传入 CSS API 的 `text` 参数）取得 TTF。当前 TTF SHA-256 为 `c3492f6d53c0832a6bc998a55f1b1c9ae685417457df4ae50d2c3d950945d8dd`。字体更新后运行 reader 测试核对所有 UI 和正文所需字形。

```powershell
python hardware/tools/pack_reader_demo.py --out 'D:\zhiya-hardware-workspace\temp\h06-font'
$glyphs = Get-Content 'D:\zhiya-hardware-workspace\temp\h06-font\glyphs.txt' -Raw -Encoding UTF8
npx --yes lv_font_conv@1.5.3 --font NotoSansSC-500.ttf --range 0x20-0x7e "--symbols=$glyphs" --size 24 --bpp 2 --format lvgl --lv-include lvgl.h --no-compress --no-prefilter -o hardware/firmware/main/h06_reader_font.c
```

## 与 H07 的后续集成

用 `git merge-tree --write-tree` 比较 PR #98 审查基线 `e4f3533` 和 PR #93 的 `ab46d41`，得到 `hardware/README.md`、`firmware/main/CMakeLists.txt` 与 `firmware/main/app_main.c` 三处文本冲突。H07 还增加了 Kconfig 音频诊断开关、`sdkconfig.h07`、共享 I²C/扩展器接口和 `esp_codec_dev` 1.6.2 锁定。

在独立集成分支统一三个互斥启动模式。保留默认 H02、H06 阅读和 H07 音频诊断的构建入口。共享 H01 自检及一次 BSP 初始化，按模式选择 UI 和启动对应任务。CMake 和 Kconfig 使用同一选择，处理 ESP-IDF 组件发现及锁文件生成，确认 H06 继续使用 LVGL 9.2.0，H07 使用原音频配置。保留 H07 对共享 I²C 和功放扩展器位的所有权。模式集成后分别构建 H02、H06 单本与三本、H07，并复验 LCD、触摸和 H07 录放。H06 与音频同时运行的内存与时序属于后续集成验收。

## 软件对照与后续接口

复核 SRS HW-01～05、API 契约的 ID/版本/事件边界、移动端 `readingSessionMachine.js` 的开始和翻页请求、Web 阅读页与 store、后端 storybooks、reading_sessions、授权入口和媒体实现、schema 的绘本和会话表。最新后端的 API-024 存在受控图片读取和 WebP 变体，本地 `imageRef=null` 的 fixture 仍通过 H03 本地键显示 JPEG。H06 只覆盖 HW-02 的离线演示部分。

H08 可在 DTO 边界增加输运适配。H11 须在 H10 设备授权和正式阅读契约就绪后接入 API-011/013/015/019，使用真实服务端会话、revision、事件幂等键及查询式恢复。H12 必须区分本地资产和 API-024 受控媒体键，按返回格式解码。H13 等待 H07 实板和受控语音链路。H06 的本地会话编号只用于调试与本地流程。
