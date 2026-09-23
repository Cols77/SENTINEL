// Room-agent mode (Task C): pure view-model helpers, node-testable.
export const ROOM_AGENT = 'room_agent';
export const SURVEY = 'survey';
export const TERMINAL_STATUSES = new Set(['COMPLETED','ABORTED','REFUSED','FAILED','REJECTED','INCOMPLETE_RETURNED','SAFE_LANDED','BLOCKED','CLARIFY']);

export function architectureOptions(catalogue){
 const rows=catalogue?.architectures??[{id:SURVEY,label:'Survey (rules proposer)'},{id:ROOM_AGENT,label:'Room agent (Jev adaptive)'}];
 return rows.map(row=>({value:row.id,label:row.label,description:row.description??''}));
}

export function selectArchitecture(value,options){
 return options.some(option=>option.value===value)?value:SURVEY;
}

export function modeOptions(catalogue){
 return (catalogue?.modes??[]).map(row=>({value:row.id,label:row.label,disabled:!row.available,title:row.reason??''}));
}

export function scenarioOptions(catalogue){
 return (catalogue?.scenarios??[]).map(row=>({value:row.id,label:row.label,description:row.description}));
}

export function proposalCommand({mode,scenario,requestText,localIp}){
 const text=(requestText??'').trim();
 if(!text)return {error:'Type a request first.'};
 if(new TextEncoder().encode(text).length>4096)return {error:'The request is too long (4096 bytes at most).'};
 const command={mode,scenario,request_text:text};
 const ip=(localIp??'').trim();
 if(mode==='LIVE'&&ip){
  if(!/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)||ip.split('.').some(part=>Number(part)>255))return {error:'Local IP must be an IPv4 address such as 192.168.10.2.'};
  command.local_ip=ip;
 }
 return {command};
}

const fmt=(value,digits=0)=>value===null||value===undefined||Number.isNaN(Number(value))?'--':Number(value).toFixed(digits);

export function localizationText(run){
 const loc=run?.localization;
 if(!loc||loc.state==='UNAVAILABLE')return 'UNAVAILABLE';
 return loc.state;
}

export function fixAgeText(run){
 const age=run?.localization?.fix_age_s;
 return age===null||age===undefined?'fix age --':`fix age ${fmt(age,1)} s`;
}

export function poseText(run){
 const pose=run?.pose;
 if(!pose)return 'Pose unavailable';
 return `x ${fmt(pose.x_cm)} cm · y ${fmt(pose.y_cm)} cm · z ${fmt(pose.z_cm)} cm · heading ${fmt(pose.heading_deg)} deg`;
}

export function answerText(run){
 const answer=run?.answer;
 if(!answer)return TERMINAL_STATUSES.has(run?.status)?'No answer recorded.':'Awaiting answer…';
 return answer.summary??answer.text.split('\n')[0];
}

export function returnedHomeText(run){
 if(!run)return '';
 if(run.returned_home)return 'Returned home';
 if(run.returning)return 'Returning home…';
 return TERMINAL_STATUSES.has(run.status)?'Not returned home':'Not yet returning';
}

export function landingText(run){
 if(!run?.landing)return run?.land_status==='REQUESTED'?'Landing requested…':'Not landed';
 return `Landing: ${run.landing.state}${run.landing.reason?` (${run.landing.reason})`:''}`;
}

export function statusText(run){
 if(!run)return 'No room-agent run yet.';
 const phase=run.phase&&run.phase!==run.status?` · ${run.phase}`:'';
 return `${run.mode_label??run.mode} · ${run.status}${phase}${run.error?` · ${run.error}`:''}`;
}

export function decisionLines(run){
 return (run?.decisions??[]).map(d=>`${d.turn}. ${d.label} — ${d.reason}${d.confidence!==null&&d.confidence!==undefined?` (p ${fmt(d.confidence,2)})`:''}${d.result?` → ${d.result}`:''}`);
}

export function reanchorLines(run){
 return (run?.reanchor_log??[]).map(row=>({kind:row.kind,text:row.text}));
}

export function regionLines(run){
 return (run?.regions??[]).map(r=>`${r.label}: ${r.status}${r.detections?` · ${r.detections} detections`:''}`);
}

export function detectionLines(run){
 return (run?.detections??[]).map(d=>`${d.class_name} (${d.confidence}) at (${fmt(d.x_cm)}, ${fmt(d.y_cm)}) cm · ${d.region}`);
}

export function isRunning(run){return run?.status==='RUNNING';}
export function isAwaitingApproval(run){return run?.status==='AWAITING_APPROVAL'&&Boolean(run?.proposal);}
export function canPropose(run){return !run||!isRunning(run);}

export function roomViewModel(state){
 const run=state?.run??null;
 return {
  status:statusText(run),
  localization:localizationText(run),
  fixAge:fixAgeText(run),
  pose:poseText(run),
  battery:run?.battery_percent===null||run?.battery_percent===undefined?'battery --':`battery ${run.battery_percent}%`,
  goal:run?.goal??'',
  reanchor:reanchorLines(run),
  decisions:decisionLines(run),
  regions:regionLines(run),
  detections:detectionLines(run),
  captures:run?.captures??0,
  answer:answerText(run),
  answerDetail:run?.answer?.text??'',
  returnedHome:returnedHomeText(run),
  landing:landingText(run),
  proposal:isAwaitingApproval(run)?run.proposal:null,
  running:isRunning(run),
  canPropose:canPropose(run),
  yawRotations:run?.yaw_rotations??0,
  reanchorsAccepted:run?.reanchors_accepted??0,
 };
}

export function safetyIdentity(uuid=()=>crypto.randomUUID()){
 const id=uuid();return {command_id:id,correlation_id:id,idempotency_key:id};
}
