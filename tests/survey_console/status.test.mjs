import assert from 'node:assert/strict';
import test from 'node:test';
import {liveReadinessModel,missionStatusText} from '../../examples/survey-console/view-model.mjs';

test('persistent status text exposes budget, sweep progress and localization age',()=>{
  const text=missionStatusText({
    flights:{used:1,max:2},
    sweep:{status:'ACTIVE',stops:[{status:'COMPLETED'},{status:'ACTIVE'}]},
    localization:{status:'AGING',age_ms:500}
  });
  assert.match(text,/Flights 1\/2/);
  assert.match(text,/Sweep ACTIVE/);
  assert.match(text,/Stops 1\/2/);
  assert.match(text,/Localization AGING/);
  assert.match(text,/500 ms old/);
});

const capabilities=['planning','flight','detection','emergency'];
function snapshot(state,readyCapabilities){
 return {mode:'LIVE',readiness:{state,command_template:'sentinel mission-control --mode live --site <SITE_JSON>',checks:capabilities.map(capability=>({
  capability,ready:readyCapabilities.includes(capability),
  reason_code:readyCapabilities.includes(capability)?null:`LIVE_${capability.toUpperCase()}_REQUIRED`,
  remediation:readyCapabilities.includes(capability)?null:`Supply --${capability}.`,
 }))}};
}

test('live readiness model identifies unarmed state and lists only failed checks',()=>{
 const notReady=liveReadinessModel(snapshot('NOT_READY',[]));
 assert.equal(notReady.visible,true);
 assert.equal(notReady.armed,false);
 assert.equal(notReady.label,'LIVE · UNARMED · NOT_READY');
 assert.deepEqual(notReady.failed.map(check=>check.capability),capabilities);
 assert.equal(Object.hasOwn(notReady,'command_template'),false);

 const partial=liveReadinessModel(snapshot('READY_TO_PLAN',['planning']));
 assert.deepEqual(partial.failed.map(check=>check.capability),['flight','detection','emergency']);

 const configured=liveReadinessModel(snapshot('READY_TO_DISPATCH',capabilities));
 assert.deepEqual(configured.failed,[]);
 assert.equal(configured.armed,false);
});

test('rehearsal suppresses Live remediation',()=>{
 const model=liveReadinessModel({mode:'REHEARSAL',readiness:snapshot('NOT_READY',[]).readiness});
 assert.equal(model.visible,false);
 assert.deepEqual(model.failed,[]);
 assert.equal(Object.hasOwn(model,'command_template'),false);
});
