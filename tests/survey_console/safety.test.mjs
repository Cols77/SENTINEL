import test from 'node:test';import assert from 'node:assert/strict';
import {createControlledLandController,createHoldAction} from '../../examples/survey-console/safety.mjs';
test('early release cancels emergency and repeated begin does not duplicate it',()=>{
 let fire,delay,sent=0,cancelled=0;
 const hold=createHoldAction(()=>sent++,{schedule:(fn,ms)=>{fire=fn;delay=ms;return 1;},cancel:()=>cancelled++});
 hold.begin();hold.begin();assert.equal(delay,1200);hold.cancel();assert.equal(sent,0);assert.equal(cancelled,1);
 hold.begin();fire();assert.equal(sent,1);hold.cancel();assert.equal(sent,1);
});

test('controlled land rejects illegal snapshots and reuses one identity for duplicate or uncertain requests',async()=>{
 let snapshot={run:{id:'run-1'},safety:{land:{available:false}},legal_actions:[{action:'LAND',enabled:false}]};
 let calls=[];let next=0;
 const controller=createControlledLandController({getSnapshot:()=>snapshot,requestLand:async(id,attempt)=>{calls.push([id,attempt]);return {status:'IN_DOUBT'};},identity:()=>({command_id:`c-${++next}`,correlation_id:'corr',idempotency_key:'key'})});
 await controller.request();assert.equal(calls.length,0);
 snapshot.safety.land.available=true;snapshot.legal_actions[0].enabled=true;
 await controller.request();await controller.request();
 assert.equal(calls.length,2);assert.equal(calls[0][1].command_id,'c-1');assert.equal(calls[1][1].command_id,'c-1');
});
