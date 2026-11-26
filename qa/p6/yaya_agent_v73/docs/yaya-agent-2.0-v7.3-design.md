# Yaya Agent 2.0 v7.3｜最小设计

只在独立合成入口 demo.py --v73 选择 SYSTEM_V73。旧 --v7 的提示和数据不变。Qwen 继续自主决定接话、追问、倾听或停一停。系统不按孩子原话分流，也不规定统一字数。既有 Book Evidence Index、Story Timeline、Context Builder、Provider、协议 B 和 REPLY_ONLY 投影不变。

| v7.2 原增量的职责 | v7.3 增量 | 目的与需要观察的代价 |
| --- | --- | --- |
| 不把推测说成事实；没有说什么时候该用书证 | 孩子续编就接最新想象，可以短接或倾听；孩子问原文再直接按已读文字答 | 降低重复讲解；要防进入事实核实时仍无依据地续编 |
| 动机、动作和对象均按书证 | 保留该约束，明确区分角色、孩子与芽芽各自的想法；孩子改口按最新表达 | 降低归属混淆；要防每轮机械提醒来源 |
| 感叹和引用可在正文，实际提问另列 | 原句保留 | 不牺牲 v7.2 正文保留；不擅删正文问题 |

派生的 v7.3 虚构资料把 forest-light p2.e3 结构化动作从“蹲下／看”更正为“蹲下说／自己的猜测”；文字、主体、同页猜测条目均不变。旧被冻结的文件与证据保留。其余 28 条书证和 18 条诊断保持独立人工核对；此更正不代表其它条目已核签。没有用关键词给模型答案。

## 复跑

本目录完全独立；先装 requirements.txt。使用同一 Python 运行 python -m unittest discover -v、node --test test_browser.mjs test_coreading_browser.mjs、python run_v73.py --batch diagnostics --out NEW_MOCK_DIR。另用 --batch live-sample（无 --live）验证同一份固定实跑输入。旧草稿投影用 replay_v73_history.py --help；输入是原 v7.2 离线证据 ZIP 与 v7.2 实跑 JSONL，均不进 Git。生成诊断提示不传评分标签；评分说明独立存 v73_scoring_goals.json。

真实小批：运行 python run_v73.py --batch live-sample --live --prompt-key --base-url BEIJING_WORKSPACE_URL --out NEW_LIVE_DIR。输入密钥时无回显；无密钥不调用。先单次预检，再跑 14 次非空合成回合；保留 request ID、原始正文、原始响应、上下文、最终回执与回退原因。将报告及与其 SHA 对应的原始 record/v7-evidence.jsonl 留存，不上传到 Git，不混同 Mock 或浏览器显示。发布审查只在实跑与一份新单人盲评都完成后。

## 单人盲评执行

14 次新草稿产出后，从完整序列选有代表性的短卡（优先 8 张，覆盖想象、事实、来源与倾听）。在版本未知的情况下阅读共同书证和实际前文。分别对理解、自然度、承接、书证准确、陪伴想象、尊重停问打 1–5 或“不适用/不好判断”；低分写一句具体原因。真模型草稿与实际回执分开呈现。若后续取得同新输入的对照输出，则在同类卡片间平衡甲乙位置并共享前文。不多约一位评审，不用旧结果、静态分析或模型代评填这份答卷。
