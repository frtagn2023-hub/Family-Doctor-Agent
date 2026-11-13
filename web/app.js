/* Cognitive statistics only. Import content stays in memory; no browser persistence. */
(() => {
  'use strict';
  const icons={home:'<path d="m3 11 9-8 9 8v10H3Z"/><path d="M8 14h8m-4-4v8"/>',users:'<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6m3 11v-4a5 5 0 0 0-2-4"/>',brain:'<path d="M12 4c-3-3-7 0-6 3-4 0-5 6-2 8-1 4 3 7 6 5l2-2V4Zm0 0c3-3 7 0 6 3 4 0 5 6 2 8 1 4-3 7-6 5l-2-2"/><path d="M6 7c0 3 2 4 3 4m-5 4c3-1 4 1 4 2m10-10c0 3-2 4-3 4m5 4c-3-1-4 1-4 2"/>',activity:'<path d="M2 12h5l3-8 4 16 3-8h5"/>',refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 14 6M4 12a8 8 0 0 0 14 6"/>',upload:'<path d="M12 16V3m-5 5 5-5 5 5M3 15v6h18v-6"/>',info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',check:'<path d="m4 12 5 5L20 6"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>',file:'<path d="M14 2H5v20h14V8Zm0 0v6h5M8 12h8m-8 5h8"/>',arrow:'<path d="M4 12h16m-5-5 5 5-5 5"/>',shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>'};
  const icon=name=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]||icons.info}</svg>`;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=value=>Number.isFinite(Number(value))?Math.max(0,Number(value)):0;
  const state={data:null,member:'all',import:null,generation:0,lastFocus:null,loading:false,supplementOpen:false};
  const $=id=>document.getElementById(id);
  const focusTargets=root=>[...root.querySelectorAll('button,a[href],select,input,summary,[tabindex]')];
  const focusIdentity=el=>JSON.stringify(el.id?['id',el.id]:[el.tagName,el.getAttribute('data-action'),el.getAttribute('data-member'),el.getAttribute('href'),el.getAttribute('class')]);
  function captureFocus(root,active=document.activeElement){
    if(!root?.contains(active))return null;
    if(active===root)return {root:true};
    const identity=focusIdentity(active);return {identity,index:focusTargets(root).filter(el=>focusIdentity(el)===identity).indexOf(active)};
  }
  function restoreFocus(root,saved,fallback=null){
    const target=saved?.root?root:saved?focusTargets(root).filter(el=>focusIdentity(el)===saved.identity)[saved.index]:null;
    const visible=el=>el&&!el.disabled&&el.getClientRects().length>0;
    (visible(target)?target:visible(fallback)?fallback:null)?.focus({preventScroll:true});
  }
  const selectIsActive=()=>document.activeElement?.tagName==='SELECT';
  let backgroundInert=[];
  function isolateImport(){
    backgroundInert=[...document.querySelectorAll('main,header,footer,.skip-link')].map(el=>({el,inert:el.inert}));
    backgroundInert.forEach(({el})=>{el.inert=true;});
  }
  const isDemo=()=>state.data?.meta.mode==='demo';
  const date=(value,full=false)=>{
    if(!value)return '尚无记录';
    const parsed=new Date(value);if(Number.isNaN(parsed.getTime()))return '日期未知';
    return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',...(full?{hour:'2-digit',minute:'2-digit'}:{})}).format(parsed).replaceAll('/','.');
  };
  const empty=(title,description)=>`<div class="empty"><span class="empty-symbol">${icon('brain')}</span><h3>${esc(title)}</h3><p>${esc(description)}</p></div>`;
  const badge=m=>`<span class="tag ${m.status==='needs_review'?'amber':m.status==='not_assessed'?'neutral':''}">${esc(m.label||'尚未评估')}</span>`;
  function stat(title,value,unit,note,symbol,amber=false){return `<article class="stat"><div class="stat-top">${title}<span class="stat-icon ${amber?'amber':''}">${icon(symbol)}</span></div><div class="stat-number">${esc(value)}<small>${unit}</small></div><p class="stat-note">${note}</p></article>`;}
  function chart(){
    const members=state.data.members.filter(m=>state.member==='all'||m.id===state.member);
    if(!members.some(m=>num(m.eventCount)>0))return `<div class="chart-empty">${empty('还没有有效观察趋势','先导入观察数据。没有记录或资料质量不足，不代表认知状态正常。')}</div>`;
    const daily=new Map();
    members.forEach(m=>(m.trend||[]).forEach(p=>{if(!/^\d{4}-\d{2}-\d{2}$/.test(p.date))return;const old=daily.get(p.date)||{observations:0,signals:0};daily.set(p.date,{observations:old.observations+num(p.observations),signals:old.signals+num(p.signals)});}));
    const rows=[...daily].sort(([a],[b])=>a.localeCompare(b));
    if(!rows.length)return `<div class="chart-empty">${empty('趋势数据暂不可用','已有记录，但未提供可展示的每日统计。')}</div>`;
    const width=1020,height=190,left=25,right=17,top=17,bottom=29,max=Math.max(4,...rows.map(([,v])=>v.observations));
    const x=i=>left+i*(width-left-right)/Math.max(1,rows.length-1),y=v=>height-bottom-v/max*(height-top-bottom);
    const grid=[0,.5,1].map(t=>`<line x1="${left}" x2="${width-right}" y1="${y(max*t)}" y2="${y(max*t)}" stroke="#e7edde" stroke-dasharray="3 5"/><text x="5" y="${y(max*t)+3}" fill="#718267" font-size="10">${Math.round(max*t)}</text>`).join('');
    const lines=['observations','signals'].map((key,idx)=>{const color=idx?'#bd925c':'#819b74';const points=rows.map(([,v],i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(v[key]).toFixed(1)}`).join(' ');return `<path d="${points}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>${rows.map(([d,v],i)=>`<circle cx="${x(i)}" cy="${y(v[key])}" r="2.3" fill="white" stroke="${color}" stroke-width="1.5"><title>${esc(d)}：${idx?'待核实变化':'有效观察'} ${v[key]} 次</title></circle>`).join('')}`;}).join('');
    const every=Math.max(1,Math.ceil(rows.length/7));
    const ticks=rows.map(([d],i)=>i%every&&i!==rows.length-1?'':`<text x="${x(i)}" y="${height-5}" text-anchor="middle" fill="#718267" font-size="10">${d.slice(5).replace('-','.')}</text>`).join('');
    return `<div class="chart-legend"><span><i></i>有效观察</span><span><i class="amber"></i>其中：待核实变化</span><span>单位：去重后的观察事件</span></div><div class="chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="近30天有效观察及待核实变化的次数趋势，不是患病概率">${grid}${lines}${ticks}</svg></div>`;
  }
  const chatOf=m=>m.chatAnalysis||null;
  const chatCount=c=>num(c?.self?.windowCount)+num(c?.caregiver?.windowCount);
  const hasChat=c=>!!c&&(chatCount(c)>0||num(c.quality?.usable)>0||num(c.quality?.limited)>0||num(c.quality?.excluded)>0||(c.alerts||[]).length>0);
  const chatLabels={insufficient:'观察机会不足',observing:'持续观察',review:'需家人核实',urgent:'需及时关注'};
  function memberBadge(m){
    const c=chatOf(m);if(!hasChat(c))return badge(m);
    return `<span class="tag ${c.status==='urgent'?'urgent':c.status==='review'?'amber':'neutral'}">${esc(chatLabels[c.status]||'尚未评估')}</span>`;
  }
  function alertPanel(){
    const rank={urgent:0,review:1,info:2};
    const alerts=state.data.members.flatMap(m=>(chatOf(m)?.alerts||[]).map(a=>({m,a}))).sort((x,y)=>(rank[x.a.level]??3)-(rank[y.a.level]??3));
    const rows=alerts.map(({m,a})=>{
      const source={self:'本人表达',caregiver:'家属报告'}[a.sourceMode]||'突然变化记录';
      const domain=(chatOf(m)?.dimensions||[]).find(d=>d.key===a.domain)?.label;
      return `<article class="family-alert ${a.level==='urgent'?'urgent':a.level==='review'?'review':'info'}"><span class="alert-symbol">${icon(a.level==='urgent'?'activity':'info')}</span><div><div class="alert-title"><strong>${esc(m.name)}</strong><span>${esc(a.label||'观察线索待核实')}</span></div><p>${a.level==='urgent'?'记录中有明确描述的突然变化，请家人及时核实当前情况并寻求医疗帮助；不等待累计次数。':a.level==='review'?'已在不同日期观察到相关线索，建议家人结合实际生活情况核实。':'当前属于有限观察线索，先核对背景，不能据此判断认知下降。'}</p><small>${source}${domain?' · '+esc(domain):''} · 涉及 ${num(a.days)} 个观察日 · 最近 ${date(a.lastSeen)}</small></div><button class="button small" data-member="${esc(m.id)}">查看分析 ${icon('arrow')}</button></article>`;
    }).join('');
    return `<section class="panel reminders-panel" aria-labelledby="reminder-title"><div class="panel-header"><div><h2 id="reminder-title">家人关注</h2><p>提醒只在此页显示，不发送飞书消息</p></div><span class="tag neutral">${alerts.length} 项线索</span></div>${rows?`<div class="family-alerts">${rows}</div>`:'<div class="quiet-state">当前没有聊天分析提醒。资料不足或没有提示，都不能说明认知状态正常。</div>'}</section>`;
  }
  function chatPanel(){
    const member=state.data.members.find(m=>m.id===state.member),c=member&&chatOf(member);
    const options=`<option value="all">选择成员</option>${state.data.members.map(m=>`<option value="${esc(m.id)}" ${state.member===m.id?'selected':''}>${esc(m.name)}</option>`).join('')}`;
    const head=`<div class="panel-header"><div><h2 id="chat-detail-title" tabindex="-1">${member?esc(member.name)+' · ':''}聊天分析</h2><p>从具体表达机会观察变化，不按聊天数量判断疾病</p></div><select class="select" id="chat-member-filter" aria-label="选择聊天分析成员">${options}</select></div>`;
    if(!member)return `<section class="panel chat-analysis-panel">${head}${empty('选择一位家人，查看具体观察维度','本人表达与家属观察分别统计；只展示分析结果，不展示聊天原文。')}</section>`;
    if(!hasChat(c))return `<section class="panel chat-analysis-panel">${head}${empty('尚未收到这位成员的聊天分析','启用 Hermes 聊天观察接入后，分析结果会显示在这里。已导入的补充观察继续独立保留。')}</section>`;
    const self=c.self||{},proxy=c.caregiver||{},q=c.quality||{};
    const summaries=`<div class="chat-coverage"><div><strong>${num(self.windowCount)}</strong><span>本人可用片段 · ${num(self.observationDays)} 天</span></div><div><strong>${num(proxy.windowCount)}</strong><span>家属观察片段 · ${num(proxy.observationDays)} 天</span></div><div><strong>${num(q.limited)+num(q.excluded)}</strong><span>受限或排除片段 · 不计普通维度统计</span></div></div>`;
    const dimensions=(c.dimensions||[]).map(d=>{
      const status={not_assessable:'缺少观察机会',no_signal:'未记录线索',possible_signal:'有待核实线索'}[d.status]||'缺少观察机会';
      const r=d.reference||{},comparison=r.available?({more_signals:'近期线索比例较高，需核对交流场景是否可比',no_increase:'未见线索比例增加，不代表认知正常'}[r.comparison]||'参考资料已积累，暂不能比较'):'早期参考资料尚不足，暂不判断变化';
      return `<article class="dimension-card ${d.status==='possible_signal'?'has-signal':''}"><div class="dimension-heading"><h3>${esc(d.label||d.key)}</h3><span class="tag ${d.status==='possible_signal'?'amber':'neutral'}">${status}</span></div><p class="dimension-count"><strong>${num(d.signalCount)}</strong> 次本人线索 <span>/ ${num(d.opportunities)} 次可观察机会</span></p><p class="subtle">本人涉及 ${num(d.signalDays)} 天 · 家属报告 ${num(d.caregiverSignalDays)} 天</p><p class="reference-note">${icon('info')}${comparison}</p></article>`;
    }).join('');
    return `<section class="panel chat-analysis-panel">${head}${summaries}<div class="dimension-grid">${dimensions}</div><div class="chart-caption"><span>各维度单独判断。未记录线索不等于正常；个人早期参考不是临床正常基线。</span><span>最近分析 ${date(c.lastAnalyzedAt,true)}</span></div></section>`;
  }
  function render(fallbackFocus=null){
    if(!state.data)return;
    const main=$('main'),focus=captureFocus(main)||(document.activeElement===document.body?fallbackFocus:null);
    const supplement=$('supplement-details');if(supplement)state.supplementOpen=supplement.open;
    const d=state.data,members=d.members;
    const observed=members.filter(m=>num(m.eventCount)>0||chatCount(chatOf(m))>0).length;
    const needs=members.filter(m=>['review','urgent'].includes(chatOf(m)?.status)||m.status==='needs_review').length;
    const totalChat=members.reduce((n,m)=>n+chatCount(chatOf(m)),0);
    $('mode-label').textContent=isDemo()?'演示 · 合成数据':'认知观察';$('mode-label').classList.toggle('demo',isDemo());
    const table=members.map((m,i)=>{const c=chatOf(m),signals=(c?.dimensions||[]).filter(x=>x.status==='possible_signal'||num(x.caregiverSignalDays)>0).length;return `<tr class="${state.member===m.id?'selected-row':''}"><td><button class="member-button" data-member="${esc(m.id)}"><span class="avatar ${['','clay','lilac'][i%3]}" aria-hidden="true">${esc(Array.from(m.name||'家')[0])}</span>${esc(m.name)}</button></td><td>${memberBadge(m)}</td><td class="table-number">${hasChat(c)?chatCount(c):'—'}<span class="subtle"> 段</span></td><td class="table-number">${num(m.eventCount)}<span class="subtle"> 次</span>${m.status==='needs_review'?'<span class="supplement-marker">有待核实变化</span>':''}</td><td class="table-number">${hasChat(c)?signals:'—'}<span class="subtle"> 项</span></td><td class="subtle">${date(c?.lastAnalyzedAt||m.lastObservedAt)}</td><td><button class="icon-button row-arrow" data-member="${esc(m.id)}" aria-label="查看${esc(m.name)}的聊天分析">${icon('arrow')}</button></td></tr>`;}).join('');
    $('main').innerHTML=`<div class="page-heading"><div><div class="eyebrow">COGNITIVE OBSERVATION</div><h1>认知观察统计</h1><p>${esc(d.meta.familyName||'我的家庭')} · 近 ${num(d.meta.windowDays||30)} 天 · 聊天分析与补充观察</p></div><div class="heading-actions"><button class="icon-button" id="refresh" aria-label="刷新统计" title="刷新统计">${icon('refresh')}</button><button class="button primary" data-action="open-import">${icon('upload')}导入数据</button></div></div>
      ${isDemo()?`<div class="demo-banner">${icon('info')}合成数据演示。所有分析和提醒均为虚构示例，不代表家人实际情况。</div>`:''}
      <div class="stats">${stat('有有效观察的成员',observed,'位',`共 ${members.length} 位成员，${members.length-observed} 位待补充`,'users')}${stat('需家人核实的成员',needs,'位','仅为观察提示，不是风险等级','brain',true)}${stat('可用聊天片段',totalChat,'段','与人工补充观察分别统计','activity')}</div>
      ${alertPanel()}
      <section class="panel members-panel"><div class="panel-header"><div><h2>成员统计</h2><p>点击姓名查看各维度、资料覆盖和早期参考</p></div><span class="tag neutral">仅 Web 提醒</span></div>${members.length?`<div class="table-scroll"><table><thead><tr><th>成员</th><th>观察状态</th><th>聊天片段</th><th>补充观察</th><th>聊天线索维度</th><th>最近记录</th><th></th></tr></thead><tbody>${table}</tbody></table></div>`:empty('尚未登记家庭成员','请先在家庭登记中关联成员姓名和 Hermes profile。')}<div class="table-note">${icon('info')}聊天片段与补充观察不相加；“尚未评估”或“持续观察”均不是低风险结论。</div></section>
      ${chatPanel()}
      <details id="supplement-details" class="panel chart-panel supplement-panel" ${state.supplementOpen?'open':''}><summary>补充观察趋势 <span>查看手动导入的数据</span></summary><div class="panel-header"><div><h2>观察趋势</h2><p>按实际观察日期统计，与聊天分析独立</p></div><div class="panel-tools"><select class="select" id="member-filter" aria-label="选择趋势成员"><option value="all">全部成员</option>${members.map(m=>`<option value="${esc(m.id)}" ${state.member===m.id?'selected':''}>${esc(m.name)}</option>`).join('')}</select></div></div>${chart()}<div class="chart-caption"><span>未发现信号不等于排除认知障碍。分析规则尚未经临床验证。</span><span>读取时间 ${date(d.meta.generatedAt,true)}</span></div></details>
      <p class="refresh-note">页面打开时约每 30 秒更新；关闭页面后不会弹出通知或发送飞书消息。</p>`;
    if(!state.import)restoreFocus(main,focus);
  }
  function toast(message){const el=$('toast');el.textContent=message;el.classList.add('visible');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('visible'),3000);}
  let activeLoad=null;
  async function load(refresh=false,automatic=false){
    if(activeLoad){await activeLoad;return load(refresh,automatic);}
    if(automatic&&(document.hidden||state.import||selectIsActive()))return false;
    let finished;activeLoad=new Promise(resolve=>{finished=resolve;});state.loading=true;
    const pendingFocus=captureFocus($('main')),btn=$('refresh');if(btn&&!automatic)btn.disabled=true;
    try{
      const response=await fetch('/api/dashboard',{cache:'no-store',credentials:'same-origin'});
      if(!response.ok)throw new Error();
      const data=await response.json();if(!data.meta||!Array.isArray(data.members)||!data.members.every(m=>typeof m.id==='string'&&typeof m.name==='string'))throw new Error();
      if(automatic&&(document.hidden||state.import||selectIsActive()))return false;
      state.data=data;if(state.member!=='all'&&!data.members.some(m=>m.id===state.member))state.member='all';
      render(pendingFocus);if(refresh)toast('认知观察统计已刷新');return true;
    }catch(error){if(automatic&&(document.hidden||state.import||selectIsActive()))return false;state.data=null;$('mode-label').textContent='读取失败';$('main').innerHTML=`<div class="loading"><h1>暂时无法读取观察统计</h1><p>请检查家庭配置或观察数据。读取失败不会替换成空数据或演示结果。</p><button class="button primary" data-action="retry" style="margin-top:20px">重新读取</button></div>`;return false;}
    finally{state.loading=false;activeLoad=null;finished();const current=$('refresh');if(current)current.disabled=false;}
  }
  const newImport=()=>({phase:'select',name:'',size:0,format:null,content:null,preview:null,result:null,error:'',help:false});
  function openImport(trigger){if(state.import)return;state.lastFocus=captureFocus($('main'),trigger||$('main').querySelector('[data-action="open-import"]'));state.import=newImport();state.generation++;isolateImport();document.body.style.overflow='hidden';renderImport();}
  function closeImport(){if(state.import?.phase==='committing')return;state.generation++;state.import=null;$('import-root').innerHTML='';document.body.style.overflow='';backgroundInert.forEach(({el,inert})=>{el.inert=inert;});backgroundInert=[];restoreFocus($('main'),state.lastFocus,$('main').querySelector('[data-action="open-import"]')||$('main'));}
  function formatHelp(){
    const mapping=isDemo()?(state.data.meta.demoProfileMapping||[]):[];
    return `<div class="format-help"><h3>只导入结构化观察，不上传聊天原文</h3><p>JSON 使用 <code>schema_version: 1</code> 与 <code>records</code> 数组。CSV 第一行使用模板字段名；signals 和 confounders 的多个值都用竖线 | 分隔。</p><ul><li>先替换模板中的姓名、profile、来源编号和带时区的实际观察时间。</li><li>每个来源使用稳定编号，重试保留同一编号。</li><li>本人表达、家属观察、医生记录分开标记；家属表达不会计为本人语言样本。</li><li>同一事件可共用 event_key，但观察时刻必须一致；不确定时留空。</li><li>不接收原文、摘要、疾病概率或评分字段；有错误则整批不入档。</li></ul>${mapping.length?`<p>演示可用来源：${mapping.map(x=>`${esc(x.name)} → <code>${esc(x.profile)}</code>`).join('；')}。</p>`:''}<details><summary>查看字段与枚举</summary>${[['source_record_id','稳定来源编号'],['member','已登记的准确姓名'],['reporter_profile','信息提供者的已登记 profile'],['observed_at','例如 2026-11-11T08:00:00+08:00'],['source_type','self_expression / caregiver_observation / clinician_record'],['quality','usable / limited / excluded'],['signals','repeated_question / disorientation / topic_discontinuity / word_finding / daily_function_change'],['confounders','sleep / mood / medication / hearing_vision / transcription / other / unknown'],['event_key','明确同次观察的标记，可留空']].map(([k,v])=>`<div class="field-row"><code>${k}</code><p>${v}</p></div>`).join('')}</details></div>`;
  }
  const fieldLabels={member:'成员',reporter_profile:'提供者 profile',observed_at:'观察时间',source_record_id:'来源编号',source_type:'来源类型',quality:'资料质量',signals:'观察信号',confounders:'影响因素',event_key:'事件标记',record:'记录结构',records:'记录列表',schema_version:'格式版本'};
  function previewContent(p){
    const errors=p.errors||[];
    return `<div class="import-stats"><div class="import-stat"><strong>${num(p.new)}</strong><span>可新增来源</span></div><div class="import-stat"><strong>${num(p.duplicates)}</strong><span>重复来源将跳过</span></div><div class="import-stat ${errors.length?'error':''}"><strong>${errors.length}</strong><span>校验问题</span></div></div>${errors.length?`<div class="notice error">${icon('info')}本批有错误，尚未写入任何观察。修正文件后重新选择。</div><ul class="error-list">${errors.slice(0,50).map(e=>`<li><strong>${e.row?'第 '+num(e.row)+' 条记录':'文件'} · ${esc(fieldLabels[e.field]||e.field||'格式')}</strong>${esc(e.message||'请检查该字段')}</li>`).join('')}</ul>${errors.length>50?'<p class="import-footnote">仅展示前 50 项问题，请先修正再校验。</p>':''}`:`<div class="notice">${icon('check')}校验通过。核对成员归属和数量后即可导入。</div>`}${p.members?.length?`<div class="preview-table"><table><thead><tr><th>成员</th><th>新增来源</th><th>重复来源</th></tr></thead><tbody>${p.members.map(m=>`<tr><td>${esc(m.name)}</td><td>${num(m.new)}</td><td>${num(m.duplicates)}</td></tr>`).join('')}</tbody></table></div>`:''}${p.warnings?.length?`<div class="notice amber">${icon('info')}<span>${p.warnings.map(w=>esc(typeof w==='string'?w:w.message||'请核对来源信息')).join('<br>')}</span></div>`:''}`;
  }
  function renderImport(){
    const imp=state.import;if(!imp)return;
    const root=$('import-root'),focus=captureFocus(root),previous=root.querySelector('.import-panel'),scroll=previous?.scrollTop||0;
    const helpOpen=previous?.querySelector('.format-help details')?.open||false;
    const busy=['previewing','committing'].includes(imp.phase),step=imp.phase==='success'?2:imp.phase==='select'?0:1;
    let body='';
    if(imp.phase==='success')body=`<div class="success-view"><div class="success-icon">${icon('check')}</div><h3>导入已完成</h3><p>新增 ${num(imp.result.new)} 条来源，跳过 ${num(imp.result.duplicates)} 条重复来源。</p></div><div class="notice ${imp.error?'amber':''}">${icon(imp.error?'info':'shield')}${imp.error?esc(imp.error):'统计已按成员归属、来源质量和事件去重更新；原有观察继续保留。'}</div><div class="import-actions"><button class="button" data-action="another-import">继续导入</button><button class="button primary" data-action="close-import">返回统计</button></div>`;
    else{
      body=`${imp.name?`<div class="file-chip">${icon('file')}<div><strong>${esc(imp.name)}</strong><small>${(imp.size/1024).toFixed(1)} KB · ${esc((imp.format||'').toUpperCase())}</small></div><button class="button text" data-action="choose-file" ${busy?'disabled':''}>重新选择</button></div>`:`<div class="dropzone" id="dropzone"><span class="upload-icon">${icon('upload')}</span><h3>选择或拖入观察数据</h3><p>支持 JSON / CSV · 最大 1 MB · 最多 1,000 条</p><button class="button primary" data-action="choose-file">选择文件</button></div>`}
      <input class="visually-hidden" type="file" id="import-file" accept=".json,.csv,application/json,text/csv" aria-label="选择认知观察数据文件" ${busy?'disabled':''}>
      <div class="templates"><span>下载模板</span><a href="/api/import/template.json" download>JSON</a><a href="/api/import/template.csv" download>CSV</a><button data-action="toggle-help" aria-expanded="${imp.help}">格式说明</button></div>
      ${imp.help?formatHelp():''}${imp.error?`<div class="notice error" role="alert">${icon('info')}${esc(imp.error)}</div>`:''}
      ${busy?`<div class="progress-view"><span class="spinner"></span><p>${imp.phase==='committing'?'正在安全写入，请稍候…':'正在核对格式、成员与重复来源…'}</p></div>`:imp.preview?previewContent(imp.preview):''}
      ${imp.preview&&!busy?`<div class="import-actions"><button class="button" data-action="choose-file">更换文件</button>${imp.preview.ok&&!imp.preview.errors?.length?`<button class="button primary" data-action="commit-import" ${isDemo()||!imp.preview.previewToken?'disabled':''}>${isDemo()?'演示模式不写入':imp.preview.new?'导入这 '+num(imp.preview.new)+' 条新来源':'确认跳过重复来源'}</button>`:''}</div>`:''}
      ${imp.content&&!busy&&imp.error?'<button class="button small" data-action="repreview">重新校验此文件</button>':''}
      ${isDemo()?'<p class="import-warning">演示模式仅校验文件，不会保存观察。实际导入请连接家庭资料目录。</p>':''}
      <p class="import-footnote">模板中的占位内容必须替换。新增的是观察来源，不是确诊人数；无效或重复来源不会增加有效事件数。</p>`;
    }
    root.innerHTML=`<div class="overlay"><section class="import-panel" role="dialog" aria-modal="true" aria-labelledby="import-title" tabindex="-1"><div class="import-heading"><div><h2 id="import-title">导入认知观察</h2><p>先核对，再入档；每条来源都有明确归属。</p></div><button class="icon-button" id="close-import" data-action="close-import" aria-label="关闭导入" ${imp.phase==='committing'?'disabled':''}>${icon('close')}</button></div><div class="steps">${['选择文件','校验预览','完成导入'].map((text,i)=>`<div class="step ${step===i?'active':step>i?'done':''}"><span class="step-number">${i+1}</span>${text}</div>`).join('')}</div>${body}</section></div>`;
    const dialog=root.querySelector('.import-panel'),help=dialog.querySelector('.format-help details');if(help)help.open=helpOpen;
    dialog.scrollTop=scroll;restoreFocus(root,focus,$('close-import').disabled?dialog:$('close-import'));
  }
  async function selectFile(file){
    if(!file||!state.import)return;
    const imp=state.import;state.generation++;const generation=state.generation;
    const format=file.name.toLowerCase().endsWith('.json')?'json':file.name.toLowerCase().endsWith('.csv')?'csv':null;
    Object.assign(imp,{phase:'select',name:file.name,size:file.size,format,content:null,preview:null,error:''});
    if(!format){imp.error='请选择 .json 或 .csv 文件。';renderImport();return;}
    if(file.size>1024*1024||file.size===0){imp.error=file.size?'文件超过 1 MB，请拆成较小批次。':'文件为空，请填写观察记录后再导入。';renderImport();return;}
    imp.phase='previewing';renderImport();
    try{const content=await file.text();if(!state.import||generation!==state.generation)return;imp.content=content;await previewImport();}
    catch(error){if(state.import&&generation===state.generation){imp.phase='select';imp.error='无法读取文件，请检查文件后重新选择。';renderImport();}}
  }
  async function apiPost(path,body){
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'FamilyDoctorImport'},credentials:'same-origin',cache:'no-store',body:JSON.stringify(body)});
    let data;try{data=await response.json();}catch(error){throw new Error('服务响应异常，请重新校验；已保存的来源会自动去重。');}
    return {response,data};
  }
  async function previewImport(){
    const imp=state.import;if(!imp||imp.content===null)return;
    const generation=++state.generation;imp.phase='previewing';imp.preview=null;imp.error='';renderImport();
    try{
      const {response,data}=await apiPost('/api/import/preview',{format:imp.format,content:imp.content});
      if(!state.import||generation!==state.generation)return;
      if(!response.ok&&response.status!==422)throw new Error(data.error||'校验失败，请检查连接后重试。');
      if(typeof data.ok!=='boolean'||!Array.isArray(data.errors))throw new Error('校验结果不完整，请重新校验。');
      imp.preview=data;imp.phase='preview';renderImport();
    }catch(error){if(state.import&&generation===state.generation){imp.phase='preview';imp.error=error.message||'无法校验，请重试。';renderImport();}}
  }
  async function commitImport(){
    const imp=state.import;if(!imp||isDemo()||imp.phase!=='preview'||!imp.preview?.ok||!imp.preview.previewToken||imp.preview.errors?.length)return;
    const generation=++state.generation;imp.phase='committing';imp.error='';renderImport();
    try{
      const {response,data}=await apiPost('/api/import/commit',{format:imp.format,content:imp.content,previewToken:imp.preview.previewToken});
      if(!state.import||generation!==state.generation)return;
      if(!response.ok||data.ok===false){if(response.status===409){imp.preview=null;imp.phase='preview';imp.error=data.error||'资料已发生变化，请重新校验后导入。';renderImport();return;}throw new Error(data.error||'导入未完成，请重新校验。');}
      imp.result=data;imp.phase='success';imp.content=null;imp.preview=null;
      const refreshed=await load();if(!refreshed)imp.error='导入已完成，但统计刷新失败，请稍后刷新。';
      renderImport();
      if(!refreshed)toast('观察已保存；统计暂时无法刷新');
    }catch(error){if(state.import&&generation===state.generation){imp.phase='preview';imp.error='未能确认导入结果。请重新校验再试，稳定来源编号可避免重复入档。';imp.preview=null;renderImport();}}
  }
  document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
  document.addEventListener('click',event=>{
    const el=event.target.closest('button');
    if(el?.disabled)return;
    if(state.import&&!$('import-root').contains(event.target))return;
    if(el?.dataset.member){state.member=el.dataset.member;render();$('chat-detail-title')?.focus();$('chat-detail-title')?.scrollIntoView?.({block:'start',behavior:'smooth'});return;}
    if(event.target.classList.contains('overlay')){closeImport();return;}
    const action=el?.dataset.action;
    if(action==='open-import')openImport(el);
    else if(action==='close-import')closeImport();
    else if(action==='choose-file')$('import-file')?.click();
    else if(action==='toggle-help'){state.import.help=!state.import.help;renderImport();}
    else if(action==='another-import'){state.import=newImport();state.generation++;renderImport();}
    else if(action==='repreview')previewImport();
    else if(action==='commit-import')commitImport();
    else if(action==='retry'||el?.id==='refresh')load(true);
  });
  document.addEventListener('change',event=>{
    if(event.target.id==='member-filter'||event.target.id==='chat-member-filter'){if(state.import)return;const id=event.target.id;state.member=event.target.value;render();$(id)?.focus({preventScroll:true});}
    else if(event.target.id==='import-file')selectFile(event.target.files?.[0]);
  });
  document.addEventListener('dragover',event=>{const target=event.target.closest('#dropzone');if(target){event.preventDefault();target.classList.add('over');}});
  document.addEventListener('dragleave',event=>{event.target.closest('#dropzone')?.classList.remove('over');});
  document.addEventListener('drop',event=>{if(event.target.closest('#dropzone')){event.preventDefault();selectFile(event.dataTransfer?.files?.[0]);}});
  document.addEventListener('keydown',event=>{
    const dialog=document.querySelector('.import-panel');if(!dialog)return;
    if(event.key==='Escape'){event.preventDefault();closeImport();}
    if(event.key==='Tab'){const nodes=focusTargets(dialog).filter(el=>!el.disabled&&el.getClientRects().length>0&&el.tabIndex!==-1);const first=nodes[0],last=nodes[nodes.length-1];if(!nodes.includes(document.activeElement)){event.preventDefault();(event.shiftKey?last:first)?.focus({preventScroll:true});}else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus({preventScroll:true});}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus({preventScroll:true});}}
  });
  async function autoRefresh(){if(!document.hidden&&!state.import&&!state.loading&&!selectIsActive())await load(false,true);setTimeout(autoRefresh,30000);}
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!state.import&&!state.loading&&!selectIsActive())load(false,true);});
  load();setTimeout(autoRefresh,30000);
})();
