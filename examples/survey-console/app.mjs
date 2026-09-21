import { makeScenario, scenarioIds } from "./fixtures.mjs";
import { createFixtureClient } from "./fixture-client.mjs";
import { createController } from "./controller.mjs";
import { renderShell } from "./shell.mjs";
import { renderCounts } from "./counts.mjs";
import { createEvidenceView, supportingSightings } from "./evidence.mjs";
import { createDecisionController, renderApproval } from "./approval.mjs";
import { el, button, meta } from "./dom.mjs";
const root = document.getElementById("app");
let unsubscribe = () => {},
  unsubscribeDecision = () => {},
  currentIndex = 0,
  scenario,
  client,
  controller,
  decision,
  evidence = null,
  selection = null,
  lastVersion = null,
  appEpoch = 0;
function select(kind, id) {
  selection = { kind, id };
  updateSelection(controller.getState().survey, true);
}
function updateSelection(s, load) {
  if (!s || !selection) return;
  const ids =
    selection.kind === "frame"
      ? [selection.id]
      : supportingSightings(s, selection).map((x) => x.frame_id);
  for (const b of root.querySelectorAll("[data-frame-id]"))
    b.setAttribute("aria-pressed", String(ids.includes(b.dataset.frameId)));
  if (load) evidence.select(s, selection);
}
const handlers = {
  onFrame: (id) => select("frame", id),
  onClass: (id) => select("class", id),
  onObject: (id) => select("object", id),
};
const decisionHandlers = {
  onApprove: () => decision.submit("APPROVE"),
  onReject: () => decision.submit("REJECT"),
  onRetry: () => decision.retry(),
};
function renderDecision() {
  const state = controller.getState();
  if (state.survey)
    renderApproval(
      document.getElementById("approval"),
      state.survey,
      decision.getState(),
      decisionHandlers,
      state.needs_refresh,
    );
  const resolve = document.getElementById("resolve-fixture-decision");
  if (resolve) resolve.disabled = !decision.getState().pending;
}
function controls() {
  const host = document.getElementById("reviewer-controls");
  if (!host.querySelector("#scenario")) {
    const picker = el(
      "select",
      { id: "scenario" },
      scenarioIds.map((id) =>
        el("option", { value: id }, [makeScenario(id).title]),
      ),
    );
    picker.addEventListener("change", () => open(picker.value));
    const results = el(
      "select",
      { id: "fixture-decision-result" },
      ["ACKNOWLEDGED", "REJECTED", "CONFLICT", "IN_DOUBT"].map((value) =>
        el("option", { value }, [value]),
      ),
    );
    host.append(
      el("div", { class: "section-label" }, ["REVIEWER CONTROLS"]),
      el("label", { for: "scenario" }, ["Fixture scenario"]),
      picker,
      button(
        "Next fixture snapshot",
        async () => {
          const active = appEpoch,
            c = controller;
          client.advance();
          currentIndex++;
          await c.refresh();
          if (active === appEpoch) controls();
        },
        { id: "next-snapshot" },
      ),
      el("label", { for: "fixture-decision-result" }, [
        "Fixture submission result",
      ]),
      results,
      button(
        "Resolve fixture submission",
        () => client.resolveDecision(results.value),
        { id: "resolve-fixture-decision", disabled: true },
      ),
      meta(
        "Advances supplied snapshots only. No drone or provider connection.",
      ),
    );
  }
  host.querySelector("#scenario").value = scenario.id;
  host.querySelector("#next-snapshot").disabled =
    currentIndex >= scenario.snapshots.length - 1;
  host.querySelector("#resolve-fixture-decision").disabled =
    !decision?.getState().pending;
}
async function open(id) {
  const active = ++appEpoch;
  unsubscribe();
  unsubscribeDecision();
  evidence?.dispose();
  evidence = null;
  selection = null;
  lastVersion = null;
  scenario = makeScenario(id);
  currentIndex = 0;
  client = createFixtureClient(scenario);
  controller = createController(client);
  decision = createDecisionController(client);
  const localController = controller,
    handledConflicts = new Set();
  unsubscribeDecision = decision.subscribe(async (ds) => {
    if (active !== appEpoch) return;
    renderDecision();
    if (
      ds.result?.status === "CONFLICT" &&
      !handledConflicts.has(ds.attempt.attempt_id)
    ) {
      handledConflicts.add(ds.attempt.attempt_id);
      await localController.refresh();
      if (active === appEpoch)
        document
          .getElementById("decision-heading")
          ?.focus({ preventScroll: true });
    }
  });
  unsubscribe = controller.subscribe((state) => {
    if (active !== appEpoch) return;
    renderShell(root, state, handlers);
    if (!evidence)
      evidence = createEvidenceView(
        document.getElementById("evidence"),
        client,
      );
    decision.update(state.survey, state.needs_refresh);
    if (state.survey) {
      renderCounts(document.getElementById("counts"), state.survey, handlers);
      updateSelection(state.survey, lastVersion !== state.survey.version);
      lastVersion = state.survey.version;
      renderDecision();
    }
    controls();
  });
  await localController.open(scenario.snapshots[0].run_id);
}
await open("computers-followup");
