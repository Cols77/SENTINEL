import assert from 'node:assert/strict';
import test from 'node:test';
import {missionStatusText} from '../../examples/survey-console/view-model.mjs';

test('persistent status text exposes budget, sweep progress and localization age',()=>{
  const text=missionStatusText({
    flights:{used:1,max:2},
    sweep:{status:'ACTIVE',stops:[{status:'COMPLETED'},{status:'ACTIVE'}]},
    localization:{status:'AGING',age_ms:500}
  });
  assert.match(text,/Flights 1\/2/);
  assert.match(text,/Sweep ACTIVE/);
  assert.match(text,/Stops 1\/2/);
  assert.match(text,/Localization AGING/);
  assert.match(text,/500 ms old/);
});
