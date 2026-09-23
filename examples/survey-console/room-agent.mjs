// Room-agent mode (Task C): architecture selector and the room-agent panel.
// Additive to Mission Control: its own API (/api/v1/room-agent) plus the
// existing Land and Emergency endpoints, which act on the room run's
// registered flight runner. Every control carries a stable data-testid.
import {el,button,replace} from './dom.mjs';
import {ROOM_AGENT,SURVEY,architectureOptions,selectArchitecture,modeOptions,scenarioOptions,proposalCommand,roomViewModel,safetyIdentity} from './room-agent-model.mjs';

const STORAGE_KEY='sentinel:architecture';
const store={get(){try{return localStorage.getItem(STORAGE_KEY);}catch{return null;}},set(v){try{localStorage.setItem(STORAGE_KEY,v);}catch{}}};

function createRoomClient(base='/api/v1',fetchImpl=fetch){
 async function json(url,init){const response=await fetchImpl(url,init);const body=await response.json().catch(()=>({}));return {ok:response.ok,status:response.status,body};}
 const post=(url,body)=>json(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
 return {
  session:()=>json(`${base}/session`),
  state:()=>json(`${base}/room-agent`),
  propose:command=>post(`${base}/room-agent/proposals`,command),
  decide:(proposal_id,decision)=>post(`${base}/room-agent/decisions`,{proposal_id,decision}),
  land:(runId,identity)=>post(`${base}/runs/${encodeURIComponent(runId)}/land`,identity),
  emergency:(session,identity)=>post(`${base}/safety/emergency`,{session_id:session.id,identity,active_run_id:session.active_run_id}),
 };
}

const option=(row,selected)=>el('option',{value:row.value,selected:row.value===selected,disabled:Boolean(row.disabled),title:row.title||null},[row.label]);
const list=(testid,lines,empty)=>el('ol',{'data-testid':testid,class:'room-log'},lines.length?lines.map(line=>typeof line==='string'?el('li',{},[line]):el('li',{'data-kind':line.kind},[line.text])):[el('li',{class:'meta'},[empty])]);

export function mountRoomAgent({root,surveyLayout,client=createRoomClient()}){
 let catalogue=null,state=null,session=null,architecture=SURVEY,message='',timer=null,landAttempt=null,emergencyAttempt=null;
 const form={mode:'REHEARSAL',scenario:'a',requestText:'',localIp:''};
 const archSelect=el('select',{id:'architecture-select','data-testid':'architecture-select','aria-label':'Mission architecture'});
 const archHelp=el('p',{class:'meta',id:'architecture-help'});
 const bar=el('section',{id:'architecture-bar',class:'architecture-bar','aria-label':'Mission architecture'},[
  el('label',{for:'architecture-select',class:'section-label'},['ARCHITECTURE']),archSelect,archHelp]);
 const panel=el('section',{id:'room-agent',class:'room-agent','data-testid':'room-agent-panel',hidden:true,'aria-label':'Room agent'});
 root.insertBefore(bar,surveyLayout);
 root.insertBefore(panel,surveyLayout);
 archSelect.addEventListener('change',()=>{architecture=archSelect.value;store.set(architecture);apply();});

 function apply(){
  const options=architectureOptions(catalogue);
  architecture=selectArchitecture(architecture,options);
  replace(archSelect,options.map(row=>option(row,architecture)));
  archSelect.value=architecture;
  archHelp.textContent=options.find(row=>row.value===architecture)?.description??'';
  const room=architecture===ROOM_AGENT;
  panel.hidden=!room;
  if(surveyLayout)surveyLayout.hidden=room;
  if(room)render();
 }

 async function refresh(){
  try{const r=await client.state();if(r.ok){state=r.body;catalogue=state.catalogue;if(!form.localIp&&catalogue.local_ip)form.localIp=catalogue.local_ip;}}
  catch{message='Room-agent service unavailable.';}
  if(architecture===ROOM_AGENT)render();
 }

 async function propose(){
  const {command,error}=proposalCommand(form);
  if(error){message=error;render();return;}
  message='Preparing proposal…';render();
  const r=await client.propose(command);
  message=r.ok?'Proposal ready: review and approve to dispatch.':`Refused: ${r.body.reason_code??r.status}`;
  landAttempt=null;emergencyAttempt=null;
  await refresh();
 }

 async function decide(decision){
  const proposal=state?.run?.proposal;if(!proposal)return;
  message=decision==='APPROVE'?'Approved: dispatching…':'Rejecting…';render();
  const r=await client.decide(proposal.id,decision);
  message=r.ok?(decision==='APPROVE'?'Dispatched.':'Rejected.'):`Refused: ${r.body.reason_code??r.status}`;
  await refresh();
 }

 async function land(){
  session??=(await client.session()).body;
  landAttempt??=safetyIdentity();
  message='Land requested…';render();
  const r=await client.land(session.active_run_id,landAttempt);
  message=`Land: ${r.body.status??r.status}${r.body.reason_code?` (${r.body.reason_code})`:''}`;
  await refresh();
 }

 async function emergency(){
  session??=(await client.session()).body;
  emergencyAttempt??=safetyIdentity();
  message='EMERGENCY requested…';render();
  const r=await client.emergency(session,emergencyAttempt);
  message=`Emergency: ${r.body.status??r.status}${r.body.reason_code?` (${r.body.reason_code})`:''}`;
  await refresh();
 }

 // The setup form is built once (typing is never interrupted by polling);
 // only its options/disabled state and the dynamic facts are re-rendered.
 const modeSelect=el('select',{id:'room-mode','data-testid':'room-mode-select'});
 modeSelect.addEventListener('change',()=>{form.mode=modeSelect.value;render();});
 const scenarioSelect=el('select',{id:'room-scenario','data-testid':'room-scenario-select'});
 scenarioSelect.addEventListener('change',()=>{form.scenario=scenarioSelect.value;render();});
 const request=el('textarea',{id:'room-request','data-testid':'room-request',rows:2,placeholder:'count the phones and computers in the room'});
 request.addEventListener('input',()=>{form.requestText=request.value;});
 const ip=el('input',{id:'room-local-ip','data-testid':'room-local-ip',type:'text',inputmode:'decimal',placeholder:'auto (192.168.10.x)'});
 ip.addEventListener('input',()=>{form.localIp=ip.value;});
 const submit=button('Propose run',()=>void propose(),{id:'room-propose','data-testid':'room-propose'});
 const setup=el('div',{class:'room-setup'},[
  el('label',{for:'room-mode'},['Run mode']),modeSelect,
  el('label',{for:'room-scenario'},['Rehearsal scenario']),scenarioSelect,
  el('label',{for:'room-local-ip'},['Tello local IP (Live)']),ip,
  el('label',{for:'room-request'},['Request']),request,submit,
 ]);
 const landButton=button('Land',()=>void land(),{id:'room-land','data-testid':'room-land'});
 const emergencyButton=button('EMERGENCY STOP',()=>void emergency(),{id:'room-emergency','data-testid':'room-emergency',class:'emergency-control'});
 const messageLine=el('p',{'data-testid':'room-message',role:'status',class:'meta'});
 const approvalHost=el('div',{});
 const dynamic=el('div',{class:'room-dynamic'});
 const logs=el('div',{class:'room-col'});
 const lists=el('div',{class:'room-col'});
 panel.append(
  el('header',{class:'room-header'},[el('p',{class:'section-label'},['ROOM AGENT · JEV ADAPTIVE']),el('div',{class:'room-safety'},[landButton,emergencyButton])]),
  messageLine,
  el('div',{class:'room-grid'},[el('div',{class:'room-col'},[setup,approvalHost,dynamic]),logs,lists]),
 );
 let lastModes='',lastScenarios='';

 function render(){
  const view=roomViewModel(state);
  const modes=modeOptions(catalogue),scenarios=scenarioOptions(catalogue);
  const modesKey=JSON.stringify(modes)+form.mode,scenariosKey=JSON.stringify(scenarios)+form.scenario;
  if(modesKey!==lastModes){lastModes=modesKey;replace(modeSelect,modes.map(row=>option(row,form.mode)));modeSelect.value=form.mode;}
  if(scenariosKey!==lastScenarios){lastScenarios=scenariosKey;replace(scenarioSelect,scenarios.map(row=>option(row,form.scenario)));scenarioSelect.value=form.scenario;}
  if(document.activeElement!==ip&&ip.value!==form.localIp)ip.value=form.localIp;
  if(catalogue?.default_request)request.placeholder=catalogue.default_request;
  modeSelect.disabled=!view.canPropose;
  scenarioSelect.disabled=!view.canPropose||form.mode==='LIVE';
  request.disabled=!view.canPropose;
  ip.disabled=!view.canPropose||form.mode!=='LIVE';
  submit.disabled=!view.canPropose;
  landButton.disabled=!view.running;
  emergencyButton.disabled=!view.running;
  messageLine.textContent=message;
  const proposalId=view.proposal?.id??'';
  if(approvalHost.dataset.proposal!==proposalId){
   approvalHost.dataset.proposal=proposalId;
   replace(approvalHost,view.proposal?[el('div',{class:'room-approval','data-testid':'room-approval'},[
    el('h3',{},['Approve this run?']),
    el('p',{'data-testid':'room-proposal-request'},[`“${view.proposal.request_text}”`]),
    el('ul',{},view.proposal.lines.map(line=>el('li',{},[line]))),
    button(view.proposal.flies?'Approve and fly':'Approve',()=>void decide('APPROVE'),{id:'room-approve','data-testid':'room-approve',class:view.proposal.flies?'danger':''}),
    button('Reject',()=>void decide('REJECT'),{id:'room-reject','data-testid':'room-reject'}),
   ])]:[]);
  }
  replace(dynamic,[
   el('dl',{class:'room-facts'},[
    el('dt',{},['Status']),el('dd',{'data-testid':'room-status'},[view.status]),
    el('dt',{},['Localization']),el('dd',{'data-testid':'room-localization-state',class:`loc-${view.localization.toLowerCase()}`},[view.localization]),
    el('dt',{},['Fix age']),el('dd',{'data-testid':'room-fix-age'},[view.fixAge]),
    el('dt',{},['Pose']),el('dd',{'data-testid':'room-pose'},[`${view.pose} · ${view.battery}`]),
    el('dt',{},['Goal']),el('dd',{'data-testid':'room-goal'},[view.goal||'--']),
    el('dt',{},['Captures']),el('dd',{'data-testid':'room-captures'},[String(view.captures)]),
    el('dt',{},['Return home']),el('dd',{'data-testid':'room-returned-home'},[view.returnedHome]),
    el('dt',{},['Landing']),el('dd',{'data-testid':'room-landing'},[view.landing]),
   ]),
   el('div',{class:'room-answer'},[
    el('h3',{},['Answer']),
    el('p',{'data-testid':'room-answer',class:'room-answer-summary'},[view.answer]),
    el('pre',{'data-testid':'room-answer-detail'},[view.answerDetail]),
   ]),
  ]);
  replace(logs,[
   el('h3',{},['Jev decisions']),list('room-decision-log',view.decisions,'No Jev turn yet.'),
   el('h3',{'data-testid':'room-reanchor-heading'},[`Re-anchor and recovery (${view.yawRotations} yaw rotations, ${view.reanchorsAccepted} accepted)`]),
   list('room-reanchor-log',view.reanchor,'No re-anchor or recovery step yet.'),
  ]);
  replace(lists,[
   el('h3',{},['Regions']),list('room-regions',view.regions,'No region offered yet.'),
   el('h3',{},['Detections']),list('room-detections',view.detections,'No detection yet.'),
  ]);
 }

 function start(){
  const tick=async()=>{await refresh();timer=setTimeout(tick,document.hidden?3000:500);};
  void tick();
 }
 architecture=store.get()??SURVEY;
 apply();start();
 return {dispose(){clearTimeout(timer);},refresh,getState:()=>state};
}

export function injectRoomAgentStyles(doc=document){
 if(doc.getElementById('room-agent-css'))return;
 doc.head.append(el('link',{id:'room-agent-css',rel:'stylesheet',href:'./room-agent.css'}));
}
