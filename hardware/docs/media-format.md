# 知芽 4.3C 封面与内页资源格式与校验说明（H03）

> 任务：H03 绘本封面 / 内页素材校验、图片转换脚本与资源清单生成
> 基线：`MumLauds/202610-zhiya` `main` `3ce9a23`（H05 Mock 已合并；H03 内页增补分支 `feat/hw-story-page-assets`）
> 适用硬件：Waveshare **ESP32-S3-Touch-LCD-4.3C，SKU 33799，无外壳版**（800×480 RGB565、16 MiB Flash、8 MiB PSRAM）
> 本文所有数量、尺寸、哈希与体积均来自本机实际运行 `hardware/tools/prepare_assets.py`、`prepare_page_assets.py` 与对应校验工具的输出；**真机解码与显示均未验证**。

**本文档的边界**：只描述 H03 的素材处理结果。H03 不实现 LCD、LVGL、FreeRTOS 或网络驱动，也不修改 `backend/`、`frontend/`、`database/`、`knowledge-base_v2.5/`、`data/seed/` 的原始文件、`hardware/firmware/` 或 `hardware/mock/`。

---

## 1. 现有 20 张封面的真实检查结果

素材唯一来源是 `data/seed/cover_mapping.json`（20 条记录），脚本不重建、不推断任何 `book_no`、`storybook_id` 或封面路径。20 张原图位于 `knowledge-base_v2.5/图片/`，处理过程**只读**：不重命名、不覆盖、不删除、不就地重新压缩。

检查项：JSON 结构与字段完整性、`book_no`/`storybook_id` 重复、源文件存在性、完整解码（`Pillow.verify()` + `load()`）、扩展名与真实编码是否一致、尺寸 / 色彩模式 / 透明通道 / 帧数 / EXIF 方向、字节数与 SHA-256、异常尺寸、路径是否越界或指向素材目录之外、以及 `storybook_id → story_version_id` 的种子对照。

**结果：20/20 通过，0 个错误，0 个告警。** 封面映射覆盖 20 本已上架的课程原创。

| bookNo | storybookId | storyVersionId | 封面文件 | 源尺寸 | 源字节 | 模式 | SHA-256 前 12 位 |
| --- | ---: | ---: | --- | --- | ---: | --- | --- |
| B-002 | 2 | 2 | PIC-B01_小狐狸的雨伞.png | 2364×1773 | 7,186,438 | RGB | `718e424ea66d` |
| B-003 | 3 | 3 | PIC-B02_青蛙的音乐会.png | 2364×1773 | 5,369,186 | RGB | `3bc5145e793a` |
| B-004 | 4 | 4 | PIC-B03_晚安小熊.png | 2364×1773 | 6,130,052 | RGB | `37c4093ce6af` |
| B-005 | 5 | 5 | PIC-B04_蜗牛的旅行.png | 2364×1773 | 5,026,369 | RGB | `8942de24dc57` |
| B-006 | 6 | 6 | PIC-B05_胡萝卜的种子.png | 2364×1773 | 5,787,390 | RGB | `8e15af27914c` |
| B-007 | 7 | 7 | PIC-B06_猫咪和蝴蝶.png | 2364×1773 | 5,622,507 | RGB | `a0d695842837` |
| B-008 | 8 | 8 | PIC-B07_月亮的小被子.png | 2364×1773 | 6,131,869 | RGB | `9e1f0b1e7220` |
| B-009 | 9 | 9 | PIC-B08_小熊的面包店.png | 1600×1200 | 2,660,337 | RGB | `85333cf18397` |
| B-010 | 10 | 10 | PIC-B09_小鸭子学游泳.png | 2364×1773 | 4,751,590 | RGB | `3fca0103f431` |
| B-011 | 11 | 11 | PIC-B10_云朵棉花糖.png | 2364×1773 | 4,079,893 | RGB | `ecb5d4c30db7` |
| B-012 | 12 | 12 | PIC-B11_蚂蚁搬家.png | 2364×1773 | 6,589,591 | RGB | `736e357cfc54` |
| B-013 | 13 | 13 | PIC-B12_小刺猬的礼物.png | 2364×1773 | 7,122,423 | RGB | `2fa4417d4d7e` |
| B-014 | 14 | 14 | PIC-B13_会唱歌的树.png | 2364×1773 | 7,564,519 | RGB | `71f69cbc5e25` |
| B-015 | 15 | 15 | PIC-B14_萤火虫找朋友.png | 2364×1773 | 6,841,806 | RGB | `7df1f0e5280f` |
| B-016 | 16 | 16 | PIC-B15_小松鼠的冬天.png | 2364×1773 | 6,207,854 | RGB | `d26989674071` |
| B-017 | 17 | 17 | PIC-B16_种子去旅行.png | 2364×1773 | 5,896,003 | RGB | `0e76e6150b55` |
| B-018 | 18 | 18 | PIC-B17_钟表小镇的一天.png | 2364×1773 | 6,273,057 | RGB | `3212df50327c` |
| B-019 | 19 | 19 | PIC-B18_小鲸鱼的大海.png | 2364×1773 | 5,852,881 | RGB | `2cb8e28164ec` |
| B-020 | 20 | 20 | PIC-B19_彩虹桥那边.png | 2364×1773 | 5,105,026 | RGB | `393b260f44de` |
| B-021 | 21 | 21 | PIC-B20_图书馆的小老鼠.png | 2364×1773 | 7,049,674 | RGB | `d610017a40af` |

完整字段（含完整 SHA-256、`verifyOk`、`decodeOk`、`titleMatches`、逐条 issue）见 `hardware/assets/generated/source-audit.json`。

**真实数据特征**

- 20/20 均为 **PNG**，扩展名与真实编码一致；没有「改后缀冒充」的素材。
- 色彩模式全部为 **RGB**，**0 张含透明通道**（无 `RGBA`/`LA`/`tRNS`），因此本次转换没有发生任何 alpha 合成。
- 全部为**单帧**，没有动画 PNG；`info` 中无 ICC 配置、无 DPI、无 EXIF 方向标记（`exifOrientation` 均为 `null`）。
- 19 张为 **2364×1773**（约 4:3），1 张为 **1600×1200**（`B-009 小熊的面包店`，同为 4:3）。没有损坏、截断、零字节或异常长宽比。
- 源文件合计 **117,248,465 字节（111.82 MiB）**，单张 2,660,337～7,564,519 字节。原图合计体积远超 16 MiB Flash，**不得直接入固件**。
- **文件名序号与书号存在固定错位**：`PIC-B01…PIC-B20` 对应 `book_no` `B-002…B-021`。这是素材既有事实，映射文件才是权威；脚本按 `cover_mapping.json` 取路径，不按文件名推断书号，也不修改原文件名。

**种子对照结论**

- 20 条映射的 `storybook_id` 全部存在于 `data/seed/storybooks.json`（21 本，含联调用《逃家小兔》）。
- 每个 `storybook_id` 在 `data/seed/story_versions.json` 中恰好有 1 个版本，因此 `storyVersionId` **可可靠确定**，脚本通过 `storybook_id` 查找得到，而不是直接把 `storybookId` 当作 `storyVersionId`。
- 当前种子里两者的数值恰好相等（2→2、21→21）。这是**数据巧合，不是语义等价**：转换工具用字典查找，校验工具用种子反查并专门比对，测试里也构造了「id=1901 / storybook=901」的数据来防止该错误回流。
- 20 本课程原创的缩略图和详情图均记录 `storyVersionStatus: "PUBLISHED"`。联调用《逃家小兔》仍为 `DRAFT`，未列入封面映射。

---

## 2. 原始资源与设备资源的区别

| 维度 | 原始资源 | 设备资源 |
| --- | --- | --- |
| 位置 | `knowledge-base_v2.5/图片/`（仓库既有素材，只读） | `hardware/assets/generated/`（H03 新增生成物） |
| 格式 | PNG（RGB，无 alpha） | Baseline JPEG（RGB） |
| 尺寸 | 2364×1773 / 1600×1200 | 缩略图 213×160；详情图 400×300 |
| 单张体积 | 2.66～7.56 MB | 缩略图 5,964～12,677 B；详情图 16,856～39,543 B |
| 合计 | 111.82 MiB | 787.2 KiB（40 个文件） |
| 用途 | 内容源、审计对象 | 800×480 屏的书架缩略图与详情页封面 |
| 变更权限 | 任何人不得改名/覆盖/删除/重压缩 | 由脚本重生成，可随时丢弃重建 |
| 授权状态 | 团队原创，仅限课程学习与演示 | 沿用课程学习与演示许可 |

设备资源不是「原图的副本」，而是**重新编码的派生物**：像素经过等比缩放与 JPEG 有损压缩，无法从设备资源还原原图质量。原图仍是唯一权威来源。

---

## 3. 缩略图与详情图的尺寸规则

| assetRole | 上限（宽×高） | 当前实际输出 | 用途 |
| --- | --- | --- | --- |
| `THUMBNAIL` | 240×160 | 213×160（20/20） | 书架网格封面 |
| `DETAIL` | 400×300 | 400×300（20/20） | 绘本详情页封面 |

规则：

1. **等比缩放，绝不拉伸**：取 `scale = min(maxW/w, maxH/h, 1.0)`，宽高同比缩小；输出宽高四舍五入到整数像素后仍受上限约束。
2. **不放大**：源图小于上限时保持原尺寸（本批源图远大于上限，不涉及）。
3. **默认不填充画布**：4:3 素材放进 3:2 的 240×160 框，得到 213×160，右侧留白由 UI 决定。加 `--pad` 才会把内容居中贴到精确 240×160 画布，并用 `--background` 颜色补边。
4. **不裁剪**：不通过裁切改变构图，避免丢失画面信息。
5. 两个角色由同一源图独立缩放，互不派生（详情图不是缩略图的放大）。

尺寸可命令行调整：`--thumb-size 240x160`、`--detail-size 400x300`；一旦修改，Manifest 中 `assetSet.roles` 的上限与每条资源的实际尺寸会一起变化，必须重新生成并重新校验。

---

## 4. 输出格式选择依据

- **默认 JPEG**：封面是连续色调插画，PNG 无损编码在 400×300 下仍然是数百 KB 量级；JPEG q85 把 40 个文件压到 787.2 KiB，适合 16 MiB Flash 的演示分区。
- **Baseline（非 progressive）**：`progressive=False`。渐进式 JPEG 对多数嵌入式解码器（含 LVGL 常用 tjpgd 路径）不友好；基线 JPEG 是兼容性最好的子集。
- **`optimize=True`、`quality=85`**：在体积与观感之间取默认平衡；`--quality` 可调 1～100。质量提高会同时改变 SHA-256 与体积，必须重跑校验。
- **透明通道显式合成**：JPEG 没有 alpha。工具检测到 `RGBA`/`LA`/`PA`/`tRNS` 时，先按 `--background`（默认 `#FFFFFF`）合成再编码，并在 Manifest 写 `alphaComposited: true`；**不会静默丢弃透明信息**。本批 20 张均为 RGB，实际 `alphaComposited` 全为 `false`。
- **EXIF 方向**：读取 `0x0112` 后调用 `ImageOps.exif_transpose`，把像素转正，再**剥离全部 EXIF**，避免设备端重复旋转和元数据泄露。本批无 EXIF，`exifOrientationApplied` 全为 `false`。
- **不做的事**：不引入 RGB565 二进制、SJPG、LVGL 私有图片格式或完整解码库；这些属于后续方案，必须由 H06 结合所选解码器另行设计（见第 10 节）。
- **不允许**：把 PNG 改扩展名冒充 JPEG。输出一律经 Pillow 重新编码，校验工具再用 `Image.open()` 读回真实格式比对。

---

## 5. Manifest 字段说明与示例

文件：`hardware/assets/generated/assets-manifest.json`（36.1 KiB，UTF-8，LF，无生成时间戳以保证可复现与可 diff）。

### 5.1 顶层字段

| 字段 | 含义 |
| --- | --- |
| `schemaVersion` | 固定 `"1.0"`，校验工具据此判断结构 |
| `kind` | 固定 `"device-cover-assets"` |
| `notice` | 本地 DEMO 声明：不代表正式发布授权，`assetPath` 不是 API-024 resource key |
| `assetSet.sourceMapping` / `sourceMappingSha256` | 映射文件路径及其哈希，用于确认「这批资源由哪份映射生成」 |
| `assetSet.sourceRoot` / `outputDir` | 素材目录与输出目录，均为仓库相对 POSIX 路径 |
| `assetSet.mappingRecords` | 参与生成的映射条数（当前 20） |
| `assetSet.roles.THUMBNAIL / DETAIL` | 角色的 `maxWidth`、`maxHeight`、`maxFileBytes`、`count` |
| `assetSet.encoder` | 编码器指纹：工具路径、Pillow 版本、质量、是否渐进、重采样、背景色、是否填充、EXIF/alpha 策略 |
| `assetSet.targetDisplay` / `decoderVerified` | 目标屏 `800x480 RGB565`；`decoderVerified=false` 明确「未在真机验证解码」 |
| `totals` | `assets`、`sourceBytes`、`assetBytes`、`compressionRatio` |

`encoder.pillowVersion` 参与记录，是因为不同 Pillow 版本的 JPEG 编码结果可能不同，进而改变 `sha256`。复现前请先按第 6 节固定版本。

### 5.2 单条资源字段

| 字段 | 含义 |
| --- | --- |
| `bookNo` | 书号，直接取自映射，如 `B-002` |
| `storybookId` | 绘本本体整数主键，取自映射 |
| `storyVersionId` | 版本整数主键，由 `storybook_id` 在种子中唯一匹配得到；无法唯一确定时为 `null` |
| `storyVersionStatus` | 种子版本状态，本批映射的原创版本均为 `PUBLISHED` |
| `title` | 种子标题（UTF-8），供 UI 显示，不参与文件名 |
| `assetRole` | `THUMBNAIL` 或 `DETAIL` |
| `sourcePath` | 原图仓库相对路径（只读） |
| `assetPath` | 设备资源仓库相对路径，如 `hardware/assets/generated/book-b002-thumb.jpg` |
| `devicePath` | **建议的固件内逻辑路径**，`/assets/covers/book-b002-thumb.jpg`；不是 API 资源键，H06 可调整 |
| `width` / `height` | 输出实际像素尺寸 |
| `format` | `"JPEG"` |
| `fileSize` | 输出字节数 |
| `sha256` | 输出文件 SHA-256 |
| `sourceFormat` / `sourceWidth` / `sourceHeight` / `sourceFileSize` / `sourceSha256` | 原图溯源信息 |
| `alphaComposited` / `exifOrientationApplied` | 是否发生透明合成 / 是否应用过 EXIF 旋转 |
| `sizeRatio` | `fileSize / sourceFileSize`，逐条压缩比 |

### 5.3 示例（B-002 缩略图，实际输出）

```json
{
  "bookNo": "B-002",
  "storybookId": 2,
  "storyVersionId": 2,
  "storyVersionStatus": "PUBLISHED",
  "title": "小狐狸的雨伞",
  "assetRole": "THUMBNAIL",
  "sourcePath": "knowledge-base_v2.5/图片/PIC-B01_小狐狸的雨伞.png",
  "assetPath": "hardware/assets/generated/book-b002-thumb.jpg",
  "devicePath": "/assets/covers/book-b002-thumb.jpg",
  "width": 213,
  "height": 160,
  "format": "JPEG",
  "fileSize": 12011,
  "sha256": "ae20451a4ba1aeddd1f9796a94d424dc670a99e9cec5391002a8ac38898e3be4",
  "sourceFormat": "PNG",
  "sourceWidth": 2364,
  "sourceHeight": 1773,
  "sourceFileSize": 7186438,
  "sourceSha256": "718e424ea66de0ce6111f56a04e62e6f1f298588dff5279b210aee01ab5e86fb",
  "alphaComposited": false,
  "exifOrientationApplied": false,
  "sizeRatio": 0.001671
}
```

### 5.4 命名与路径规则

- 设备文件名 = `book-<slug>-<role>.jpg`，`slug` 为 `book_no` 去掉所有非字母数字字符并转小写：`B-002 → b002`，得到 `book-b002-thumb.jpg`、`book-b002-detail.jpg`。
- 文件名全 ASCII、小写、只含 `[a-z0-9-]`，不依赖中文路径；中文标题只出现在 `title` 字段。
- 两个书号若归一化后同名（如 `B-901` 与 `B901`），工具报 `DUPLICATE_ASSET_NAME` 并终止写入，不会静默覆盖。
- 所有 `*Path` 均为仓库相对 POSIX 路径（`/` 分隔），Manifest 内不出现开发者电脑的盘符或绝对路径。
- 重复运行产生**相同文件名、相同顺序、相同内容**；Manifest 不写时间戳，字节级可复现（同一 Pillow 版本下）。

---

## 6. 安装、运行与验证命令

依赖只有 Pillow（测试用标准库 `unittest`）：

```powershell
# 建议在仓库根目录的虚拟环境中安装；版本固定以保证 JPEG 输出与 sha256 可复现
python -m pip install -r hardware/tools/requirements.txt
python -c "import PIL; print(PIL.__version__)"   # 期望 13.0.0
```

```powershell
# 1) 只读审计：不写任何图片，只打印结果并可选输出 JSON 报告
python hardware/tools/prepare_assets.py --audit-only --report hardware/assets/generated/source-audit.json

# 2) 完整生成：审计 + 转换 40 个设备资源 + 写 Manifest
python hardware/tools/prepare_assets.py

# 3) 调整尺寸 / 质量 / 背景（示例）
python hardware/tools/prepare_assets.py --thumb-size 240x160 --detail-size 400x300 --quality 80 --background "#FFFFFF"
python hardware/tools/prepare_assets.py --pad          # 按角色框精确填充

# 4) 独立校验：文件存在性、SHA-256、格式、尺寸、重复、覆盖、体积、未引用文件，
#    以及 bookNo/storybookId/sourcePath 与 cover_mapping.json 的三元一致性和 sourceMappingSha256
python hardware/tools/validate_assets.py
python hardware/tools/validate_assets.py --json          # 机器可读摘要

# 5) 自动测试（离线，临时目录 + 手工构造图片）
python -m unittest discover -s hardware/tests/assets -v
```

**退出码约定**

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部通过（允许存在告警，例如 `DRAFT` 提示） |
| `1` | 数据问题：源图缺失/损坏/格式不符/路径越界/重复/超限/未引用文件/Manifest 不一致 |
| `2` | 无法继续：映射文件缺失、JSON 非法、参数非法、输出目录落在素材目录内 |

**失败时的写入策略**：只要存在错误或资源数量不等于 `映射条数 × 2`，`prepare_assets.py` **不写 Manifest**（避免半个清单覆盖上一次可用清单），但仍保留已成功转换的单张文件，便于定位问题。`validate_assets.py` 默认把「文件缺失」判为错误；在未生成二进制的干净克隆中可加 `--allow-missing` 降级为告警，仅用于结构巡检。

**验证可复算性**：`assetSet.sourceMappingSha256` + `sourceSha256` + `encoder` 三项足以判断「这份 Manifest 是否由同一份映射、同一编码参数生成」。校验工具会重新计算 `cover_mapping.json` 的 SHA-256 并与 `sourceMappingSha256` 比对，同时把每条资源的 `bookNo` / `storybookId` / `sourcePath` 三个字段一起回绑到映射记录——只比对 `bookNo` 覆盖是不够的，互换两本书的绘本 ID 或封面路径时覆盖率、体积、哈希都可以仍然自洽，必须靠三元比对才能发现。

---

## 7. Flash 与 PSRAM 占用估算方法

### 7.1 Flash（存储体积）

实测（本次运行）：

- 20 张缩略图：5,964～12,677 B，合计 **198,512 B**
- 20 张详情图：16,856～39,543 B，合计 **607,578 B**
- 40 个资源合计 **806,090 B（787.2 KiB）**，为原图 117,248,465 B 的 **0.69%**
- Manifest 36,986 B，两者合计约 **823 KiB**

估算方法：

```
资源总量 = Σ fileSize(Manifest 中全部 assets)
分区占用 ≈ 资源总量 + 文件系统元数据 + 块对齐开销
```

- 若走 LittleFS/SPIFFS 分区：按块大小（常见 4 KiB）对齐，每个文件至少占一个块，40 个文件最多再多约 160 KiB 量级；实际以 H06 选定的分区与文件系统参数为准。
- 若编译进固件只读段：还要加上链接对齐与符号表开销，通常可忽略但不应假定为零。
- **演示取舍**：H-Demo 只需要 1～3 本绘本，可按 `bookNo` 子集只烧入 2～6 个文件（约 25～100 KiB），不必把 40 个全部进分区。

### 7.2 PSRAM / 内部 RAM（解码后缓冲）

**文件体积不等于解码后内存。** JPEG 是压缩码流，显示前必须先解码成位图；解码缓冲按像素计，与 `fileSize` 无关。

理论公式（仅计算像素缓冲，不含解码器工作区、LVGL 对象与缓存开销）：

```
RGB565: width × height × 2 字节
RGB888: width × height × 3 字节
```

| 场景 | 尺寸 | RGB565 | RGB888 |
| --- | --- | --- | ---: |
| 缩略图（当前） | 213×160 | 68,160 B（66.6 KiB） | 102,240 B（99.8 KiB） |
| 缩略图上界 | 240×160 | 76,800 B（75.0 KiB） | 115,200 B（112.5 KiB） |
| 详情图 | 400×300 | 240,000 B（234.4 KiB） | 360,000 B（351.6 KiB） |
| 整屏 | 800×480 | 768,000 B（750.0 KiB） | 1,152,000 B（1,125.0 KiB） |

估算方法：

```
同时驻留峰值 ≈ Σ(可见封面解码缓冲) + 解码器工作区 + LVGL 绘制缓冲 + 其他任务
```

- 书架一屏若同时显示 6 张缩略图：6 × 66.6 KiB ≈ 400 KiB（RGB565），尚未计解码器与缓存。
- 详情页 1 张 400×300：234.4 KiB；若 UI 同时保留缩略图，需要叠加。
- 上述全部是**理论值**，不是实测值。H06 必须用 `heap_caps_get_free_size()`、最大连续块与 PSRAM 水位实测后再确定缓存数量与是否降分辨率。

---

## 8. 损坏、缺失、格式不兼容的处理策略

| 情形 | 工具行为（已实现） | 设备侧建议（H06 责任） |
| --- | --- | --- |
| 源文件缺失 | 报 `SOURCE_MISSING`，该书记录失败，其余继续；不写 Manifest，退出码 1 | 不适用（构建期问题） |
| 文件截断/损坏 | `verify()` 与 `load()` 双重检测，报 `SOURCE_VERIFY_FAILED` / `SOURCE_DECODE_FAILED` | 运行期解码失败应显示占位图并允许返回，不得崩溃 |
| 扩展名与真实格式不符 | 报 `FORMAT_EXTENSION_MISMATCH`（按字节判定，不看后缀） | 以 Manifest 的 `format` 为准，不按路径猜格式 |
| 尺寸异常（0 边、超大、极端长宽比） | 报 `SOURCE_DIMENSIONS_ABNORMAL` | 设定最大可解码像素，超限拒绝并占位 |
| `book_no` / `storybook_id` 重复 | 报 `MAPPING_DUPLICATE_*`，重复项不参与生成 | 不适用 |
| 路径越界 / 绝对路径 / 目录穿越 | 报 `PATH_ABSOLUTE` / `PATH_TRAVERSAL` / `PATH_OUTSIDE_SOURCE_ROOT` | 不适用 |
| 输出超过角色框或字节预算 | 报 `ASSET_TOO_LARGE` / `OUTPUT_EXCEEDS_ROLE_BOX`，不写 Manifest | 加载前用 `Content-Length`/尺寸字段做上限检查 |
| 输出目录误设到素材目录 | 报 `OUTPUT_OVERLAPS_SOURCE`，退出码 2 | 不适用 |
| Manifest 与实际文件不一致 | 校验工具报 `ASSET_FILE_MISSING` / `ASSET_SHA256_MISMATCH` / `ASSET_DIMENSION_MISMATCH` / `UNREFERENCED_ASSET` | 烧录/打包前必须让校验通过 |
| Manifest 与 `cover_mapping.json` 不一致（含互换绘本 ID 或封面路径） | 报 `SOURCE_MAPPING_SHA_MISMATCH` / `ASSET_STORYBOOK_ID_MISMATCH` / `ASSET_SOURCE_PATH_MISMATCH` | 重新生成 Manifest，不得手工编辑映射字段 |
| Manifest 条目不是 JSON 对象 | 报 `ASSET_NOT_OBJECT` 并继续检查其余条目，退出码 1 | 不适用 |
| 设备端不支持该编码 | 无法在构建期发现 | 必须由 H06 真机验证；不支持则退回占位或改用已验证格式 |

统一原则：**构建期失败要显式失败，运行期失败要可见降级**。H06 不应把损坏资源当作正常内容渲染，也不应因为一张封面失败而阻塞整个书架。

---

## 9. H06 如何读取和使用这些资源

### 9.1 读取入口

- 资源根：`hardware/assets/generated/`
- 清单：`hardware/assets/generated/assets-manifest.json`
- 每个 `bookNo` 对应两条记录：`assetRole = THUMBNAIL`（书架）与 `DETAIL`（详情页）
- 固件内建议路径：`devicePath`（`/assets/covers/book-b002-thumb.jpg`），H06 可映射到实际分区挂载点或只读段符号名

### 9.2 建议的最小读取契约

| 需求 | 使用字段 |
| --- | --- |
| 按书号取封面 | `bookNo` + `assetRole` → `devicePath` |
| 创建阅读会话/查版本 | `storybookId`、`storyVersionId`（**两个字段不可互换**） |
| 显示中文标题 | `title`（UTF-8），不要从文件名推断 |
| 布局占位 | `width`、`height`（按实际像素创建 `lv_img` 区域） |
| 加载前空间检查 | `fileSize`，对照分区剩余空间与解码缓冲预算 |
| 完整性抽查 | `sha256`（可选，量产或 OTA 场景使用） |
| 内容状态提示 | `storyVersionStatus`（`DRAFT` 时必须显示 DEMO 标识） |

### 9.3 使用约束

- **不要把 `assetPath` 当作 API-024 的 `resource_key`**，也不要把仓库路径暴露成公网 URL。线上受控媒体由后端按版本与授权签发，与本地演示资源是两套体系。
- 本地设备演示包包含封面与内页两套独立资源：20 本课程原创的 40 张封面，以及 104 张 PAGE 内页；两者分别由 `assets-manifest.json` 和 `pages-manifest.json` 索引。
- 设备端按每条资源的 `storyVersionStatus` 展示内容状态。本批 20 本已上架，使用范围仍限非商业课程学习与演示。
- 书架一屏建议只解码可见项；滚动/翻页时释放不可见缩略图，避免 20 张同时驻留。阅读页一次只解码当前页内页，翻页后释放上一页。
- 图片解码必须放在独立任务或分块流程中，不在 LVGL 绘制回调里做文件 IO 与解码（H06/H08 责任）。

### 9.4 是否把图片放进仓库

本次**决定入库全部 40 个封面 JPEG 与 104 个内页 PAGE JPEG**：

- 封面合计 787.2 KiB，内页合计 2,951,679 B（2.81 MiB），对仓库与 16 MiB Flash 的演示子集都可接受；
- 干净克隆即可取用已生成的 JPEG 做 H-Demo。校验 Manifest 前，在仓库根目录运行 `python -m pip install -r hardware/tools/requirements.txt` 安装锁定的 Pillow；
- 原图 111.82 MiB 封面源与 168.43 MiB 内页源不重复入库，`knowledge-base_v2.5/` 保持唯一权威来源。

若后续在设备包中引入更多绘本内页，改为只入库工具、Manifest 与少量演示样本，并对生成物配置忽略规则。`hardware/.gitignore` 已忽略临时文件与半成品清单。

---

## 10. 尚未完成的兼容性验证

以下事项**没有**在本任务中验证，不得据此宣称真机可用：

1. **LVGL / ESP-IDF 的 JPEG 解码是否启用**：候选路径为 LVGL 自带 tjpgd 或 ESP-IDF `esp_jpeg` 组件，选哪条、版本如何锁定由 H06 决定；本任务的 Manifest 只声明 `decoderVerified: false`。
2. **真机显示效果**：未上板，未观察颜色、亮度、缩放锯齿、书架排版留白。
3. **解码内存峰值**：第 7.2 节全部是公式推算，没有 `heap_caps_get_free_size()` 实测数据。
4. **解码耗时与帧率**：未测量单张 400×300 解码耗时，不能承诺翻页/滚动帧率。
5. **不同 Pillow 版本的可复现性**：换版本重生成可能改变 `sha256`；跨机器复现请固定 `Pillow==13.0.0`。
6. **色彩管理**：原图无 ICC 配置，转换未做色彩空间转换，屏上颜色以实板为准。
7. **ESP-IDF 分区与文件系统选择**：本任务不触碰 `hardware/firmware/partitions.csv`，资源如何烧入（LittleFS、SPIFFS、只读段或 TF 卡）由 H06 决定。

结论：H03 交付的是**经过审计与校验的构建期资源与清单**，不是「已在屏幕上显示」的证明。真机解码与显示验收属于 H06。

---

## 11. 内页资源增补（H03-P1/P2/P3）

### 11.1 真实映射与审计

内页任务只处理 20 本课程原创、104 页；`version_id=1` 的《逃家小兔》是 DRAFT 联调本，6 页，明确排除。映射链路为：

`cover_mapping.json` 的 `book_no/storybook_id` → `storybooks.json` 身份 → `story_versions.json` 唯一版本 → `story_pages.json` 的 `(version_id,page_no)` → `image_ref` 真实仓库相对路径。

不使用文件名序号或 `storybook_id == version_id` 猜测。实际审计结果：

| 指标 | 实际值 |
| --- | ---: |
| 映射封面记录 | 20 |
| 绘本 / 版本 | 20 / 20 |
| 内页记录 | 104 |
| 通过 / 失败 / 告警 | 104 / 0 / 0 |
| 原始格式 | PNG 104/104 |
| 原始尺寸 | 1200×900 104/104 |
| 含 alpha / EXIF 方向 | 0 / 0 |
| 原始内页合计 | 176,613,480 B（168.43 MiB） |
| 排除版本 | v1 DRAFT，6 页 |

页面数分布来自种子实际数据：B-002/B-003/B-005/B-006 各 6 页，其余 16 本各 5 页，共 104 页。完整逐页路径、哈希与告警见 `hardware/assets/generated-pages/pages-source-audit.json`。

### 11.2 真实转换样例与视觉核对

默认 PAGE 框为 400×300，等比缩放、不放大、不裁剪、不拉伸。本批全部 1200×900 源图转为 400×300。真实 B-002 首末页对照图见 [`reports/images/h03-b002-pages-preview.jpg`](reports/images/h03-b002-pages-preview.jpg)（JPEG 197,941 B；左侧源图仅缩小用于并排预览，右侧为实际 400×300 设备图）。

| 样例 | 源尺寸 / 字节 | 输出尺寸 / 字节 | 输出文件 |
| --- | --- | --- | --- |
| B-002 第 1 页 | 1200×900 / 1,640,462 B | 400×300 / 23,734 B | `book-b002-v2-p001.jpg` |
| B-002 末页（第 6 页） | 1200×900 / 1,796,677 B | 400×300 / 33,842 B | `book-b002-v2-p006.jpg` |
| B-013 第 1 页（最重） | 1200×900 / 1,978,304 B | 400×300 / 39,357 B | `book-b013-v13-p001.jpg` |

B-013 第 1 页同时是本批输出体积最大的资源，已做视觉核对。源图全部为 1200×900，因此最宽、最高源尺寸相同，取体积最大且细节较多的 B-013 第 1 页作为代表。示例使用的是仓库真实内页，没有使用网图或生成式重绘。

### 11.3 输出目录与命名

```text
hardware/assets/generated-pages/
├── pages-manifest.json
├── pages-source-audit.json
├── book-b002-v2-p001.jpg
├── book-b002-v2-p002.jpg
└── ...  # 104 张 PAGE JPEG
```

命名规则：`book-<slug>-v<storyVersionId>-p<pageNumber:03d>.jpg`，例如 `book-b002-v2-p001.jpg`。文件名全 ASCII、小写、确定性生成。`devicePath` 为 `/assets/pages/<file>`，仅表示 H06 待定本地路径，**不是** API-024 的资源键。

## 12. `pages-manifest.json` 合同

顶层固定 `schemaVersion="1.0"`、`kind="device-page-assets"`，与封面 `device-cover-assets` 分开。关键字段：

| 字段 | 实际内容 |
| --- | --- |
| `assetSet.sourceRoot` | `knowledge-base_v2.5/图片/内页` |
| `assetSet.outputDir` | `hardware/assets/generated-pages` |
| `assetSet.excludedVersions` | v1 DRAFT，6 页 |
| `assetSet.sourceFiles` | `cover_mapping.json`、`storybooks.json`、`story_versions.json`、`story_pages.json` 的路径与 SHA-256 |
| `assetSet.roles.PAGE` | 400×300，单张 256 KiB 上限，count=104 |
| `assetSet.encoder` | `prepare_page_assets.py`、Pillow 13.0.0、JPEG q85、Baseline、LANCZOS、白底、无 pad、EXIF 转正后剥离 |
| `assetSet.decoderVerified` | `false` |
| `totals` | books=20、versions=20、pages=104、assets=104、sourceBytes=176,613,480、assetBytes=2,951,679、compressionRatio=0.016713 |
| `assets` | 按 `bookNo/storyVersionId/pageNumber` 稳定排序 |

单条资源保留 `bookNo`、`storybookId`、`storyVersionId`、`storyVersionStatus`、`title`、`pageNumber`、`assetRole`、`sourcePath`、`assetPath`、`devicePath`、`imageLicenseNote`、`format`、实际 `width/height/fileSize/sha256`、源图 `sourceFormat/sourceWidth/sourceHeight/sourceFileSize/sourceSha256`、`alphaComposited`、`exifOrientationApplied`、`sizeRatio`。不把故事正文或会话数据写进图像清单。

B-002 第 1 页的实际条目：

```json
{
  "bookNo": "B-002",
  "storybookId": 2,
  "storyVersionId": 2,
  "storyVersionStatus": "PUBLISHED",
  "title": "小狐狸的雨伞",
  "pageNumber": 1,
  "assetRole": "PAGE",
  "sourcePath": "knowledge-base_v2.5/图片/内页/PIC-B01_小狐狸的雨伞/PIC-B01_小狐狸的雨伞-P1.png",
  "assetPath": "hardware/assets/generated-pages/book-b002-v2-p001.jpg",
  "devicePath": "/assets/pages/book-b002-v2-p001.jpg",
  "imageLicenseNote": "NO-COMMERCIAL",
  "format": "JPEG",
  "width": 400,
  "height": 300,
  "fileSize": 23734,
  "sha256": "a52d99be4afc2c1eb57f3216a403b2972a4a8d22029317a3e68ec0a5b558b92b",
  "sourceFormat": "PNG",
  "sourceWidth": 1200,
  "sourceHeight": 900,
  "sourceFileSize": 1640462,
  "sourceSha256": "ae87a12508c6d28c1ceec913eb1f003b9ca808b4b2109ad1c87f2d3e6c6509cb",
  "alphaComposited": false,
  "exifOrientationApplied": false,
  "sizeRatio": 0.014468
}
```

## 13. 内页命令、独立校验与测试

```powershell
# 只读审计，不写图片；报告写到指定安全路径
python hardware/tools/prepare_page_assets.py --audit-only --report hardware/assets/generated-pages/pages-source-audit.json

# 生成 104 张 PAGE，先在同级临时目录组装并校验，成功后才替换正式输出
python hardware/tools/prepare_page_assets.py

# 独立校验：重新读取真实种子、原图、输出、哈希、覆盖和未引用文件
python hardware/tools/validate_page_assets.py --json
```

实际结果：

```text
[audit] covers: 20  books: 20  versions: 20  pages: 104  passed: 104  failed: 0  warnings: 0  errors: 0
[audit] excluded versions: v1(DRAFT/6p)
[assets] converted: 104/104  source: 168.43 MiB  asset: 2.81 MiB
[manifest] written: hardware/assets/generated-pages/pages-manifest.json
[result] OK assets=104 errors=0 warnings=0

{"ok": true, "assets": 104, "passed": 104, "failed": 0, "books": 20, "versions": 20, "pages": 104}
```

测试命令：

```powershell
python -m unittest discover -s hardware/tests/assets -v
```

覆盖原有 40 项封面测试，并新增 12 项内页/真实包测试，共 52 项。实际输出：

```text
Ran 52 tests in 45.618s
OK
```

新增覆盖：全量映射、manifest 合同、横竖方图不放大、透明/EXIF、重复页码、URL/目录穿越、损坏源图、输出篡改、源哈希互换、缺失/多余输出、生成失败保留旧包、二次生成字节一致，以及真实 104 页包和原 40 张封面 manifest 的兼容性。

校验失败不能被忽略：`--audit-only` 不写图片；生成阶段先写同级 staging，独立校验通过后才替换正式目录；失败时保留旧的有效资源，不发布半套清单。真实 104 页包已执行二次生成：106 个文件（104 张 JPEG + manifest + 审计）SHA-256 全部一致，`DIFF_COUNT=0`。

## 14. 容量、H05/H06 交接与未验证项

### 14.1 实际容量

| 项目 | 实际体积 |
| --- | ---: |
| 104 张 PAGE | 2,951,679 B（2.81 MiB） |
| 单张最小 / 平均 / 最大 | 12,665 / 28,381.5 / 39,357 B |
| `pages-manifest.json` | 105,500 B |
| `pages-source-audit.json` | 87,755 B（构建审计用，可不进设备包） |
| B-002 全 6 页 | 180,421 B |
| 原 40 张封面 | 787.2 KiB |
| 内页原图合计（只读、不入库） | 176,613,480 B（168.43 MiB） |

当前固件仍是 4 MiB factory 应用分区，没有专用资源分区。104 张 PAGE 加上封面、清单和文件系统元数据约 3.7 MiB；若按 4 KiB 块对齐，104 个 PAGE 文件最多再增加约 416 KiB 开销。**不能直接把全部资源塞进现有应用分区**。建议 H06 先选 B-002 全 6 页 + 1–3 本绘本做离线 DEMO 子集，或另立资源分区 / Micro SD 方案；H03 不修改分区表，也不要求购买 SD 卡。

PSRAM 初始化剩余约 7.26 MiB 不代表可同时解码全部页面。400×300 RGB565 单页像素缓冲为 240,000 B（234.4 KiB），RGB888 为 360,000 B；H06 应按需解码、限制缓存并释放上一页。

### 14.2 H05 交接

H05 后续应按 `(storyVersionId, pageNumber)` 读取 `pages-manifest.json`，把每页记录映射到本地的 `assetPath` / `devicePath`，同时保留 `sourcePath` 作为内容溯源，不把 `devicePath` 冒充 API-024 资源键。封面索引与内页索引独立：封面继续使用 `hardware/assets/generated/assets-manifest.json`，内页使用 `hardware/assets/generated-pages/pages-manifest.json`，不得把 PAGE 塞进旧封面目录。

当前 H03 分支按任务边界**没有修改 H05 Mock**，H05 fixtures 的 `localPageAsset` 仍为 `null`；这是待 H05 单独接入的状态，不是内页资源缺失。H03 已完成的是资源生成、独立 manifest、审计和校验；H05 的 fixture 回填需要在 H05 分支单独完成，并按 `(storyVersionId, pageNumber)` 保持键一致。

### 14.3 H06 与未验证项

- H06 先用 B-002 全 6 页验证离线阅读闭环，缺图使用占位，不用封面冒充正文插画。
- H06 按 `pages-manifest.json` 的 `devicePath` 映射到实际分区/文件系统；当前没有定义资源分区，也没有烧录。
- `decoderVerified=false`：ESP-IDF/LVGL 的 JPEG 解码能力、颜色、耗时、最大连续块、峰值内存和翻页稳定性均需实板验证。
- 同一 Pillow 版本、同参数、同输入重复生成应得到相同文件集合和字节；跨机器/跨 Pillow 版本不保证字节一致。
- 原图、种子、40 张封面及封面 manifest 未在本任务中修改；H03 不生成新插画，不引入 LFS，不修改固件。
