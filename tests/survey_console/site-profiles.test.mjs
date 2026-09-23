import test from 'node:test';
import assert from 'node:assert/strict';
import {createSiteProfileController} from '../../examples/survey-console/site-profiles.mjs';

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
 let finish;
 const control=createSiteProfileController({read_site_profiles:()=>new Promise(resolve=>finish=resolve),select_site_profile:async()=>({...catalogue('Harbor'),status:'ACKNOWLEDGED',reason_code:null})});
 const old=control.load();
 // A catalogue can be pending when a server response to a selection arrives.
 control.update(catalogue());
 await control.select('Harbor');
 finish(catalogue());await old;
 assert.equal(control.getState().selected_profile_id,'Harbor');
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
