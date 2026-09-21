import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';
import {createController} from '../../examples/survey-console/controller.mjs';
import {missionContract} from '../../examples/survey-console/view-model.mjs';
const fixture=JSON.parse(readFileSync(new URL('../../fixtures/mission-control/contract-v2.json',import.meta.url)));
test('v2 refresh uses display version and event watermark',async()=>{
 let s=structuredClone(fixture);s.run.version=1;
 const c=createController({read_survey:async()=>structuredClone(s),read_survey_events:async()=>[]},missionContract);
 await c.open('run');assert.equal(c.getState().survey.run.version,1);
 s.run.version=2;s.localization.age_ms=500;
 await c.refresh();assert.equal(c.getState().survey.localization.age_ms,500);
});
