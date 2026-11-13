// Exercise the actual controller in a synthetic DOM; no browser or network.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(mode='family'){
 return {meta:{mode,familyName:'合成家庭',generatedAt:'2026-11-11T10:00:00+08:00',windowDays:30,modelEnabled:false,importEnabled:mode==='family'},
 members:[{id:'m_test',name:'合成成员',status:'not_assessed',label:'尚未评估',eventCount:0,signalCount:0,validSamples:0,lastObservedAt:null,trend:[]}],imports:[],totals:{},revision:0};
}
const preview={ok:true,revision:0,digest:'digest',total:1,new:1,duplicates:0,errors:[],warnings:[],members:[{id:'m_test',name:'合成成员',new:1,duplicates:0}],previewToken:'ticket-one'};
function syntheticDocument(){
 const document={hidden:false};
 class Element{
  constructor(tag,attributes={}){this.tagName=tag.toUpperCase();this.attributes=attributes;this.children=[];this.parentElement=null;this.style={};this.inert=false;this.scrollTop=0;this._html='';this.textContent='';this._disabled='disabled' in attributes;
   this.classList={contains:name=>this.className.split(/\s+/).includes(name),add:name=>{if(!this.classList.contains(name))this.attributes.class=(this.className+' '+name).trim();},remove:name=>{this.attributes.class=this.className.split(/\s+/).filter(v=>v!==name).join(' ');},toggle:(name,force)=>{const on=force??!this.classList.contains(name);this.classList[on?'add':'remove'](name);return on;}};
  }
  get id(){return this.attributes.id||'';}get className(){return this.attributes.class||'';}
  get dataset(){return Object.fromEntries(Object.entries(this.attributes).filter(([k])=>k.startsWith('data-')).map(([k,v])=>[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase()),v]));}
  get disabled(){return this._disabled;}set disabled(value){this._disabled=!!value;if(value&&document.activeElement===this)document.activeElement=document.body;}
  get open(){return 'open' in this.attributes;}set open(value){if(value)this.attributes.open='';else delete this.attributes.open;}
  get tabIndex(){return 'tabindex' in this.attributes?Number(this.attributes.tabindex):['BUTTON','INPUT','SELECT','SUMMARY','A'].includes(this.tagName)?0:-1;}
  get value(){if(this._value!==undefined)return this._value;const option=this.querySelector('option[selected]')||this.querySelector('option');return this.attributes.value??option?.getAttribute('value')??'';}set value(value){this._value=value;}
  get isConnected(){return document.body===this||!!document.body?.contains(this);}
  get innerHTML(){return this._html;}set innerHTML(html){
   if(document.activeElement!==this&&this.contains(document.activeElement))document.activeElement=document.body;
   this.children.forEach(child=>{child.parentElement=null;});this.children=[];this._html=html;
   const stack=[this],voids=new Set(['INPUT','BR','HR','IMG','META','LINK']);
   for(const token of html.match(/<\/?[A-Za-z][^>]*>/g)||[]){
    const tag=token.match(/^<\/?([\w-]+)/)[1].toUpperCase();
    if(token.startsWith('</')){for(let i=stack.length-1;i>0;i--)if(stack[i].tagName===tag){stack.length=i;break;}continue;}
    const attributes={};const rest=token.slice(token.indexOf(' ')+1).replace(/\/?>(?:$)/,'');
    if(token.includes(' '))for(const match of rest.matchAll(/([\w:-]+)(?:="([^"]*)"|='([^']*)'|=([^\s>]+))?/g))attributes[match[1]]=match[2]??match[3]??match[4]??'';
    const child=new Element(tag,attributes),parent=stack[stack.length-1];child.parentElement=parent;parent.children.push(child);
    if(!voids.has(tag)&&!token.endsWith('/>'))stack.push(child);
   }
  }
  getAttribute(name){return this.attributes[name]??null;}setAttribute(name,value){this.attributes[name]=String(value);}removeAttribute(name){delete this.attributes[name];}
  contains(other){return !!other&&(other===this||this.children.some(child=>child.contains(other)));}
  matches(selector){
   const pieces=selector.trim().split(/\s+/),last=pieces.pop();if(pieces.length){if(!this.matches(last))return false;let parent=this.parentElement;while(parent){if(parent.matches(pieces.join(' ')))return true;parent=parent.parentElement;}return false;}
   const tag=last.match(/^[\w-]+/);if(tag&&this.tagName!==tag[0].toUpperCase())return false;
   const id=last.match(/#([\w-]+)/);if(id&&this.id!==id[1])return false;
   for(const match of last.matchAll(/\.([\w-]+)/g))if(!this.classList.contains(match[1]))return false;
   for(const match of last.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)){if(!(match[1] in this.attributes))return false;if(match[2]!==undefined&&this.attributes[match[1]]!==match[2])return false;}
   return true;
  }
  querySelectorAll(selector){const choices=selector.split(',');const found=[];for(const child of this.children){if(choices.some(choice=>child.matches(choice)))found.push(child);found.push(...child.querySelectorAll(selector));}return found;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  closest(selector){for(let el=this;el;el=el.parentElement)if(el.matches(selector))return el;return null;}
  getClientRects(){if(!this.isConnected)return [];for(let el=this;el;el=el.parentElement){if(el.getAttribute('hidden')!==null)return [];if(el.tagName==='DETAILS'&&!el.open&&!el.children.find(child=>child.tagName==='SUMMARY')?.contains(this))return [];}return [{}];}
  focus(options){if(this.disabled||!this.getClientRects().length)return;for(let el=this;el;el=el.parentElement)if(el.inert)return;document.activeElement=this;this.focusOptions=options;}
  click(){}scrollIntoView(){}
 }
 document.body=new Element('body');document.activeElement=document.body;
 document.body.innerHTML=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8').match(/<body>([\s\S]*)<\/body>/)[1];
 document.querySelectorAll=selector=>document.body.querySelectorAll(selector);document.querySelector=selector=>document.body.querySelector(selector);
 document.getElementById=id=>document.querySelectorAll('[id]').find(el=>el.id===id)||null;
 return document;
}
async function app(data=fixture(),responses={}){
 const listeners={},requests=[],timers=[],document=syntheticDocument();
 const element=id=>document.getElementById(id);document.addEventListener=(name,callback)=>listeners[name]=callback;
 const context={console,Intl,Date,Number,String,Array,Object,Set,Map,JSON,Promise,setTimeout:(fn,delay)=>{timers.push({fn,delay});return timers.length;},clearTimeout(){},
 document,
 fetch:async(url,options={})=>{requests.push({url,options});const handler=responses[url];const result=handler?await handler(options,requests):{status:200,data};if(result instanceof Error)throw result;return {ok:result.status>=200&&result.status<300,status:result.status,json:async()=>result.data};}};
 vm.runInNewContext(source,context,{filename:'web/app.js'});await settle();
 const click=async(action,{focus=true}={})=>{const button=action==='refresh'?element('refresh'):document.querySelector(`button[data-action="${action}"]`);if(button){if(focus)button.focus();listeners.click({target:button});}await settle();};
 const choose=async(name='sample.json',content='{"schema_version":1,"records":[{}]}')=>{listeners.change({target:{id:'import-file',files:[{name,size:Buffer.byteLength(content),text:async()=>content}]}});await settle();await settle();};
 return {element,listeners,requests,click,choose,timers,document:context.document};
}

test('only cognition is rendered; unknown is not healthy and names are escaped',async()=>{
 const data=fixture();data.meta.familyName='<img src=x> & 家庭';data.members[0].name='<b>合成成员</b>';
 const view=await app(data);const html=view.element('main').innerHTML;
 assert.ok(html.includes('&lt;img src=x&gt; &amp; 家庭'));assert.ok(html.includes('&lt;b&gt;合成成员&lt;/b&gt;'));
 assert.ok(!html.includes('<img'));assert.ok(!html.includes('<b>合成成员'));
 assert.ok(html.includes('尚未评估'));assert.ok(html.includes('还没有有效观察趋势'));
 for(const old of ['健康资料','健康事件','家庭总览','连接与隐私'])assert.ok(!html.includes(old));
});

test('invalid preview exposes row errors and never permits a commit',async()=>{
 const invalid={...preview,ok:false,previewToken:undefined,errors:[{row:1,field:'member',message:'成员未登记'},{row:1,field:'record',message:'记录含有不支持的字段'}]};
 const view=await app(fixture(),{'/api/import/preview':()=>({status:422,data:invalid})});
 await view.click('open-import');await view.choose();
 assert.ok(view.element('import-root').innerHTML.includes('第 1 条记录'));
 assert.ok(view.element('import-root').innerHTML.includes('成员未登记'));
 assert.ok(view.element('import-root').innerHTML.includes('第 1 条记录 · 记录结构'));
 assert.ok(!view.element('import-root').innerHTML.includes('data-action="commit-import"'));
 await view.click('commit-import');assert.equal(view.requests.filter(r=>r.url==='/api/import/commit').length,0);
});

test('validated import submits bound content, clears it and refreshes statistics',async()=>{
 let imported=false;
 const view=await app(fixture(),{
 '/api/dashboard':()=>({status:200,data:imported?{...fixture(),members:[{...fixture().members[0],status:'needs_review',label:'待核实变化',eventCount:1,signalCount:1}]}:fixture()}),
 '/api/import/preview':()=>({status:200,data:preview}),
 '/api/import/commit':()=>{imported=true;return {status:200,data:{...preview,revision:1,batchId:'batch',importedAt:'now'}};}
 });
 await view.click('open-import');await view.choose('合成观察.json','payload-marker');await view.click('commit-import');await settle();
 const request=view.requests.find(r=>r.url==='/api/import/commit');
 assert.equal(request.options.headers['X-Requested-With'],'FamilyDoctorImport');
 assert.deepEqual(JSON.parse(request.options.body),{format:'json',content:'payload-marker',previewToken:'ticket-one'});
 assert.ok(view.element('import-root').innerHTML.includes('导入已完成'));
 assert.ok(!view.element('import-root').innerHTML.includes('payload-marker'));
 assert.ok(view.element('main').innerHTML.includes('待核实变化'));
 await view.click('commit-import');assert.equal(view.requests.filter(r=>r.url==='/api/import/commit').length,1);
});

test('stale preview requires new validation; it does not retry the mutation automatically',async()=>{
 const view=await app(fixture(),{'/api/import/preview':()=>({status:200,data:preview}),'/api/import/commit':()=>({status:409,data:{error:'预览后资料已更新，请重新预览'}})});
 await view.click('open-import');await view.choose();await view.click('commit-import');
 const html=view.element('import-root').innerHTML;
 assert.ok(html.includes('预览后资料已更新'));assert.ok(html.includes('重新校验此文件'));
 assert.ok(!html.includes('data-action="commit-import"'));
 await view.click('commit-import');assert.equal(view.requests.filter(r=>r.url==='/api/import/commit').length,1);
 await view.click('repreview');assert.equal(view.requests.filter(r=>r.url==='/api/import/preview').length,2);
});

test('demo can preview but never commit even when a valid ticket exists',async()=>{
 const view=await app(fixture('demo'),{'/api/import/preview':()=>({status:200,data:{...preview,canCommit:false}})});
 await view.click('open-import');await view.choose();await view.click('commit-import');
 assert.ok(view.element('import-root').innerHTML.includes('演示模式不写入'));
 assert.equal(view.requests.filter(r=>r.url==='/api/import/commit').length,0);
});

test('saved import with failed refresh remains clearly saved, not an updated statistic',async()=>{
 let saved=false;
 const view=await app(fixture(),{'/api/dashboard':()=>saved?new Error('offline'):{status:200,data:fixture()},'/api/import/preview':()=>({status:200,data:preview}),'/api/import/commit':()=>{saved=true;return {status:200,data:preview};}});
 await view.click('open-import');await view.choose();await view.click('commit-import');await settle();
 const html=view.element('import-root').innerHTML;
 assert.ok(html.includes('导入已完成'));assert.ok(html.includes('导入已完成，但统计刷新失败'));
 assert.ok(!html.includes('统计已按成员归属'));
 assert.ok(view.element('main').innerHTML.includes('暂时无法读取观察统计'));
});

test('file validation is local for unsupported or empty uploads, and errors escape markup',async()=>{
 const view=await app(fixture());await view.click('open-import');await view.choose('<img>.txt','not-json');
 assert.ok(view.element('import-root').innerHTML.includes('&lt;img&gt;.txt'));
 assert.ok(view.element('import-root').innerHTML.includes('请选择 .json 或 .csv'));
 assert.equal(view.requests.filter(r=>r.url==='/api/import/preview').length,0);
 await view.choose('empty.csv','');assert.ok(view.element('import-root').innerHTML.includes('文件为空'));
});


test('an in-flight refresh cannot replace the post-import snapshot',async()=>{
 let reads=0,saved=false,releaseOld;
 const fresh={...fixture(),members:[{...fixture().members[0],status:'needs_review',label:'待核实变化',eventCount:3,signalCount:2}]};
 const view=await app(fixture(),{
 '/api/dashboard':()=>{reads++;if(reads===2)return new Promise(resolve=>{releaseOld=()=>resolve({status:200,data:fixture()});});return {status:200,data:saved?fresh:fixture()};},
 '/api/import/preview':()=>({status:200,data:preview}),
 '/api/import/commit':()=>{saved=true;return {status:200,data:preview};}
 });
 await view.click('refresh');await view.click('open-import');await view.choose();await view.click('commit-import');
 assert.equal(reads,2);releaseOld();await settle();await settle();
 assert.equal(reads,3);assert.ok(view.element('main').innerHTML.includes('待核实变化'));
 assert.ok(view.element('import-root').innerHTML.includes('导入已完成'));
 assert.ok(!view.element('import-root').innerHTML.includes('刷新失败'));
});

function chatFixture(){
 return {status:'review',label:'需家人核实',lastAnalyzedAt:'2026-11-12T09:00:00+08:00',clinicalValidation:false,
 self:{windowCount:5,observationDays:3,opportunities:8,signalDays:3},caregiver:{windowCount:2,observationDays:2,opportunities:2,signalDays:1},quality:{usable:7,limited:1,excluded:1},
 dimensions:[{key:'comprehension',label:'理解与回应',status:'possible_signal',opportunities:8,signalCount:3,signalDays:3,caregiverSignalDays:1,reference:{available:false,days:0,opportunities:0,signalCount:0,comparison:'unavailable'}}],trend:[],alerts:[]};
}
test('web-only alerts sort urgent first and never render raw analysis fields',async()=>{
 const data=fixture(),chat=chatFixture();data.members[0].chatAnalysis=chat;
 chat.raw_text='PRIVATE_RAW_CHAT';chat.dimensions[0].evidence='PRIVATE_EVIDENCE';
 chat.alerts=[{level:'info',label:'单次合成线索',days:1,lastSeen:'2026-11-12',sourceMode:'caregiver',domain:'comprehension'},{level:'urgent',label:'突然变化合成线索',days:1,lastSeen:'2026-11-12',quote:'PRIVATE_QUOTE'}];
 const view=await app(data),html=view.element('main').innerHTML;
 assert.ok(html.indexOf('突然变化合成线索')<html.indexOf('单次合成线索'));
 assert.ok(html.includes('提醒只在此页显示，不发送飞书消息'));
 assert.ok(html.includes('家属报告 · 理解与回应'));
 for(const marker of ['PRIVATE_RAW_CHAT','PRIVATE_EVIDENCE','PRIVATE_QUOTE'])assert.ok(!html.includes(marker));
 assert.ok(view.requests.every(r=>r.url==='/api/dashboard'));
});
test('member detail separates self and caregiver and does not label sparse reference normal',async()=>{
 const data=fixture();data.members[0].chatAnalysis=chatFixture();
 const view=await app(data);view.listeners.change({target:{id:'chat-member-filter',value:'m_test'}});await settle();
 const html=view.element('main').innerHTML;
 assert.ok(html.includes('本人可用片段'));assert.ok(html.includes('家属观察片段'));
 assert.ok(html.includes('早期参考资料尚不足'));assert.ok(html.includes('3</strong> 次本人线索'));
 assert.ok(html.includes('受限或排除片段'));assert.ok(!html.includes('低风险成员'));
 assert.ok(html.includes('与人工补充观察分别统计'));
});
test('automatic refresh pauses for hidden page and import and resumes without notifications',async()=>{
 const view=await app(fixture());const tick=view.timers.find(t=>t.delay===30000).fn;
 const before=view.requests.length;view.document.hidden=true;await tick();assert.equal(view.requests.length,before);
 view.document.hidden=false;await view.click('open-import');await tick();assert.equal(view.requests.length,before);
 await view.click('close-import');await tick();assert.equal(view.requests.length,before+1);
 assert.ok(view.requests.every(r=>r.url==='/api/dashboard'));
});
test('chat labels and names are escaped and absent chat data remains explicit',async()=>{
 const data=fixture();data.members[0].name='<script>name</script>';
 const view=await app(data);view.listeners.change({target:{id:'chat-member-filter',value:'m_test'}});await settle();
 let html=view.element('main').innerHTML;assert.ok(html.includes('尚未收到这位成员的聊天分析'));assert.ok(!html.includes('<script>name'));
 data.members[0].chatAnalysis=chatFixture();data.members[0].chatAnalysis.dimensions[0].label='<img src=x>';
 await view.click('refresh');html=view.element('main').innerHTML;
 assert.ok(html.includes('&lt;img src=x&gt;'));assert.ok(!html.includes('<img src=x>'));
});
test('analyzed windows without ordinary opportunities still show their real state',async()=>{
 const data=fixture(),chat=chatFixture();data.members[0].chatAnalysis=chat;
 for(const mode of ['self','caregiver'])chat[mode]={windowCount:0,observationDays:0,opportunities:0,signalDays:0};
 chat.quality={usable:1,limited:0,excluded:0};chat.status='urgent';
 chat.alerts=[{level:'urgent',label:'突然变化合成线索',days:1,lastSeen:'2026-11-12'}];
 chat.dimensions[0].status='not_assessable';chat.dimensions[0].opportunities=0;chat.dimensions[0].signalCount=0;
 const view=await app(data);view.listeners.change({target:{id:'chat-member-filter',value:'m_test'}});await settle();
 const html=view.element('main').innerHTML;
 assert.ok(html.includes('需及时关注'));assert.ok(html.includes('缺少观察机会'));
 assert.ok(!html.includes('尚未收到这位成员的聊天分析'));
 chat.status='insufficient';chat.alerts=[];await view.click('refresh');
 assert.ok(view.element('main').innerHTML.includes('观察机会不足'));
 assert.ok(!view.element('main').innerHTML.includes('尚未收到这位成员的聊天分析'));
});

test('trend expansion, member selection and ordinary focus survive real node replacement',async()=>{
 const view=await app(fixture()),tick=view.timers.find(t=>t.delay===30000).fn;
 const oldDetails=view.element('supplement-details');oldDetails.open=true;
 const select=view.element('member-filter');select.value='m_test';select.focus();
 view.listeners.change({target:select});await settle();
 assert.notEqual(view.element('supplement-details'),oldDetails);
 assert.equal(view.element('supplement-details').open,true);
 assert.equal(view.element('member-filter').value,'m_test');
 assert.equal(view.document.activeElement,view.element('member-filter'));
 assert.ok(view.document.activeElement.getClientRects().length>0);
 const before=view.requests.length;await tick();assert.equal(view.requests.length,before,'a focused select pauses automatic refresh');
 const oldButton=view.element('refresh');oldButton.focus();await tick();
 assert.equal(view.requests.length,before+1);
 assert.equal(oldButton.isConnected,false);
 assert.equal(view.document.activeElement,view.element('refresh'));
 assert.equal(view.document.activeElement.focusOptions.preventScroll,true);
 assert.equal(view.element('supplement-details').open,true);
 assert.equal(view.element('member-filter').value,'m_test');
 view.element('supplement-details').open=false;await view.click('refresh');
 assert.equal(view.element('supplement-details').open,false);
 assert.equal(view.document.activeElement,view.element('refresh'));
});

test('automatic responses cannot replace a select entered while the request was pending',async()=>{
 let reads=0,release;
 const updated=fixture();updated.meta.familyName='合成更新后的家庭';
 const view=await app(fixture(),{'/api/dashboard':()=>{reads++;return reads===2?new Promise(resolve=>{release=()=>resolve({status:200,data:updated});}):{status:200,data:reads>2?updated:fixture()};}});
 const tick=view.timers.find(t=>t.delay===30000).fn,pending=tick();await settle();
 const select=view.element('chat-member-filter');select.focus();release();await pending;
 assert.equal(view.element('chat-member-filter'),select);
 assert.equal(view.document.activeElement,select);
 assert.ok(!view.element('main').innerHTML.includes('合成更新后的家庭'));
 view.document.body.focus();await tick();
 assert.ok(view.element('main').innerHTML.includes('合成更新后的家庭'));
});

test('dialog repaint preserves focus and recaptures outside Tab while background stays inert',async()=>{
 const view=await app(fixture());await view.click('open-import');
 const backgrounds=view.document.querySelectorAll('main,header,footer,.skip-link');
 assert.ok(backgrounds.every(el=>el.inert));
 assert.equal(view.document.activeElement,view.element('close-import'));
 const oldHelp=view.document.querySelector('[data-action="toggle-help"]');await view.click('toggle-help');
 assert.equal(oldHelp.isConnected,false);
 assert.equal(view.document.activeElement,view.document.querySelector('[data-action="toggle-help"]'));
 const help=view.document.querySelector('.format-help details');help.open=true;
 await view.choose('invalid.txt','synthetic');
 assert.equal(view.document.querySelector('.format-help details').open,true);
 assert.ok(view.element('import-root').contains(view.document.activeElement));
 view.document.activeElement=view.document.body;let prevented=false;
 view.listeners.keydown({key:'Tab',shiftKey:false,preventDefault(){prevented=true;}});
 assert.equal(prevented,true);assert.equal(view.document.activeElement,view.element('close-import'));
 view.document.activeElement=view.document.body;prevented=false;
 view.listeners.keydown({key:'Tab',shiftKey:true,preventDefault(){prevented=true;}});
 assert.equal(prevented,true);assert.ok(view.element('import-root').contains(view.document.activeElement));
 await view.click('close-import');assert.ok(backgrounds.every(el=>!el.inert));
 assert.equal(view.document.activeElement,view.document.querySelector('[data-action="open-import"]'));
});

test('pending commit cannot be reopened from the background and closing restores the new trigger',async()=>{
 let release;
 const view=await app(fixture(),{'/api/import/preview':()=>({status:200,data:preview}),'/api/import/commit':()=>new Promise(resolve=>{release=()=>resolve({status:200,data:preview});})});
 const originalTrigger=view.document.querySelector('[data-action="open-import"]');
 await view.click('open-import');await view.choose();await view.click('commit-import');
 assert.ok(view.element('import-root').contains(view.document.activeElement));
 assert.ok(view.element('main').inert);
 const waiting=view.element('import-root').innerHTML;await view.click('open-import');await view.click('close-import');
 assert.equal(view.element('import-root').innerHTML,waiting);
 assert.equal(view.requests.filter(r=>r.url==='/api/import/commit').length,1);
 release();await settle();await settle();
 assert.ok(view.element('import-root').innerHTML.includes('导入已完成'));
 assert.equal(originalTrigger.isConnected,false,'the successful import refresh replaced the old trigger');
 await view.click('close-import');
 const newTrigger=view.document.querySelector('[data-action="open-import"]');
 assert.equal(view.document.activeElement,newTrigger);
 assert.equal(newTrigger.focusOptions.preventScroll,true);
 assert.equal(view.element('main').inert,false);
});

test('Safari mouse activation restores the actual import trigger even when it never received focus',async()=>{
 const view=await app(fixture());view.element('main').focus();
 assert.equal(view.document.activeElement,view.element('main'));
 await view.click('open-import',{focus:false});
 assert.equal(view.document.activeElement,view.element('close-import'));
 let prevented=false;view.listeners.keydown({key:'Escape',preventDefault(){prevented=true;}});
 assert.equal(prevented,true);
 assert.equal(view.element('import-root').innerHTML,'');
 assert.equal(view.document.activeElement,view.document.querySelector('[data-action="open-import"]'));
 assert.equal(view.document.activeElement.focusOptions.preventScroll,true);
});
