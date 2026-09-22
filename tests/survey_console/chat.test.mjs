import test from 'node:test';import assert from 'node:assert/strict';
import {terminalChatResponse} from '../../examples/survey-console/chat.mjs';
test('terminal and recovery statuses produce explicit non-dispatch responses',()=>{
 assert.equal(terminalChatResponse('COMPLETE'),'COMPLETE - run is complete; no new dispatch is available.');
 assert.equal(terminalChatResponse('ABORTED'),'ABORTED - run was aborted; no new dispatch is available.');
 assert.equal(terminalChatResponse('RECOVERY_REQUIRED'),'RECOVERY_REQUIRED - reconcile the aircraft before dispatch.');
});
