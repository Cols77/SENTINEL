import test from 'node:test';import assert from 'node:assert/strict';
import {poseStyle,tagPolygon,verticalConsistencyText} from '../../examples/survey-console/localization.mjs';
test('stale pose uses a distinct marker and corners respect image bounds',()=>{
 assert.equal(poseStyle('STALE','INSIDE'),'pose-stale');
 assert.equal(poseStyle('FRESH','OUTSIDE'),'pose-outside');
 assert.equal(tagPolygon([[0,0],[1,0],[1,1],[0,1]],960,720),'0,0 960,0 960,720 0,720');
 assert.throws(()=>tagPolygon([[2,0],[1,0],[1,1],[0,1]],960,720));
});
test('vertical consistency remains explicit and AprilTag IDs stay authoritative',()=>{
 assert.equal(verticalConsistencyText({status:'CONSISTENT',measured_m:1.2,expected_m:1.2,error_m:0,reason:'within tolerance'}),'CONSISTENT - 0 m - within tolerance');
 assert.equal(verticalConsistencyText({status:'UNAVAILABLE',measured_m:null,expected_m:null,error_m:null,reason:'No reference'}),'UNAVAILABLE - No reference');
});
