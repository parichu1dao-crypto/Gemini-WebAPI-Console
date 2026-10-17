if (document.getElementById('transport')) {
 // Loaded after the settings page's existing script, without changing its save logic.
 (() => {
  const el=id=>document.getElementById(id);
  const previousMode=window.mode, previousRender=window.render, previousLoad=window.loadModels;
  let cookieLoad=0;
  window.loadModels=async function(){
   const seq=++cookieLoad;
   if(el('transport').value!=='cookie')return previousLoad();
   const chosen=el('model').value||saved?.model;
   const data=await api('models?transport=cookie');
   if(seq!==cookieLoad||el('transport').value!=='cookie')return;
   options(data.models,chosen);
  };
  window.mode=function(){
   previousMode();
   if(el('transport').value==='cookie')options(saved?.model?[saved.model]:[],saved?.model);
  };
  window.render=function(data,config=false){
   previousRender(data,config);
   if(data.ownership==='external')el('status').textContent='运行中 · 外部管理 / Running · External ownership';
   el('stop').disabled=el('start').disabled=data.ownership==='external';
   if(config&&data.transport==='cookie')window.loadModels().catch(e=>{el('notice').textContent=e.message});
  };
  const refresh=document.createElement('button');
  refresh.textContent='刷新 Cookie 模型 / Refresh Cookie models';
  refresh.onclick=()=>action(window.loadModels);
  const refreshRow=document.createElement('div');
  refreshRow.className='model-refresh-row';
  refreshRow.append(refresh);
  el('model').after(refreshRow);
  const guide=[...document.querySelectorAll('p')].find(p=>p.textContent.includes('仅保留旧模型'));
  if(guide)guide.textContent='Cookie：模型来自本机 Cookie 服务动态列表（含上游支持的 Argon）；离线保留旧模型及已保存选择。已有浏览器服务与本台托管池独立。';
 })();
} else {
const $=id=>document.getElementById(id), h=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let key='',period='today',page='overview',offset=0,accounts=[],report=null,action=null,actionId=null,loading=false,refreshQueued=false;
const n=x=>Number(x||0).toLocaleString(window.uiLanguage==='en'?'en-US':'zh-CN'),dt=x=>x?new Date(x*1000).toLocaleString(window.uiLanguage==='en'?'en-GB':'zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
async function api(path,body){const r=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:{'X-Management-Key':key,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);return d;}
async function run(fn){$('message').textContent='';document.querySelectorAll('.dialogError').forEach(x=>x.remove());try{await fn()}catch(e){$('message').textContent=e.message;if($('connection').textContent==='正在加载…'){$('connection').textContent='加载失败';$('connection').className='tag bad'}const d=document.querySelector('dialog[open]');if(d){const p=document.createElement('p');p.className='dialogError';p.setAttribute('role','alert');p.style.color='#a23838';p.textContent=e.message;d.append(p)}}}
function table(headers,rows){return rows.length?'<table><thead><tr>'+headers.map(x=>'<th>'+h(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table>':'<div class="empty">这个范围内暂无记录。可切换日期或来源查看。</div>'}
function label(status){return ({success:'成功',failed:'失败',running:'执行中',interrupted:'中断／未知',external:'外部生产',pending_login:'待登录',verified:'已验证'})[status]||status}
async function refresh(){if(!key)return $('auth').showModal();if(loading){refreshQueued=true;return}loading=true;$('connection').textContent='正在加载…';try{accounts=(await api('accounts')).accounts;const chosen=$('accountFilter').value;$('accountFilter').innerHTML='<option value="">全部账号</option>'+accounts.map(a=>`<option value="${h(a.id)}">${h(a.name)}</option>`).join('');$('accountFilter').value=chosen;const q=new URLSearchParams({period,account:$('accountFilter').value,source:$('source').value,offset});if(period==='custom'){q.set('start',$('from').value);q.set('end',$('to').value)}report=await api('activity?'+q);render();$('connection').textContent='已连接 · '+new Date().toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});$('connection').className='tag good'}finally{loading=false;if(refreshQueued){refreshQueued=false;run(refresh)}}}
function render(){
 const s=report.summary;
 $('metrics').innerHTML=[['请求次数',n(s.requests),`${n(s.success)} 成功 / ${n(s.failed)} 失败 / ${n(s.running)} 执行中`],['实际输入 / 输出',s.reported_requests?n(s.actual_input)+' / '+n(s.actual_output):'暂未提供','仅统计上游明确返回的 usage'],['粗估输入 / 输出',n(s.estimated_input)+' / '+n(s.estimated_output),'字符数 ÷ 4；不是计费用量'],['用量未知',n(s.unknown_usage),`${n(s.interrupted)} 次缺少结束事件`]].map(x=>`<div class="metric"><label>${x[0]}</label><div class="number">${x[1]}</div><small>${x[2]}</small></div>`).join('');
 $('usageNote').textContent=(report.importing?'历史日志仍在导入，当前统计尚不完整，请稍后刷新。 ':'')+report.note;
 const max=Math.max(1,...report.daily.map(x=>x.requests));
 $('daily').innerHTML=table(['日期','调用','成功 / 失败','粗估输入 / 输出'],report.daily.map(x=>[h(x.day),`<span class="bar" style="width:${Math.round(x.requests/max*65)}px"></span> ${n(x.requests)}`,n(x.success)+' / '+n(x.failed),n(x.estimated_input)+' / '+n(x.estimated_output)]));
 const names=new Map(accounts.map(a=>[a.id,a.name]));$('distribution').innerHTML=table(['账号','调用','成功','失败'],report.accounts.map(x=>[h(names.get(x.account)||x.account),n(x.requests),n(x.success),n(x.failed)]));
 $('recordCount').textContent=`共 ${n(s.requests)} 次 · 每页100条`;
 $('requestRows').innerHTML=table(['开始时间 / 耗时','账号 / 模型','结果','输入 token','输出 token','计量依据','详情'],report.records.map(x=>[h(dt(x.started))+`<small>${x.ended?Math.max(0,x.ended-x.started).toFixed(1)+' 秒':'未记录结束'}</small>`,h(x.account_name||x.account)+`<small>${h(x.model)}</small>`,`<span class="tag ${x.status==='success'?'good':x.status==='failed'?'bad':'warn'}">${h(label(x.status))}</span>`,x.input_tokens===null?'—':n(x.input_tokens),x.output_tokens===null?'—':n(x.output_tokens),h(({reported:'上游实际',estimated_chars_div4:'字符粗估',unknown:'未知'})[x.usage_kind]),`<details><summary>展开</summary><small>来源：${h(x.source)}<br>ID：${h(x.id)}<br>结束：${h(dt(x.ended))}<br>${h(x.error||'')}</small></details>`]));
 $('pageNumber').textContent='第 '+(offset/100+1)+' 页';$('prev').disabled=offset===0;$('next').disabled=offset+100>=s.requests;
 const counts=new Map(report.accounts.map(x=>[x.account,x]));
 $('accountRows').innerHTML=table(['名称 / 邮箱','归属 / 状态','限额恢复时间（北京时间）','当前时段调用','最近验证','操作'],accounts.filter(a=>!$('accountFilter').value||a.id===$('accountFilter').value).map(a=>{
   const c=counts.get(a.id)||{}, button=(action,text)=>`<button data-action="${action}" data-id="${h(a.id)}">${text}</button>`;
   let actions=button('rename','重命名');
   if(a.external_key&&a.browser_bound)actions+=button('open','打开账号浏览器');
   if(a.production_control)actions+=button('production-toggle',a.production_enabled?'停止账号':'启动账号');
   else if(a.external_key&&a.browser_bound)actions+='<button disabled title="需要先升级账号服务">启停：待服务升级</button>';
   if(a.production_control)actions+=`<small>${h(window.translateUI(a.production_enabled?'已启用新请求':'已停止新请求'))} · ${h(a.admission?.reason||'')}</small>`;
   if(!a.external_key){actions+=a.archived?button('restore','恢复'):button('open','打开登录页')+button('verify','验证身份')+button('toggle',a.enabled?'停用':'启用')+button('archive','归档');actions+=button('delete','删除');}
   const q=a.quota, limited=q&&q.until>Date.now()/1000;
   const quota=q?`<span class="tag ${limited?'warn':'good'}">${limited?(a.pause_on_limit?'冷却中':'允许基础模型'):'已到恢复时间'}</span><small>${h(dt(q.until))}</small><small>${q.source==='default_12h'?'默认估计12小时':'网页恢复时间'}</small><small>${h(q.evidence)}</small>`:'—';
   return [h(a.name)+`<small>${h(a.email||'尚未核对邮箱')}</small>`,`${a.external_key?(a.status==='historical'?'历史账号':'现有生产池'):'本台托管'}<small>${a.archived?'已归档':a.production_control?(a.production_enabled?'已启用新请求':'已停止新请求'):a.external_key?'只读观测':a.enabled?'已启用':label(a.status)+' · 未启用'}</small>`,quota,`${n(c.requests)} 次<small>${n(c.success)} 成功 / ${n(c.failed)} 失败</small>`,h(dt(a.verified)),`<div class="actions">${actions}</div>`];
 }));
}
const helpNav=document.createElement('button');helpNav.dataset.page='help';helpNav.textContent='错误与帮助';document.querySelector('nav').append(helpNav);
const helpSection=document.createElement('section');helpSection.id='help';helpSection.hidden=true;const helpFrame=document.createElement('iframe');helpFrame.title='错误合集与反馈 / Errors and feedback';helpFrame.style.cssText='width:100%;height:calc(100vh - 160px);min-height:600px;border:0';helpSection.append(helpFrame);document.querySelector('main').append(helpSection);
const opsNav=document.createElement('button');opsNav.dataset.page='operations';opsNav.textContent='运维与安全 / Operations';document.querySelector('nav').append(opsNav);
const opsSection=document.createElement('section');opsSection.id='operations';opsSection.hidden=true;const opsFrame=document.createElement('iframe');opsFrame.title='运维与安全 / Operations';opsFrame.style.cssText='width:100%;height:calc(100vh - 120px);min-height:600px;border:0';opsSection.append(opsFrame);document.querySelector('main').append(opsSection);
window.consoleOperationsApi=(path,body)=>api('ops/'+path,body);
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{page=b.dataset.page;document.querySelectorAll('[data-page]').forEach(x=>x.classList.toggle('active',x===b));for(const id of ['overview','records','accounts','settings','help','operations'])$(id).hidden=id!==page;$('filters').hidden=['settings','help','operations'].includes(page);$('title').textContent=({overview:'运行概览',records:'调用记录',accounts:'账号管理',settings:'运行设置',help:'错误与帮助',operations:'运维与安全 / Operations'})[page];if(page==='operations'&&!opsFrame.src)opsFrame.src='/operations';if(page==='help'&&!helpFrame.src)helpFrame.src='/help';if(page==='settings'){const frame=$('settingsFrame');if(!frame.src){frame.onload=()=>{const doc=frame.contentDocument;if(key){doc.getElementById('key').value=key;doc.getElementById('connect').click()}};frame.src='/settings'}}});
document.querySelectorAll('[data-period]').forEach(b=>b.onclick=()=>{period=b.dataset.period;offset=0;$('custom').hidden=period!=='custom';document.querySelectorAll('[data-period]').forEach(x=>x.classList.toggle('selected',x===b));if(period!=='custom')run(refresh)});
for(const id of ['accountFilter','source'])$(id).onchange=()=>{offset=0;run(refresh)};
$('applyDates').onclick=()=>run(refresh);$('refresh').onclick=()=>run(refresh);$('login').onclick=()=>$('auth').showModal();$('connect').onclick=()=>run(async()=>{key=$('key').value;await refresh();$('auth').close();$('key').value=''});$('key').onkeydown=e=>{if(e.key==='Enter')$('connect').click()};$('prev').onclick=()=>{offset=Math.max(0,offset-100);run(refresh)};$('next').onclick=()=>{offset+=100;run(refresh)};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
function dialog(kind,id){action=kind;actionId=id;const a=accounts.find(x=>x.id===id);$('dialogTitle').textContent=kind==='create'?'录入新账号':kind==='verify'?'核对登录身份':'重命名账号';$('dialogHelp').textContent=kind==='create'?'将打开独立 Chrome 档案，名称默认按北京时间日期生成；同日自动加序号。':kind==='verify'?'请先在该账号独立窗口登录 Gemini，再填写实际邮箱。网页建议使用英文界面。':'仅修改显示名称，历史统计和浏览器档案保持不变。';$('fieldLabel').textContent=kind==='verify'?'实际登录邮箱':'名称（录入时可留空）';$('accountValue').value=kind==='rename'?a.name:kind==='verify'?a.email||'':'';$('accountSubmit').textContent=kind==='create'?'创建并打开登录页':kind==='verify'?'验证并录入':'保存名称';$('accountDialog').showModal();}
$('addAccount').onclick=()=>{if(!key)return $('auth').showModal();dialog('create')};
let deletingAccount=null;
$('deleteValue').oninput=()=>{$('deleteSubmit').disabled=!deletingAccount||$('deleteValue').value!==deletingAccount.name};
$('deleteSubmit').onclick=()=>run(async()=>{const a=deletingAccount;if(!a||$('deleteValue').value!==a.name)throw Error(window.translateUI('账号名称不匹配'));$('deleteSubmit').disabled=true;try{await api('accounts/delete',{id:a.id,confirmation:$('deleteValue').value});$('deleteDialog').close();deletingAccount=null;await refresh();$('message').textContent=window.translateUI('账号已删除，历史记录已保留')}finally{$('deleteValue').oninput()}});
$('accountRows').addEventListener('click',e=>{const b=e.target.closest('[data-action="delete"]');if(!b)return;e.stopImmediatePropagation();deletingAccount=accounts.find(a=>a.id===b.dataset.id);$('deleteName').textContent=deletingAccount.name;$('deleteValue').value='';$('deleteSubmit').disabled=true;$('deleteDialog').querySelectorAll('.dialogError').forEach(x=>x.remove());$('deleteDialog').showModal();$('deleteValue').focus()},true);
$('accountRows').addEventListener('click',e=>{const b=e.target.closest('[data-action="open"]');if(!b)return;e.stopImmediatePropagation();run(async()=>{b.disabled=true;try{const result=await api('accounts/open',{id:b.dataset.id});$('message').textContent=result.warning||window.translateUI(result.identity_verified?'已核对账号身份并打开浏览器':'已打开独立登录窗口，请核对账号');}finally{b.disabled=false}})},true);
$('accountRows').addEventListener('click',e=>{const b=e.target.closest('[data-action="production-toggle"]');if(!b)return;e.stopImmediatePropagation();const a=accounts.find(x=>x.id===b.dataset.id);run(async()=>{b.disabled=true;try{await api('accounts/production-toggle',{id:a.id,enabled:!a.production_enabled});await refresh()}finally{b.disabled=false}})},true);
$('accountRows').onclick=e=>{const b=e.target.closest('[data-action]');if(!b)return;const a=accounts.find(x=>x.id===b.dataset.id),kind=b.dataset.action;if(['rename','verify'].includes(kind))return dialog(kind,a.id);if(!['toggle','archive','restore'].includes(kind))return;run(async()=>{b.disabled=true;try{if(kind==='archive'&&!confirm(window.translateUI('归档会停止该托管账号的新请求，但不删除档案和历史。继续？')))return;await api('accounts/update',{id:a.id,changes:kind==='toggle'?{enabled:!a.enabled}:{archived:kind==='archive'}});await refresh()}finally{b.disabled=false}})};
$('accountSubmit').onclick=()=>run(async()=>{const b=$('accountSubmit');b.disabled=true;try{const value=$('accountValue').value.trim();let result;if(action==='create')result=await api('accounts/create',{name:value||null});else if(action==='verify')result=await api('accounts/verify',{id:actionId,email:value});else result=await api('accounts/update',{id:actionId,changes:{name:value}});$('accountDialog').close();await refresh();$('message').textContent=result.warning||'已保存。新录入账号验证后，需手动启用才能参与本台托管池。'}finally{b.disabled=false}});
const today=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});$('from').value=today;$('to').value=today;$('auth').showModal();setInterval(()=>{if(key&&!loading&&document.visibilityState==='visible'&&page!=='settings')run(refresh)},30000);
window.addEventListener('uilanguage',()=>{$('title').textContent=({overview:'运行概览',records:'调用记录',accounts:'账号管理',settings:'运行设置',help:'错误与帮助',operations:'运维与安全 / Operations'})[page];if(report)render()});
}
