import test from 'node:test';import assert from 'node:assert/strict';
import {missionMapModel} from '../../examples/survey-console/mission-map.mjs';
test('map preserves backend coordinates and absence of measured pose',()=>{
 const s={mission:{envelope:{min_x:0,max_x:100,min_y:0,max_y:100},route:[{stop_id:'a',x:25,y:35}],active_stop_id:null,commanded_position:{x:20,y:30}},localization:{map_position:null,status:'UNAVAILABLE',containment:'UNAVAILABLE'}};
 const model=missionMapModel(s);assert.equal(model.measured,null);assert.deepEqual(model.route,[{stop_id:'a',x:25,y:35}]);
 s.localization={map_position:{x:19,y:31},status:'STALE',containment:'INSIDE'};
 assert.deepEqual(missionMapModel(s).measured,{x:19,y:31});
});
