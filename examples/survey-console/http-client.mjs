import {validateMission} from './mission-contract.mjs';
export function createHttpClient(base='/api/v1',fetchImpl=fetch){
 const snapshots=new Map();
 const runPath=id=>`${base}/runs/${encodeURIComponent(id)}`;
 async function json(url,init,typedConflict=false){
  const response=await fetchImpl(url,init);const body=await response.json();
  const expectedTypedStatus={409:'CONFLICT',422:'INVALID',503:'UNAVAILABLE'}[response.status];
  if(!response.ok && !(typedConflict&&expectedTypedStatus!==undefined&&expectedTypedStatus===body.status))throw new Error(body.message??'Request failed');
  return body;
 }
 const post=(url,body,typed=false)=>json(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)},typed);
 return {
  session:()=>json(`${base}/session`),
  read_site_profiles:()=>json(`${base}/site-profiles`),
  select_site_profile:profile_id=>json(`${base}/site-profiles/select`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({profile_id})},true),
  async read_survey(id){const s=validateMission(await json(`${runPath(id)}/snapshot`));if(s.run.id!==id)throw Error('Run mismatch');snapshots.set(id,s);return s;},
  read_survey_events:(id,after,limit)=>json(`${runPath(id)}/events?after_sequence=${after}&limit=${limit}`),
  submit_decision:attempt=>post(`${runPath(attempt.run_id)}/decisions`,attempt,true),
  submit_chat:(id,command)=>post(`${runPath(id)}/chat`,command,true),
  request_land:(id,command)=>post(`${runPath(id)}/land`,command),
  trigger_emergency:command=>post(`${base}/safety/emergency`,command),
  async read_pose_evidence(id,evidence){
   if(evidence.run_id!==id)throw Error('Pose run mismatch');
   const response=await fetchImpl(`${runPath(id)}/evidence/${encodeURIComponent(evidence.artifact_id)}`,{cache:'no-store'});
   if(!response.ok)throw Error('Pose evidence unavailable');
   const bytes=new Uint8Array(await response.arrayBuffer());
   const media_type=response.headers.get('content-type');
   const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
   if(bytes.length>10*1024*1024||digest!==evidence.sha256||!['image/png','image/jpeg'].includes(media_type)||Number(response.headers.get('x-image-width'))!==evidence.width_px||Number(response.headers.get('x-image-height'))!==evidence.height_px)throw Error('Pose evidence binding failed');
   return {bytes,media_type};
  },
  async read_evidence(id,frameId){
   const frame=snapshots.get(id)?.evidence.frames.find(f=>f.id===frameId);
   const denied=(status,reason)=>({status,run_id:id,frame_id:frameId,reason});
   if(!frame?.artifact_id)return denied('NOT_FOUND','No bound artifact');
   if(frame.evidence!=='AVAILABLE')return denied(frame.evidence==='MISSING'?'NOT_FOUND':frame.evidence,'Evidence unavailable');
   const response=await fetchImpl(`${runPath(id)}/evidence/${encodeURIComponent(frame.artifact_id)}`,{cache:'no-store'});
   if(!response.ok){if([403,404,410].includes(response.status))return denied({403:'FORBIDDEN',404:'NOT_FOUND',410:'REVOKED'}[response.status],'Evidence unavailable');throw Error('Evidence transport failed');}
   if(snapshots.get(id)?.evidence.frames.find(f=>f.id===frameId)?.evidence!=='AVAILABLE')return denied('REVOKED','Capability changed');
   const bytes=new Uint8Array(await response.arrayBuffer());
   const width=Number(response.headers.get('x-image-width')),height=Number(response.headers.get('x-image-height'));
   const media_type=response.headers.get('content-type');
   if(bytes.length>10*1024*1024||!['image/png','image/jpeg'].includes(media_type)||width!==frame.width||height!==frame.height||response.headers.get('x-artifact-sha256')!==frame.sha256)throw Error('Invalid evidence binding');
   const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
   if(digest!==frame.sha256)throw Error('Evidence hash mismatch');
   return {status:'AVAILABLE',run_id:id,frame_id:frameId,media_type,bytes,width,height};
  }
 };
}
export function startPolling(refresh,{hidden=()=>document.hidden,schedule=setTimeout,cancel=clearTimeout}={}){
 let stopped=false,timer;
 async function tick(){try{await refresh();}finally{if(!stopped)timer=schedule(tick,hidden()?5000:500);}}
 tick();return ()=>{stopped=true;cancel(timer);};
}
