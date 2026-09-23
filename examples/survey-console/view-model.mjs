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
 source:s.source.kind==='LIVE'?'LIVE':'REPLAY',freshness:s.source.freshness,status:s.run.status,step:s.run.phase,request_text:s.run.request_text,
 decision:s.decision,proposal:s.proposal,proposer:s.decision??s.proposal?.proposer??null,escalation_reason:s.decision?.escalation_reason??s.proposal?.escalation_reason??null,report:s.report,flights:s.mission.flights,sweep:s.mission.sweep,
 frames:s.evidence.frames,sightings:s.evidence.sightings,objects:s.evidence.objects,counts:s.results.counts,
 analysis:s.evidence.analysis,provider:s.evidence.provider,failures:s.evidence.failures,mode:s.session.mode,
 readiness:s.readiness??{state:s.session.mode==='LIVE'?'NOT_READY':'ACTIVE',checks:[],command_template:''},
 localization:s.localization,route:s.mission.route,return_to_start:s.mission.return_to_start,return_error_cm:s.mission.return_error_cm};
}

export function missionStatusText(view){
 const flights=view?.flights??{};
 const sweep=view?.sweep??{};
 const stops=Array.isArray(sweep.stops)?sweep.stops:[];
 const completed=stops.filter(stop=>stop.status==='COMPLETED').length;
 const localization=view?.localization??{};
 const age=Number.isFinite(localization.age_ms)?`${Math.round(localization.age_ms)} ms old`:'no observation age';
 return `Flights ${flights.used??0}/${flights.max??0} · Sweep ${sweep.status??'PLANNED'} · Stops ${completed}/${stops.length} · Localization ${localization.status??'UNAVAILABLE'} · ${age}`;
}

export function liveReadinessModel(view){
 const visible=view?.mode==='LIVE';
 if(!visible)return {visible:false,armed:false,label:'',failed:[]};
 const readiness=view?.readiness??{state:'NOT_READY',checks:[],command_template:''};
 const armed=readiness.state==='ACTIVE';
 return {visible:true,armed,label:`LIVE · ${armed?'ACTIVE':'UNARMED'} · ${readiness.state}`,
  failed:readiness.checks.filter(check=>!check.ready)};
}
