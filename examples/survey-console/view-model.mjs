import {validateMission} from './mission-contract.mjs';
export const missionContract={
 validate:validateMission,
 run:s=>s?.run.id,
 version:s=>s?.run.version,
 watermark:s=>s?.events.watermark??0,
 validateEvent:e=>{
  if(!e||!Number.isSafeInteger(e.sequence)||e.sequence<1||typeof e.id!=='string'||typeof e.run_id!=='string'||typeof e.summary!=='string'||typeof e.occurred_at!=='string'||typeof e.type!=='string'||!Array.isArray(e.refs))throw Error('Invalid mission event');
 }
};
export function toSurveyView(s){
 return {run_id:s.run.id,version:s.run.decision_version,event_sequence:s.events.watermark,updated_at:s.run.updated_at,
 source:s.source.kind==='LIVE'?'LIVE':'REPLAY',freshness:s.source.freshness,status:s.run.status==='ACTIVE'?'ACTIVE':s.run.status==='COMPLETE'?'COMPLETE':'ABORTED',step:s.run.phase,request_text:s.run.request_text,
 proposal:s.proposal,report:s.report,flights:s.mission.flights,sweep:s.mission.sweep,
 frames:s.evidence.frames,sightings:s.evidence.sightings,objects:s.evidence.objects,counts:s.results.counts,
 analysis:s.evidence.analysis,provider:s.evidence.provider,failures:s.evidence.failures,mode:s.session.mode};
}
