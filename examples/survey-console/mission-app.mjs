import {el,button,meta,replace} from './dom.mjs';
import {createHttpClient,startPolling} from './http-client.mjs';
import {createController} from './controller.mjs';
import {missionContract,toSurveyView} from './view-model.mjs';
import {createDecisionController,renderApproval} from './approval.mjs';
import {createEvidenceView,supportingSightings} from './evidence.mjs';
import {renderCounts} from './counts.mjs';
import {createChatView} from './chat.mjs';
import {createSafetyView} from './safety.mjs';
const root=document.getElementById('app');
root.classList.add('mission-console');document.title='SENTINEL · Mission Control';
const status=el('p',{id:'mission-state',role:'status',class:'meta'},['Connecting to local runtime…']);
const safetyHost=el('div',{id:'safety-controls'});
const top=el('header',{class:'mission-command-bar'},[el('div',{},[el('p',{class:'section-label'},['SENTINEL / MISSION CONTROL']),status]),safetyHost]);
const chatHost=el('section',{id:'chat',class:'mission-chat','aria-label':'Mission thread'},[el('h2',{},['Mission thread'])]);
const proposalHost=el('section',{id:'approval'});chatHost.append(proposalHost);
const mapHost=el('section',{id:'mission-map'},[el('h2',{},['Mission workspace'])]);
const evidenceHost=el('section',{id:'evidence'});
const framesHost=el('nav',{id:'captures','aria-label':'Captured frames'});
const spine=el('ol',{id:'evidence-spine','aria-label':'Evidence spine'});
const center=el('section',{id:'center',class:'mission-workspace',tabindex:-1},[mapHost,evidenceHost,framesHost,el('h3',{},['Evidence spine']),spine]);
const counts=el('aside',{id:'counts',class:'mission-facts','aria-label':'Survey facts'});
const audit=el('details',{class:'mission-audit'},[el('summary',{},['Audit log']),el('ol',{id:'audit-events'})]);
root.append(top,el('div',{class:'mission-layout'},[chatHost,center,counts]),audit);
const client=createHttpClient();
const controller=createController(client,missionContract);
let snapshot=null,selection=null,restored=false;
const chat=createChatView(chatHost,client,()=>snapshot);
const safety=createSafetyView(safetyHost,client,()=>snapshot);
chatHost.append(proposalHost);
const evidence=createEvidenceView(evidenceHost,client);evidence.clear();
const decision=createDecisionController(client,()=>crypto.randomUUID(),id=>({session_id:snapshot.session.id,command_id:id,correlation_id:snapshot.proposal.correlation_id}));
function persistDecision(ds){if(snapshot&&ds.attempt)localStorage.setItem(`sentinel:${snapshot.session.id}:${snapshot.run.id}:decision`,JSON.stringify(ds.attempt));}
function drawDecision(){if(snapshot)renderApproval(proposalHost,toSurveyView(snapshot),decision.getState(),{onApprove:()=>decision.submit('APPROVE'),onReject:()=>decision.submit('REJECT'),onRetry:()=>decision.retry()},controller.getState().needs_refresh);}
decision.subscribe(ds=>{persistDecision(ds);drawDecision();if(ds.result)controller.refresh();});
function select(kind,id){selection={kind,id};evidence.select(toSurveyView(snapshot),selection);}
controller.subscribe(state=>{
 if(!state.survey){if(state.error)status.textContent=`Connection unavailable: ${state.error}`;return;}
 snapshot=state.survey;
 status.textContent=`${snapshot.session.mode} · ${snapshot.source.kind} · ${snapshot.run.phase} · ${snapshot.run.id}${state.needs_refresh?' · refresh required':''}`;
 safety.render(snapshot);
 const view=toSurveyView(snapshot);
 chat.render(snapshot.chat.messages);decision.update(view,state.needs_refresh);drawDecision();
 renderCounts(counts,view,{onClass:id=>select('class',id),onObject:id=>select('object',id)});
 replace(framesHost,snapshot.evidence.frames.map(f=>button(`Frame ${f.stop_id} · ${f.analysis}`,()=>select('frame',f.id),{'data-frame-id':f.id})));
 replace(spine,state.events.map(e=>el('li',{},[e.summary])));
 replace(document.getElementById('audit-events'),state.events.map(e=>el('li',{},[`${e.sequence} · ${e.occurred_at} · ${e.summary}`])));
 if(state.log_gaps.length)spine.append(el('li',{class:'warning'},['Audit gap: '+state.log_gaps.join(', ')]));
 if(selection){const ids=selection.kind==='frame'?[selection.id]:supportingSightings(view,selection).map(x=>x.frame_id);if(ids.some(id=>snapshot.evidence.frames.find(f=>f.id===id)?.evidence!=='AVAILABLE')){evidence.clear();selection=null;}}
 if(!restored){restored=true;const saved=localStorage.getItem(`sentinel:${snapshot.session.id}:${snapshot.run.id}:decision`);if(saved){try{decision.restore(JSON.parse(saved));}catch{localStorage.removeItem(`sentinel:${snapshot.session.id}:${snapshot.run.id}:decision`);}}}
});
try{const session=await client.session();await controller.open(session.active_run_id);const stop=startPolling(()=>controller.refresh());window.addEventListener('pagehide',()=>{stop();chat.dispose();evidence.dispose();safety.dispose();});}
catch(e){status.textContent='Local runtime unavailable: '+e.message;}
