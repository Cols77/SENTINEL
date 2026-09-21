import test from 'node:test';import assert from 'node:assert/strict';
import {poseStyle,tagPolygon} from '../../examples/survey-console/localization.mjs';
test('stale pose uses a distinct marker and corners respect image bounds',()=>{
 assert.equal(poseStyle('STALE','INSIDE'),'pose-stale');
 assert.equal(poseStyle('FRESH','OUTSIDE'),'pose-outside');
 assert.equal(tagPolygon([[0,0],[1,0],[1,1],[0,1]],960,720),'0,0 960,0 960,720 0,720');
 assert.throws(()=>tagPolygon([[2,0],[1,0],[1,1],[0,1]],960,720));
});
