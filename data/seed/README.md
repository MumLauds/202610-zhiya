# 种子数据

本目录保存 SQLite 初始化种子。首次导入先运行 `python database/init_db.py`，再运行 `python database/import_seed.py`。已有库只需重跑导入命令。

## 文件说明

| 文件 | 内容 | 当前数量 |
| --- | --- | ---: |
| `knowledge_items.json` | 领域知识条目 | 20 条，均为 `DRAFT` |
| `storybooks.json` | 绘本基本信息 | 21 本 |
| `story_versions.json` | 绘本版本信息 | 21 个版本 |
| `story_pages.json` | 绘本分页正文及资源引用 | 110 页 |
| `cover_mapping.json` | 原创绘本编号、整数 ID 与封面文件的映射 | 20 条 |

21 本包括 20 本原创绘本（104 页）及保留用于联调的旧版《逃家小兔》（6 页）。21 个版本均为 `DRAFT`，原创版本须完成审核并记录审核人和审核时间后才能发布。

## 使用与交接

- 字段和表结构见[数据库设计说明书](../../docs/design/妈妈夸我说的队_数据库设计说明书.pdf)和[接口契约](../../docs/design/api-contract.md)。
- 管理员审核本批库内绘本的正文、适龄性、使用授权和资源完整性。资源齐备并审核通过后，服务端从鉴权上下文取得 `reviewed_by_id`，以服务器时间记录 `reviewed_at`，并与 `PUBLISHED` 状态在同一事务保存。
- 104 页原创绘本的 `image_ref` 指向 `knowledge-base_v2.5/图片/内页/` 下对应书名和页号的 PNG。旧版联调用例的 6 页保留画面描述。已有库重跑导入时，仅回填来源匹配的库内种子草稿中仍为 `/assets/placeholder.png`、且正文和配图许可均与种子一致的页。已审核版本和人工改图均保留。
- 20 条知识当前均为 `DRAFT`；来源与内容的审核及后续检索启用须按团队确定的流程完成。
- 种子的 `task_types_json` 对应接口的 `taskTypes`，枚举值按[公共术语与枚举字典](../../docs/standards/glossary.md)定义的知识任务类型原样传递。
- 封面映射中的路径指向 `knowledge-base_v2.5/图片/` 下的仓库素材。客户端使用 API-008/009 返回的受控媒体键，通过 API-024 读取图片。知识条目来源链接指向 v2.5 文档。
