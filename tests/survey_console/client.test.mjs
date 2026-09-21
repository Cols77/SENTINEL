import test from "node:test";
import assert from "node:assert/strict";
import { makeScenario } from "../../examples/survey-console/fixtures.mjs";
import { createFixtureClient } from "../../examples/survey-console/fixture-client.mjs";
function setup() {
  const s = makeScenario("computers-followup");
  const c = createFixtureClient(s);
  for (let i = 1; i < s.snapshots.length; i++) c.advance();
  const last = s.snapshots.at(-1);
  const a = {
    run_id: last.run_id,
    proposal_id: "p2",
    proposal_sha256: last.proposal.sha256,
    expected_version: last.version,
    decision: "APPROVE",
    attempt_id: "a1",
    idempotency_key: "k1",
  };
  return { c, a };
}
test("evidence scope, consent, missing and cloned reads", async () => {
  const c = createFixtureClient(makeScenario("consent-required"));
  assert.equal(
    (await c.read_evidence("fixture-consent-required", "f1")).status,
    "FORBIDDEN",
  );
  assert.equal((await c.read_evidence("foreign", "f1")).status, "NOT_FOUND");
  assert.equal(
    (await c.read_evidence("fixture-consent-required", "absent")).status,
    "NOT_FOUND",
  );
  await assert.rejects(c.read_survey("foreign"), { code: "NOT_FOUND" });
  const { c: d, a } = setup();
  const s = await d.read_survey(a.run_id);
  s.flights.used = 99;
  assert.equal((await d.read_survey(a.run_id)).flights.used, 1);
  const img = await d.read_evidence(a.run_id, "f1");
  assert.equal(img.media_type, "image/png");
  assert.ok(img.bytes instanceof Uint8Array);
});
test("cursor paging and bounded limits", async () => {
  const { c, a } = setup();
  assert.deepEqual(
    (await c.read_survey_events(a.run_id, 1, 2)).map((e) => e.sequence),
    [2, 3],
  );
  for (const n of [0, -1, 201, 1.5])
    await assert.rejects(c.read_survey_events(a.run_id, 0, n), {
      code: "INVALID_ARGUMENT",
    });
});
test("pending and completed retries share one identity and never dispatch flight", async () => {
  const { c, a } = setup();
  const p = c.submit_decision(a),
    q = c.submit_decision({ ...a });
  assert.equal(p, q);
  assert.equal(c.calls.length, 1);
  await assert.rejects(c.submit_decision({ ...a, decision: "REJECT" }), {
    code: "IDENTITY_REUSED",
  });
  c.resolveDecision("ACKNOWLEDGED");
  assert.equal((await p).attempt_id, "a1");
  assert.deepEqual(await q, await c.submit_decision(a));
  assert.equal((await c.read_survey(a.run_id)).flights.used, 1);
});
test("conflicts are settled and identity cannot cross payloads", async () => {
  const { c, a } = setup();
  const bad = { ...a, expected_version: 0 };
  assert.equal((await c.submit_decision(bad)).status, "CONFLICT");
  await assert.rejects(c.submit_decision(a), { code: "IDENTITY_REUSED" });
  await assert.rejects(c.submit_decision({ ...a, idempotency_key: "new" }), {
    code: "IDENTITY_REUSED",
  });
});
test("fixture advance does not exceed final snapshot", async () => {
  const { c, a } = setup();
  for (let i = 0; i < 10; i++) c.advance();
  assert.equal((await c.read_survey(a.run_id)).version, 6);
});

test("uncertain fixture result reconciles under the original attempt identity", async () => {
  const { c, a } = setup();
  const first = c.submit_decision(a);
  c.resolveDecision("IN_DOUBT");
  assert.equal((await first).status, "IN_DOUBT");
  const retry = c.submit_decision({ ...a });
  const duplicate = c.submit_decision({ ...a });
  assert.notEqual(retry, first);
  assert.equal(retry, duplicate);
  c.resolveDecision("ACKNOWLEDGED");
  assert.equal((await retry).status, "ACKNOWLEDGED");
  assert.equal((await retry).attempt_id, a.attempt_id);
  assert.deepEqual(c.calls, [a, a]);
  assert.deepEqual(await c.submit_decision(a), await retry);
  assert.equal((await c.read_survey(a.run_id)).flights.used, 1);
});

test("uncertain fixture outcome blocks a different decision identity", async () => {
  const { c, a } = setup();
  const first = c.submit_decision(a);
  c.resolveDecision("IN_DOUBT");
  await first;
  const outcome = await Promise.race([
    c
      .submit_decision({ ...a, attempt_id: "other", idempotency_key: "other" })
      .then(
        () => "accepted",
        (e) => e.code,
      ),
    new Promise((resolve) => setImmediate(() => resolve("pending"))),
  ]);
  assert.equal(outcome, "INVALID_ARGUMENT");
});
