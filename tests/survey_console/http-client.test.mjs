import test from 'node:test';
import assert from 'node:assert/strict';
import {createHttpClient} from '../../examples/survey-console/http-client.mjs';
test('one-argument decision preserves run, body and typed conflict',async()=>{
 const attempt={run_id:'r',attempt_id:'a'};
 const client=createHttpClient('/api/v1',async(url,init)=>{
  assert.equal(url,'/api/v1/runs/r/decisions');assert.deepEqual(JSON.parse(init.body),attempt);
  return new Response(JSON.stringify({attempt_id:'a',status:'CONFLICT',message:'Changed'}),{status:409});
 });
 assert.equal((await client.submit_decision(attempt)).status,'CONFLICT');
});
test('uncertain decisions retain their result envelope',async()=>{
 const client=createHttpClient('/api',async()=>new Response(JSON.stringify({attempt_id:'a',status:'IN_DOUBT',message:'Pending'}),{status:202}));
 assert.equal((await client.submit_decision({run_id:'r',attempt_id:'a'})).status,'IN_DOUBT');
});
test('binary evidence needs a run-scoped snapshot binding',async()=>{
 const client=createHttpClient('/api',async()=>{throw Error('must not fetch');});
 assert.equal((await client.read_evidence('r','unknown')).status,'NOT_FOUND');
});

test('poller schedules only after the outstanding request finishes',async()=>{
 const {startPolling}=await import('../../examples/survey-console/http-client.mjs');
 let resolve;const pending=new Promise(r=>resolve=r);const scheduled=[];
 const stop=startPolling(()=>pending,{hidden:()=>false,schedule:(fn,delay)=>{scheduled.push(delay);return 1;},cancel:()=>{}});
 assert.deepEqual(scheduled,[]);resolve();await pending;await Promise.resolve();
 assert.deepEqual(scheduled,[500]);stop();
});
