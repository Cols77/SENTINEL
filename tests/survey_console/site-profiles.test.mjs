import test from 'node:test';
import assert from 'node:assert/strict';
import {createSiteProfileController,profileActivityKey,createProfileActivityWatcher} from '../../examples/survey-console/site-profiles.mjs';

const catalogue=(selected='CanningSt',allowed=true)=>({version:1,selected_profile_id:selected,switch_allowed:allowed,switch_reason:allowed?null:'MISSION_NOT_IDLE',profiles:[
 {id:'CanningSt',label:'Canning Street',enabled:true,setup_status:'Ready'},
 {id:'Harbor',label:'Harbor',enabled:true,setup_status:'Ready'},
 {id:'Draft',label:'Draft site',enabled:false,setup_status:'Calibration incomplete'},
]});

test('catalogue exposes selected and disabled draft options',async()=>{
 const control=createSiteProfileController({read_site_profiles:async()=>catalogue()});
 await control.load();
 assert.equal(control.getState().selected_profile_id,'CanningSt');
 assert.equal(control.getState().profiles[2].enabled,false);
});

test('initial catalogue failure can be retried without a mission change or duplicate requests',async()=>{
 let calls=0,finish;
 const control=createSiteProfileController({read_site_profiles:()=>{
  calls++;
  if(calls===1)throw Error('private transport detail');
  return new Promise(resolve=>finish=resolve);
 }});
 await control.load();
 assert.equal(control.getState().catalogue_unavailable,true);
 assert.equal(control.getState().feedback,'Site catalogue unavailable.');
 const first=control.retry();const duplicate=control.retry();
 assert.equal(calls,2);
 finish(catalogue());await Promise.all([first,duplicate]);
 assert.equal(calls,2);
 assert.equal(control.getState().catalogue_unavailable,false);
 assert.equal(control.getState().selected_profile_id,'CanningSt');
});

test('selection suppresses duplicates and refreshes snapshot on success',async()=>{
 let finish,calls=0,refreshes=0;
 const control=createSiteProfileController({read_site_profiles:async()=>catalogue(),select_site_profile:()=>{calls++;return new Promise(resolve=>finish=resolve);}},async()=>{refreshes++;});
 await control.load();
 const pending=control.select('Harbor');
 assert.equal(control.getState().pending,true);
 await control.select('Harbor');
 assert.equal(calls,1);
 finish({...catalogue('Harbor'),status:'ACKNOWLEDGED',reason_code:null});
 await pending;
 assert.equal(control.getState().selected_profile_id,'Harbor');
 assert.equal(refreshes,1);
});

test('typed failure retains previous selection and allowlisted feedback',async()=>{
 const control=createSiteProfileController({read_site_profiles:async()=>catalogue(),select_site_profile:async()=>({...catalogue('CanningSt',false),status:'CONFLICT',reason_code:'MISSION_NOT_IDLE'})});
 await control.load();await control.select('Harbor');
 assert.equal(control.getState().selected_profile_id,'CanningSt');
 assert.equal(control.getState().switch_allowed,false);
 assert.match(control.getState().feedback,/mission/i);
});

test('stale catalogue response cannot undo a successful selection',async()=>{
 let finish,reads=0;
 const control=createSiteProfileController({read_site_profiles:()=>++reads===1?new Promise(resolve=>finish=resolve):Promise.resolve(catalogue('Harbor')),select_site_profile:async()=>({...catalogue('Harbor'),status:'ACKNOWLEDGED',reason_code:null})});
 const old=control.load();
 // A catalogue can be pending when a server response to a selection arrives.
 control.update(catalogue());
 const selection=control.select('Harbor');
 finish(catalogue());await Promise.all([old,selection]);
 assert.equal(control.getState().selected_profile_id,'Harbor');
 assert.equal(reads,2);
});

test('acknowledged selection remains selected when snapshot refresh fails',async()=>{
 const control=createSiteProfileController({read_site_profiles:async()=>catalogue(),select_site_profile:async()=>({...catalogue('Harbor'),status:'ACKNOWLEDGED',reason_code:null})},async()=>{throw Error('offline');});
 await control.load();await control.select('Harbor');
 assert.equal(control.getState().selected_profile_id,'Harbor');
 assert.match(control.getState().feedback,/refresh/i);
});

test('phase change queues one fresh catalogue read behind an in-flight read',async()=>{
 let finish,calls=0;
 const control=createSiteProfileController({read_site_profiles:()=>{calls++;return calls===1?new Promise(resolve=>finish=resolve):Promise.resolve(catalogue('CanningSt',false));}});
 const first=control.load();const second=control.load();const third=control.load();
 finish(catalogue());await Promise.all([first,second,third]);
 assert.equal(calls,2);
 assert.equal(control.getState().switch_allowed,false);
});

test('activity key detects chat and event changes without refreshing unchanged polls',async()=>{
 const calls=[];
 const watch=createProfileActivityWatcher(()=>{calls.push('load');});
 const snapshot={session:{mode:'LIVE'},run:{id:'r',status:'ACTIVE',phase:'REQUEST',version:1},events:{watermark:0},proposal:null};
 assert.equal(typeof profileActivityKey(snapshot),'string');
 watch(snapshot);watch(structuredClone(snapshot));
 const readinessOnly=structuredClone(snapshot);readinessOnly.run.version=2;
 watch(readinessOnly);
 assert.equal(calls.length,1);
 const message=structuredClone(readinessOnly);message.chat={messages:[{id:'chat-1'}]};
 watch(message);watch(structuredClone(message));
 assert.equal(calls.length,2);
 const chat=structuredClone(message);chat.events.watermark=1;
 watch(chat);watch(structuredClone(chat));
 assert.equal(calls.length,3);
 watch({...chat,session:{mode:'REHEARSAL'}});
 assert.equal(calls.length,3);
});

test('catalogue response during pending selection triggers one post-settlement reload',async()=>{
 let finishSelect,finishRead,readCalls=0;
 const control=createSiteProfileController({
  read_site_profiles:()=>{readCalls++;return readCalls===1?Promise.resolve(catalogue()):readCalls===2?new Promise(resolve=>finishRead=resolve):Promise.resolve(catalogue('Harbor',false));},
  select_site_profile:()=>new Promise(resolve=>finishSelect=resolve),
 });
 const watch=createProfileActivityWatcher(()=>control.load());
 const initial={session:{mode:'LIVE'},run:{id:'r',status:'ACTIVE',phase:'REQUEST',version:1},events:{watermark:0},proposal:null};
 await watch(initial);
 const selection=control.select('Harbor');
 const activity={...initial,run:{...initial.run,version:2},events:{watermark:1}};
 const activityRead=watch(activity);
 watch(structuredClone(activity));
 finishRead(catalogue('CanningSt',false));
 finishSelect({...catalogue('Harbor'),status:'ACKNOWLEDGED',reason_code:null});
 await Promise.all([activityRead,selection]);
 assert.equal(control.getState().selected_profile_id,'Harbor');
 assert.equal(control.getState().switch_allowed,false);
 assert.equal(readCalls,3);
});

test('pending selection failure also rechecks a discarded activity catalogue',async()=>{
 let finishSelect,finishRead,readCalls=0;
 const control=createSiteProfileController({
  read_site_profiles:()=>{readCalls++;return readCalls===1?Promise.resolve(catalogue()):readCalls===2?new Promise(resolve=>finishRead=resolve):Promise.resolve(catalogue('CanningSt',false));},
  select_site_profile:()=>new Promise(resolve=>finishSelect=resolve),
 });
 await control.load();
 const selection=control.select('Harbor');
 const activityRead=control.load();
 finishRead(catalogue('CanningSt',false));await activityRead;
 finishSelect({...catalogue(),status:'CONFLICT',reason_code:'MISSION_NOT_IDLE'});
 await selection;
 assert.equal(control.getState().selected_profile_id,'CanningSt');
 assert.equal(control.getState().switch_allowed,false);
 assert.equal(readCalls,3);
});

test('same-tick invalid response drains a discarded catalogue after the active read clears',async()=>{
 let finishSelect,finishRead,readCalls=0;
 const control=createSiteProfileController({
  read_site_profiles:()=>{readCalls++;return readCalls===1?Promise.resolve(catalogue()):readCalls===2?new Promise(resolve=>finishRead=resolve):Promise.resolve(catalogue('CanningSt',false));},
  select_site_profile:()=>new Promise(resolve=>finishSelect=resolve),
 });
 await control.load();
 const selection=control.select('Harbor');
 const activityRead=control.load();
 finishRead(catalogue('CanningSt',false));
 finishSelect({...catalogue(),status:'INVALID',reason_code:'SITE_PROFILE_INCOMPLETE'});
 await Promise.all([activityRead,selection]);
 assert.equal(readCalls,3);
 assert.equal(control.getState().selected_profile_id,'CanningSt');
 assert.equal(control.getState().switch_allowed,false);
});
