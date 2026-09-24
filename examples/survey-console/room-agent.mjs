// Room-agent mode (Task C): architecture selector and the room-agent panel.
// Additive to Mission Control: its own API (/api/v1/room-agent) plus the
// existing Land and Emergency endpoints, which act on the room run's
// registered flight runner. Every control carries a stable data-testid.
import {el,button,replace} from './dom.mjs';
import {ROOM_AGENT,SURVEY,architectureOptions,selectArchitecture,modeOptions,proposalCommand,roomViewModel,safetyIdentity} from './room-agent-model.mjs';
import {createRoomCamera} from './room-camera.mjs';

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
const regionParts=line=>{
 const state=/\b(OBSERVED|OFFERED|LOW_CONFIDENCE)\b/.exec(line);
 if(!state)return [line];
 return [line.slice(0,state.index),el('span',{class:`room-region-state is-${state[0].toLowerCase()}`},[state[0]]),line.slice(state.index+state[0].length)];
};
const list=(testid,lines,empty,parts=line=>[line])=>el('ol',{'data-testid':testid,class:'room-log'},lines.length?lines.map(line=>typeof line==='string'?el('li',{},parts(line)):el('li',{'data-kind':line.kind},[line.text])):[el('li',{class:'meta'},[empty])]);

function decisionList(decisions){
 const rows=decisions?.length?decisions.map(d=>{
  const action=d.action?.startsWith('OBSERVE:')?'observe':d.action?.startsWith('REANCHOR')?'reanchor':d.action?.startsWith('ANSWER')?'answer':'other';
  const tone=d.refused?'refused':action;
  const result=d.result||d.refused||d.outcome||'PENDING';
  const confidence=d.probability??d.confidence;
  const odds=confidence==null?'':`Jev p ${Number(confidence).toFixed(2)}`;
  const mass=d.kind_mass==null?'':` · observe mass ${Number(d.kind_mass).toFixed(2)}`;
  return el('li',{class:`room-decision is-${tone}`},[
   el('div',{class:'room-decision-top'},[
    el('span',{class:'room-decision-turn'},[`TURN ${String(d.turn).padStart(2,'0')}`]),
    el('strong',{},[d.label||'No usable choice']),
    el('span',{class:'room-decision-result'},[result.replaceAll('_',' ')]),
   ]),
   el('p',{class:'room-decision-reason'},[d.reason||'No reason recorded.']),
   el('p',{class:'room-decision-meta'},[d.refused?`Refused · ${d.refused}`:'Jev selected this action',odds?` · ${odds}${mass}`:'']),
  ]);
 }):[el('li',{class:'meta'},['No Jev turn yet.'])];
 return el('ol',{'data-testid':'room-decision-log',class:'room-log room-decision-list','aria-label':'Jev decisions'},rows);
}

function reanchorList(steps){
 return el('ol',{'data-testid':'room-reanchor-log',class:'room-log room-reanchor-list'},steps.length
  ?steps.map(row=>el('li',{'data-kind':row.kind,class:row.kind==='TAG_BURST'&&row.text.includes('no tags in view')?'is-tagless':''},[
    el('span',{class:'room-reanchor-kind'},[row.kind.replaceAll('_',' ')]),
    el('span',{class:'room-reanchor-text'},[row.text]),
   ]))
  :[el('li',{class:'meta'},['No re-anchor or recovery step yet.'])]);
}

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
  else camera.render(null);
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
 const request=el('textarea',{id:'room-request','data-testid':'room-request',rows:2,placeholder:'count the phones and computers in the room'});
 request.addEventListener('input',()=>{form.requestText=request.value;});
 const ip=el('input',{id:'room-local-ip','data-testid':'room-local-ip',type:'text',inputmode:'decimal',placeholder:'auto (192.168.10.x)'});
 ip.addEventListener('input',()=>{form.localIp=ip.value;});
 const submit=button('Propose run',()=>void propose(),{id:'room-propose','data-testid':'room-propose'});
 const setup=el('div',{class:'room-setup'},[
  el('label',{for:'room-mode'},['Run mode']),modeSelect,
  el('label',{for:'room-local-ip'},['Tello local IP (Live)']),ip,
  el('label',{for:'room-request'},['Request']),request,submit,
 ]);
 const landButton=button('Land',()=>void land(),{id:'room-land','data-testid':'room-land'});
 const emergencyButton=button('EMERGENCY STOP',()=>void emergency(),{id:'room-emergency','data-testid':'room-emergency',class:'emergency-control'});
 const messageLine=el('p',{'data-testid':'room-message',role:'status',class:'meta'});
 const approvalHost=el('div',{});
 const cameraHost=el('section',{class:'room-col room-camera','aria-label':'Drone camera'});
 const camera=createRoomCamera(cameraHost);
 const dynamic=el('div',{class:'room-dynamic room-col'});
 const logs=el('div',{class:'room-col room-logs'});
 const lists=el('div',{class:'room-col room-results'});
 panel.append(
  el('header',{class:'room-header'},[el('p',{class:'section-label'},['ROOM AGENT · JEV ADAPTIVE']),el('div',{class:'room-safety'},[landButton,emergencyButton])]),
  messageLine,
  el('div',{class:'room-grid'},[el('div',{class:'room-col room-setup-col'},[setup,approvalHost]),cameraHost,dynamic,logs,lists]),
 );
 let lastModes='';

 function render(){
  const scrollPositions=new Map([...panel.querySelectorAll('.room-log, .room-answer pre')].map(node=>[node.dataset.testid,node.scrollTop]));
  const view=roomViewModel(state);
  const modes=modeOptions(catalogue);
  const modesKey=JSON.stringify(modes)+form.mode;
  if(modesKey!==lastModes){lastModes=modesKey;replace(modeSelect,modes.map(row=>option(row,form.mode)));modeSelect.value=form.mode;}
  if(document.activeElement!==ip&&ip.value!==form.localIp)ip.value=form.localIp;
  if(catalogue?.default_request)request.placeholder=catalogue.default_request;
  modeSelect.disabled=!view.canPropose;
  request.disabled=!view.canPropose;
  ip.disabled=!view.canPropose||form.mode!=='LIVE';
  submit.disabled=!view.canPropose;
  landButton.disabled=!view.running;
  emergencyButton.disabled=!view.running;
  messageLine.textContent=message;
  camera.render(state?.run);
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
    el('dt',{},['Return home']),el('dd',{'data-testid':'room-returned-home',class:view.returnedHome==='Returned home'?'outcome-complete':''},[view.returnedHome]),
    el('dt',{},['Landing']),el('dd',{'data-testid':'room-landing',class:/EMERGENCY|FAILED/.test(view.landing)?'outcome-emergency':/^Landing: LANDED\b/.test(view.landing)?'outcome-complete':''},[view.landing]),
   ]),
   el('div',{class:`room-answer${view.answer==='Awaiting answer…'||view.answer==='No answer recorded.'?'':' is-complete'}`},[
    el('h3',{},['Answer']),
    el('p',{'data-testid':'room-answer',class:'room-answer-summary'},[view.answer]),
    el('pre',{'data-testid':'room-answer-detail'},[view.answerDetail]),
   ]),
  ]);
  replace(logs,[
   el('h3',{},['Jev decisions']),decisionList(state?.run?.decisions),
   el('h3',{'data-testid':'room-reanchor-heading'},[`Re-anchor and recovery (${view.yawRotations} yaw rotations, ${view.reanchorsAccepted} accepted)`]),
   reanchorList(view.reanchor),
  ]);
  replace(lists,[
   el('h3',{},['Regions']),list('room-regions',view.regions,'No region offered yet.',regionParts),
   el('h3',{},['Detections']),list('room-detections',view.detections,'No detection yet.'),
  ]);
  for(const node of panel.querySelectorAll('.room-log, .room-answer pre')){
   const top=scrollPositions.get(node.dataset.testid);
   if(top!==undefined)node.scrollTop=top;
  }
 }

 function start(){
  const tick=async()=>{await refresh();timer=setTimeout(tick,document.hidden?3000:500);};
  void tick();
 }
 architecture=store.get()??SURVEY;
 apply();start();
 return {dispose(){clearTimeout(timer);camera.dispose();},refresh,getState:()=>state};
}

export function injectRoomAgentStyles(doc=document){
 if(doc.getElementById('room-agent-css'))return;
 doc.head.append(el('link',{id:'room-agent-css',rel:'stylesheet',href:'./room-agent.css'}));
}
