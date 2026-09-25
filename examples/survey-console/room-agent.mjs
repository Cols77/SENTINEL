// Room-agent mode (Task C): architecture selector and the room-agent panel.
// Additive to Mission Control: its own API (/api/v1/room-agent) plus the
// existing Land endpoint, which acts on the room run's
// registered flight runner. Every control carries a stable data-testid.
import {el,button,replace} from './dom.mjs';
import {ROOM_AGENT,SURVEY,architectureOptions,selectArchitecture,reviewOptions,proposalCommand,roomViewModel,safetyIdentity,flightControlMessages} from './room-agent-model.mjs';
import {createRoomCamera} from './room-camera.mjs';

const STORAGE_KEY='sentinel:architecture';
const store={get(){try{return localStorage.getItem(STORAGE_KEY);}catch{return null;}},set(v){try{localStorage.setItem(STORAGE_KEY,v);}catch{}}};

function createRoomClient(base='/api/v1',fetchImpl=fetch){
 async function json(url,init){const response=await fetchImpl(url,init);const body=await response.json().catch(()=>({}));return {ok:response.ok,status:response.status,body};}
 const post=(url,body)=>json(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
 return {
  session:()=>json(`${base}/session`),
  state:()=>json(`${base}/room-agent`),
  prepare:command=>post(`${base}/room-agent/preparations`,command),
  propose:command=>post(`${base}/room-agent/proposals`,command),
   decide:(proposal_id,decision)=>post(`${base}/room-agent/decisions`,{proposal_id,decision}),
   analyze:runId=>post(`${base}/room-agent/runs/${encodeURIComponent(runId)}/analysis`,{}),
   reviewResult:runId=>json(`${base}/room-agent/runs/${encodeURIComponent(runId)}/analysis/result`),
  land:(runId,identity)=>post(`${base}/runs/${encodeURIComponent(runId)}/land`,identity),
 };
}

const option=(row,selected)=>el('option',{value:row.value,selected:row.value===selected,disabled:Boolean(row.disabled),title:row.title||null},[row.label]);
const regionParts=line=>{
 const state=/\b(OBSERVED|OFFERED|LOW_CONFIDENCE)\b/.exec(line);
 if(!state)return [line];
 return [line.slice(0,state.index),el('span',{class:`room-region-state is-${state[0].toLowerCase()}`},[state[0]]),line.slice(state.index+state[0].length)];
};
const list=(testid,lines,empty,parts=line=>[line])=>el('ol',{'data-testid':testid,class:'room-log'},lines.length?lines.map(line=>typeof line==='string'?el('li',{},parts(line)):el('li',{'data-kind':line.kind},[line.text])):[el('li',{class:'meta'},[empty])]);

// The panel is rebuilt on every poll; a <details> rebuilt closed snapped
// shut about a second after it was opened. Remember which ones are open.
const openReasons=new Set();

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
   el('p',{class:'room-decision-meta'},[d.refused?`Refused · ${d.refused}`:'Jev selected this action',odds?` · ${odds}${mass}`:'']),
   reasonDetail(d),
  ]);
 }):[el('li',{class:'meta'},['No Jev turn yet.'])];
 return el('ol',{'data-testid':'room-decision-log',class:'room-log room-decision-list','aria-label':'Jev decisions'},rows);
}

function reasonDetail(d){
 const key=`${d.turn}:${d.label??''}`;
 const detail=el('details',{class:'room-decision-detail',open:openReasons.has(key)},[
  el('summary',{},['Why this step']),
  el('p',{class:'room-decision-reason'},[d.reason||'No reason recorded.']),
 ]);
 detail.addEventListener('toggle',()=>{if(detail.open)openReasons.add(key);else openReasons.delete(key);});
 return detail;
}

function reanchorList(steps){
 const recoveryKinds=new Set(['LOCALIZATION','RECOVERY','YAW_ROTATION','TAG_BURST','REANCHOR_ACCEPTED','EXPECTED_REANCHOR','CONTINGENCY','RETURN']);
 const recovery=steps.filter(row=>recoveryKinds.has(row.kind));
 return el('ol',{'data-testid':'room-reanchor-log',class:'room-log room-reanchor-list'},recovery.length
  ?recovery.map(row=>el('li',{'data-kind':row.kind,class:row.kind==='TAG_BURST'&&row.text.includes('no tags in view')?'is-tagless':''},[
    el('span',{class:'room-reanchor-kind'},[row.kind.replaceAll('_',' ')]),
    el('span',{class:'room-reanchor-text'},[row.text]),
   ]))
  :[el('li',{class:'meta'},['No re-anchor or recovery step yet.'])]);
}

export function mountRoomAgent({root,surveyLayout,client=createRoomClient()}){
 let catalogue=null,state=null,session=null,architecture=SURVEY,message='',timer=null,landAttempt=null,prepBusy=false;
 const form={mode:'LIVE',perceptionReview:'STANDARD',scenario:'a',requestText:'',localIp:''};
 const archSelect=el('select',{id:'architecture-select','data-testid':'architecture-select','aria-label':'Mission architecture'});
 const archHelp=el('p',{class:'meta',id:'architecture-help'});
 const bar=el('section',{id:'architecture-bar',class:'architecture-bar','aria-label':'Mission architecture'},[
  el('label',{for:'architecture-select',class:'section-label'},['ARCHITECTURE']),archSelect,archHelp]);
 const panel=el('section',{id:'room-agent',class:'room-agent','data-testid':'room-agent-panel',hidden:true,tabindex:'-1','aria-label':'Room agent'});
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
  root.classList.toggle('is-room-agent',room);
  const skip=document.querySelector('.skip');
  if(skip){skip.href=room?'#room-agent':'#center';skip.textContent=room?'Skip to room agent':'Skip to sweep';}
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
  landAttempt=null;
  await refresh();
 }

 async function analyze(){
  const runId=state?.run?.id;if(!runId)return;
  message='Starting local recorded review…';render();
  try{
   const r=await client.analyze(runId);
   message=r.ok?'Recorded review running.':`Review unavailable: ${r.body.detail??r.body.reason_code??r.status}`;
  }catch{message='Recorded review could not start.';}
  await refresh();
 }

 async function prepare(){
  if(prepBusy)return;
  prepBusy=true;message='Creating the next flight pack…';render();
  try{
   const r=await client.prepare({local_ip:form.localIp.trim()||null});
   message=r.ok?`New flight pack ${r.body.run_id} prepared.${r.body.server_restarting?' Resetting the Tello connection…':r.body.restart_required?' Restart Mission Control to release the Tello receiver.':''}`:
    `Preparation refused: ${r.body.detail??r.body.reason_code??r.status}`;
  }catch{message='Preparation failed: room-agent service unavailable.';}
  finally{prepBusy=false;await refresh();}
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

 // The setup form is built once (typing is never interrupted by polling);
 // only its options/disabled state and the dynamic facts are re-rendered.
 const reviewSelect=el('select',{id:'room-perception-review','data-testid':'room-perception-review'});
 reviewSelect.addEventListener('change',()=>{form.perceptionReview=reviewSelect.value;});
 const request=el('textarea',{id:'room-request','data-testid':'room-request',rows:2,placeholder:'count the phones and computers in the room'});
 request.addEventListener('input',()=>{form.requestText=request.value;});
 const ip=el('input',{id:'room-local-ip','data-testid':'room-local-ip',type:'text',inputmode:'decimal',placeholder:'auto (192.168.10.x)'});
 ip.addEventListener('input',()=>{form.localIp=ip.value;});
 const submit=button('Propose run',()=>void propose(),{id:'room-propose','data-testid':'room-propose','aria-describedby':'room-propose-hint'});
 const proposalHint=el('p',{id:'room-propose-hint',class:'room-action-hint','data-testid':'room-propose-hint'});
 const prepButton=button('Prepare next flight',()=>void prepare(),{id:'room-prepare','data-testid':'room-prepare','aria-describedby':'room-prep-hint'});
 const prepPack=el('p',{class:'room-prep-pack','data-testid':'room-prep-pack',role:'status'});
 const prepPort=el('p',{class:'room-prep-port','data-testid':'room-prep-port'});
 const prepHint=el('p',{id:'room-prep-hint',class:'room-prep-hint','data-testid':'room-prep-hint'});
 const prepPanel=el('section',{class:'room-preparation','aria-label':'Next flight preparation'},[
  el('p',{class:'room-prep-eyebrow'},['NEXT FLIGHT']),
  el('h3',{},['Flight pack']),
  prepPack,prepButton,prepPort,prepHint,
 ]);
 const setup=el('div',{class:'room-setup'},[
  el('p',{class:'room-live-marker'},['LIVE · REAL TELLO']),
  el('label',{for:'room-perception-review'},['Post-flight review']),reviewSelect,
  el('label',{for:'room-local-ip'},['Tello local IP']),ip,
  el('label',{for:'room-request'},['Request']),request,submit,proposalHint,
 ]);
  const landButton=button('Emergency land',()=>void land(),{id:'room-land','data-testid':'room-land',class:'emergency-control',title:'Request a controlled landing'});
 const messageLine=el('p',{'data-testid':'room-message',role:'status',class:'meta'});
 const approvalHost=el('div',{});
 const cameraHost=el('section',{class:'room-col room-camera','aria-label':'Drone camera'});
 const camera=createRoomCamera(cameraHost);
 const dynamic=el('div',{class:'room-dynamic room-col'});
 const logs=el('div',{class:'room-col room-logs'});
 const lists=el('div',{class:'room-col room-results'});
 const reviewStatus=el('p',{'data-testid':'room-review-status',role:'status'});
 const recordingLink=el('a',{'data-testid':'room-recording-download',download:'flight.h264'},['Download raw recording']);
 const analyzeButton=button('Analyze recording',()=>void analyze(),{'data-testid':'room-analyze'});
 const reviewVideo=el('video',{'data-testid':'room-review-video',controls:true,preload:'metadata'});
 const reviewRows=el('ol',{'data-testid':'room-review-detections',class:'room-log'});
 const reviewJson=el('a',{'data-testid':'room-review-json'},['Full detection timeline (JSON)']);
 const reviewPanel=el('section',{class:'room-recorded-review','data-testid':'room-recorded-review',hidden:true},[
  el('h3',{},['Recorded perception review']),
  el('p',{class:'meta'},['After landing, Grounding DINO marks sampled frames for visual inspection. Live decisions and counts are unchanged.']),
  reviewStatus,recordingLink,analyzeButton,reviewVideo,reviewRows,reviewJson,
 ]);
 panel.append(
   el('header',{class:'room-header'},[el('p',{class:'section-label'},['SENTINEL / ROOM AGENT · JEV ADAPTIVE']),el('div',{class:'room-safety'},[landButton])]),
   messageLine,
   prepPanel,
   el('div',{class:'room-grid'},[el('div',{class:'room-col room-setup-col'},[setup,approvalHost]),cameraHost,dynamic,logs,lists]),
 );
 camera.attachReview(reviewPanel);
 let lastReviewOptions='',loadedReviewId='',reviewData=null;

 function render(){
  const scrollPositions=new Map([...panel.querySelectorAll('.room-log, .room-answer pre')].map(node=>[node.dataset.testid,node.scrollTop]));
  const view=roomViewModel(state);
  const choices=reviewOptions(catalogue);
  const choicesKey=JSON.stringify(choices)+form.perceptionReview;
  if(choicesKey!==lastReviewOptions){
   lastReviewOptions=choicesKey;
   replace(reviewSelect,choices.map(row=>option(row,form.perceptionReview)));
   reviewSelect.value=form.perceptionReview;
  }
  if(document.activeElement!==ip&&ip.value!==form.localIp)ip.value=form.localIp;
  if(catalogue?.default_request)request.placeholder=catalogue.default_request;
  reviewSelect.disabled=!view.canPropose;
  request.disabled=!view.canPropose;
  ip.disabled=!view.canPropose;
  const prep=state?.preparation;
  const port=prep?.command_port;
   const liveReason=catalogue?.modes?.find(row=>row.id==='LIVE'&&!row.available)?.reason??'';
   const controls=flightControlMessages(prep,view.canPropose,liveReason);
   submit.disabled=Boolean(controls.propose);
   proposalHint.textContent=controls.propose;
   proposalHint.hidden=!controls.propose;
   prepButton.disabled=prepBusy||!prep?.can_prepare;
   prepButton.textContent=prepBusy?'Preparing…':'Prepare next flight';
   prepButton.title=controls.prepare;
  const expiry=prep?.assurances_expire_at?Date.parse(prep.assurances_expire_at):NaN;
  const expiryLabel=Number.isFinite(expiry)?new Date(expiry).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'unknown';
  const expired=Number.isFinite(expiry)&&expiry<=Date.now();
  const packLabel=prep?.pack_run_id?`${prep.pack_used?'Pack used':!Number.isFinite(expiry)?'Pack status unknown':expired?'Pack expired':'Pack staged'} · ${prep.pack_run_id} · assurances until ${expiryLabel}`:'No flight pack staged.';
  if(prepPack.textContent!==packLabel)prepPack.textContent=packLabel;
  prepPack.classList.toggle('is-expired',expired||Boolean(prep?.pack_used));
  prepPort.textContent=view.running?'Tello port in use by this flight. Land before preparing again.':
   port?.owner_is_this_server?'This server owns Tello port 8889. Prepare will restart its receiver.':
   port?.status==='BUSY'?`Tello port 8889 busy${port.owner_pid?` · process ${port.owner_pid}`:''}. Close that session before flying.`:
   port?.status==='FREE'?'Tello port 8889 free.':'Tello port status unavailable.';
  prepPort.classList.toggle('is-busy',port?.status==='BUSY');
  prepPort.classList.toggle('is-attention',view.running||Boolean(port?.owner_is_this_server)||port?.status==='BUSY');
   prepHint.textContent=prepBusy?'Creating a new grant, manifest and assurances…':controls.prepare;
  landButton.disabled=!view.running;
  messageLine.textContent=message;
  camera.render(state?.run);
  const run=state?.run, review=run?.review;
  if(run?.perception_review==='RECORDED_REVIEW'){
   const recording=review?.recording, analysis=review?.analysis;
   const recStatus=recording?.status??'PENDING', analysisStatus=analysis?.status??'UNAVAILABLE';
   reviewStatus.textContent=`Recording: ${recStatus}${recording?.bytes?` · ${(recording.bytes/1048576).toFixed(1)} MiB`:''}. Analysis: ${analysisStatus}${analysis?.reason_code?` (${analysis.reason_code})`:''}${analysis?.detail?` · ${analysis.detail}`:''}${recording?.error?` · ${recording.error}`:''}`;
   recordingLink.hidden=!['READY','FAILED'].includes(recStatus);
   recordingLink.href=`/api/v1/room-agent/runs/${encodeURIComponent(run.id)}/recording`;
   analyzeButton.hidden=analysisStatus==='COMPLETE';
   analyzeButton.disabled=analysisStatus!=='READY';
   reviewVideo.hidden=analysisStatus!=='COMPLETE';
   reviewJson.hidden=analysisStatus!=='COMPLETE';
   if(analysisStatus==='COMPLETE'){
    const base=`/api/v1/room-agent/runs/${encodeURIComponent(run.id)}/analysis`;
    if(reviewVideo.dataset.run!==run.id){reviewVideo.dataset.run=run.id;reviewVideo.src=`${base}/video`;}
    reviewJson.href=`${base}/result`;
    if(loadedReviewId!==run.id){
     reviewData=null;
     loadedReviewId=run.id;
     void client.reviewResult(run.id).then(r=>{
      if(loadedReviewId!==run.id)return;
      if(r.ok){reviewData=r.body;render();}
      else loadedReviewId='';
     }).catch(()=>{if(loadedReviewId===run.id)loadedReviewId='';});
    }
   }else if(loadedReviewId!==run.id){reviewData=null;loadedReviewId='';}
   replace(reviewRows,(reviewData?.detections??[]).slice(0,100).map(row=>el('li',{},[
     `${reviewData?.timestamp_basis==='estimated_30fps'?'~':''}${Number(row.time_s).toFixed(1)} s · ${row.label} · score ${Number(row.score).toFixed(2)} · frame ${row.frame}`,
   ])));
  }else{
   reviewStatus.textContent=run?'Recorded review was not enabled for this flight.':'Choose recorded review before proposing a Live flight.';
   recordingLink.hidden=true;analyzeButton.hidden=true;reviewVideo.hidden=true;reviewJson.hidden=true;
   replace(reviewRows,[]);
  }
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
   ...(view.preflightBlock?[el('div',{class:'room-preflight-block','data-testid':'room-preflight-block',role:'status'},[
    el('strong',{},[view.preflightBlock.title]),
    el('p',{},[view.preflightBlock.detail]),
    el('code',{},[view.preflightBlock.code]),
   ])]:[]),
   el('dl',{class:'room-facts'},[
    el('dt',{},['Status']),el('dd',{'data-testid':'room-status',class:`room-status is-${view.statusTone}`},[view.status]),
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
