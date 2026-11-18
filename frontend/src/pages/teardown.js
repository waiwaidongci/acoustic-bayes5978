import {research} from '../stores/research.js';
import {session} from '../stores/session.js';
import {teardownApi} from '../api/research.js';
import {runOptions} from '../components/common/business.js';
import {status} from '../components/common/status.js';
import {table} from '../components/common/table.js';
import {esc,fmt,formObject,localTime} from '../utils/format.js';
import {action,bind,toast} from '../hooks/actions.js';

const pct=value=>value===null||value===undefined?'—':(value*100).toLocaleString('zh-CN',{maximumFractionDigits:4})+'%';
const label=value=>value===null||value===undefined?'—':fmt(value);

function planOptions(){return research.teardown.map(p=>'<option value="'+p.id+'" '+(p.id==research.planId?'selected':'')+'>'+esc(p.name)+' · #'+p.id+'</option>').join('')}

function editionLedger(plan){
  if(!plan)return '';
  if(!plan.editions.length)return '<div class="empty">还没有封账版本。先锁定抽取数并记录拆检回件，再按收件截止封账。</div>';
  return '<div class="ledger">'+plan.editions.map(e=>'<a href="#teardown" data-edition="'+e.id+'"><small>第'+e.version_no+'版</small><strong>截止 '+esc(localTime(e.receipt_cutoff))+'</strong>'+status(e.state)+'<time>'+(e.reportable?'估计 '+pct(e.estimated_rate):'暂不报数')+' · '+esc(localTime(e.created_at))+'</time></a>').join('')+'</div>'
}

function strataTable(edition){
  return table(edition.strata.map(s=>({key:s.stratum_key,population:s.population,sample:s.sample,pi:pct(s.inclusion_probability),received:s.received,missing:s.missing,misjudged:s.misjudged,weighted:s.weighted_misjudged===null?'—':fmt(s.weighted_misjudged),state:s.status})),[
    {key:'key',label:'折外判读层'},{key:'population',label:'层内全量'},{key:'sample',label:'锁定抽取数'},{key:'pi',label:'入样概率'},
    {key:'received',label:'已回件'},{key:'missing',label:'缺回件'},{key:'misjudged',label:'样本内误判'},{key:'weighted',label:'计权误判数'},{key:'state',label:'计权状态'}
  ])
}

function estimateSheet(edition){
  const est=edition.estimates||{};
  const reportable=est.reportable===true;
  return '<div class="result-grid"><section><h2>全批误判率</h2>'+(!edition.estimates?'<div class="pending"><h3>尚未分层计权</h3><p>封账后执行分层计权；缺回件或零概率层会先拦住报数。</p></div>':'<div class="metrics">'+
    '<div><dt>计权误判率</dt><dd>'+pct(est.estimated_rate)+'</dd></div>'+
    '<div><dt>计权误判数</dt><dd>'+(est.estimated_misjudged===null?'暂不报数':fmt(est.estimated_misjudged))+'</dd></div>'+
    '<div><dt>拆检样本误判率</dt><dd>'+pct(est.naive_sample_rate)+'</dd></div>'+
    '<div><dt>回件 / 锁定样本</dt><dd>'+fmt(est.received)+' / '+fmt(est.sampled)+'</dd></div></div>'+
    (reportable?'<p class="fine">全批 '+fmt(est.frame_size)+' 条；Σ（层内误判数 ÷ 入样概率）。拆检样本误判率仅作对照，未按入样概率倒数计权，不能直接当全批结论。</p>':'<div class="failure"><h3>各层尚未对账齐，暂不报数</h3><ul>'+est.blockers.map(b=>'<li>'+esc(b)+'</li>').join('')+'</ul></div>')+'</section><aside class="method-sheet"><p class="eyebrow">ESTIMATION RULE</p><p>冻结研究的折外判读分层抽样，层内无放回随机抽取；每层抽取数与入样概率在封账时锁定。全批误判数按入样概率倒数估计，误判率再除以全批样本数。</p><p>封账按收件截止时刻取值；晚到或截止后的更正留给下一版。已发布版本按当时的拆检结论复算。</p><small>冻结输入 SHA256<br><code>'+esc(edition.plan.frame_checksum||'')+'</code></small></aside></div>')
}

function editionPanel(edition){
  if(!edition)return '<div class="pending"><h2>尚未选择版本</h2><p>选择计划并封账后，这里显示各层对账与全批估算。</p></div>';
  const late=edition.pending_late?.length?'<p class="fine">截止前已回件但尚未纳入本版计权：'+esc(edition.pending_late.join('、'))+'。重新执行分层计权即可纳入。</p>':'';
  const after=edition.after_cutoff_count?'<p class="fine">另有 '+edition.after_cutoff_count+' 条记录晚于本版收件截止，留给下一版。</p>':'';
  return '<div class="run-title"><h2>第 '+edition.version_no+' 版 · '+esc(edition.plan.name)+'</h2>'+status(edition.state)+'<small>截止 '+esc(localTime(edition.receipt_cutoff))+' · 研究 #'+edition.plan.run_id+'</small></div>'+
    '<p class="fine">计划锁定 '+edition.plan.frame_size+' 条全批、'+edition.strata.reduce((a,s)=>a+s.sample,0)+' 条抽取；本版冻结回件 '+Object.keys(edition.receipt_snapshot||{}).length+' 条。</p>'+late+after+
    '<div class="split"><section><h2>各层对账</h2>'+strataTable(edition)+'<div class="pagination">'+
    (session.writable&&edition.state==='draft'?'<button id="compute-edition">执行分层计权 ↻</button>':'')+
    '<button class="secondary" id="preview-edition">按当时拆检结论复算 ↗</button>'+
    (session.user.role==='admin'&&edition.state==='draft'?'<button id="publish-edition">对账齐了，发布 ✓</button>':'')+
    '</div>'+(edition.error?'<p class="fine">'+esc(edition.error)+'</p>':'')+'</section><aside>'+estimateSheet(edition)+'</aside></div>'+
    '<div id="preview-result"></div>'
}

export async function teardownPage(){
  const planId=research.planId||research.teardown[0]?.id;
  const detail=planId?await teardownApi.plan(planId):null;
  if(detail&&research.planId!==detail.plan.id)research.planId=detail.plan.id;
  const editionId=research.editionId||detail?.editions[0]?.id;
  const edition=editionId?await teardownApi.edition(editionId):null;
  research.edition=edition;
  if(edition)research.editionId=edition.id;
  const plan=detail?.plan;
  const sampled=plan?plan.strata.reduce((a,s)=>a+s.references.length,0):0;
  const recorded=new Set((detail?.receipts||[]).map(r=>r.reference));
  const outstanding=plan?plan.strata.flatMap(s=>s.references).filter(r=>!recorded.has(r)):[];
  return '<section class="page-heading"><p class="eyebrow">06 / TEARDOWN ESTIMATION</p><h1>拆检不是只看挑中的那几台。</h1><p>按已完成研究冻结的折外判读分层抽样，先锁定各层抽取数与入样概率，再用拆检结论按入样概率倒数估算全批误判率。</p></section>'+
  '<div class="split"><section>'+
  (research.teardown.length?'<label class="compact">拆检计划<select id="plan-select">'+planOptions()+'</select></label>':'')+
  (plan?'<p class="fine">'+esc(plan.name)+' · 研究 '+esc(plan.run_name)+' · 全批 '+plan.frame_size+' 条 · 抽取 '+sampled+' 条 · seed '+plan.seed+'</p>'+
    table(plan.strata.map(s=>({key:s.key,population:s.population,sample:s.sample,pi:pct(s.inclusion_probability),rule:s.sampling})),[{key:'key',label:'折外判读层'},{key:'population',label:'层内全量'},{key:'sample',label:'抽取数'},{key:'pi',label:'入样概率'},{key:'rule',label:'抽样口径'}],{limit:20})+
    '<h2>拆检回件 <small>共 '+(detail?.receipts.length||0)+' 条；按收到时刻归属版本</small></h2>'+
    table((detail?.receipts||[]).map(r=>({reference:r.reference,confirmed_label:r.confirmed_label,received_at:localTime(r.received_at),recorded_by:r.recorded_by})),[{key:'reference',label:'样本编号'},{key:'confirmed_label',label:'拆检结论'},{key:'received_at',label:'收到时间（北京）'},{key:'recorded_by',label:'记录人'}],{limit:20})+
    '<h2>封账版本</h2>'+editionLedger(detail):'<div class="empty">还没有拆检计划。选择一条已完成研究，按折外判读分层锁定抽样。</div>')+
  '</section><aside class="entry-sheet">'+
  (session.writable?'<h2>新建拆检计划</h2><form id="plan-form"><label>已完成研究<select name="run_id">'+runOptions(research.runs,null,true)+'</select></label><label>计划名称<input name="name" required value="十一月拆检复核"></label><label>每层抽取数<input name="per_stratum_sample" type="number" min="0" step="1" required value="12"></label><label>随机种子<input name="seed" type="number" min="0" step="1" required value="42"></label><button type="submit">锁定分层抽样 →</button></form>'+
    (plan?'<details open><summary>记录拆检回件</summary><form id="receipt-form"><label>抽中样本<select name="reference">'+(outstanding.length?outstanding.map(r=>'<option>'+esc(r)+'</option>').join(''):'<option value="">全部已回件</option>')+'</select></label><label>拆检结论<select name="confirmed_label">'+plan.strata.map(s=>'<option>'+esc(s.key)+'</option>').join('')+'</select></label><label>收到时间（北京时间，可留空取当前）<input name="received_at" type="datetime-local"></label><button type="submit" '+(outstanding.length?'':'disabled')+'>记录回件 →</button></form></details>'+
      '<details><summary>封账为新版本</summary><form id="edition-form"><label>收件截止（北京时间）<input name="receipt_cutoff" type="datetime-local" required value="'+bjInputNow()+'"></label><p class="fine">按截止时刻的收件版本封账；晚到记录自动留给下一版。</p><button type="submit">封账并进入对账</button></form></details>':'')+
    '</aside></div>':'')+editionPanel(edition);
}

function bjInputNow(){
  return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date()).replace(' ','T');
}

function bjToIso(value){
  if(!value)return null;
  const text=value.length===16?value+':00':value;
  const date=new Date(text+'+08:00');
  if(Number.isNaN(date.getTime()))throw Error('收件时间格式不正确');
  return date.toISOString();
}

export function bindTeardown(refresh){
  bind('#plan-select','change',async e=>{research.planId=Number(e.target.value);research.editionId=null;await refresh()});
  for(const a of document.querySelectorAll('[data-edition]'))a.addEventListener('click',async()=>{research.editionId=Number(a.dataset.edition);await refresh()});
  action('#plan-form',async form=>{const data=formObject(form);const plan=await teardownApi.createPlan({run_id:Number(data.run_id),name:data.name,seed:Number(data.seed),per_stratum_sample:Number(data.per_stratum_sample)});research.planId=plan.plan.id;research.editionId=null;await refresh();toast('已锁定各层抽取数与入样概率')});
  action('#receipt-form',async form=>{const data=formObject(form);if(!data.reference)throw Error('没有待回件样本');await teardownApi.addReceipts(research.planId,[{reference:data.reference,confirmed_label:data.confirmed_label,received_at:bjToIso(data.received_at)}]);await refresh();toast('拆检回件已记录')});
  action('#edition-form',async form=>{const data=formObject(form);const edition=await teardownApi.createEdition(research.planId,bjToIso(data.receipt_cutoff));research.editionId=edition.id;await refresh();toast('已按收件截止封账，第 '+edition.version_no+' 版待计权')});
  bind('#compute-edition','click',async e=>{e.target.disabled=true;try{await teardownApi.compute(research.editionId);await refresh();toast('分层计权完成，请核对各层对账')}catch(error){toast(error.message,true);e.target.disabled=false}});
  bind('#publish-edition','click',async e=>{e.target.disabled=true;try{await teardownApi.publish(research.editionId);await refresh();toast('各层对账齐，已发布该版本')}catch(error){toast(error.message,true);e.target.disabled=false}});
  bind('#preview-edition','click',async()=>{const box=document.getElementById('preview-result');if(!box)return;try{const preview=await teardownApi.editionRecompute(research.editionId);box.innerHTML='<h2>按当时的拆检结论复算 <small>'+esc('第 '+preview.version_no+' 版 · 截止 '+localTime(preview.receipt_cutoff))+'</small></h2>'+table([{scope:'本版冻结快照',reportable:preview.estimate.reportable?'可报数':'暂不报数',rate:pct(preview.estimate.estimated_rate),total:label(preview.estimate.estimated_misjudged),received:preview.estimate.received,sampled:preview.estimate.sampled}],[{key:'scope',label:'复算口径'},{key:'reportable',label:'报数状态'},{key:'rate',label:'计权误判率'},{key:'total',label:'计权误判数'},{key:'received',label:'回件'},{key:'sampled',label:'锁定样本'}])+'<p class="fine">'+(preview.matches_stored?'与已存计权一致。':'与已存计权存在差异（'+esc(preview.mismatches.join('、')||'总量')+'），请重新执行分层计权后再发布。')+'</p>'}catch(error){box.innerHTML='<div class="failure">'+esc(error.message)+'</div>'}});
}
