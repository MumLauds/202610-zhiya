"use strict";
const el = (id) => document.getElementById(id);
const outcomes = [];
let state = null, busy = false, mode = null, cards = [], epoch = 0;
const visibleBudgetMs = 4700; // reserve ~300ms for the browser; classify against actual 5000ms.
const reasonText = {
  BROWSER_DEADLINE:"浏览器等待截止，未及时收到后端回复",
  BROWSER_FETCH_ERROR:"浏览器未收到正常的后端响应",
  DEMO_DEADLINE:"演示后端在时限内未等到模型",
  UPSTREAM_BUSY:"上一轮模型请求仍在处理",
  TIMEOUT:"模型请求超时",
  API_ERROR:"模型接口调用失败",
  PARSE_ERROR:"模型接口返回无法解析",
  INCOMPLETE_OUTPUT:"模型未返回完整草稿",
  INVALID_DRAFT:"模型草稿未通过演示格式检查",
  QUESTION_FORMAT:"本轮提问格式不合格，已显示备用回复",
};
function add(parent, tag, text, css) {
  const n = document.createElement(tag);
  n.textContent = text;
  if (css) n.className = css;
  parent.append(n); return n;
}
async function request(path, data, signal) {
  const response = await fetch(path, data ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data),signal} : {signal});
  const body = await response.json();
  if (!response.ok) throw Error(body.error ?? "请求失败");
  return body;
}
function buttons() {
  const choice = Boolean(state?.card.choices);
  el("restart").disabled = busy;
  el("restart").hidden = choice;
  el("card").disabled = busy || cards.length === 0;
  el("choices").hidden = !choice || state.number > 0 || state.closed;
  for (const button of el("choices").children) button.disabled = busy;
  el("retry-choice").hidden = !choice || state.number === 0;
  el("retry-choice").disabled = busy;
  const phrase = state && !state.closed && state.card.turns[state.number];
  const button = el("turn");
  button.hidden = choice;
  button.disabled = !phrase || busy;
  button.textContent = phrase ? "提交合成话语：「" + phrase + "」" : "本卡已结束";
}
async function start() {
  const card = cards[el("card").selectedIndex];
  if (!card) return;
  const n = ++epoch;
  busy = true; buttons(); el("status").textContent = "正在准备演示卡……";
  try {
    const {session} = await request("/start",{id:card.id,arm:card.arm});
    if (n !== epoch) return;
    state = {session, card, number:0, closed:false};
    const pageText = Object.values(card.page).join(" ");
    el("page").textContent = card.choices ? "绘本原文："+card.book_text : pageText;
    el("demo-evidence").hidden = !card.choices;
    el("demo-evidence").textContent = card.choices ? "演示书页依据："+card.demo_evidence : "";
    el("chat").replaceChildren();
    el("preset").replaceChildren();
    el("choices").replaceChildren();
    el("preset").hidden = !card.choices;
    if (card.choices) {
      add(el("preset"),"h3","演示预设");
      for (const row of card.preset) {
        add(el("preset"),"p","芽芽："+row.actual_question);
        add(el("preset"),"p","孩子："+row.child);
        add(el("preset"),"p","芽芽："+row.diagnostic_reply);
      }
      for (const item of card.choices) {
        const button = add(el("choices"),"button",item.text);
        button.type = "button";
        button.addEventListener("click",()=>turn(item.id));
      }
    } else add(el("chat"),"p","芽芽起始问题："+card.question);
    el("status").textContent = "";
  } catch(e) {
    state = null; el("status").textContent = "未能打开演示卡："+e.message;
  } finally { busy = false; buttons(); }
}
function afterPaint() {
  // First rAF is before a paint; the second is at the next frame, conservatively measuring completion.
  return new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
}
async function turn(choiceId) {
  if (!state || busy || state.closed) return;
  const choices = state.card.choices;
  const phrase = choices ? (state.number === 0 ? choices.find(item=>item.id===choiceId)?.text : null) : state.card.turns[state.number];
  if (!phrase) return;
  const sent = performance.now();
  let backgrounded = document.visibilityState !== "visible";
  const visibility = () => { if (document.visibilityState !== "visible") backgrounded = true; };
  document.addEventListener("visibilitychange",visibility);
  busy = true; buttons(); el("status").textContent = "正在等待完整回复……";
  add(el("chat"),"p","合成孩子："+phrase);
  const controller = new AbortController();
  const deadline = setTimeout(()=>controller.abort(),visibleBudgetMs);
  let result, localNote;
  try {
    const payload = choices ? {session:state.session,choice_id:choiceId} : {session:state.session,number:state.number};
    result = await request("/turn",payload,controller.signal);
    if (performance.now()-sent>=visibleBudgetMs) throw Error("late");
  } catch(e) {
    localNote = (e.name==="AbortError" || e.message==="late") ? "BROWSER_DEADLINE" : "BROWSER_FETCH_ERROR";
    result={kind:"FALLBACK",spoken:{reply:localNote==="BROWSER_DEADLINE" ?
      "这次等得有点久，我们先一起看看这一页吧。" : "这次没能接上，我们先一起看看这一页吧。",
      followup_question:null},provider_status:null};
    // A late server thread may have a different history. Do not reuse it or show late replies.
    state.closed=true;
  } finally { clearTimeout(deadline); }
  const kind = result.kind === "MODEL" ? "MODEL" : "FALLBACK";
  const label = kind === "FALLBACK" ? (mode==="MOCK" ? "模拟回退" : "系统回退") :
    (mode==="MOCK" ? "模拟显示" : "Qwen 现场生成 · 合成实验草稿（未经独立事实核验）");
  add(el("chat"),"p",label+"："+result.spoken.reply,kind==="MODEL"?"candidate":"receipt");
  if (result.spoken.followup_question) add(el("chat"),"p","芽芽追问："+result.spoken.followup_question);
  await afterPaint();
  const ms=Math.round((performance.now()-sent)*1000)/1000;
  const reason = localNote ?? (kind==="FALLBACK" ? result.fallback_reason ?? result.provider_status : null);
  outcomes.push({event_time:new Date().toISOString(),mode,card:state.card.id,arm:state.card.arm,turn:state.number+1,
    ms,kind,category:backgrounded||document.visibilityState!=="visible"?"BACKGROUND_EXCLUDED":ms>5000?"OVER_5S":kind+"_WITHIN_5S",
    source:localNote ? reasonText[localNote] : result.provider_status??"unknown",
    fallback_reason:reason,server_ms:result.server_ms??null,
    ...(choices ? {choice_id:choiceId,session_id:state.session,displayed_reply:result.spoken.reply,
      displayed_followup:result.spoken.followup_question??null} : {})});
  document.removeEventListener("visibilitychange",visibility);
  state.number++; busy=false; buttons(); refreshCounts();
  if (backgrounded || document.visibilityState!=="visible") el("status").textContent = "本轮有切到后台，已单列，不计正常样本。";
  else el("status").textContent="本轮用时 "+ms+" 毫秒"+
    (reason ? "；"+(reasonText[reason]??"后端已回退") : "")+
    (localNote ? (choices ? "。点「换个说法再试」可返回选择。" : "。点「重新开始」可继续。") : "。");
}
function refreshCounts() {
  const tally = (cat) => outcomes.filter(x=>x.category===cat).length;
  el("counts").textContent=(mode==="MOCK" ? "模拟 · " : "")+
    "五秒内模型正文 "+tally("MODEL_WITHIN_5S")+" 次；五秒内回退 "+tally("FALLBACK_WITHIN_5S")+
    " 次；超过五秒 "+tally("OVER_5S")+" 次；后台排除 "+tally("BACKGROUND_EXCLUDED")+" 次。";
  el("download").disabled = !outcomes.length;
}
el("download").addEventListener("click",()=>{
  const a=document.createElement("a"); a.download="yaya-page-timings-"+String(mode).toLowerCase()+".json";
  a.href=URL.createObjectURL(new Blob([JSON.stringify({mode,timings:outcomes},null,2)],{type:"application/json"}));
  a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
});
el("card").addEventListener("change",start);
el("restart").addEventListener("click",start);
el("retry-choice").addEventListener("click",start);
el("turn").addEventListener("click",()=>turn());
(async()=>{
  try {
    const data=await request("/cards");
    cards=data.cards; mode=data.mode;
    el("mode").textContent = mode==="LIVE" ? "LIVE · 北京百炼 Qwen3.8-Flash · 协议 B · 仅合成数据" : "MOCK · 本地模拟，未调用模型";
    for(const card of cards) add(el("card"),"option",card.choices ? card.title : card.title+" · "+String.fromCharCode(65+card.arm));
    await start();
  } catch(e) { el("mode").textContent="演示后端未连接："+e.message; }
  try {
    for (const row of await request("/replay")) {
      const d=document.createElement("details"); el("replay").append(d);
      add(d,"summary",row.title+" · "+row.child);
      add(d,"p","孩子原话："+row.child);
      add(d,"p","书页依据："+Object.values(row.page).join(" "));
      add(d,"p","当时待答问题："+(row.question??"无"));
      const draft=add(d,"div","模型草稿（未展示给儿童）","candidate label");
      add(draft,"pre",JSON.stringify(row.draft,null,2));
      const receipt=add(d,"div","实验最终回执","receipt label");
      add(receipt,"p",row.receipt.status+" · "+row.receipt.reason+(row.receipt_text?" · "+row.receipt_text:""));
    }
  } catch(e) { add(el("replay"),"p","历史回放读取失败。"); }
})();
