import test from "node:test";
import assert from "node:assert/strict";
import {
  fitBox,
  supportingSightings,
  frameDetectionText,
} from "../../examples/survey-console/evidence.mjs";
import { makeScenario } from "../../examples/survey-console/fixtures.mjs";
test("normalized boxes align within portrait and landscape letterboxing", () => {
  assert.deepEqual(fitBox(400, 400, 800, 400, [0.25, 0.25, 0.75, 0.75]), {
    left: 100,
    top: 150,
    width: 200,
    height: 100,
  });
  assert.deepEqual(fitBox(400, 400, 400, 800, [0.25, 0.25, 0.75, 0.75]), {
    left: 150,
    top: 100,
    width: 100,
    height: 200,
  });
  for (const values of [
    [400, 400, 0, 400, [0, 0, 1, 1]],
    [400, 400, 400, 400, [0.9, 0, 0.1, 1]],
    [400, 400, 400, 400, [-1, 0, 1, 1]],
  ])
    assert.throws(() => fitBox(...values));
});
test('detection progression preserves latency, errors and explicit evidence gaps',()=>{
 assert.equal(frameDetectionText({detection_status:'CAPTURED',detection_latency_ms:null,detection_error:null,gap_reason:null}),'CAPTURED');
 assert.equal(frameDetectionText({detection_status:'ANALYSED',detection_latency_ms:42,detection_error:null,gap_reason:null}),'ANALYSED - 42 ms');
 assert.equal(frameDetectionText({detection_status:'FAILED',detection_latency_ms:null,detection_error:'provider unavailable',gap_reason:'provider unavailable'}),'FAILED - provider unavailable');
});
test("class and object selection only traverse supplied supporting sightings", () => {
  const s = makeScenario("computers-followup").snapshots.at(-1);
  assert.deepEqual(
    supportingSightings(s, { kind: "object", id: "computer-6" }).map(
      (x) => x.id,
    ),
    ["c6"],
  );
  assert.equal(
    supportingSightings(s, { kind: "class", id: "computer" }).length,
    6,
  );
  assert.deepEqual(
    supportingSightings(s, { kind: "frame", id: "f1" }).map((x) => x.id),
    ["c1", "c2"],
  );
  assert.deepEqual(
    supportingSightings(s, { kind: "object", id: "missing" }),
    [],
  );
});
