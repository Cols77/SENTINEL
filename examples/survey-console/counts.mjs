import { el, button, replace, meta } from "./dom.mjs";
export function countText(c) {
  if (c.observability === "NOT_OBSERVABLE") return "NOT_OBSERVABLE";
  if (c.semantics === "SINGLE_VIEW_LOWER_BOUND")
    return c.lower_bound === null || c.captured_at === null
      ? "Unknown"
      : `at least ${c.lower_bound}, as of ${c.captured_at}`;
  if (c.semantics !== "RANGE" || c.confirmed === null || c.uncertain === null)
    return "Unknown";
  return `at least ${c.confirmed}, possibly ${c.confirmed + c.uncertain}`;
}
export function renderCounts(host, survey, { onClass, onObject } = {}) {
  const rows = survey.counts.map((c) => {
    const action = button("", () => onClass?.(c.class_name), {
      id: `count-${c.class_name}`,
      class: "count-action",
      "data-class": c.class_name,
    });
    action.append(
      el("strong", {}, [c.class_name]),
      el("span", {}, [countText(c)]),
    );
    const details = el(
      "details",
      { open: c.observability === "NOT_OBSERVABLE" },
      [
        el("summary", {}, ["Observability & support"]),
        meta(`${c.observability} · ${c.semantics}`),
        meta(c.reason),
        meta(
          `${c.sighting_ids.length} supporting sightings · ${c.distinct_stops ?? "—"} distinct stops`,
        ),
      ],
    );
    if (c.projected_px !== null)
      details.append(
        meta(
          `Projected: ${c.projected_px} px · threshold: ${c.min_px ?? "—"} px`,
        ),
      );
    if (c.arithmetic) details.append(meta(c.arithmetic));
    if (c.geometry_degraded)
      details.append(el("p", { class: "warning" }, ["Geometry degraded"]));
    return el("section", { class: "count-row" }, [action, details]);
  });
  const uncertain = survey.objects.filter((o) => o.state === "UNCERTAIN");
  const objects = el(
    "div",
    { class: "objects" },
    survey.objects.map((o) =>
      button(`${o.id} · ${o.state}`, () => onObject?.(o.id), {
        id: `object-${o.id}`,
        "data-object-id": o.id,
        class: o.state === "UNCERTAIN" ? "warning" : "",
      }),
    ),
  );
  const report = el("section", { id: "report-status" }, [
    el("h3", {}, ["Report"]),
    meta(`${survey.report.status}${survey.report.id?` - ${survey.report.id}`:""} - run ${survey.run_id}`),
    ...(survey.proposer?[meta(`Provenance: ${survey.proposer.mode} / ${survey.proposer.source}`)]:[]),
    ...(survey.escalation_reason?[meta(`Escalation: ${survey.escalation_reason}`)]:[]),
  ]);
  if (survey.report.status === "ACCEPTED")
    report.append(
      el("p", { id: "accepted-report" }, [
        survey.report.text ?? "No report text supplied",
      ]),
    );
  else
    report.append(
      meta(
        survey.report.status === "REJECTED"
          ? survey.report.reason
          : "No accepted report",
      ),
    );
  replace(host, [
    el("div", { class: "section-label" }, ["OBSERVATIONS"]),
    el("h2", {}, ["Class counts"]),
    ...rows,
    el("h3", {}, ["Uncertainty & failures"]),
    meta(
      `${uncertain.length} uncertain object${uncertain.length === 1 ? "" : "s"}`,
    ),
    ...uncertain.map((o) => meta(`${o.id}: ${o.reason}`)),
    ...survey.failures.map((f) => el("p", { class: "warning" }, [f])),
    objects,
    report,
  ]);
}
