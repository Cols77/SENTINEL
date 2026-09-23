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

test('controller preserves readiness facts across refreshes',async()=>{
 let s=structuredClone(fixture);s.run.version=1;
 s.readiness={state:'NOT_READY',checks:['planning','flight','detection','emergency'].map(capability=>({capability,ready:false,reason_code:`LIVE_${capability.toUpperCase()}_REQUIRED`,remediation:`Supply --${capability}.`})),command_template:'sentinel mission-control --mode live'};
 s.session.mode='LIVE';s.source.mode='LIVE';s.source.kind='LIVE';
 const c=createController({read_survey:async()=>structuredClone(s),read_survey_events:async()=>[]},missionContract);
 await c.open('run');assert.equal(c.getState().survey.readiness.state,'NOT_READY');
 s.run.version=2;s.readiness.state='READY_TO_PLAN';s.readiness.checks[0]={capability:'planning',ready:true,reason_code:null,remediation:null};
 await c.refresh();assert.equal(c.getState().survey.readiness.state,'READY_TO_PLAN');
});
