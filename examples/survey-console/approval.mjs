import { el, button, replace, meta } from "./dom.mjs";
export function terminalDecisionText(status){return {COMPLETE:'COMPLETE - run is complete; no new dispatch is available.',ABORTED:'ABORTED - run was aborted; no new dispatch is available.',RECOVERY_REQUIRED:'RECOVERY_REQUIRED - reconcile the aircraft before dispatch.'}[status]||null;}
export function canDecide(s, needsRefresh, decision = "APPROVE") {
  const p = s?.proposal,
    e = p?.eligibility;
  const current = Boolean(
    p &&
      s.status === "ACTIVE" &&
      s.freshness === "FRESH" &&
      !needsRefresh &&
      e?.current === true,
  );
  if (!current || decision === "REJECT") return current;
  return e.envelope === true && p.disabled_reasons.length === 0;
}
function matches(s, a) {
  return Boolean(
    a &&
      s?.run_id === a.run_id &&
      s.proposal?.id === a.proposal_id &&
      s.proposal?.sha256 === a.proposal_sha256,
  );
}
function newAttemptAllowed(s, state, needsRefresh, decision = "APPROVE") {
  if (!canDecide(s, needsRefresh, decision) || state.pending) return false;
  if (!state.attempt) return true;
  if (!state.result || state.result.status === "IN_DOUBT") return false;
  if (state.result.status === "CONFLICT")
    return s.version > state.attempt.expected_version;
  return !matches(s, state.attempt);
}
export function createDecisionController(
  client,
  newId = () => crypto.randomUUID(),
  attemptContext = () => ({}),
) {
  let survey = null,
    needsRefresh = true,
    state = {
      attempt: null,
      result: null,
      pending: false,
      error: null,
      detached: false,
      history: [],
    };
  const listeners = new Set();
  const getState = () => structuredClone(state);
  const emit = () => {
    for (const fn of listeners) fn(getState());
  };
  async function send(attempt) {
    state = { ...state, pending: true, error: null };
    emit();
    try {
      const result = await client.submit_decision(attempt);
      if (
        !result ||
        result.attempt_id !== attempt.attempt_id ||
        !["ACKNOWLEDGED", "REJECTED", "CONFLICT", "IN_DOUBT"].includes(
          result.status,
        ) ||
        typeof result.message !== "string"
      )
        throw new Error("Unmatched or malformed decision response");
      state = {
        ...state,
        result: structuredClone(result),
        pending: false,
        detached: !matches(survey, attempt),
      };
    } catch (e) {
      state = {
        ...state,
        pending: false,
        error: e.message,
        result: {
          attempt_id: attempt.attempt_id,
          status: "IN_DOUBT",
          message: e.message,
        },
        detached: !matches(survey, attempt),
      };
    }
    emit();
  }
  return {
    getState,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    update(s, refresh) {
      survey = structuredClone(s);
      needsRefresh = refresh;
      const detached = Boolean(state.attempt && !matches(s, state.attempt));
      if (state.detached !== detached) {
        state = { ...state, detached };
        emit();
      }
    },
    async submit(decision) {
      if (
        !["APPROVE", "REJECT"].includes(decision) ||
        !newAttemptAllowed(survey, state, needsRefresh, decision)
      )
        return;
      const id = newId();
      const attempt = Object.freeze({
        ...attemptContext(id),
        run_id: survey.run_id,
        proposal_id: survey.proposal.id,
        proposal_sha256: survey.proposal.sha256,
        expected_version: survey.version,
        decision,
        attempt_id: id,
        idempotency_key: id,
      });
      const history = state.attempt
        ? [...state.history, { attempt: state.attempt, result: state.result }]
        : state.history;
      state = {
        attempt,
        result: null,
        pending: false,
        error: null,
        detached: false,
        history,
      };
      await send(attempt);
    },
    async restore(attempt) {
      if (state.pending || !attempt || attempt.run_id !== survey?.run_id) return;
      state = {...state, attempt: Object.freeze(structuredClone(attempt))};
      await send(state.attempt);
    },
    async retry() {
      if (
        !state.attempt ||
        state.pending ||
        state.result?.status !== "IN_DOUBT" ||
        state.attempt.run_id !== survey?.run_id
      )
        return;
      await send(state.attempt);
    },
  };
}
export function renderApproval(
  host,
  s,
  state,
  { onApprove, onReject, onRetry } = {},
  needsRefresh = true,
) {
  const heading = el("h2", { id: "decision-heading", tabindex: "-1" }, [
      "Flight approval",
    ]),
    p = s?.proposal;
  const nodes = [
    el("div", { class: "section-label" }, ["OPERATOR DECISION"]),
    heading,
  ];
  const terminal=terminalDecisionText(s?.status);
  if(terminal){nodes.push(el('p',{class:'warning',role:'status'},[terminal]));replace(host,nodes);return;}
  if (p) {
    nodes.push(
      meta(`Proposal ${p.id} · ${p.option_id}`),
      meta(`Binding: ${p.sha256}`),
      el("p", {}, [s.request_text]),
      meta(
        `${p.range_m} m range · ${p.altitude_m} m altitude · ${p.extent_m} m extent · ${p.duration_s} s`,
      ),
      meta(`Resolvable classes: ${p.resolvable_classes.join(", ")}`),
      meta(`Proposer ${p.proposer.mode} · ${p.proposer.source}${p.proposer.escalation_reason?` · escalation: ${p.proposer.escalation_reason}`:""}`),
      meta(`Code-derived justification: ${p.justification}`),
      ...(p.escalation_reason?[meta(`Public escalation reason: ${p.escalation_reason}`)]:[]),
      meta(
        `Guardrail disagreements: ${p.guardrail_disagreements.join("; ") || "None reported"}`,
      ),
    );
    nodes.push(
      el(
        "ol",
        { class: "proposal-stops" },
        p.stops.map((t) =>
          el("li", {}, [
            `${t.id} · ${t.status}`,
            meta(
              `${t.range_m} m range · ${t.altitude_m} m altitude · ${t.extent_m} m extent · ${t.duration_s} s`,
            ),
            meta(`Classes: ${t.resolvable_classes.join(", ")}`),
            meta(`${t.coverage} · ${t.coverage_reason} · ${t.predicted_tag_count??"--"} predicted tags · fix ${t.predicted_fix_error_cm??"--"} cm`),
            meta(`${t.trim_progress}/${t.trim_budget} trims · ${t.leg_speed_cm_s??"--"} cm/s · arrival ${t.arrival_error_cm??"--"} cm / tolerance ${t.tolerance_cm??"--"} cm (${t.tolerance_verdict})${t.return_leg?" · RETURN LEG":""}`),
          ]),
        ),
      ),
    );
    nodes.push(
      meta(`Follow-up targets: ${p.target_object_ids.join(", ") || "None"}`),
      ...p.target_object_ids.map((id) =>
        meta(
          `${id}: ${s.objects.find((o) => o.id === id)?.reason ?? "Unavailable"}`,
        ),
      ),
    );
    for (const key of ["consent", "envelope", "preflight", "current"])
      nodes.push(
        meta(
          `${key[0].toUpperCase() + key.slice(1)}: ${p.eligibility[key] === null ? "Unknown" : p.eligibility[key] ? "Eligible" : "Not eligible"}`,
        ),
      );
    nodes.push(
      ...p.disabled_reasons.map((reason) =>
        el("p", { class: "warning" }, [reason]),
      ),
    );
    if (s.flights.used < s.flights.max)
      nodes.push(
        meta(`Proposed flight: ${s.flights.used + 1} of ${s.flights.max}`),
      );
  } else nodes.push(meta("No current proposal"));
  const approveEnabled = newAttemptAllowed(s, state, needsRefresh, "APPROVE");
  const rejectEnabled = newAttemptAllowed(s, state, needsRefresh, "REJECT");
  if (needsRefresh || s?.freshness === "STALE")
    nodes.push(
      el("p", { class: "warning" }, [
        "Current projection required; decisions disabled",
      ]),
    );
  nodes.push(
    el("div", { class: "decision-buttons" }, [
      button("Approve flight", onApprove, {
        id: "approve",
        disabled: !approveEnabled,
      }),
      button("Reject", onReject, { id: "reject", disabled: !rejectEnabled }),
    ]),
    meta(s.mode ? "Decision acknowledgement is not flight completion." : "Fixture acknowledgement is not flight dispatch."),
  );
  if (state.attempt) {
    nodes.push(
      el(
        "div",
        {
          id: "attempt-status",
          role: state.result?.status === "IN_DOUBT" ? "alert" : "status",
        },
        [
          meta(
            `Attempt ${state.attempt.attempt_id} · proposal ${state.attempt.proposal_id}`,
          ),
          meta(`Idempotency key: ${state.attempt.idempotency_key}`),
          el("strong", {}, [
            state.pending ? "PENDING" : (state.result?.status ?? "Unknown"),
          ]),
          meta(state.result?.message ?? "Awaiting fixture result"),
          ...(state.detached
            ? [
                el("p", { class: "warning" }, [
                  "Result belongs to a previous proposal; current proposal is not approved.",
                ]),
              ]
            : []),
        ],
      ),
    );
    if (state.result?.status === "IN_DOUBT")
      nodes.push(
        button("Retry same attempt", onRetry, {
          id: "retry-decision",
          disabled:
            state.pending || state.attempt?.run_id !== s?.run_id,
        }),
      );
  }
  for (const previous of state.history ?? [])
    nodes.push(
      meta(
        `Previous attempt ${previous.attempt.attempt_id} · proposal ${previous.attempt.proposal_id} · ${previous.result?.status ?? "Unknown"}`,
      ),
    );
  replace(host, nodes);
}
