import test from 'node:test';
import assert from 'node:assert/strict';
import {startMissionLifecycle} from '../../examples/survey-console/mission-lifecycle.mjs';

test('never-settling catalogue load cannot block polling or cleanup registration',async()=>{
 const events=[];let cleanup;
 const stopped=startMissionLifecycle({
  startPolling:()=>{events.push('poll');return ()=>events.push('stop');},
  registerCleanup:fn=>{events.push('register');cleanup=fn;},
  dispose:()=>events.push('dispose'),
  loadProfiles:()=>{events.push('load');return new Promise(()=>{});},
 });
 assert.equal(typeof stopped,'function');
 assert.deepEqual(events.slice(0,2),['poll','register']);
 await Promise.resolve();
 assert.deepEqual(events,['poll','register','load']);
 cleanup();
 assert.deepEqual(events,['poll','register','load','stop','dispose']);
});

test('catalogue rejection is observed without blocking startup',async()=>{
 const errors=[];
 startMissionLifecycle({startPolling:()=>()=>{},registerCleanup:()=>{},dispose:()=>{},loadProfiles:async()=>{throw Error('offline');},onLoadError:error=>errors.push(error.message)});
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(errors,['offline']);
});
