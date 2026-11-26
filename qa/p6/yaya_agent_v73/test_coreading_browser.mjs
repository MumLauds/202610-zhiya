import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
import test from "node:test";
import vm from "node:vm";

const js=readFileSync(new URL("./coreading.js",import.meta.url),"utf8"), html=readFileSync(new URL("./coreading.html",import.meta.url),"utf8");
class Element {
  constructor(){this.children=[];this.handlers={};this.selectedIndex=0;this.ownText="";}
  set textContent(v){this.ownText=v;this.children=[];}
  get textContent(){return this.ownText+this.children.map(x=>x.textContent).join("");}
  append(e){this.children.push(e);} replaceChildren(){this.textContent="";}
  addEventListener(event,fn){this.handlers[event]=fn;}
  click(){if(!this.disabled) return this.handlers.click?.();}
}
const book={id:"b",title:"合成绘本",age:"AGE_4_5",pages:[{id:"p1",text:"当页红石。",facts:["第一条书页依据"]},{id:"p2",text:"未来橘树。",facts:["第二条书页依据"]}]};
const script={id:"c",book_id:"b",label:"合成片段",turns:[{page:"p1",child:"合成一"},{page:"p1",child:"合成二"},{page:"p2",child:"合成三"},{page:"p2",child:"合成四"}]};
async function ready(response,mode="MOCK") {
  const nodes=new Map([...html.matchAll(/ id="([^"]+)"/g)].map(x=>[x[1],new Element()]));
  const htmlNode=key=>{assert.ok(nodes.has(key),key);return nodes.get(key);};
  const doc={visibilityState:"visible",getElementById:htmlNode,createElement:()=>new Element(),addEventListener(){},removeEventListener(){}};
  const seen=[], saved=[]; let time=0, frame=8, seq=0, session, page, visit;
  const context=vm.createContext({
    document:doc,performance:{now:()=>time},setTimeout:()=>1,clearTimeout(){},AbortController,
    requestAnimationFrame:f=>{time+=frame;f();},Blob,URL:{createObjectURL(b){saved.push(b);return "blob:test";},revokeObjectURL(){}},
    fetch:async(path,options)=>{
      const data=options.body&&JSON.parse(options.body);seen.push([path,data]);
      let body;const stamp=()=>({session,mode,case_id:"c",page_id:page,visit_id:visit,state:"CONTINUE"});
      if(path==="/v6/catalog") body={mode:"LIVE",books:[book],cases:[script]};
      else if(path==="/v6/start") {session="sid-"+ ++seq;page="p1";visit=1;body=stamp();}
      else if(path==="/v6/page"){page=data.page_id;++visit;body=stamp();}
      else {assert.equal(path,"/v6/turn");
        const draft={reply:"模拟正文 "+data.number,next_action:"RESPOND",followup_question:null};
        body=await response({...stamp(),kind:mode==="MOCK"?"MOCK":"MODEL",number:data.number,child:script.turns[data.number].child,
          actual_question:null,spoken:draft,draft:{...draft},independent_review:"NOT_PERFORMED",provider_status:"RETURNED",server_ms:31}, x=>{time+=x;}, doc);
      }
      return {ok:true,json:async()=>body};
    }
  });
  vm.runInContext(js,context);
  await new Promise(setImmediate);
  return {el:htmlNode,seen,frame:ms=>{frame=ms},async export(){await htmlNode("download").click(); return JSON.parse(await saved.at(-1).text());}};
}
test("手动翻页前不显示未来页正文/话语，换页保留实际对话，非 CONTINUE 也允许下一轮孩子主动开口",async()=>{
  const u=await ready(r=>({...r,state:["LISTEN","PAUSE","END_TOPIC","CONTINUE"][r.number]}));
  assert.match(u.el("mode").textContent,/MOCK/);assert.match(u.el("page-id").textContent,/4–5/);
  assert.doesNotMatch(u.el("page-text").textContent,/未来/);
  await u.el("send").click();assert.match(u.el("state").textContent,/倾听/);
  await u.el("send").click();assert.match(u.el("state").textContent,/暂停/);
  assert.equal(u.el("send").hidden,true);assert.equal(u.el("page-button").hidden,false);
  assert.doesNotMatch(u.el("next-text").textContent,/合成三/);
  assert.equal(u.seen.filter(([p])=>p==="/v6/page").length,0);
  await u.el("page-button").click();
  assert.equal(u.el("page-text").textContent,"未来橘树。");assert.match(u.el("chat").textContent,/模拟正文 0/);
  await u.el("send").click();assert.match(u.el("state").textContent,/这个话题已结束/);assert.equal(u.el("send").disabled,false);
  await u.el("send").click();assert.equal(u.el("send").hidden,true);
  assert.deepEqual(u.seen.filter(([p])=>p==="/v6/turn").map(([,x])=>x),[0,1,2,3].map(number=>({session:"sid-1",number})));
  const out=await u.export();assert.equal(out.mode,"MOCK");assert.equal(out.timings[0].category,"MOCK_WITHIN_5S");
  assert.deepEqual(out.timings.map(x=>x.page_id),["p1","p1","p2","p2"]);assert.equal(out.timings[3].server_receipt.state,"CONTINUE");
  await u.el("restart").click();assert.equal(u.el("chat").textContent,"");assert.equal(u.el("download").disabled,true);
  await u.el("send").click();const second=await u.export();assert.equal(second.session_id,"sid-2");assert.equal(second.timings.length,1);
});
test("后端回退时区分草稿、核验和页面回执；保留具体原因",async()=>{
  const u=await ready(r=>({...r,kind:"FALLBACK",state:"PAUSE",fallback_reason:"QUESTION_FORMAT",spoken:{reply:"实际备用文本"}}),"LIVE");
  await u.el("send").click();const row=(await u.export()).timings[0];
  assert.match(u.el("chat").textContent,/后端回退/);assert.match(u.el("chat").textContent,/未执行/);
  assert.equal(row.displayed_reply,"实际备用文本");assert.equal(row.server_receipt.draft.reply,"模拟正文 0");assert.equal(row.category,"FALLBACK_WITHIN_5S");assert.equal(row.fallback_reason,"QUESTION_FORMAT");
});
test("浏览器截止后禁止迟到正文显示或沿用不一致会话",async()=>{
  const u=await ready((r,tick)=>{tick(4701);return r;});
  await u.el("send").click();const row=(await u.export()).timings[0];
  assert.equal(row.server_receipt,null);assert.equal(row.source,"BROWSER_DEADLINE");assert.equal(row.server_ms,null);assert.equal(u.el("send").hidden,true);
  assert.doesNotMatch(u.el("chat").textContent,/模拟正文 0/);
});
test("背景、超过五秒、服务端错误分别计数；MODE 取自新会话",async()=>{
  let u=await ready((r,tick,doc)=>{doc.visibilityState="hidden";return r;});
  await u.el("send").click();assert.equal((await u.export()).timings[0].category,"BACKGROUND_EXCLUDED");
  u=await ready((r,tick)=>{tick(4600);return r;});u.frame(210);await u.el("send").click();assert.equal((await u.export()).timings[0].category,"OVER_5S");
  u=await ready(()=>{throw new Error("network");});await u.el("send").click();assert.equal((await u.export()).timings[0].fallback_reason,"BROWSER_FETCH_ERROR");
});
test("空输入显示无发言，不伪造正文、不计模型或回退",async()=>{
  const old=script.turns[0].child;
  try {
    script.turns[0].child="";
    const u=await ready(r=>({...r,kind:"NO_SUBMISSION",provider_status:"NOT_CALLED",draft:null,spoken:null}));
    assert.match(u.el("send").textContent,/不调用模型/);await u.el("send").click();
    const row=(await u.export()).timings[0];assert.equal(row.category,"NO_SUBMISSION");assert.equal(row.displayed_reply,null);
    assert.match(u.el("chat").textContent,/无发言/);assert.doesNotMatch(u.el("chat").textContent,/芽芽：/);
  } finally {script.turns[0].child=old;}
});
test("回执混入其他会话时拒绝显示其草稿",async()=>{
  const u=await ready(r=>({...r,session:"wrong-session"}));
  await u.el("send").click();assert.equal((await u.export()).timings[0].server_receipt,null);
  assert.doesNotMatch(u.el("chat").textContent,/模拟正文 0/);
});

// Optional integration: render the exact served HTML and execute the unmodified browser JS
// in jsdom against a running local MOCK demo. No browser latency/quality or cloud claim.
test("实际 MOCK HTTP 入口 + jsdom：选择、提交、手动翻页、重新开始",{skip:!process.env.YAYA_V6_MOCK_ORIGIN},async()=>{
  const base=new URL(process.env.YAYA_V6_MOCK_ORIGIN);
  assert.equal(base.protocol,"http:");assert.ok(["127.0.0.1","localhost"].includes(base.hostname));
  const catalog=await (await fetch(new URL("/v6/catalog",base))).json();
  assert.equal(catalog.mode,"MOCK","integration test must not spend live tokens");
  const {JSDOM}=createRequire(import.meta.url)("jsdom");
  const dom=await JSDOM.fromURL(new URL("/v6",base).href,{resources:"usable",runScripts:"dangerously",pretendToBeVisual:true,beforeParse(win){
    win.AbortController=globalThis.AbortController;
    win.fetch=(path,opt={})=>fetch(new URL(path,base),{...opt,headers:{...opt.headers,Origin:base.origin}});
  }});
  try{
    const get=k=>dom.window.document.getElementById(k);
    async function wait(fn){const until=Date.now()+5000;while(!fn()){assert.ok(Date.now()<until,"DOM did not update");await new Promise(r=>setTimeout(r,15));}}
    await wait(()=>get("mode").textContent.includes("MOCK")&&!get("case").disabled);
    const idx=catalog.cases.findIndex(c=>c.turns.some(t=>t.page!==c.turns[0].page));
    assert.ok(idx>=0);get("case").selectedIndex=idx;get("case").dispatchEvent(new dom.window.Event("change"));
    await wait(()=>!get("case").disabled);
    const s=catalog.cases[idx],b=catalog.books.find(x=>x.id===s.book_id),initial=s.turns[0].page;
    for(let i=0;i<s.turns.length && s.turns[i].page===initial;++i){get("send").click();await wait(()=>!get("case").disabled);assert.match(get("chat").textContent,/模拟生成/);}
    assert.equal(get("page-button").hidden,false); const target=s.turns.find(t=>t.page!==initial).page,text=b.pages.find(p=>p.id===target).text;
    assert.ok(!dom.window.document.body.textContent.includes(text));get("page-button").click();await wait(()=>get("page-text").textContent===text);
    assert.match(get("chat").textContent,/手动翻页/);get("restart").click();await wait(()=>!get("case").disabled);
    assert.equal(get("chat").textContent,"");
  } finally {dom.window.close();}
});
