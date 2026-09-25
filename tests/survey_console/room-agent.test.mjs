import test from 'node:test';import assert from 'node:assert/strict';
import {ROOM_AGENT,SURVEY,architectureOptions,selectArchitecture,modeOptions,scenarioOptions,proposalCommand,roomViewModel,safetyIdentity,answerText,returnedHomeText,landingText,reviewOptions,flightControlMessages,statusTone,batteryLevel} from '../../examples/survey-console/room-agent-model.mjs';

const catalogue={
 architectures:[{id:'survey',label:'Survey (rules proposer)',description:'s'},{id:'room_agent',label:'Room agent (Jev adaptive)',description:'r'}],
 modes:[{id:'REHEARSAL',label:'Rehearsal',available:true,reason:null},{id:'JEV_REHEARSAL',label:'Jev rehearsal',available:true,reason:null},{id:'LIVE',label:'Live',available:false,reason:'Start with --room-live-config'}],
 scenarios:[{id:'a',label:'(a) three regions',description:'x'},{id:'b',label:'(b) tag loss',description:'y'}],
 default_request:'count the phones and computers in the room',
};

test('the selector offers the room agent next to the survey and falls back to the survey',()=>{
 const options=architectureOptions(catalogue);
 assert.deepEqual(options.map(o=>o.value),[SURVEY,ROOM_AGENT]);
 assert.equal(options[1].label,'Room agent (Jev adaptive)');
 assert.equal(selectArchitecture(ROOM_AGENT,options),ROOM_AGENT);
 assert.equal(selectArchitecture('bogus',options),SURVEY);
 assert.deepEqual(architectureOptions(null).map(o=>o.value),[SURVEY,ROOM_AGENT]);
});

test('run modes and scenarios come from the catalogue; an unavailable mode is disabled with its reason',()=>{
 const modes=modeOptions(catalogue);
 assert.deepEqual(modes.map(m=>[m.value,m.disabled]),[['REHEARSAL',false],['JEV_REHEARSAL',false],['LIVE',true]]);
 assert.equal(modes[2].title,'Start with --room-live-config');
 assert.deepEqual(scenarioOptions(catalogue).map(s=>s.value),['a','b']);
});

test('a proposal needs request text; the local IP is sent only for Live and must be IPv4',()=>{
 assert.match(proposalCommand({mode:'REHEARSAL',scenario:'a',requestText:'  '}).error,/request/);
 assert.deepEqual(proposalCommand({mode:'REHEARSAL',scenario:'b',requestText:' count the phones ',localIp:'192.168.10.2'}).command,
  {mode:'REHEARSAL',scenario:'b',request_text:'count the phones'});
 assert.deepEqual(proposalCommand({mode:'LIVE',scenario:'a',requestText:'count',localIp:'192.168.10.2'}).command,
   {mode:'LIVE',scenario:'a',request_text:'count',perception_review:'STANDARD',local_ip:'192.168.10.2'});
 assert.match(proposalCommand({mode:'LIVE',scenario:'a',requestText:'count',localIp:'192.168.10.300'}).error,/IPv4/);
});

test('recorded review is a separate Live-only choice in the proposal',()=>{
 const choices=reviewOptions({perception_reviews:[{id:'STANDARD',label:'Standard'},{id:'RECORDED_REVIEW',label:'Recorded review'}]});
 assert.deepEqual(choices.map(row=>row.value),['STANDARD','RECORDED_REVIEW']);
 assert.equal(proposalCommand({mode:'LIVE',scenario:'a',requestText:'count',perceptionReview:'RECORDED_REVIEW'}).command.perception_review,'RECORDED_REVIEW');
 assert.equal(proposalCommand({mode:'REHEARSAL',scenario:'a',requestText:'count',perceptionReview:'RECORDED_REVIEW'}).command.perception_review,undefined);
});

test('disabled Live flight controls explain the missing pack and the next step',()=>{
 const missing=flightControlMessages({reason_code:'ROOM_LIVE_CONFIG_REQUIRED'},true);
 assert.match(missing.prepare,/--room-live-config/);
 assert.match(missing.propose,/Live pack/);
 const unstaged=flightControlMessages({can_prepare:true},true);
 assert.equal(unstaged.prepare,'');
 assert.match(unstaged.propose,/Prepare next flight/);
 const staged=flightControlMessages({can_prepare:true,pack_run_id:'flight-1',pack_expired:false,pack_used:false},true);
 assert.equal(staged.propose,'');
});

const run={
 id:'room-1',mode:'REHEARSAL',mode_label:'Rehearsal',status:'COMPLETED',phase:'LANDED',error:null,
 pose:{x_cm:250,y_cm:190,z_cm:100,heading_deg:90},battery_percent:88,
 localization:{state:'ANCHORED',fix_age_s:0.42},goal:'COUNT phone, computer in room then reported',
 regions:[{pose_id:'left',label:'Left region',status:'OBSERVED',detections:2}],captures:3,
 detections:[{class_name:'phone',confidence:'low',x_cm:286,y_cm:278,region:'Right region'}],
 reanchor_log:[{kind:'LOCALIZATION',text:'Localization ANCHORED -> COASTING'},{kind:'YAW_ROTATION',text:'Yaw rotation ccw 15 deg -> heading 90 deg'},{kind:'TAG_BURST',text:'Recovery yaw burst at heading 90 deg: tags 0, 2'},{kind:'REANCHOR_ACCEPTED',text:'Re-anchor accepted at (230, 190) cm, heading 90 deg (mapped_joint_tags)'}],
 decisions:[{turn:1,label:'Observe left region',reason:'resolves 2 cells',confidence:0.99,result:'OBSERVE_SUCCEEDED'}],
 answer:{summary:'phones: 1 confirmed, 1 uncertain; computers: 2 confirmed',text:'Objective g1:c1 (room-wide count):\ncomputer: 2 confirmed'},
 returning:true,returned_home:true,landing:{state:'LANDED',reason:'REQUESTED'},land_status:'IDLE',
 yaw_rotations:1,reanchors_accepted:1,proposal:null,
};

test('the new fields are projected for the panels',()=>{
 const view=roomViewModel({catalogue,run});
 assert.equal(view.localization,'ANCHORED');
 assert.equal(view.fixAge,'fix age 0.4 s');
 assert.equal(view.pose,'x 250 cm · y 190 cm · z 100 cm · heading 90 deg');
 assert.equal(view.battery,'battery 88%');
 assert.equal(view.answer,'phones: 1 confirmed, 1 uncertain; computers: 2 confirmed');
 assert.equal(view.returnedHome,'Returned home');
 assert.equal(view.landing,'Landing: LANDED (REQUESTED)');
 assert.deepEqual(view.reanchor.map(r=>r.kind),['LOCALIZATION','YAW_ROTATION','TAG_BURST','REANCHOR_ACCEPTED']);
 assert.equal(view.decisions[0],'1. Observe left region — resolves 2 cells (p 0.99) → OBSERVE_SUCCEEDED');
 assert.equal(view.regions[0],'Left region: OBSERVED · 2 detections');
 assert.equal(view.detections[0],'phone (low) at (286, 278) cm · Right region');
 assert.equal(view.yawRotations,1);assert.equal(view.reanchorsAccepted,1);
 assert.equal(view.running,false);assert.equal(view.canPropose,true);assert.equal(view.proposal,null);
 assert.equal(view.batteryPercent,88);assert.equal(view.batteryLevel,'ok');
});

test('battery level follows a green/yellow/red code: >50 ok, 21-50 warn, <=20 critical, unknown with no reading',()=>{
 assert.equal(batteryLevel(null),'unknown');
 assert.equal(batteryLevel(undefined),'unknown');
 assert.equal(batteryLevel(20),'critical');
 assert.equal(batteryLevel(21),'warn');
 assert.equal(batteryLevel(50),'warn');
 assert.equal(batteryLevel(51),'ok');
 assert.equal(roomViewModel({run:null}).batteryLevel,'unknown');
});

test('a pending proposal and a running run gate the controls',()=>{
 const pending=roomViewModel({run:{...run,status:'AWAITING_APPROVAL',proposal:{id:'p1',request_text:'count',lines:[],flies:false}}});
 assert.equal(pending.proposal.id,'p1');
 const running=roomViewModel({run:{...run,status:'RUNNING',answer:null,returned_home:false,returning:false,landing:null}});
 assert.equal(running.running,true);assert.equal(running.canPropose,false);
 assert.equal(running.answer,'Awaiting answer…');
 assert.equal(running.returnedHome,'Not yet returning');
 assert.equal(running.landing,'Not landed');
 assert.equal(roomViewModel({run:null}).status,'No room-agent run yet.');
 assert.equal(roomViewModel({run:null}).localization,'UNAVAILABLE');
});

test('a search answer headlines its first finding, not the objective header',()=>{
 const text='Objective g1:c1 (search for phone):\nphone: seen from the center-rear-middle region; high confidence. [observation=x; event=y]';
 assert.equal(answerText({status:'COMPLETED',answer:{text,summary:null}}),'phone: seen from the center-rear-middle region; high confidence.');
});

test('terminal texts without an answer or a return are explicit',()=>{
 assert.equal(answerText({status:'ABORTED',answer:null}),'No answer recorded.');
 assert.equal(returnedHomeText({status:'ABORTED',returned_home:false,returning:false}),'Not returned home');
 assert.equal(landingText({landing:null,land_status:'REQUESTED'}),'Landing requested…');
});

test('a refused turn shows the real choice, its probability and the refusal code',()=>{
 const decisions=[
  {turn:1,label:'Observe center region',reason:'resolves 2 cells',confidence:0.36,probability:0.44,refused:'LOW_CONFIDENCE',kind_mass:null,result:'LOW_CONFIDENCE'},
  {turn:2,label:'Observe right region',reason:'resolves 1 cell',confidence:0.85,probability:0.44,refused:null,kind_mass:0.85,result:'OBSERVE_SUCCEEDED'},
  {turn:3,label:'No usable choice',reason:'provider error: TimeoutError',confidence:null,probability:null,refused:'PROVIDER_ERROR',kind_mass:null},
 ];
 const view=roomViewModel({run:{...run,decisions}});
 assert.equal(view.decisions[0],'1. Observe center region (refused: LOW_CONFIDENCE, p 0.44)');
 assert.equal(view.decisions[1],'2. Observe right region — resolves 1 cell (p 0.44, observe 0.85) → OBSERVE_SUCCEEDED');
 assert.equal(view.decisions[2],'3. No usable choice (refused: PROVIDER_ERROR)');
});

test('safety identities are one id used three ways',()=>{
 assert.deepEqual(safetyIdentity(()=>'x'),{command_id:'x',correlation_id:'x',idempotency_key:'x'});
});

test('the status badge colours the run by what the operator should notice',()=>{
 assert.equal(statusTone(null),'idle');
 assert.equal(statusTone({status:'RUNNING'}),'live');
 assert.equal(statusTone({status:'AWAITING_APPROVAL'}),'pending');
 assert.equal(statusTone({status:'COMPLETED'}),'success');
 assert.equal(statusTone({status:'INCOMPLETE_RETURNED'}),'partial');
 assert.equal(statusTone({status:'ABORTED'}),'alert');
 assert.equal(statusTone({status:'SOMETHING_NEW'}),'partial');
 assert.equal(roomViewModel({run:{status:'RUNNING',mode:'LIVE'}}).statusTone,'live');
});
