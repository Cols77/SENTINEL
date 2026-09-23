/**
 * @typedef {'computer'|'person'|'phone'|'pen'} ClassName
 * @typedef {'REQUEST'|'PROPOSAL'|'APPROVAL'|'FLIGHT'|'ANALYSIS'|'REVIEW'|'REPORT'} Step
 * @typedef {{x_m:number,y_m:number,z_m:number,yaw_deg:number}} Pose
 * @typedef {{id:string,status:'PLANNED'|'ACTIVE'|'COMPLETED'|'FAILED'|'ABORTED'|'UNKNOWN',
 *   range_m:number,altitude_m:number,extent_m:number,duration_s:number,
 *   resolvable_classes:ClassName[]}} Stop
 * @typedef {{id:string,stop_id:string,captured_at:string|null,commanded_pose:Pose|null,
 *   measured_pose:Pose|null,analysis:'PENDING'|'ANALYSED'|'FAILED'|'NOT_RETAINED',
 *   evidence:'AVAILABLE'|'MISSING'|'FORBIDDEN'|'REVOKED',failure:string|null,
 *   merge_eligible:boolean}} Frame
 * @typedef {{id:string,frame_id:string,class_name:ClassName,
 *   bbox:[number,number,number,number]|null,raw_score:number|null,
 *   score_semantics:string,provider:string,model:string}} Sighting
 * @typedef {{id:string,class_name:ClassName,state:'UNCERTAIN'|'CONFIRMED',
 *   sighting_ids:string[],reason:string,geometry_degraded:boolean}} ObjectFact
 * @typedef {{class_name:ClassName,semantics:'RANGE'|'SINGLE_VIEW_LOWER_BOUND'|'UNKNOWN',
 *   confirmed:number|null,uncertain:number|null,lower_bound:number|null,
 *   captured_at:string|null,observability:'OBSERVABLE'|'LIMITED'|'NOT_OBSERVABLE'|'UNKNOWN',
 *   projected_px:number|null,min_px:number|null,reason:string,arithmetic:string|null,
 *   sighting_ids:string[],distinct_stops:number|null,geometry_degraded:boolean}} Count
 * @typedef {{id:string,sha256:string,option_id:string,stops:Stop[],range_m:number,
 *   altitude_m:number,extent_m:number,duration_s:number,resolvable_classes:ClassName[],
 *   proposer:{mode:'F'|'E'|'J',source:string},justification:string,
 *   guardrail_disagreements:string[],target_object_ids:string[],
 *   eligibility:{consent:boolean|null,envelope:boolean|null,preflight:boolean|null,
 *     current:boolean},disabled_reasons:string[]}} Proposal
 * @typedef {{schema_version:1,run_id:string,version:number,event_sequence:number,
 *   updated_at:string,source:'LIVE'|'REPLAY',freshness:'FRESH'|'STALE',
 *   status:'ACTIVE'|'COMPLETE'|'ABORTED',step:Step,request_text:string,
 *   proposer:{mode:'F'|'E'|'J',source:string},
 *   flights:{used:number,max:2},sweep:{id:string,option_id:string,status:'PLANNED'|'ACTIVE'|'COMPLETE'|'ABORTED',
 *     merged:boolean,flights_used_before:number,stops:Stop[]},
 *   frames:Frame[],sightings:Sighting[],objects:ObjectFact[],counts:Count[],
 *   analysis:{analysed:number,total:number,dropped_labels:string[],unknown_labels:string[]},
 *   provider:{name:string,model:string,source_kind:string},failures:string[],
 *   proposal:Proposal|null,report:{status:'ABSENT'|'ACCEPTED'|'REJECTED',text:string|null,reason:string|null}} Survey
 * @typedef {{id:string,run_id:string,sequence:number,occurred_at:string,summary:string}} SurveyEvent
 * @typedef {{run_id:string,proposal_id:string,proposal_sha256:string,expected_version:number,
 *   decision:'APPROVE'|'REJECT',attempt_id:string,idempotency_key:string}} Attempt
 * @typedef {{attempt_id:string,status:'ACKNOWLEDGED'|'REJECTED'|'CONFLICT'|'IN_DOUBT',message:string}} DecisionResult
 * @typedef {{status:'AVAILABLE',run_id:string,frame_id:string,media_type:'image/png'|'image/jpeg',
 *   bytes:Uint8Array,width:number,height:number}|{status:'NOT_FOUND'|'FORBIDDEN'|'REVOKED',
 *   run_id:string,frame_id:string,reason:string}} EvidenceResult
 * @typedef {{id:string,title:string,snapshots:Survey[],events:SurveyEvent[],
 *   decision_results:DecisionResult['status'][]}} Scenario
 */

/** Proposed fixture projection v1; not an adopted coordinator wire protocol. */
export const STEPS = Object.freeze([
  "REQUEST",
  "PROPOSAL",
  "APPROVAL",
  "FLIGHT",
  "ANALYSIS",
  "REVIEW",
  "REPORT",
]);
export const CLASSES = Object.freeze(["computer", "person", "phone", "pen"]);
function requireFact(ok, path) {
  if (!ok)
    throw Object.assign(new Error(`Invalid projection: ${path}`), {
      code: "INVALID_PROJECTION",
    });
}
const str = (v, p) =>
  requireFact(typeof v === "string" && v.trim().length > 0, p);
const bool = (v, p) => requireFact(typeof v === "boolean", p);
const num = (v, p) =>
  requireFact(typeof v === "number" && Number.isFinite(v), p);
const positive = (v, p) => {
  num(v, p);
  requireFact(v >= 0, p);
};
const integer = (v, p) => requireFact(Number.isSafeInteger(v) && v >= 0, p);
const member = (values) => (v, p) => requireFact(values.includes(v), p);
const nullable = (check) => (v, p) => {
  if (v !== null) check(v, p);
};
const array = (check) => (v, p) => {
  requireFact(Array.isArray(v), p);
  v.forEach((x, i) => check(x, `${p}[${i}]`));
};
const time = (v, p) => {
  str(v, p);
  requireFact(
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/.test(
      v,
    ) && Number.isFinite(Date.parse(v)),
    p,
  );
};
const record = (fields) => (v, p) => {
  requireFact(v !== null && typeof v === "object" && !Array.isArray(v), p);
  requireFact(Object.keys(v).length === Object.keys(fields).length, p);
  for (const [key, check] of Object.entries(fields)) {
    requireFact(Object.hasOwn(v, key), `${p}.${key}`);
    check(v[key], `${p}.${key}`);
  }
};
const unique = (rows, key, p) =>
  requireFact(
    new Set(rows.map((x) => (key ? x[key] : x))).size === rows.length,
    p,
  );
const strings = array(str),
  classes = array(member(CLASSES));
const pose = record({ x_m: num, y_m: num, z_m: num, yaw_deg: num });
const stop = record({
  id: str,
  status: member([
    "PLANNED",
    "ACTIVE",
    "COMPLETED",
    "FAILED",
    "ABORTED",
    "UNKNOWN",
  ]),
  range_m: positive,
  altitude_m: positive,
  extent_m: positive,
  duration_s: positive,
  resolvable_classes: classes,
});
const proposer = record({ mode: member(["F", "E", "J"]), source: str });
const eligibility = record({
  consent: nullable(bool),
  envelope: nullable(bool),
  preflight: nullable(bool),
  current: bool,
});
const proposal = record({
  id: str,
  sha256: str,
  option_id: str,
  stops: array(stop),
  range_m: positive,
  altitude_m: positive,
  extent_m: positive,
  duration_s: positive,
  resolvable_classes: classes,
  proposer,
  justification: str,
  guardrail_disagreements: strings,
  target_object_ids: strings,
  eligibility,
  disabled_reasons: strings,
});
const frame = record({
  id: str,
  stop_id: str,
  captured_at: nullable(time),
  commanded_pose: nullable(pose),
  measured_pose: nullable(pose),
  analysis: member(["PENDING", "ANALYSED", "FAILED", "NOT_RETAINED"]),
  evidence: member(["AVAILABLE", "MISSING", "FORBIDDEN", "REVOKED"]),
  failure: nullable(str),
  merge_eligible: bool,
});
const box = (v, p) => {
  requireFact(
    Array.isArray(v) &&
      v.length === 4 &&
      v.every((x) => Number.isFinite(x) && x >= 0 && x <= 1) &&
      v[0] <= v[2] &&
      v[1] <= v[3],
    p,
  );
};
const sighting = record({
  id: str,
  frame_id: str,
  class_name: member(CLASSES),
  bbox: nullable(box),
  raw_score: nullable(num),
  score_semantics: str,
  provider: str,
  model: str,
});
const object = record({
  id: str,
  class_name: member(["computer", "phone", "pen"]),
  state: member(["UNCERTAIN", "CONFIRMED"]),
  sighting_ids: strings,
  reason: str,
  geometry_degraded: bool,
});
const count = record({
  class_name: member(CLASSES),
  semantics: member(["RANGE", "SINGLE_VIEW_LOWER_BOUND", "UNKNOWN"]),
  confirmed: nullable(integer),
  uncertain: nullable(integer),
  lower_bound: nullable(integer),
  captured_at: nullable(time),
  observability: member(["OBSERVABLE", "LIMITED", "NOT_OBSERVABLE", "UNKNOWN"]),
  projected_px: nullable(positive),
  min_px: nullable(positive),
  reason: str,
  arithmetic: nullable(str),
  sighting_ids: strings,
  distinct_stops: nullable(integer),
  geometry_degraded: bool,
});
const survey = record({
  schema_version: member([1]),
  run_id: str,
  version: integer,
  event_sequence: integer,
  updated_at: time,
  source: member(["LIVE", "REPLAY"]),
  freshness: member(["FRESH", "STALE"]),
  status: member(["ACTIVE", "COMPLETE", "ABORTED"]),
  step: member(STEPS),
  request_text: str,
  proposer,
  flights: record({ used: integer, max: member([2]) }),
  sweep: record({
    id: str,
    option_id: str,
    status: member(["PLANNED", "ACTIVE", "COMPLETE", "ABORTED"]),
    merged: bool,
    flights_used_before: integer,
    stops: array(stop),
  }),
  frames: array(frame),
  sightings: array(sighting),
  objects: array(object),
  counts: array(count),
  analysis: record({
    analysed: integer,
    total: integer,
    dropped_labels: strings,
    unknown_labels: strings,
  }),
  provider: record({ name: str, model: str, source_kind: str }),
  failures: strings,
  proposal: nullable(proposal),
  report: record({
    status: member(["ABSENT", "ACCEPTED", "REJECTED"]),
    text: nullable(str),
    reason: nullable(str),
  }),
});
export function validateSurvey(s) {
  survey(s, "survey");
  for (const key of ["frames", "sightings", "objects"])
    unique(s[key], "id", key);
  unique(s.sweep.stops, "id", "stops");
  unique(s.counts, "class_name", "counts");
  requireFact(s.counts.length === 4, "counts");
  requireFact(
    s.flights.used <= 2 && s.sweep.flights_used_before <= s.flights.used,
    "flights",
  );
  const frames = new Map(s.frames.map((f) => [f.id, f])),
    sightings = new Map(s.sightings.map((x) => [x.id, x]));
  for (const f of s.frames) {
    requireFact(
      s.sweep.stops.some((t) => t.id === f.stop_id),
      "frame.stop_id",
    );
    if (f.analysis === "NOT_RETAINED")
      requireFact(
        f.evidence !== "AVAILABLE" &&
          f.captured_at === null &&
          !f.merge_eligible,
        "frame.not_retained",
      );
  }
  for (const x of s.sightings)
    requireFact(
      frames.has(x.frame_id) && frames.get(x.frame_id).analysis === "ANALYSED",
      "sighting.frame",
    );
  for (const o of [...s.objects, ...s.counts]) {
    unique(o.sighting_ids, null, "sighting_ids");
    for (const id of o.sighting_ids)
      requireFact(
        sightings.has(id) &&
          sightings.get(id).class_name === o.class_name &&
          frames.get(sightings.get(id).frame_id).merge_eligible,
        "supporting sighting",
      );
  }
  for (const c of s.counts) {
    if (c.observability === "NOT_OBSERVABLE" || c.semantics === "UNKNOWN")
      requireFact(
        c.confirmed === null && c.uncertain === null && c.lower_bound === null,
        "unavailable estimate",
      );
    if (c.class_name === "person")
      requireFact(c.semantics !== "RANGE", "person semantics");
    if (c.semantics === "SINGLE_VIEW_LOWER_BOUND") {
      requireFact(
        c.class_name === "person" &&
          c.lower_bound !== null &&
          c.captured_at !== null &&
          c.confirmed === null &&
          c.uncertain === null,
        "person timestamp",
      );
      const supporting = new Set(
        c.sighting_ids.map((id) => sightings.get(id).frame_id),
      );
      requireFact(supporting.size === 1, "single person view");
      requireFact(
        frames.get([...supporting][0]).captured_at === c.captured_at,
        "person capture",
      );
    }
    if (c.semantics === "RANGE")
      requireFact(
        c.lower_bound === null && c.confirmed !== null && c.uncertain !== null,
        "range",
      );
  }
  requireFact(
    s.analysis.total === s.frames.length &&
      s.analysis.analysed ===
        s.frames.filter((f) => f.analysis === "ANALYSED").length,
    "analysis",
  );
  if (s.sweep.status === "ABORTED")
    requireFact(
      !s.sweep.merged &&
        s.flights.used === s.sweep.flights_used_before &&
        s.frames.every((f) => !f.merge_eligible),
      "aborted sweep",
    );
  if (s.proposal) {
    unique(s.proposal.stops, "id", "proposal.stops");
    for (const id of s.proposal.target_object_ids)
      requireFact(
        s.objects.some((o) => o.id === id && o.state === "UNCERTAIN"),
        "proposal targets",
      );
  }
  if (s.report.status === "REJECTED")
    requireFact(
      s.report.text === null && s.report.reason !== null,
      "rejected report",
    );
  return s;
}
export function validateEvent(e) {
  record({
    id: str,
    run_id: str,
    sequence: integer,
    occurred_at: time,
    summary: str,
  })(e, "event");
  requireFact(e.sequence > 0, "event.sequence");
  return e;
}
