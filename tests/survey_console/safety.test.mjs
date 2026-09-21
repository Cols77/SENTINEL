import test from 'node:test';import assert from 'node:assert/strict';
import {createHoldAction} from '../../examples/survey-console/safety.mjs';
test('early release cancels emergency and repeated begin does not duplicate it',()=>{
 let fire,delay,sent=0,cancelled=0;
 const hold=createHoldAction(()=>sent++,{schedule:(fn,ms)=>{fire=fn;delay=ms;return 1;},cancel:()=>cancelled++});
 hold.begin();hold.begin();assert.equal(delay,1200);hold.cancel();assert.equal(sent,0);assert.equal(cancelled,1);
 hold.begin();fire();assert.equal(sent,1);hold.cancel();assert.equal(sent,1);
});
