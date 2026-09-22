import test from 'node:test';import assert from 'node:assert/strict';
import {createChatSubmissionController,terminalChatResponse} from '../../examples/survey-console/chat.mjs';
test('terminal and recovery statuses produce explicit non-dispatch responses',()=>{
 assert.equal(terminalChatResponse('COMPLETE'),'COMPLETE - run is complete; no new dispatch is available.');
 assert.equal(terminalChatResponse('ABORTED'),'ABORTED - run was aborted; no new dispatch is available.');
 assert.equal(terminalChatResponse('RECOVERY_REQUIRED'),'RECOVERY_REQUIRED - reconcile the aircraft before dispatch.');
});

test('shared chat submission preserves one attempt identity across uncertain retry',async()=>{
 const snapshot={session:{id:'session-1'},run:{id:'run-1',status:'ACTIVE',decision_version:4}};
 const attempts=[];let count=0;let input='voice transcript';
 const controller=createChatSubmissionController({getSnapshot:()=>snapshot,setInput:text=>{input=text;},client:{submit_chat:async(id,attempt)=>{attempts.push([id,attempt]);if(++count===1)throw Error('lost');return {status:'ACKNOWLEDGED',message:'accepted'};}},identity:()=>({command_id:'cmd-1',correlation_id:'cmd-1',idempotency_key:'cmd-1'})});
 await controller.submit(input);assert.equal(input,'voice transcript');
 await controller.submit(input);assert.equal(input,'');
 assert.equal(attempts.length,2);assert.equal(attempts[0][1].command_id,attempts[1][1].command_id);assert.equal(attempts[0][0],'run-1');
});

test('chat emits pending false after acknowledgement so sequential submissions are allowed',async()=>{
 const snapshot={session:{id:'session-1'},run:{id:'run-1',status:'ACTIVE',decision_version:4}};
 const pending=[];const sent=[];let input='first';
 const controller=createChatSubmissionController({getSnapshot:()=>snapshot,onPending:value=>pending.push(value),setInput:value=>{input=value;},client:{submit_chat:async(id,attempt)=>{sent.push(attempt.text);return {status:'ACKNOWLEDGED',message:'accepted'};}}});
 await controller.submit(input);input='second';await controller.submit(input);
 assert.deepEqual(sent,['first','second']);
 assert.deepEqual(pending,[true,false,true,false]);
 assert.equal(controller.isPending(),false);
});

test('chat emits pending false after failure so retry is enabled',async()=>{
 const snapshot={session:{id:'session-1'},run:{id:'run-1',status:'ACTIVE',decision_version:4}};
 const pending=[];let calls=0;
 const controller=createChatSubmissionController({getSnapshot:()=>snapshot,onPending:value=>pending.push(value),client:{submit_chat:async()=>{if(++calls===1)throw Error('lost');return {status:'ACKNOWLEDGED',message:'accepted'};}}});
 await controller.submit('retry me');
 assert.equal(controller.isPending(),false);
 await controller.submit('retry me');
 assert.deepEqual(pending,[true,false,true,false]);
});
