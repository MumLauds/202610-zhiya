# H06 离线绘本阅读

H06 运行于 Waveshare 4.3C SKU 33799，复用 ESP-IDF v5.5.5、LVGL 9.2.0 和已验板的 H01/H02。软件基线为 `48844b2ee267bc539ed0580a358470beca13e449`。H06 默认随开发包嵌入三本绘本。H02 仍是未指定模式的默认构建，H07 独立开发。

## 数据和界面

- `pack_reader_demo.py` 从 H05 书架、详情和 DEMO 会话 fixture 读取书目与文案，按 `(storybookId, storyVersionId)` 校验版本。DEMO fixture 指向书架首项《小狐狸的雨伞》，另含《青蛙的音乐会》和《晚安小熊》。三本分别为 6、6、5 页。书架两本一组。详情的简介只有来源提供时才显示。
- H03 清单与 fixture 按书目、版本、用途及页码交叉匹配，JPEG 校验尺寸上限、字节数、SHA-256 和仓库内路径。有效输入生成只读 C 目录和压缩 JPEG 包。资源路径只在构建阶段访问。DEMO 保留课程素材许可边界，详情图和书架缩略图与阅读正文来自各自的记录。
- 界面沿用 H04 的暖米色、森林绿、纸白和鼠尾草色，采用 800×480 原生 LVGL。图框 456×342，400×300 正文图居中，右侧正文卡宽 280。正文用 24 px 子集字体和约 36 px 行距。首尾箭头受业务状态约束，阅读可点击翻页或水平滑动，结束前确认，返回书架保留分页。绑定、设置、朗读和提问以禁用入口展示。

## 状态与所有权

- `reader_core` 的目录、版本、正文和媒体 DTO 不依赖 LVGL，也不依赖 JSON。按显式后继页码前进，按路径栈回退，另记去重访问集合以生成本地小结。ID 使用 64 位，覆盖公共契约的最大整数。会话存于 RAM，重启回到欢迎页。重复结束幂等。保留本地状态类型中的 `INTERRUPTED` 给后续恢复边界，H06 的用户流程只有确认结束。
- UI 事件只投递一项操作，由 LVGL 定时器处理页切换。媒体请求为单槽覆盖队列，结果为单槽有界队列。独立任务串行解码，任务不调用 LVGL。界面代号过期的结果直接释放。换页先删控件并丢弃旧图片缓存，再释放 PSRAM；加载失败保留正文和图片占位。整机运行期只有一个媒体任务，H02 模式不启动它。
- 采用 ESP32-S3 ROM 的 TJpgDec Baseline JPEG 解码入口，避免重复引入第三方解码库。按 JPEG 区块的边界将 ROM RGB888 转为 LVGL 本机字节序 RGB565，解码前匹配预期尺寸。每张最大 240,000 B。显示中、排队结果和解码中各至多一批，每批为一张详情/正文或两张缩略图，像素缓冲保守上界 720,000 B PSRAM。解码工作区 8 KiB，任务栈 4 KiB。保留 H02 的 768,000 B PSRAM RGB 扫描缓冲、32,000 B 内部 DMA 弹跳缓冲和 32,000 B LVGL 绘制缓冲。DMA 堆为内部堆子集。留出的 H07 480,000 B 录音与未来 TLS 内存尚无组合实测。[Espressif TJpgDec 文档](https://components.espressif.com/components/espressif/esp_jpeg/versions/1.3.1/readme)
- 单本 JPEG 包约 231 kB，三本约 626 kB。完整 20 本的封面和正文已有 3,757,769 B 压缩素材，再加既有固件与字体无法放入当前 4 MiB factory 分区。H06 维持现有分区和 Flash 写入范围。

## 中文字体复现

字体为 Noto Sans SC 500，24 px、2 bpp，ASCII 与三本科目和界面实际使用的 Unicode 字符。复用 `firmware/main/OFL-NotoSansSC.txt`。从仓库根目录生成 `glyphs.txt` 后，用 Google Fonts 的 Noto Sans SC 500 文本子集（ASCII 32–126 与 `glyphs.txt` 合并传入 CSS API 的 `text` 参数）取得 TTF。当前 TTF SHA-256 为 `367dd6d705ec1d551f8b8fefd5cfd89b0910fcccd765fb37f36202e9d8418d7e`。字体更新后运行 reader 测试核对所有 UI 和正文所需字形。

```powershell
python hardware/tools/pack_reader_demo.py --out 'D:\zhiya-hardware-workspace\temp\h06-font'
$glyphs = Get-Content 'D:\zhiya-hardware-workspace\temp\h06-font\glyphs.txt' -Raw -Encoding UTF8
npx --yes lv_font_conv@1.5.3 --font NotoSansSC-500.ttf --range 0x20-0x7e --symbols $glyphs --size 24 --bpp 2 --format lvgl --lv-include lvgl.h --no-compress --no-prefilter -o hardware/firmware/main/h06_reader_font.c
```

## 软件对照与后续接口

复核 SRS HW-01～05、API 契约的 ID/版本/事件边界、移动端 `readingSessionMachine.js` 的开始和翻页请求、Web 阅读页与 store、后端 storybooks、reading_sessions、授权入口和媒体实现、schema 的绘本和会话表。最新后端的 API-024 存在受控图片读取和 WebP 变体，本地 `imageRef=null` 的 fixture 仍通过 H03 本地键显示 JPEG。H06 只覆盖 HW-02 的离线演示部分。

H08 可在 DTO 边界增加输运适配。H11 须在 H10 设备授权和正式阅读契约就绪后接入 API-011/013/015/019，使用真实服务端会话、revision、事件幂等键及查询式恢复。H12 必须区分本地资产和 API-024 受控媒体键，按返回格式解码。H13 等待 H07 实板和受控语音链路。H06 的本地会话编号只用于调试与本地流程。
