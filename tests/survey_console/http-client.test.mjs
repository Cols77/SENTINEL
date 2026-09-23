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

test('site profile client reads catalogue and posts only the selected id',async()=>{
 const calls=[];
 const client=createHttpClient('/api/v1',async(url,init)=>{
  calls.push([url,init]);
  return new Response(JSON.stringify({status:'ACKNOWLEDGED',selected_profile_id:'CanningSt'}));
 });
 await client.read_site_profiles();
 await client.select_site_profile('CanningSt');
 assert.equal(calls[0][0],'/api/v1/site-profiles');
 assert.equal(calls[1][0],'/api/v1/site-profiles/select');
 assert.equal(calls[1][1].method,'POST');
 assert.deepEqual(JSON.parse(calls[1][1].body),{profile_id:'CanningSt'});
});

test('site profile client retains typed failure bodies',async()=>{
 for(const [status,type] of [[409,'CONFLICT'],[422,'INVALID'],[503,'UNAVAILABLE']]){
  const client=createHttpClient('/api/v1',async()=>new Response(JSON.stringify({status:type,reason_code:'MISSION_NOT_IDLE',selected_profile_id:'CanningSt'}),{status}));
  const result=await client.select_site_profile('Harbor');
  assert.equal(result.status,type);
  assert.equal(result.reason_code,'MISSION_NOT_IDLE');
 }
});

test('typed failures require the exact HTTP status and body pair',async()=>{
 for(const [status,type] of [[409,'INVALID'],[409,'UNAVAILABLE'],[422,'CONFLICT'],[422,'UNAVAILABLE'],[503,'CONFLICT'],[503,'INVALID']]){
  const client=createHttpClient('/api/v1',async()=>new Response(JSON.stringify({status:type,message:'Mismatched response'}),{status}));
  await assert.rejects(client.select_site_profile('Harbor'),/Mismatched response/);
 }
});

test('poller schedules only after the outstanding request finishes',async()=>{
 const {startPolling}=await import('../../examples/survey-console/http-client.mjs');
 let resolve;const pending=new Promise(r=>resolve=r);const scheduled=[];
 const stop=startPolling(()=>pending,{hidden:()=>false,schedule:(fn,delay)=>{scheduled.push(delay);return 1;},cancel:()=>{}});
 assert.deepEqual(scheduled,[]);resolve();await pending;await Promise.resolve();
 assert.deepEqual(scheduled,[500]);stop();
});
