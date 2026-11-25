import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./app.js", import.meta.url), "utf8");
const card = {id:"dev-10",arm:0,title:"测试卡",turns:["合成话语","另一句"],page:{e:"虚构书页"},question:"想看看什么？"};
const result = (body) => ({ok:true,json:async()=>body});

async function browserFixture(fetch) {
  const nodes = new Map();
  const node = () => ({textContent:"",children:[],selectedIndex:0, listeners:{},
    addEventListener(name,fn){this.listeners[name]=fn},
    append(n){this.children.push(n)},replaceChildren(){this.children=[]}});
  const document = {visibilityState:"visible",createElement:node, addEventListener(){},removeEventListener(){},
    getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)}};
  let time = 0;
  const ctx = vm.createContext({
    document, performance:{now:()=>time}, AbortController,
    setTimeout:()=>1,clearTimeout(){},requestAnimationFrame(fn){time+=8;fn()},
    fetch:(path,options)=>fetch(path,options,ms=>{time+=ms})
  });
  vm.runInContext(source,ctx);
  await new Promise(setImmediate);
  return {ctx,view:document.getElementById};
}

async function run(next) {
  const {ctx,view}=await browserFixture(async(path,_,tick)=>{
    if(path==="/cards")return result({mode:"LIVE",cards:[card]});
    if(path==="/start")return result({session:"test-session"});
    if(path==="/replay")return result([]);
    tick(next.ms);
    if(next.error)throw next.error;
    return result(next.body);
  });
  await vm.runInContext("turn()",ctx);
  return {row:JSON.parse(vm.runInContext("JSON.stringify(outcomes[0])",ctx)),
    status:view("status").textContent, closed:vm.runInContext("state.closed",ctx)};
}

test("后端及时给出模型正文：记录页面与后端时间", async()=>{
  const {row,closed}=await run({ms:1200,body:{kind:"MODEL",spoken:{reply:"我在听。"},provider_status:"RETURNED",server_ms:1100}});
  assert.equal(row.category,"MODEL_WITHIN_5S");
  assert.equal(row.fallback_reason,null);
  assert.equal(row.server_ms,1100);
  assert.equal(closed,false);
});

test("后端等模型超时：保留服务端原因，允许下一轮", async()=>{
  const {row,status,closed}=await run({ms:4300,body:{kind:"FALLBACK",spoken:{reply:"备用回复"},provider_status:"DEMO_DEADLINE",fallback_reason:"DEMO_DEADLINE",server_ms:4100}});
  assert.equal(row.category,"FALLBACK_WITHIN_5S");
  assert.equal(row.fallback_reason,"DEMO_DEADLINE");
  assert.equal(row.server_ms,4100);
  assert.match(status,/演示后端在时限内未等到模型/);
  assert.equal(closed,false);
});

test("浏览器先等超时：后台耗时未知，结束本卡", async()=>{
  const {row,status,closed}=await run({ms:4700,error:{name:"AbortError"}});
  assert.equal(row.category,"FALLBACK_WITHIN_5S");
  assert.equal(row.fallback_reason,"BROWSER_DEADLINE");
  assert.equal(row.server_ms,null);
  assert.match(status,/4716 毫秒.*重新开始/);
  assert.equal(closed,true);
});

test("其他浏览器收包错误不能记成模型超时", async()=>{
  const {row}=await run({ms:200,error:new TypeError("network error")});
  assert.equal(row.fallback_reason,"BROWSER_FETCH_ERROR");
  assert.equal(row.server_ms,null);
});

test("两句按钮用不同会话；换个说法清掉上一条回复并保留计时记录", async()=>{
  const texts=["我想让这只小船飞上天。","等等，书里真的写它变成船了吗？"];
  const choiceCard={id:"dev-06",arm:0,title:"井沿的纽扣",turns:[],page:{e:"虚构当页原文。补充说明。"},
    book_text:"虚构当页原文。",demo_evidence:"补充说明。",
    preset:[{actual_question:"你想到什么了？",child:"它像小船。",diagnostic_reply:"你想到了小船。"}],
    choices:texts.map((text,i)=>({id:String(i+1),text}))};
  const sent=[];
  let sessions=0;
  const {ctx,view}=await browserFixture(async(path,opts)=>{
    if(path==="/cards")return result({mode:"MOCK",cards:[choiceCard]});
    if(path==="/start")return result({session:"test-"+ ++sessions});
    if(path==="/replay")return result([]);
    assert.equal(path,"/turn");
    sent.push(JSON.parse(opts.body));
    return result({kind:"MODEL",provider_status:"RETURNED",spoken:{reply:"模拟回复 "+sent.length}});
  });
  assert.equal(view("page").textContent,"绘本原文：虚构当页原文。");
  assert.equal(view("demo-evidence").textContent,"演示书页依据：补充说明。");
  const preset=()=>view("preset").children.map(x=>x.textContent);
  const before=preset();
  assert.equal(before[0],"演示预设");
  assert.deepEqual(view("choices").children.map(x=>x.textContent),texts);
  assert.equal(view("choices").hidden,false);
  await view("choices").children[0].listeners.click();
  assert.equal(view("choices").hidden,true);
  assert.equal(view("retry-choice").hidden,false);
  assert.match(view("chat").children[1].textContent,/模拟回复 1/);
  await view("retry-choice").listeners.click();
  assert.equal(view("choices").hidden,false);
  assert.deepEqual(preset(),before);
  assert.deepEqual(view("chat").children,[]);
  await view("choices").children[1].listeners.click();
  assert.deepEqual(sent,[{session:"test-1",choice_id:"1"},{session:"test-2",choice_id:"2"}]);
  assert.equal(view("chat").children[0].textContent,"合成孩子："+texts[1]);
  assert.match(view("chat").children[1].textContent,/模拟回复 2/);
  const rows=JSON.parse(vm.runInContext("JSON.stringify(outcomes)",ctx));
  assert.deepEqual(rows.map(x=>[x.choice_id,x.category,x.session_id,x.displayed_reply]),
    [["1","MODEL_WITHIN_5S","test-1","模拟回复 1"],["2","MODEL_WITHIN_5S","test-2","模拟回复 2"]]);
});
