import {el,button,meta,replace} from './dom.mjs';
import {createHttpClient,startPolling} from './http-client.mjs';
import {createController} from './controller.mjs';
import {liveReadinessModel,missionContract,toSurveyView,missionStatusText} from './view-model.mjs';
import {createDecisionController,renderApproval} from './approval.mjs';
import {createEvidenceView,supportingSightings,frameDetectionText} from './evidence.mjs';
import {renderCounts} from './counts.mjs';
import {createChatView} from './chat.mjs';
import {createControlledLandController,createSafetyView} from './safety.mjs';
import {createLocalizationView} from './localization.mjs';
import {createSiteProfileController} from './site-profiles.mjs';
import {missionMapModel,renderMissionMap} from './mission-map.mjs';
const root=document.getElementById('app');
root.classList.add('mission-console');document.title='SENTINEL · Mission Control';
const status=el('p',{id:'mission-state',role:'status',class:'meta'},['Connecting to local runtime…']);
const statusFacts=el('p',{id:'mission-facts-status',role:'status',class:'meta'},['Mission facts unavailable']);
const safetyHost=el('div',{id:'safety-controls'});
const top=el('header',{class:'mission-command-bar'},[el('div',{},[el('p',{class:'section-label'},['SENTINEL / MISSION CONTROL']),status,statusFacts]),safetyHost]);
const readinessHost=el('section',{id:'live-readiness',class:'live-readiness','aria-labelledby':'live-readiness-title',hidden:true});
const chatHost=el('section',{id:'chat',class:'mission-chat','aria-label':'Mission thread'},[el('h2',{},['Mission thread'])]);
const proposalHost=el('section',{id:'approval'});chatHost.append(proposalHost);
const mapHost=el('section',{id:'mission-map'},[el('h2',{},['Mission workspace'])]);
const evidenceHost=el('section',{id:'evidence'});
const localizationHost=el('section',{id:'localization'});
const framesHost=el('nav',{id:'captures','aria-label':'Captured frames'});
const spine=el('ol',{id:'evidence-spine','aria-label':'Evidence spine'});
const center=el('section',{id:'center',class:'mission-workspace',tabindex:-1},[mapHost,localizationHost,evidenceHost,framesHost,el('h3',{},['Evidence spine']),spine]);
const counts=el('aside',{id:'counts',class:'mission-facts','aria-label':'Survey facts'});
const audit=el('details',{class:'mission-audit'},[el('summary',{},['Audit log']),el('ol',{id:'audit-events'})]);
root.append(top,readinessHost,el('div',{class:'mission-layout'},[chatHost,center,counts]),audit);
const client=createHttpClient();
const controller=createController(client,missionContract);
const profiles=createSiteProfileController(client,()=>controller.refresh());
profiles.subscribe(()=>{if(snapshot?.session.mode==='LIVE')renderReadiness(toSurveyView(snapshot));});
let snapshot=null,selection=null,restored=false;
let profileCatalogueActive=false,profileLockKey=null;
const landController=createControlledLandController({getSnapshot:()=>snapshot,requestLand:client.request_land});
const chat=createChatView(chatHost,client,()=>snapshot,{landController});
const safety=createSafetyView(safetyHost,client,()=>snapshot,{landController});
const localization=createLocalizationView(localizationHost,client);
chatHost.append(proposalHost);
const evidence=createEvidenceView(evidenceHost,client);evidence.clear();
const decision=createDecisionController(client,()=>crypto.randomUUID(),id=>({session_id:snapshot.session.id,command_id:id,correlation_id:snapshot.proposal.correlation_id}));
function persistDecision(ds){if(snapshot&&ds.attempt)localStorage.setItem(`sentinel:${snapshot.session.id}:${snapshot.run.id}:decision`,JSON.stringify(ds.attempt));}
function drawDecision(){if(snapshot)renderApproval(proposalHost,toSurveyView(snapshot),decision.getState(),{onApprove:()=>decision.submit('APPROVE'),onReject:()=>decision.submit('REJECT'),onRetry:()=>decision.retry()},controller.getState().needs_refresh);}
const capabilityLabels={planning:'Planning',flight:'Flight',detection:'Detection',emergency:'Emergency transport'};
function renderReadiness(view){
 const model=liveReadinessModel(view);
 readinessHost.hidden=!model.visible;
 if(!model.visible){replace(readinessHost,[]);return model;}
 const nodes=[
  el('p',{class:'section-label'},['LIVE MISSION READINESS']),
  el('h2',{id:'live-readiness-title'},['Live readiness']),
  el('p',{id:'live-readiness-state',class:'readiness-state'},[model.label]),
 ];
 const profileState=profiles.getState();
 const siteSelect=el('select',{id:'site-profile','aria-describedby':'site-profile-status',disabled:profileState.pending||!profileState.switch_allowed},
  profileState.profiles.length?profileState.profiles.map(profile=>el('option',{value:profile.id,selected:profile.id===profileState.selected_profile_id,disabled:!profile.enabled},
   [`${profile.label} · ${profile.enabled?'Available':'Draft'} · ${profile.setup_status}`])):[el('option',{value:''},['Loading site profiles…'])]);
 siteSelect.addEventListener('change',()=>profiles.select(siteSelect.value));
 nodes.push(el('div',{class:'site-profile-placard'},[
  el('span',{class:'site-profile-marker','aria-hidden':'true'},['CAL / 01']),
  el('label',{for:'site-profile'},['Physical site profile']),siteSelect,
  el('p',{id:'site-profile-status',role:'status',class:'meta'},[profileState.pending?'Selecting site profile…':profileState.feedback||
   (profileState.switch_allowed?'Select the calibrated site before the mission begins.':'Site selection locked for this mission.')]),
 ]));
 if(model.failed.length)nodes.push(el('ul',{class:'readiness-checks'},model.failed.map(check=>
  el('li',{id:`readiness-${check.capability}`},[
   el('strong',{},[capabilityLabels[check.capability]??check.capability]),
   el('span',{class:'readiness-code'},[check.reason_code]),
   el('p',{},[check.remediation]),
  ]))));
 else nodes.push(el('p',{class:'meta'},['Configuration checks passed. Dispatch still requires proposal approval and bounded preflight.']));
 replace(readinessHost,nodes);
 return model;
}
function describeReadiness(model){
 const failed=new Map(model.failed.map(check=>[check.capability,check]));
 const describe=(id,capabilities,fallback)=>{
  const control=document.getElementById(id);if(!control)return;
  const reason=capabilities.find(capability=>failed.has(capability));
  control.setAttribute('aria-describedby',reason?`readiness-${reason}`:fallback);
 };
 describe('approve',['planning'],'decision-heading');
 describe('reject',['planning'],'decision-heading');
 describe('land',['flight'],'safety-status');
 describe('land-now',['flight'],'safety-status');
 describe('emergency-stop',['emergency'],'safety-status');
}
decision.subscribe(ds=>{persistDecision(ds);drawDecision();if(ds.result)controller.refresh();});
function select(kind,id){selection={kind,id};evidence.select(toSurveyView(snapshot),selection);}
controller.subscribe(state=>{
 if(!state.survey){if(state.error)status.textContent=`Connection unavailable: ${state.error}`;return;}
 snapshot=state.survey;
 if(profileCatalogueActive&&snapshot.session.mode==='LIVE'){
  const key=`${snapshot.run.status}:${snapshot.run.phase}:${snapshot.proposal?.id??''}`;
  if(key!==profileLockKey){profileLockKey=key;void profiles.load();}
 }
 safety.render(snapshot);localization.render(snapshot);renderMissionMap(mapHost,missionMapModel(snapshot));
 const view=toSurveyView(snapshot);
 const readiness=renderReadiness(view);
 status.textContent=`${readiness.visible?readiness.label:`${snapshot.session.mode} · ${snapshot.source.kind}`} · ${snapshot.run.phase} · ${snapshot.run.id}${state.needs_refresh?' · refresh required':''}`;
 statusFacts.textContent=missionStatusText(view);
 chat.render(snapshot.chat.messages);decision.update(view,state.needs_refresh);drawDecision();
 describeReadiness(readiness);
 renderCounts(counts,view,{onClass:id=>select('class',id),onObject:id=>select('object',id)});
 replace(framesHost,snapshot.evidence.frames.map(f=>{const b=button('',()=>select('frame',f.id),{'data-frame-id':f.id,'aria-label':`Frame ${f.stop_id} ${frameDetectionText(f)}`});b.append(el('strong',{},[`Frame ${f.stop_id}`]),meta(frameDetectionText(f)),meta(`${f.evidence}${f.gap_reason?` - gap ${f.gap_reason}`:''}`),meta(`Commanded ${f.commanded_pose?'bound':'--'} / Measured ${f.measured_pose?'bound':'--'}`));return b;}));
 replace(spine,state.events.map(e=>el('li',{},[e.summary])));
 replace(document.getElementById('audit-events'),state.events.map(e=>el('li',{},[`${e.sequence} · ${e.occurred_at} · ${e.summary}`])));
 if(state.log_gaps.length)spine.append(el('li',{class:'warning'},['Audit gap: '+state.log_gaps.join(', ')]));
 if(selection){const ids=selection.kind==='frame'?[selection.id]:supportingSightings(view,selection).map(x=>x.frame_id);if(ids.some(id=>snapshot.evidence.frames.find(f=>f.id===id)?.evidence!=='AVAILABLE')){evidence.clear();selection=null;}}
 if(!restored){restored=true;const saved=localStorage.getItem(`sentinel:${snapshot.session.id}:${snapshot.run.id}:decision`);if(saved){try{decision.restore(JSON.parse(saved));}catch{localStorage.removeItem(`sentinel:${snapshot.session.id}:${snapshot.run.id}:decision`);}}}
});
try{const session=await client.session();await controller.open(session.active_run_id);if(session.mode==='LIVE'){profileCatalogueActive=true;profileLockKey=`${snapshot.run.status}:${snapshot.run.phase}:${snapshot.proposal?.id??''}`;await profiles.load();}const stop=startPolling(()=>controller.refresh());window.addEventListener('pagehide',()=>{stop();chat.dispose();evidence.dispose();safety.dispose();localization.dispose();});}
catch(e){status.textContent='Local runtime unavailable: '+e.message;}
