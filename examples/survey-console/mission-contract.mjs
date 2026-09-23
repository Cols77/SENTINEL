import schema from './mission-schema.mjs';
function check(rule,value,path){
 const fail=()=>{throw new Error(`Invalid mission projection: ${path}`);};
 if(rule.anyOf){if(!rule.anyOf.some(r=>{try{check(r,value,path);return true;}catch{return false;}}))fail();return;}
 if('const' in rule && value!==rule.const)fail();
 if(rule.enum && !rule.enum.includes(value))fail();
 if(rule.type==='null' && value!==null)fail();
 if(rule.type==='boolean' && typeof value!=='boolean')fail();
 if(rule.type==='string' && (typeof value!=='string'||value.length<(rule.minLength??0)))fail();
 if(['number','integer'].includes(rule.type)){
  if(typeof value!=='number'||!Number.isFinite(value)||(rule.type==='integer'&&!Number.isSafeInteger(value))||value<(rule.minimum??-Infinity)||value>(rule.maximum??Infinity))fail();
 }
 if(rule.type==='array'){
  if(!Array.isArray(value)||value.length<(rule.minItems??0)||value.length>(rule.maxItems??Infinity))fail();
  value.forEach((v,i)=>check(rule.items,v,`${path}[${i}]`));
 }
 if(rule.type==='object'){
  if(!value||typeof value!=='object'||Array.isArray(value))fail();
  if(rule.required.some(k=>!Object.hasOwn(value,k)))fail();
  if(rule.additionalProperties===false&&Object.keys(value).some(k=>!Object.hasOwn(rule.properties,k)))fail();
  for(const [k,r] of Object.entries(rule.properties))if(Object.hasOwn(value,k))check(r,value[k],`${path}.${k}`);
 }
}
export function validateMission(value){
 check(schema,value,'snapshot');
 const bad=()=>{throw new Error('Invalid mission reference');};
 for(const row of [value.report,...value.chat.messages,value.proposal,value.localization.latest_report,value.localization.pose_evidence])if(row&&row.run_id!==value.run.id)bad();
 if(value.source.mode!==value.session.mode)bad();
 const readiness=value.readiness;
 if(readiness){
  const capabilities=['planning','flight','detection','emergency'];
  if(readiness.checks.length!==capabilities.length||readiness.checks.some((row,index)=>row.capability!==capabilities[index]))bad();
  if(readiness.checks.some(row=>row.ready!==(row.reason_code===null&&row.remediation===null)))bad();
  const ready=Object.fromEntries(readiness.checks.map(row=>[row.capability,row.ready]));
  const expected=!ready.planning?'NOT_READY':Object.values(ready).every(Boolean)?'READY_TO_DISPATCH':'READY_TO_PLAN';
  if(readiness.state==='ACTIVE'){if(!Object.values(ready).every(Boolean))bad();}
  else if(readiness.state!==expected)bad();
 }
 const ids={};
 for(const key of ['frames','sightings','objects']){ids[key]=new Set(value.evidence[key].map(r=>r.id));if(ids[key].size!==value.evidence[key].length)bad();}
 for(const row of value.evidence.sightings)if(!ids.frames.has(row.frame_id))bad();
 for(const row of [...value.evidence.objects,...value.results.counts])if(row.sighting_ids.some(id=>!ids.sightings.has(id)))bad();
 for(const row of value.evidence.frames)if(row.evidence==='AVAILABLE'&&(!row.artifact_id||!row.sha256||!row.width||!row.height))bad();
 const routeIds=new Set(value.mission.route.map(row=>row.stop_id));
 if(routeIds.size!==value.mission.route.length)bad();
 if(value.mission.active_stop_id&&!routeIds.has(value.mission.active_stop_id))bad();
 for(const row of value.evidence.frames)if(!routeIds.has(row.stop_id))bad();
 if(value.proposal){const proposalIds=new Set(value.proposal.stops.map(row=>row.id));if(proposalIds.size!==value.proposal.stops.length)bad();if([...proposalIds].some(id=>!routeIds.has(id)))bad();}
 return value;
}
