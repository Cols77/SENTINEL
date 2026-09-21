import test from 'node:test';
import assert from 'node:assert/strict';
import {validateSurvey} from '../../examples/survey-console/contract.mjs';
import {makeScenario,scenarioIds} from '../../examples/survey-console/fixtures.mjs';
const final = id => makeScenario(id).snapshots.at(-1);
test('eight timelines are validated, cloned and have complete event watermarks',()=>{
 assert.equal(scenarioIds.length,8);
 for(const id of scenarioIds){const scenario=makeScenario(id); for(const s of scenario.snapshots){assert.equal(validateSurvey(s),s);assert.equal(scenario.events.filter(e=>e.sequence<=s.event_sequence).length,s.event_sequence);} }
 const s=final('computers-followup'); s.counts[0].confirmed=99;
 assert.equal(final('computers-followup').counts[0].confirmed,5);
});
test('aborted partial sweep never consumes budget or contributes merged observations',()=>{
 const s=final('aborted');assert.equal(s.flights.used,0);assert.equal(s.sweep.merged,false);assert.ok(s.frames.every(f=>!f.merge_eligible));
 for(const mutate of [x=>x.sweep.merged=true,x=>x.flights.used=1,x=>x.frames[0].merge_eligible=true]){const bad=structuredClone(s);mutate(bad);assert.throws(()=>validateSurvey(bad),{code:'INVALID_PROJECTION'});}
});
const invalidCases={
 version:s=>s.schema_version=2,internalPath:s=>s.frames[0].storage_key='/private/x',missing:s=>delete s.request_text,
 budget:s=>s.flights.used=3,nan:s=>s.sweep.stops[0].range_m=NaN,duplicate:s=>s.frames.push(s.frames[0]),
 reference:s=>s.sightings[0].frame_id='missing',box:s=>s.sightings[0].bbox=[.7,0,.2,1],score:s=>s.sightings[0].score_semantics='',
 unknownClass:s=>s.counts[0].class_name='macbook',personObject:s=>s.objects[0].class_name='person',
 progress:s=>s.analysis.analysed=9,date:s=>s.updated_at='yesterday',timezone:s=>s.updated_at='2026-09-21T00:00:00',
 nonObservableZero:s=>s.counts[3].confirmed=0,personTimestamp:s=>{s.counts[1].semantics='SINGLE_VIEW_LOWER_BOUND';s.counts[1].lower_bound=3;},
};
for(const [name,mutate] of Object.entries(invalidCases))test(`reject invalid projection: ${name}`,()=>{const s=final('computers-followup');mutate(s);assert.throws(()=>validateSurvey(s),{code:'INVALID_PROJECTION'});});
test('fixtures keep class-specific counts and source semantics',()=>{
 const s=final('computers-followup');assert.deepEqual(s.objects.map(o=>o.state),['CONFIRMED','CONFIRMED','CONFIRMED','CONFIRMED','CONFIRMED','UNCERTAIN']);
 const p=final('people-lower-bound');assert.equal(p.counts[1].lower_bound,3);assert.equal(p.counts[1].captured_at,'2026-09-21T00:00:04Z');assert.equal(p.objects.length,0);
 const phone=makeScenario('phone-closer');assert.equal(phone.snapshots[0].counts[2].observability,'NOT_OBSERVABLE');assert.equal(phone.snapshots.at(-1).counts[2].uncertain,1);assert.equal(phone.snapshots.at(-1).flights.used,2);
 assert.deepEqual(final('provider-timeout').analysis,{analysed:1,total:3,dropped_labels:['background'],unknown_labels:['macbook']});
 assert.equal(final('report-rejected').report.text,null);
 assert.deepEqual(makeScenario('source-stale').snapshots.map(x=>[x.source,x.freshness]),[['LIVE','FRESH'],['REPLAY','FRESH'],['REPLAY','STALE']]);
});
