from pathlib import Path
def patch(name, pairs):
    p = Path(name)
    s = p.read_text(encoding="utf-8")
    for old,new in pairs:
        assert old in s, (name,old)
        s = s.replace(old,new,1)
    p.write_text(s,encoding="utf-8")

patch("coreading.js",[
    ('el("mode").textContent=s.mode==="LIVE" ? "LIVE · 北京百炼 Qwen3.8-Flash · 协议 B · 合成演示" : "MOCK · 本地模拟，不调用百炼";', 'el("mode").textContent=(s.version === 7 ? "v7 · " : "") + (s.mode==="LIVE" ? "LIVE · 北京百炼 Qwen3.8-Flash · 协议 B · 合成演示" : "MOCK · 本地模拟，不调用百炼");'),
    ('s.mode===r.mode && validState(r)', 's.mode===r.mode && s.version===r.version && validState(r)'),
    ('schema:"yaya-v6-page-timings-v1"', 'schema:`yaya-v${s.version ?? 6}-page-timings-v1`'),
    ('a.download="yaya-v6-"+s.mode.toLowerCase()', 'a.download=`yaya-v${s.version ?? 6}-`+s.mode.toLowerCase()'),
])
patch("eval_v6.py", [
    ('== "yaya-v6-page-timings-v1"', '== f"yaya-v{version}-page-timings-v1"'),
    ('UNKNOWN = "无法判断"', '''UNKNOWN = "无法判断"
RUBRIC_V7 = ("绘本理解", "跨页记忆", "时间线", "自然程度", "上下文承接", "倾听能力")
HELP_V7 = ("角色、动作和对象与书文一致；不把想象当事实", "正确关联已经读过的页面；没有相关场景可选无法判断",
           "没有把猜测、准备当成已经发生，不提前说未来结局", "像在与这个年龄的孩子自然交谈",
           "接住孩子这轮表达和改口，而不只是重复原词", "尊重停问意愿，也能接住孩子恢复交流")'''),
    ('    key=dict(batch=digest, reviewers={})', '''    dimensions, help_text = (RUBRIC_V7, HELP_V7) if report.get("version") == 7 else (RUBRIC, RATING_HELP)
    key=dict(batch=digest, reviewers={})
    if report.get("version") == 7:
        key.update(version=7, scope=report["scope"])'''),
    ('zip(RUBRIC,RATING_HELP)', 'zip(dimensions,help_text)'),
    ('    per=defaultdict(dict)', '    rubric = RUBRIC_V7 if key.get("version") == 7 else RUBRIC\n    per=defaultdict(dict)'),
    ('zip(RUBRIC,scores)', 'zip(rubric,scores)'),
    ('    for dim in RUBRIC:', '    for dim in rubric:'),
    ('return {"batch":key["batch"],"evaluation_target":"合成开发对话中的草稿，不是生产安全或泛化验收",','return {"batch":key["batch"],"scope":key.get("scope","DEVELOPMENT_ONLY"),\n            "evaluation_target":"标注范围内的合成模型草稿，不是生产安全认证",'),
    ('            require(req["page_id"] in visited and permitted <= allowed and\n                    all(e["页码"] in permitted and e["证据"].startswith(e["页码"] + ".e")',
     '            texts = {p["页码"]: p["原文"] for p in context["已读相关前页"]}\n            texts[req["page_id"]] = req["book_text"]\n            require(req["page_id"] in visited and permitted <= allowed and\n                    all(e["页码"] in permitted and e["证据"].startswith(e["页码"] + ".e") and e["原文片段"] in texts[e["页码"]]'),
])

