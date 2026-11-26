"use strict";
const el = id => document.getElementById(id);
const eventLabels = {CONTINUE:"继续交流", LISTEN:"倾听", PAUSE:"暂停交流", END_TOPIC:"这个话题已结束"};
const reasonLabels = {BROWSER_DEADLINE:"浏览器等待截止",BROWSER_FETCH_ERROR:"浏览器没有收到有效回执",DEMO_DEADLINE:"后端等待模型截止",UPSTREAM_BUSY:"上次模型请求仍在处理",TIMEOUT:"模型请求超时",API_ERROR:"模型接口调用失败",INVALID_DRAFT:"草稿格式未通过检查",PARSE_ERROR:"模型响应解析失败",QUESTION_FORMAT:"追问格式未通过检查",INCOMPLETE_OUTPUT:"模型输出不完整"};
let catalog, active, busy = false;
const BOOK_AGE = {AGE_3_4:"3–4 岁",AGE_5_6:"5–6 岁",AGE_4_5:"4–5 岁"};
function add(parent, tag, value, css) {
  const node = document.createElement(tag); node.textContent = value ?? "";
  if(css) node.className = css;
  parent.append(node); return node;
}
async function request(path, payload, signal) {
  const response = await fetch(path, payload ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal} : {});
  if(!response.ok) throw Error("HTTP "+response.status);
  return response.json();
}
function resetView() {
  for(const id of ["chat","page-text","title","page-id","facts","state","status","next-text"]) el(id).replaceChildren();
  el("notes").hidden=true; el("notes").open=false;
  el("mode").textContent="正在准备会话……"; el("counts").textContent="尚未提交。";
}
function controls() {
  const turn=active?.script.turns[active.number];
  const change=Boolean(turn && turn.page !== active.page_id);
  el("case").disabled=busy || !catalog;
  el("restart").disabled=busy || !catalog;
  el("send").hidden=change || !turn || active?.closed;
  el("send").disabled=busy || el("send").hidden;
  el("page-button").hidden=!change || active?.closed;
  el("page-button").disabled=busy || el("page-button").hidden;
  el("download").disabled=busy || !active?.rows.length;
  el("send").textContent=turn && !turn.child.trim()?"记录本轮留白（不调用模型）":"提交这句话";
  el("next-text").textContent= !active ? "" : active.closed ? "本次会话已停下，请重新开始。" :
    !turn ? "这段合成对话已完成。" : change ? "准备好后手动翻页，再继续共读。" : (turn.child.trim() ? "下一句合成话语：「"+turn.child+"」" : "本轮预设为空，不代孩子发言。");
}
function drawPage() {
  const {book,page_id,state}=active;
  const page=book.pages.find(p=>p.id===page_id);
  if(!page) throw Error("当前书页不在本书内");
  el("title").textContent=book.title;
  el("page-id").textContent="当前书页 · "+(book.pages.indexOf(page)+1)+(BOOK_AGE[book.age] ? "　"+BOOK_AGE[book.age] : "");
  el("page-text").textContent=page.text;
  el("facts").replaceChildren(); el("notes").open=false;
  for(const fact of page.facts ?? []) add(el("facts"),"li",typeof fact==="string" ? fact : fact.text);
  el("notes").hidden=!el("facts").children.length;
  el("state").textContent="交流状态："+eventLabels[state];
}
function validState(data) {
  return Object.hasOwn(eventLabels,data.state) && ["LIVE","MOCK"].includes(data.mode) && typeof data.session==="string";
}
async function start() {
  if(!catalog || busy) return;
  const script=catalog.cases[el("case").selectedIndex], book=catalog.books.find(b=>b.id===script.book_id);
  busy=true; active=null; resetView(); controls();
  try {
    const s=await request("/v6/start",{case_id:script.id});
    if(!validState(s) || !s.session || s.case_id!==script.id) throw Error("新会话回执无效");
    active={...s,book,script,number:0,rows:[],closed:false};
    el("mode").textContent=(s.version === 7 ? "v7 · " : "") + (s.mode==="LIVE" ? "LIVE · 北京百炼 Qwen3.8-Flash · 协议 B · 合成演示" : "MOCK · 本地模拟，不调用百炼");
    drawPage();
  } catch(e) { active=null; el("mode").textContent="会话未启动："+e.message; }
  finally { busy=false; controls(); }
}
function sameSession(s,r) {
  return s.session===r.session && s.case_id===r.case_id && s.mode===r.mode && s.version===r.version && validState(r);
}
async function flip() {
  const s=active, page=s?.script.turns[s.number]?.page;
  if(!s || busy || s.closed || !page || page===s.page_id) return;
  busy=true; controls();
  try {
    const r=await request("/v6/page",{session:s.session,page_id:page});
    if(!sameSession(s,r) || r.page_id!==page) throw Error("翻页回执不匹配");
    s.page_id=r.page_id; s.visit_id=r.visit_id; s.state=r.state;
    drawPage(); add(el("chat"),"li","手动翻页 · "+el("page-id").textContent);
    el("status").textContent="已翻页，前页对话仍在上方。";
  } catch(e) { s.closed=true; el("status").textContent="未能确认翻页："+e.message+"。请重新开始。"; }
  finally { busy=false; controls(); }
}
function counts(s) {
  const n=cat=>s.rows.filter(r=>r.category===cat).length;
  el("counts").textContent=(s.mode==="MOCK"?"模拟 · ":"")+"五秒内"+(s.mode==="MOCK"?"模拟正文 ":"模型正文 ")+n(s.mode==="MOCK"?"MOCK_WITHIN_5S":"MODEL_WITHIN_5S")+" 次；五秒内回退 "+n("FALLBACK_WITHIN_5S")+" 次；超过五秒 "+n("OVER_5S")+" 次；后台排除 "+n("BACKGROUND_EXCLUDED")+" 次；空输入 "+n("NO_SUBMISSION")+" 次（不计等待统计）。";
}
async function send() {
  const s=active, turn=s?.script.turns[s.number];
  if(!s || busy || s.closed || !turn || turn.page!==s.page_id) return;
  const t=performance.now(), number=s.number, ctl=new AbortController();
  let background=document.visibilityState!=="visible";
  const onHide=()=>{if(document.visibilityState!=="visible") background=true;};
  document.addEventListener("visibilitychange",onHide);
  const timer=setTimeout(()=>ctl.abort(),4700);
  busy=true; controls(); el("status").textContent="正在等待完整回复……";
  const row=add(el("chat"),"li","");
  add(row,"p","第 "+(number+1)+" 轮 · "+el("page-id").textContent,"label");
  add(row,"p",turn.child.trim()?"合成孩子："+turn.child:"合成孩子：本轮没有发言");
  let receipt=null,local=null;
  try {
    const r=await request("/v6/turn",{session:s.session,number},ctl.signal);
    if(performance.now()-t>=4700) throw Error("late");
    if(!sameSession(s,r) || r.number!==number || r.page_id!==s.page_id || r.visit_id!==s.visit_id ||
      r.child!==turn.child || (turn.child.trim() ? ![s.mode==="MOCK"?"MOCK":"MODEL","FALLBACK"].includes(r.kind) || typeof r.spoken?.reply!=="string" : r.kind!=="NO_SUBMISSION" || r.spoken!==null)) throw Error("invalid receipt");
    receipt=r;
  } catch(e) {local=ctl.signal.aborted || e.name==="AbortError" || e.message==="late" ? "BROWSER_DEADLINE":"BROWSER_FETCH_ERROR"; s.closed=true;}
  finally {clearTimeout(timer);}
  const kind=receipt?.kind??"FALLBACK", reason=local??receipt?.fallback_reason??null;
  const spoken=receipt?.spoken ?? {reply:local==="BROWSER_DEADLINE"?"这次等得有点久，我们先一起看看这一页吧。":"这次没能接上，我们先一起看看这一页吧。",followup_question:null};
  const empty=kind==="NO_SUBMISSION";
  const label=empty?"无发言 · 未调用模型":kind!=="FALLBACK"?(s.mode==="MOCK"?"模拟生成 · 不是 Qwen 回复":"Qwen 现场生成 · 合成实验草稿（未经独立事实核验）"):
    (local?"页面回退":"后端回退")+" · "+(reasonLabels[reason]??reason??"未给出原因");
  add(row,"p",label,"label"); if(!empty) add(row,"p","芽芽："+spoken.reply,"spoken");
  if(spoken.followup_question) add(row,"p","芽芽追问："+spoken.followup_question,"spoken");
  const details=add(row,"details","");
  add(details,"summary","查看草稿、核验与最终回执");
  add(details,"p","当时待答："+(receipt ? receipt.actual_question??"无（孩子主动表达）":"未知，未收到可用回执"));
  add(details,"p",s.mode==="MOCK"?"模拟草稿（非真实模型）":"模型原始草稿（解析字段）");
  add(details,"pre",receipt?.draft ? JSON.stringify(receipt.draft,null,2) : "该回执没有附上草稿，不代表模型一定没有生成。");
  if(receipt?.display_decision?.kind==="REPLY_ONLY")
    add(details,"p","展示决策：保留原正文，不展示另列追问 · "+receipt.display_decision.reason);
  else if(receipt?.display_decision?.kind==="FALLBACK")
    add(details,"p","展示决策：使用回退 · "+receipt.display_decision.reason);
  add(details,"p","独立事实核验："+(receipt?.independent_review==="NOT_PERFORMED"?"未执行":receipt?.independent_review??"未知"));
  add(details,"p","后端最终回执："+(receipt ? receipt.kind+" · "+receipt.provider_status : "页面截止前未取得有效回执"));
  add(details,"p","页面实际显示："+label);
  await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));
  const ms=Math.round((performance.now()-t)*1000)/1000;
  document.removeEventListener("visibilitychange",onHide);
  const category=empty?"NO_SUBMISSION":background || document.visibilityState!=="visible" ? "BACKGROUND_EXCLUDED":ms>5000?"OVER_5S":kind+"_WITHIN_5S";
  s.rows.push({event_time:new Date().toISOString(),mode:s.mode,session_id:s.session,case_id:s.case_id,page_id:s.page_id,visit_id:s.visit_id,
    number,turn_number:number,child:turn.child,ms,category,kind,source:local??receipt?.provider_status??null,
    displayed_reply:empty?null:spoken.reply,displayed_followup:spoken.followup_question??null,server_ms:receipt?.server_ms??null,
    fallback_reason:reason,server_receipt:receipt});
  s.number++;
  if(receipt){s.state=receipt.state;el("state").textContent="交流状态："+eventLabels[s.state];}
  else el("state").textContent="交流状态：未确认，请重新开始";
  el("status").textContent="本轮页面用时 "+ms+" 毫秒。"+(reason ? (reasonLabels[reason]??reason)+"。":"")+(category==="BACKGROUND_EXCLUDED"?"已切到后台，不计入前台样本。":"");
  busy=false; controls(); counts(s);
}
function download(){
  const s=active; if(!s?.rows.length) return;
  const a=document.createElement("a"), payload={schema:`yaya-v${s.version ?? 6}-page-timings-v1`,mode:s.mode,session_id:s.session,case_id:s.case_id,timings:s.rows};
  a.download=`yaya-v${s.version ?? 6}-`+s.mode.toLowerCase()+"-"+s.session+".json";
  a.href=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:"application/json"}));
  a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}
el("case").addEventListener("change",start);el("restart").addEventListener("click",start);
el("page-button").addEventListener("click",flip);el("send").addEventListener("click",send);el("download").addEventListener("click",download);
(async()=>{
  try {
    catalog=await request("/v6/catalog"); if(!catalog?.cases?.length) throw Error("缺少合成片段");
    for(const c of catalog.cases) add(el("case"),"option",(catalog.books.find(b=>b.id===c.book_id)?.title??"绘本")+" · "+c.label);
    await start();
  } catch(e) {catalog=null;el("mode").textContent="演示后端未连接："+e.message;controls();}
})();
