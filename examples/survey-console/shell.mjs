import {el,button,replace,meta} from './dom.mjs';import {STEPS} from './contract.mjs';
const title=t=>el('h2',{},[t]);const host=id=>el('div',{id});
function ensureShell(root){if(root.querySelector('#center'))return;
 root.className='console';root.append(
 el('header',{'data-testid':'Header',class:'header'},[host('identity'),host('run-badges'),host('header-actions')]),
 el('aside',{'data-testid':'Left',class:'left pane'},[el('div',{class:'section-label'},['Operator intent']),host('request'),host('proposer'),host('reviewer-controls')]),
 el('section',{'data-testid':'Center',class:'center pane',id:'center',tabindex:'-1'},[host('mission'),host('run-stepper'),host('sweep-details'),host('rail-host'),host('frames-host'),host('evidence')]),
 el('aside',{'data-testid':'Right',class:'right pane'},[host('counts'),host('approval')]),
 el('footer',{'data-testid':'Bottom',class:'bottom pane'},[host('progress'),host('failures'),host('event-log')])
 );
}
const pose=p=>p?`x ${p.x_m} · y ${p.y_m} · z ${p.z_m} m · yaw ${p.yaw_deg}°`:'— · unavailable';
export function renderShell(root,state,handlers={}){
 ensureShell(root);const s=state.survey;if(!s){document.getElementById('mission').textContent=state.error||'Loading fixture projection…';return;}
 const get=id=>root.querySelector(`#${id}`);
 replace(get('identity'),[el('img',{src:'../../assets/sentinel-ui/brand/symbol-dark.svg',alt:'SENTINEL',width:40,height:40}),el('div',{},[el('strong',{},['SENTINEL']),meta('SURVEY CONSOLE')])]);
 replace(get('run-badges'),[el('span',{class:'badge fixture'},['FIXTURE DEMO']),el('span',{class:'badge'},[s.source]),el('span',{class:`badge ${s.freshness==='STALE'?'warning':''}`},[s.freshness]),el('time',{datetime:s.updated_at,class:'meta'},[s.updated_at])]);
 replace(get('header-actions'),[el('a',{id:'legacy-view',href:'../../assets/sentinel-ui/console.html'},['Legacy alley ↗']),button('Emergency stop unavailable — fixture demo',null,{id:'emergency-disconnected',disabled:true})]);
 replace(get('request'),[title('Lecture-room sweep'),el('p',{},[s.request_text]),meta('Initial request · immutable')]);
 replace(get('proposer'),[el('h3',{},['Proposer activity']),meta(`${s.proposer.mode} · ${s.proposer.source}`),el('p',{},[s.step==='REQUEST'?'Request received':s.proposal?'Candidate available for review':'Read-model update received']),meta('Conversational assistant not connected')]);
 replace(get('mission'),[el('div',{class:'section-label'},[`CURRENT SWEEP · ${s.sweep.id}`]),el('h1',{},[s.status==='ABORTED'?'Partial sweep aborted':s.step==='REVIEW'?'Review the observations':`${s.step[0]}${s.step.slice(1).toLowerCase()} in progress`]),el('p',{id:'flight-budget',class:'budget'},[`Flights used: ${s.flights.used} of ${s.flights.max}${s.status==='ABORTED'?' · partial sweep aborted; excluded from merge':''}`])]);
 replace(get('run-stepper'),[el('ol',{class:'stepper','aria-label':'Run progress'},STEPS.map(step=>el('li',{'aria-current':step===s.step?'step':null,class:step===s.step?'current':''},[step])))]);
 replace(get('sweep-details'),[el('h2',{},[s.sweep.option_id]),meta(`Selected option · ${s.sweep.status} · ${s.sweep.merged?'Merged':'Not merged'}`)]);
 replace(get('rail-host'),[el('ol',{class:'rail','aria-label':'Planned sweep stops'},s.sweep.stops.map((stop,i)=>{
 const b=button([String(i+1).padStart(2,'0'),stop.id,stop.status].join(' · '),()=>{const f=s.frames.find(f=>f.stop_id===stop.id);if(f)handlers.onFrame?.(f.id);},{id:`stop-${stop.id}`,'data-stop-id':stop.id,'data-status':stop.status});
 return el('li',{},[b,meta(`${stop.range_m} m range · ${stop.altitude_m} m altitude`),meta(`${stop.extent_m} m extent · ${stop.duration_s} s`),meta(stop.resolvable_classes.join(' · '))]);
 }))]);
 replace(get('frames-host'),[el('div',{class:'section-heading'},[title('Capture strip'),meta('Commanded and measured poses are separate')]),s.frames.length?el('div',{class:'frame-strip'},s.frames.map(f=>{
 const b=button('',()=>handlers.onFrame?.(f.id),{id:`frame-${f.id}`,'data-frame-id':f.id,'aria-pressed':'false',class:'frame'});
 b.append(el('img',{src:'../../assets/sentinel-ui/placeholders/evidence-empty.svg',alt:'Synthetic fixture capture placeholder'}),el('strong',{},[`${f.stop_id} / ${f.id}`]),el('time',{datetime:f.captured_at??'',class:'meta'},[f.captured_at??'No frame retained']),meta(`Commanded: ${pose(f.commanded_pose)}`),meta(`Measured: ${pose(f.measured_pose)}`),meta(`${f.analysis} · ${f.evidence}`),meta(f.merge_eligible?'Eligible for merge':'Excluded from merge'));if(f.failure)b.append(el('p',{class:'warning'},[f.failure]));return b;
 })):el('p',{class:'empty'},['No captured frames in this snapshot.'])]);
 replace(get('progress'),[el('strong',{id:'analysis-progress'},[`${s.analysis.analysed}/${s.analysis.total} analysed · current sweep`]),meta(`Provider: ${s.provider.name} / ${s.provider.model} · source kind ${s.provider.source_kind}`),meta(`Dropped labels: ${s.analysis.dropped_labels.join(', ')||'None'} · Unknown labels: ${s.analysis.unknown_labels.join(', ')||'None'}`)]);
 replace(get('failures'),[...(state.error?[el('p',{role:'alert',class:'warning'},[state.error])]:[]),...s.failures.map(f=>el('p',{role:'alert',class:'warning'},[f])),...(state.needs_refresh?[meta('Refresh required; decisions disabled')]:[]),...(state.log_gaps.length?[meta(`Event log gap: ${state.log_gaps.join(', ')}`)]:[])]);
 const log=get('event-log'),scroll=log.scrollTop;replace(log,[el('ol',{},state.events.map(e=>el('li',{},[el('time',{datetime:e.occurred_at},[e.occurred_at.slice(11,19)]),` ${e.summary}`])))]);log.scrollTop=scroll;
 const announcement=`${s.step} · ${s.status} · ${s.analysis.analysed}/${s.analysis.total} analysed · ${s.freshness}`;
 const live=document.getElementById('announcer');if(live&&live.textContent!==announcement)live.textContent=announcement;
}
