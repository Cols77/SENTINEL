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
