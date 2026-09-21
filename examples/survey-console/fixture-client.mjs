/** In-memory client: the only mutations are reviewer-controlled fixture actions. */
export function createFixtureClient(input){
 const scenario=structuredClone(input),ledger=new Map(),attemptKeys=new Map();let index=0,pending=null;const calls=[];
 const current=()=>scenario.snapshots[index];
 const error=(code,message)=>Object.assign(new Error(message),{code});
 const requireRun=id=>{if(id!==current().run_id)throw error('NOT_FOUND','Run not found');};
 return {
  get calls(){return structuredClone(calls);},
  advance(){index=Math.min(index+1,scenario.snapshots.length-1);},
  async read_survey(id){requireRun(id);return structuredClone(current());},
  async read_survey_events(id,after,limit){requireRun(id);if(!Number.isSafeInteger(after)||after<0||!Number.isSafeInteger(limit)||limit<1||limit>200)throw error('INVALID_ARGUMENT','Invalid event cursor or limit');return structuredClone(scenario.events.filter(e=>e.sequence>after&&e.sequence<=current().event_sequence).slice(0,limit));},
  async read_evidence(run_id,frame_id){
   const f=run_id===current().run_id?current().frames.find(x=>x.id===frame_id):null;
   const denied=status=>({status,run_id,frame_id,reason:status==='FORBIDDEN'?'Consent required; no frame retained':`Evidence ${status.toLowerCase()}`});
   if(!f||f.evidence==='MISSING')return denied('NOT_FOUND');if(f.evidence!=='AVAILABLE')return denied(f.evidence);
   // Synthetic 1×1 PNG, never a retained lecture-room photograph.
   const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
   return {status:'AVAILABLE',run_id,frame_id,media_type:'image/png',bytes:Uint8Array.from(atob(png),c=>c.charCodeAt(0)),width:1,height:1};
  },
  submit_decision(a){
   try{
    const fields=['run_id','proposal_id','proposal_sha256','expected_version','decision','attempt_id','idempotency_key'];
    if(!a||Object.keys(a).length!==fields.length||fields.some(k=>!Object.hasOwn(a,k))||fields.filter(k=>k!=='expected_version').some(k=>typeof a[k]!=='string'||!a[k].trim())||!['APPROVE','REJECT'].includes(a.decision)||!Number.isSafeInteger(a.expected_version)||a.expected_version<0)throw error('INVALID_ARGUMENT','Malformed attempt');
    const identity=JSON.stringify(fields.map(k=>a[k])),prior=ledger.get(a.idempotency_key);
    if(prior){if(prior.identity!==identity)throw error('IDENTITY_REUSED','Attempt identity reused with changed payload');return prior.promise;}
    if(attemptKeys.has(a.attempt_id))throw error('IDENTITY_REUSED','Attempt ID reused with another key');
    if(pending)throw error('INVALID_ARGUMENT','An attempt is already pending');
    let resolve;const promise=new Promise(r=>resolve=r);const row={identity,promise,resolve,attempt:structuredClone(a)};
    ledger.set(a.idempotency_key,row);attemptKeys.set(a.attempt_id,a.idempotency_key);calls.push(structuredClone(a));
    const s=current(),p=s.proposal;
    if(a.run_id!==s.run_id||a.expected_version!==s.version||a.proposal_id!==p?.id||a.proposal_sha256!==p?.sha256){resolve({attempt_id:a.attempt_id,status:'CONFLICT',message:'Fixture proposal/version conflict'});return promise;}
    const eligible=p.eligibility;
    if(s.status!=='ACTIVE'||s.freshness!=='FRESH'||!eligible.current||[eligible.consent,eligible.envelope,eligible.preflight].some(x=>x!==true)||p.disabled_reasons.length){resolve({attempt_id:a.attempt_id,status:'REJECTED',message:'Fixture eligibility does not permit a decision'});return promise;}
    pending=row;return promise;
   }catch(e){return Promise.reject(e);}
  },
  resolveDecision(status){if(!scenario.decision_results.includes(status))throw error('INVALID_ARGUMENT','Unknown fixture decision result');if(!pending)return;const row=pending;pending=null;row.resolve({attempt_id:row.attempt.attempt_id,status,message:`Fixture ${status.toLowerCase()}; no command sent`});},
 };
}
