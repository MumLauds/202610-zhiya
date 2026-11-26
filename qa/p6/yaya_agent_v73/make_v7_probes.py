import json
from pathlib import Path
here = Path(__file__).parent
source = json.loads((here/"fixtures_v7.json").read_text(encoding="utf-8"))
rows = [
("speaker","forest-light",[("p1","有光。"),("p2","大家都有自己的猜法。"),("p3","我先看一下。"),("p4","刚才说星星的是谁来着？")]),
("sequence","rabbit-post",[("p1","邮局开啦。"),("p2","有信来了。"),("p3","小兔已经把信送进去了吗？")]),
("unread","rabbit-post",[("p1","我看到空篮子。"),("p3","最后信送到了吗？")]),
("revision","forest-light",[("p1","我猜小象提着灯。"),("p2","不对，我改成萤火虫。")]),
("pause-resume","moon-boat",[("p1","先不问我，我想看一会儿。"),("p1","我看好啦，是一片叶子。")]),
("short-context","forest-light",[("p1","大家一起走。"),("p2","谁把那点亮光想成了星星？"),("p3","我说刚才猜星星的，是小狐狸。")]),
]
source["cases"] = [dict(id="v7-probe-"+id, book_id=book, label=f"专项片段 {i}",
                        turns=[dict(page=p, child=c) for p,c in turns])
                   for i,(id,book,turns) in enumerate(rows,1)]
with (here/"fixtures_v7_probes.json").open("x",encoding="utf-8") as f:
    json.dump(source,f,ensure_ascii=False,indent=2)
    f.write("\n")

