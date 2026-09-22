import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateMission} from '../../examples/survey-console/mission-contract.mjs';
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
