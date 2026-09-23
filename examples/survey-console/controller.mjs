import { validateSurvey, validateEvent } from "./contract.mjs";
const blank = () => ({
  survey: null,
  events: [],
  needs_refresh: true,
  log_gaps: [],
  error: null,
});
export function createController(client, contract = {}) {
  const validate = contract.validate ?? validateSurvey;
  const validateRow = contract.validateEvent ?? validateEvent;
  const run = contract.run ?? (s => s?.run_id);
  const version = contract.version ?? (s => s?.version);
  const watermark = contract.watermark ?? (s => s?.event_sequence ?? 0);
  let state = blank(),
    epoch = 0,
    runId = null,
    readTicket = 0,
    lastEmitted = "";
  const listeners = new Set();
  const getState = () => structuredClone(state);
  function emit() {
    const key = JSON.stringify(state);
    if (key === lastEmitted) return;
    lastEmitted = key;
    for (const fn of listeners) fn(getState());
  }
  function fail(e) {
    state = { ...state, needs_refresh: true, error: e.message };
    emit();
  }
  function reconcileLog() {
    const present = new Set(state.events.map((e) => e.sequence));
    const high = Math.max(watermark(state.survey), ...present, 0);
    const gaps = [];
    // Report at most 200 gaps; pagination still starts at the first missing entry.
    for (let i = 1; i <= high && gaps.length < 200; i++)
      if (!present.has(i)) gaps.push(i);
    state.log_gaps = gaps;
  }
  function merge(rows) {
    if (!Array.isArray(rows)) throw new Error("Invalid event page");
    const bySequence = new Map(state.events.map((e) => [e.sequence, e]));
    const byId = new Map(state.events.map((e) => [e.id, e]));
    for (const e of rows) {
      if (e.run_id !== runId) continue;
      validateRow(e);
      for (const prior of [bySequence.get(e.sequence), byId.get(e.id)])
        if (prior && JSON.stringify(prior) !== JSON.stringify(e))
          throw new Error("Conflicting event identity");
      bySequence.set(e.sequence, structuredClone(e));
      byId.set(e.id, e);
    }
    state.events = [...bySequence.values()].sort(
      (a, b) => a.sequence - b.sequence,
    );
    if (
      Math.max(0, ...state.events.map((e) => e.sequence)) >
      (watermark(state.survey))
    )
      state.needs_refresh = true;
    reconcileLog();
  }
  async function refresh() {
    if (!runId) return;
    const thisEpoch = epoch,
      ticket = ++readTicket,
      id = runId;
    const active = () => thisEpoch === epoch && ticket === readTicket;
    try {
      const candidate = await client.read_survey(id);
      if (!active()) return;
      if (run(candidate) !== id) throw new Error("Snapshot run mismatch");
      validate(candidate);
      const prior = state.survey;
      if (
        prior &&
        version(candidate) === version(prior) &&
        JSON.stringify(candidate) !== JSON.stringify(prior)
      )
        throw new Error("Changed payload at same snapshot version");
      if (!prior || version(candidate) > version(prior)) {
        if (prior && watermark(candidate) < watermark(prior))
          throw new Error("Snapshot watermark regressed");
        state = {
          ...state,
          survey: structuredClone(candidate),
          error: null,
          needs_refresh: state.events.some(
            (e) => e.sequence > watermark(candidate),
          ),
        };
      }
      reconcileLog();
      emit();
      let cursor = state.log_gaps.length
        ? state.log_gaps[0] - 1
        : Math.max(0, ...state.events.map((e) => e.sequence));
      for (let page = 0; page < 1000; page++) {
        const rows = await client.read_survey_events(id, cursor, 100);
        if (!active()) return;
        if (!Array.isArray(rows) || rows.length > 100)
          throw new Error("Invalid event page");
        let next = cursor;
        for (const e of rows) {
          validateRow(e);
          if (e.run_id !== id || e.sequence <= next)
            throw new Error("Event page made no cursor progress");
          next = e.sequence;
        }
        merge(rows);
        emit();
        if (rows.length < 100) return;
        cursor = next;
      }
      throw new Error("Event pagination did not finish");
    } catch (e) {
      if (active()) fail(e);
    }
  }
  return {
    getState,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    async open(id) {
      epoch++;
      runId = id;
      state = blank();
      emit();
      await refresh();
    },
    refresh,
    async ingest(rows) {
      try {
        merge(rows);
        emit();
      } catch (e) {
        fail(e);
      }
    },
  };
}
