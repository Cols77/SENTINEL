import test from "node:test";
import assert from "node:assert/strict";
import { makeScenario } from "../../examples/survey-console/fixtures.mjs";
import {
  createDecisionController,
  canDecide,
  terminalDecisionText,
} from "../../examples/survey-console/approval.mjs";
test('terminal decision text is explicit and cannot imply a conflict',()=>{
 assert.equal(terminalDecisionText('COMPLETE'),'COMPLETE - run is complete; no new dispatch is available.');
 assert.equal(terminalDecisionText('ABORTED'),'ABORTED - run was aborted; no new dispatch is available.');
 assert.equal(terminalDecisionText('RECOVERY_REQUIRED'),'RECOVERY_REQUIRED - reconcile the aircraft before dispatch.');
});
const snapshot = () => makeScenario("computers-followup").snapshots.at(-1);
const result = (a, status = "ACKNOWLEDGED") => ({
  attempt_id: a.attempt_id,
  status,
  message: "Fixture result",
});
test("double clicks submit once and acknowledgement does not dispatch", async () => {
  let finish;
  const calls = [];
  const d = createDecisionController(
    {
      submit_decision: (a) => {
        calls.push(a);
        return new Promise((r) => (finish = r));
      },
    },
    () => "a1",
  );
  const s = snapshot();
  d.update(s, false);
  const p = d.submit("APPROVE");
  await d.submit("APPROVE");
  assert.equal(calls.length, 1);
  assert.equal(d.getState().pending, true);
  finish(result(calls[0]));
  await p;
  assert.equal(s.flights.used, 1);
  assert.equal(d.getState().attempt.proposal_id, "p2");
  await d.submit("APPROVE");
  assert.equal(calls.length, 1);
  d.update({ ...s, version: 7 }, false);
  await d.submit("APPROVE");
  assert.equal(calls.length, 1);
});
test("unknown transport outcome retries the exact same attempt", async () => {
  const calls = [];
  const d = createDecisionController(
    {
      submit_decision: async (a) => {
        calls.push(a);
        if (calls.length === 1) throw new Error("Lost connection");
        return result(a);
      },
    },
    () => "a1",
  );
  d.update(snapshot(), false);
  await d.submit("APPROVE");
  assert.equal(d.getState().result.status, "IN_DOUBT");
  await d.submit("REJECT");
  assert.equal(calls.length, 1);
  await d.retry();
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(d.getState().result.status, "ACKNOWLEDGED");
});
test("stale unknown and missing eligibility block both decisions", async () => {
  for (const mutate of [
    (s) => (s.freshness = "STALE"),
    (s) => (s.proposal.eligibility.consent = null),
    (s) => (s.proposal.eligibility.preflight = false),
    (s) => (s.status = "ABORTED"),
    (s) => (s.proposal = null),
  ]) {
    const s = snapshot();
    mutate(s);
    assert.equal(canDecide(s, false), false);
    let calls = 0;
    const d = createDecisionController({
      submit_decision: async () => calls++,
    });
    d.update(s, false);
    await d.submit("APPROVE");
    await d.submit("REJECT");
    assert.equal(calls, 0);
  }
  assert.equal(canDecide(snapshot(), true), false);
});
test("proposal replacement never inherits pending or acknowledged approval", async () => {
  let finish;
  let n = 0;
  const calls = [];
  const d = createDecisionController(
    {
      submit_decision: (a) => {
        calls.push(a);
        return new Promise((r) => (finish = r));
      },
    },
    () => `a${++n}`,
  );
  const s = snapshot();
  d.update(s, false);
  const p = d.submit("APPROVE");
  const replacement = structuredClone(s);
  replacement.version++;
  replacement.proposal.id = "p3";
  replacement.proposal.sha256 = "hash-p3";
  d.update(replacement, false);
  assert.equal(d.getState().detached, true);
  await d.submit("APPROVE");
  assert.equal(calls.length, 1);
  finish(result(calls[0]));
  await p;
  assert.equal(d.getState().attempt.proposal_id, "p2");
  const q = d.submit("APPROVE");
  assert.equal(calls[1].proposal_id, "p3");
  finish(result(calls[1]));
  await q;
  assert.equal(d.getState().history[0].attempt.proposal_id, "p2");
});
test("conflict requires newer snapshot and a deliberate new identity", async () => {
  const calls = [];
  let n = 0;
  const d = createDecisionController(
    {
      submit_decision: async (a) => {
        calls.push(a);
        return result(a, "CONFLICT");
      },
    },
    () => `a${++n}`,
  );
  const s = snapshot();
  d.update(s, false);
  await d.submit("APPROVE");
  await d.submit("APPROVE");
  assert.equal(calls.length, 1);
  d.update({ ...s, version: s.version + 1 }, false);
  await d.submit("APPROVE");
  assert.equal(calls.length, 2);
  assert.notEqual(calls[0].idempotency_key, calls[1].idempotency_key);
});
test("wrong attempt response stays uncertain and detached retries reconcile", async () => {
  let calls = 0;
  const d = createDecisionController(
    {
      submit_decision: async () => {
        calls++;
        return {
          attempt_id: "wrong",
          status: "ACKNOWLEDGED",
          message: "wrong",
        };
      },
    },
    () => "a1",
  );
  const s = snapshot();
  d.update(s, false);
  await d.submit("APPROVE");
  assert.equal(d.getState().result.status, "IN_DOUBT");
  s.proposal.id = "p3";
  d.update(s, false);
  await d.retry();
  assert.equal(calls, 2);
});
test("eligibility and payloads cannot be mutated outside the controller", async () => {
  const s = snapshot();
  let received;
  const d = createDecisionController(
    {
      submit_decision: async (a) => {
        received = a;
        return result(a);
      },
    },
    () => "a1",
  );
  d.update(s, false);
  s.proposal.id = "tampered";
  await d.submit("APPROVE");
  assert.equal(received.proposal_id, "p2");
  const copy = d.getState();
  copy.attempt.proposal_id = "tampered";
  assert.equal(d.getState().attempt.proposal_id, "p2");
});

test('lost committed response reconciles after proposal changes and permits follow-up',async()=>{
 const calls=[];let n=0;
 const d=createDecisionController({submit_decision:async a=>{calls.push(a);if(calls.length===1)throw Error('response lost');return result(a);}},()=>`lost-${++n}`);
 const s=snapshot();d.update(s,false);await d.submit('APPROVE');
 const next=structuredClone(s);next.proposal.id='followup';next.proposal.sha256='b'.repeat(64);next.version++;
 d.update(next,false);await d.submit('APPROVE');assert.equal(calls.length,1);
 await d.retry();assert.equal(calls.length,2);assert.deepEqual(calls[1],calls[0]);
 await d.submit('APPROVE');assert.equal(calls.length,3);assert.equal(calls[2].proposal_id,'followup');
});
