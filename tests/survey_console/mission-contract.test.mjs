import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateMission} from '../../examples/survey-console/mission-contract.mjs';
import {liveReadinessModel,toSurveyView} from '../../examples/survey-console/view-model.mjs';
const fixture=JSON.parse(readFileSync(new URL('../../fixtures/mission-control/contract-v2.json',import.meta.url)));
test('strict public contract rejects unknown and cross-run fields',()=>{
 assert.equal(validateMission(fixture),fixture);
 for(const mutate of [s=>s.evidence.image_path='/private',s=>s.report.run_id='other',s=>s.localization.age_ms=-1]){
  const s=structuredClone(fixture);mutate(s);assert.throws(()=>validateMission(s));
 }
});

test('strict public contract rejects a frame when the route has no referenced stop',()=>{
 const s=structuredClone(fixture);
 s.evidence.frames.push({id:'f-missing',stop_id:'missing-stop',captured_at:null,commanded_pose:null,measured_pose:null,analysis:'FAILED',detection_status:'FAILED',detection_latency_ms:null,detection_error:'gap',detection_count:null,evidence:'MISSING',gap_reason:'gap',failure:'gap',merge_eligible:false,artifact_id:null,sha256:null,width:null,height:null,source_kind:'SIMULATED'});
 assert.throws(()=>validateMission(s));
});

const capabilities=['planning','flight','detection','emergency'];
function readiness(state='NOT_READY'){
 const readyCapabilities=state==='NOT_READY'?new Set():state==='READY_TO_PLAN'?new Set(['planning']):new Set(capabilities);
 return {state,checks:capabilities.map(capability=>({capability,ready:readyCapabilities.has(capability),
  reason_code:readyCapabilities.has(capability)?null:`LIVE_${capability.toUpperCase()}_REQUIRED`,
  remediation:readyCapabilities.has(capability)?null:`Supply --${capability}.`})),
  command_template:'sentinel mission-control --mode live --site <SITE_JSON>'};
}

test('schema v3 readiness is optional, closed and ordered',()=>{
 for(const state of ['NOT_READY','READY_TO_PLAN','READY_TO_DISPATCH','ACTIVE']){
  const s=structuredClone(fixture);s.readiness=readiness(state);
  assert.equal(validateMission(s),s);
 }
 assert.equal(validateMission(structuredClone(fixture)).schema_version,3);
 for(const mutate of [
  r=>r.checks.reverse(),
  r=>r.checks[1]={...r.checks[0]},
  r=>Object.assign(r.checks[0],{ready:true,reason_code:'STILL_BLOCKED',remediation:null}),
  r=>r.command_template=7,
  r=>r.secret='private',
 ]){
  const s=structuredClone(fixture);s.readiness=readiness();mutate(s.readiness);
  assert.throws(()=>validateMission(s));
 }
});

test('older schema v3 snapshots fail closed only for Live mode',()=>{
 const rehearsal=toSurveyView(validateMission(structuredClone(fixture)));
 assert.deepEqual(rehearsal.readiness,{state:'ACTIVE',checks:[],command_template:''});
 assert.equal(liveReadinessModel(rehearsal).visible,false);

 const legacyLive=structuredClone(fixture);
 legacyLive.session.mode='LIVE';
 legacyLive.source.mode='LIVE';
 legacyLive.source.kind='LIVE';
 const live=toSurveyView(validateMission(legacyLive));
 assert.deepEqual(live.readiness,{state:'NOT_READY',checks:[],command_template:''});
 assert.deepEqual(liveReadinessModel(live),{
  visible:true,armed:false,label:'LIVE · UNARMED · NOT_READY',
  failed:[],command_template:'',
 });
});
