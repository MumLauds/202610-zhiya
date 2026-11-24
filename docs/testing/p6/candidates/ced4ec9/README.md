# 知芽课程候选版实测与部署交接

2026 年 11 月 20 日，G13、G14 共用产品提交 `ced4ec935df8b855c8dd1669089467c9906cef27`。部署请取远端 `feat/course-delivery-candidate` 并核对完整 SHA。该候选已包含 #69、#70。主线在冻结后合入 #68 原创工坊（`058d385`）；本轮供应商和公网记录只对应各自注明的提交。审查分支还承载归档和主线整合，部署时应使用冻结分支。[冻结提交](https://github.com/MumLauds/202610-zhiya/commit/ced4ec935df8b855c8dd1669089467c9906cef27)

## 现场演示

1. **绘本与知识。** 新库导入 20 本已上架的课程原创绘本。《小狐狸的雨伞》等 8 本已逐页预审，覆盖 43 页正文、8 张封面、43 张内页。8 条有来源的课程知识按草稿交付；本次在独立 SQLite 库以测试管理员批准后验证了检索。介绍素材时署名“妈妈夸我说的队 · 知芽项目自研插画”。正式内容审核由管理员按书号、版本及课程受众留名确认。[逐本依据、署名与许可](../../../g13/course-content-review.md) · [隔离库清单](prepare.json)
2. **主动问答。** 使用 Chrome 154 打开《小狐狸的雨伞》第 1 页，提交“小狐狸站在哪里？”。会话 #1 的回答事件 #3 返回“小狐狸站在门口，看着外面的雨。”，关联知识 #22。相同请求重放返回 200，刷新后仍只有 1 条 ASK。用回答事件调用百炼合成返回 201，取回 142124 字节，送回真实 ASR 返回 200，转写与回答一致。此轮用测试家庭、真实后端与真实供应商，录音输入来自刚合成的音频。[浏览器与语音证据](browser-live.json)
3. **模型对比。** `deepseek-flash` 完成 24 例，每例通用与领域两组各一次，48 条均有输出。领域组 9 例取得 2 条知识，15 例取得 3 条。现场可打开 MC-001：预期是“小熊”，通用组猜测“小猴子”等角色，领域组回答“小熊”。这是单例对照；通用组未提供书页，领域组同时获得书页、系统规则和检索知识。两位评分人尚未打分，组间质量分数留空。[运行元数据](g14.json) · [48 条逐例输出](results-20261120-165333-785904-e3cbd456.jsonl)
4. **香港站。** 北京时间 16:53 和 17:23 两次从外网读取桌面和移动入口、各自入口脚本及健康接口，均为 HTTP 200。两份线上脚本的 SHA-256 都与本候选不同。部署方上一次回执的前后端修订为 `81477f7e773e25c9846cc0c78d32ea4ddd318616`。候选版的公网电脑与实体手机业务链路留待换版后验证。[16:53 逐端摘要](public.json) · [17:23 复核](public-followup.json)

## 部署者交接

管理员界面已另用真实 Chrome 与临时测试账号核验。绘本从草稿发布再撤回，知识从草稿批准，刷新读回后停用，六次写入均已确认。此测试没有供应商调用。[后台界面验证](admin-browser.json)

在新的构建目录检出受测版本，沿用香港站的 HTTPS 入口、现行数据卷、凭据注入和备份流程。旧发布脚本里写有旧版本号、目录、镜像标签及 bundle，复用时须全部对齐下列提交。用该提交中的 `infra/Dockerfile` 构建后端；前端在锁文件对应环境执行 `npm ci` 和 `npm run build -- --mode public`。两个镜像分别标记 `org.opencontainers.image.revision`。

```sh
candidate=ced4ec935df8b855c8dd1669089467c9906cef27
git fetch origin refs/heads/feat/course-delivery-candidate
test "$(git rev-parse FETCH_HEAD)" = "$candidate"
git switch --detach "$candidate"
test -z "$(git status --porcelain)"
```

切流后，对**实际运行**的两个容器分别执行以下检查，将两行结果连同验收时间交回。这里的 `api_container` 和 `web_container` 由部署者填为实际容器名。已有卷中的审核状态须由管理员核对，测试库与测试账号仅留在本地。

```sh
for container in "$api_container" "$web_container"; do
  image_id=$(docker inspect --format '{{.Image}}' "$container") || exit 1
  revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image_id") || exit 1
  test "$revision" = "$candidate" || exit 1
  echo "$container $image_id $revision"
done
```

**冷启动和数据库日志模式。** 20 本绘本的冷书架正在加载时，默认 SQLite 日志模式下两次快速开读均返回 503，分别用时 6.8 秒和 6.9 秒，开始事件均为 0。相同提交改用 WAL（预写日志）后，两次分别在 0.8 秒和 0.9 秒返回 201，每次只写入一条 STARTED。此项对照使用独立新库和空供应商凭据。[默认模式实测](cold-start.json) · [WAL 实测](cold-start-wal.json)

香港部署者先核对实际卷是本机 SQLite 文件，按现有停写流程备份整个卷。用挂载同一卷的一次性后端容器读取现有数据库的模式；下列命令只打开已经存在的文件。若当前不是 WAL，先在恢复副本演练，再在维护窗口对目标库执行第二条。把结果和实际冷启动的开读 HTTP 状态写入回执。

```sh
python -c "import os,sqlite3,pathlib; p=pathlib.Path(os.environ['ZHIYA_DB_PATH']).resolve(); db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True); print(p,db.execute('PRAGMA journal_mode').fetchone()[0]); db.close()"
python -c "import os,sqlite3,pathlib; p=pathlib.Path(os.environ['ZHIYA_DB_PATH']).resolve(); db=sqlite3.connect(p.as_uri()+'?mode=rw',uri=True); mode=db.execute('PRAGMA journal_mode=WAL').fetchone()[0]; db.close(); print(mode); assert mode=='wal'"
```

本机另从 G13 数据副本写入 WAL、完成 checkpoint、关库后冷复制数据库和两份媒体绑定目录。全新目录可读回会话 #1 的 1 条 STARTED、1 条 ASK，P1/P2 的图像摘要与备份前逐项相同，原始 G13 数据库文件摘要保持一致。备份时保留整个卷及实际存在的 SQLite 旁文件。[WAL 冷恢复记录](wal-restore.json)

电脑、实体手机分别使用独立测试家庭，经 HTTPS 完成登录、选档、开读、翻页、刷新、麦克风授权、录音确认、问答、结束、历史与小结活动。记录设备、系统、浏览器、会话、内页图、录音结果和刷新后的记录条数。内页图可选《小狐狸的雨伞》P1 与 P2，分别检查能加载且画面不同。核对已有会话时保持其原内容和进度。正式签收按项目负责人要求暂缓。

## 复核与已知现象

候选版前端有 350 项 Vitest、9 项移动状态机测试通过，公网模式构建通过。候选原始 Python 回归有 448 项通过、1 项失败，另有 305 项子测试通过。唯一失败的 P6 中断恢复用例仍期待初始库内账号和已发布绘本都为 0。#70 的新库已有 1 个种子账号和 20 本上架绘本；测试提交 `666fe98` 更新了这条期望，并保留了重试不重复发布、审核事务回滚的断言。与候选产品代码逐文件一致的归档工作树复跑后，449 项测试、305 项子测试全部通过。

完整 G13、G14 执行步骤见[候选演示脚本](../../课程候选版演示.md)。重新准备会在全新目录生成独立数据库。本轮实际的模型输入库 SHA-256 见 `prepare.json`，逐例结果的 LF 规范化摘要和运行时原始字节摘要均见 `g14.json`。仅复核与评分时，无须再次付费调用模型，可以生成相同计划并对本目录 JSONL 汇总。

审查分支合入 #68 后，在 `13d94bb` 另测了资料维护、绘本、续写与共读的 132 项后端用例、377 项前端 Vitest、9 项移动状态机测试和公网模式构建，全部通过。这次主线整合没有再次调用供应商。

```sh
python qa/p6/run_model_compare.py --outdir ../g14-scoring
python qa/p6/summarize_model_compare.py --outdir ../g14-scoring --results docs/testing/p6/candidates/ced4ec9/results-20261120-165333-785904-e3cbd456.jsonl
```

预演版本 `109e61d` 首次出现的冷开读 503 已在本候选重现，部署者仍需在香港卷验证所选日志模式。Windows Chrome 关闭回环 TLS 连接时出现过连接重置及关闭超时日志，问答、媒体和转写的 HTTP 断言均在关停前完成。
