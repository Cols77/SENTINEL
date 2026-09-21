const { chromium } = require("playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs"),
  path = require("node:path");
const base = process.argv[2] || "http://127.0.0.1:8770";
const output = path.resolve(__dirname, "../../artifacts/survey-console");
async function main() {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  page.setDefaultTimeout(5000);
  const errors = [],
    external = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (!r.url().startsWith(base + "/") && !r.url().startsWith("blob:"))
      external.push(r.url());
  });
  try {
    await page.goto(base + "/examples/survey-console/");
    await page.locator("#scenario").waitFor();
    assert.equal(await page.locator("[data-testid]").count(), 5);
    assert.match(
      await page.locator('[data-testid="Header"]').innerText(),
      /FIXTURE DEMO/,
    );
    assert.equal(
      await page.locator("#emergency-disconnected").isDisabled(),
      true,
    );
    assert.equal(
      await page.locator("#legacy-view").getAttribute("href"),
      "../../assets/sentinel-ui/console.html",
    );
    assert.equal(
      await page.locator("#request input,#request textarea").count(),
      0,
    );
    const before = await page.locator("#run-stepper").innerText();
    await page.waitForTimeout(100);
    assert.equal(await page.locator("#run-stepper").innerText(), before);
    await page.locator("#next-snapshot").click();
    assert.equal(
      await page.locator('[aria-current="step"]').innerText(),
      "PROPOSAL",
    );
    assert.equal(await page.locator("[data-stop-id]").count(), 3);
    await page.locator("#next-snapshot").focus();
    await page.keyboard.press("Enter");
    assert.equal(
      await page.locator('[aria-current="step"]').innerText(),
      "APPROVAL",
    );
    await page.keyboard.press("Space");
    assert.equal(
      await page.locator('[aria-current="step"]').innerText(),
      "FLIGHT",
    );
    await page.emulateMedia({ reducedMotion: "reduce" });
    assert.equal(
      await page
        .locator("[data-stop-id]")
        .first()
        .evaluate((e) => getComputedStyle(e).animationName),
      "none",
    );
    const boxes = await page.locator("[data-testid]").evaluateAll((es) =>
      es.map((e) => {
        const r = e.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      }),
    );
    assert.ok(
      boxes.every(
        (r) =>
          r.x >= 0 &&
          r.y >= 0 &&
          r.w > 0 &&
          r.h > 0 &&
          r.x + r.w <= 1441 &&
          r.y + r.h <= 901,
      ),
    );
    await page.screenshot({ path: path.join(output, "console-1440.png") });
    await page.selectOption("#scenario", "computers-followup");
    for (let i = 0; i < 5; i++) await page.locator("#next-snapshot").click();
    assert.match(
      await page.locator("#count-computer").innerText(),
      /at least 5, possibly 6/,
    );
    assert.match(await page.locator("#counts").innerText(), /3 px < 12 px/);
    await page.selectOption("#scenario", "people-lower-bound");
    assert.match(
      await page.locator("#count-person").innerText(),
      /at least 3, as of 2026-09-21T00:00:04Z/,
    );
    assert.equal(await page.locator('[data-object-id^="person"]').count(), 0);
    await page.selectOption("#scenario", "phone-closer");
    assert.match(
      await page.locator("#count-phone").innerText(),
      /NOT_OBSERVABLE/,
    );
    for (let i = 0; i < 3; i++) await page.locator("#next-snapshot").click();
    assert.match(await page.locator("#count-phone").innerText(), /possibly 1/);
    await page.selectOption("#scenario", "provider-timeout");
    assert.match(
      await page.locator("#progress").innerText(),
      /Unknown labels: macbook/,
    );
    await page.selectOption("#scenario", "report-rejected");
    await page.locator("#next-snapshot").click();
    assert.match(await page.locator("#report-status").innerText(), /REJECTED/);
    assert.equal(await page.locator("#accepted-report").count(), 0);
    await page.selectOption("#scenario", "computers-followup");
    for (let i = 0; i < 5; i++) await page.locator("#next-snapshot").click();
    await page.locator("#object-computer-6").click();
    await page.locator("#evidence img").waitFor();
    assert.deepEqual(
      await page
        .locator('.frame[aria-pressed="true"]')
        .evaluateAll((es) => es.map((e) => e.dataset.frameId)),
      ["f3"],
    );
    assert.equal(await page.locator("#evidence [data-sighting-id]").count(), 1);
    assert.match(
      await page.locator("#evidence").innerText(),
      /not calibrated probability/,
    );
    const evidenceResults = await page.evaluate(async () => {
      const { createEvidenceView } = await import(
        "/examples/survey-console/evidence.mjs"
      );
      const { makeScenario } = await import(
        "/examples/survey-console/fixtures.mjs"
      );
      const { createFixtureClient } = await import(
        "/examples/survey-console/fixture-client.mjs"
      );
      const a = makeScenario("people-lower-bound").snapshots[0],
        b = makeScenario("provider-timeout").snapshots[0];
      const host = document.createElement("div");
      document.body.append(host);
      let finishA, finishB;
      const view = createEvidenceView(host, {
        read_evidence: (id) =>
          new Promise((r) => {
            if (id === a.run_id) finishA = r;
            else finishB = r;
          }),
      });
      const pa = view.select(a, { kind: "frame", id: "f1" }),
        pb = view.select(b, { kind: "frame", id: "f1" });
      const bytes = await createFixtureClient(
        makeScenario("people-lower-bound"),
      ).read_evidence(a.run_id, "f1");
      finishB({ ...bytes, run_id: b.run_id });
      await pb;
      finishA(bytes);
      await pa;
      const race =
        host.textContent.includes(b.run_id) &&
        !host.textContent.includes(a.run_id);
      b.frames[0].evidence = "REVOKED";
      await view.select(b, { kind: "frame", id: "f1" });
      const revoked =
        host.querySelectorAll("img").length === 0 &&
        host.textContent.includes("REVOKED");
      view.dispose();
      const failures = [];
      for (const result of [
        { ...bytes, media_type: "image/svg+xml" },
        { ...bytes, bytes: new Uint8Array(10485761) },
        { ...bytes, width: 22 },
        {
          status: "FORBIDDEN",
          run_id: a.run_id,
          frame_id: "f1",
          reason: "Denied",
        },
        {
          status: "NOT_FOUND",
          run_id: a.run_id,
          frame_id: "f1",
          reason: "Missing",
        },
      ]) {
        const v = createEvidenceView(host, {
          read_evidence: async () => result,
        });
        await v.select(a, { kind: "frame", id: "f1" });
        failures.push(host.querySelectorAll("img").length === 0);
        v.dispose();
      }
      host.remove();
      return { race, revoked, failures };
    });
    assert.equal(evidenceResults.race, true);
    assert.equal(evidenceResults.revoked, true);
    assert.ok(evidenceResults.failures.every(Boolean));
    await page.selectOption("#scenario", "computers-followup");
    for (let i = 0; i < 5; i++) await page.locator("#next-snapshot").click();
    const approval = await page.locator("#approval").innerText();
    for (const expected of [
      "p2",
      "illustrative-hash-p2",
      "close-rail",
      "1 m range",
      "18 s",
      "computer-6",
      "Consent",
      "Envelope",
      "Preflight",
      "None reported",
    ])
      assert.ok(approval.includes(expected), expected);
    const budget = await page.locator("#flight-budget").innerText();
    await page.locator("#approve").click();
    assert.equal(await page.locator("#approve").isDisabled(), true);
    assert.equal(await page.locator("#reject").isDisabled(), true);
    await page.selectOption("#fixture-decision-result", "ACKNOWLEDGED");
    await page.locator("#resolve-fixture-decision").click();
    assert.match(await page.locator("#approval").innerText(), /ACKNOWLEDGED/);
    assert.equal(await page.locator("#approve").isDisabled(), true);
    assert.equal(await page.locator("#flight-budget").innerText(), budget);
    const scenarioChecks = {
      "computers-followup": [
        /at least 5, possibly 6/,
        /computer-6/,
        /Flights used: 1 of 2/,
      ],
      "people-lower-bound": [/at least 3, as of 2026-09-21T00:00:04Z/],
      "phone-closer": [/at least 0, possibly 1/, /Flights used: 2 of 2/],
      "consent-required": [
        /Consent required/,
        /FORBIDDEN/,
        /No frame retained/,
      ],
      aborted: [
        /Flights used: 0 of 2/,
        /ABORTED/,
        /Not merged/,
        /Excluded from merge/,
      ],
      "provider-timeout": [
        /1\/3 analysed/,
        /Provider timeout/,
        /PENDING/,
        /macbook/,
        /background/,
      ],
      "report-rejected": [/REJECTED/, /at least 5, possibly 6/],
      "source-stale": [/REPLAY/, /STALE/, /Current projection required/],
    };
    for (const [id, patterns] of Object.entries(scenarioChecks)) {
      await page.selectOption("#scenario", id);
      for (
        let i = 0;
        i < 20 && (await page.locator("#next-snapshot").isEnabled());
        i++
      )
        await page.locator("#next-snapshot").click();
      assert.equal(await page.locator("#next-snapshot").isDisabled(), true);
      const text = await page.locator("body").innerText();
      for (const pattern of patterns) assert.match(text, pattern, id);
      if (
        [
          "consent-required",
          "aborted",
          "provider-timeout",
          "report-rejected",
          "source-stale",
        ].includes(id)
      )
        assert.equal(await page.locator("#approve").isDisabled(), true);
      await page.locator(".pane").evaluateAll(es => es.forEach(e => { e.scrollTop = 0; }));
      await page.screenshot({ path: path.join(output, id + ".png") });
    }
    await page.selectOption("#scenario", "source-stale");
    assert.match(await page.locator("#run-badges").innerText(), /LIVE/);
    const sameBudget = await page.locator("#flight-budget").innerText();
    await page.locator("#next-snapshot").click();
    assert.match(await page.locator("#run-badges").innerText(), /REPLAY/);
    assert.equal(await page.locator("#flight-budget").innerText(), sameBudget);
    await page.locator("#next-snapshot").click();
    assert.match(await page.locator("#run-badges").innerText(), /STALE/);
    // A pending submission cannot leak into a replacement scenario.
    await page.selectOption("#scenario", "computers-followup");
    await page.locator("#next-snapshot").click();
    await page.locator("#approve").click();
    await page.selectOption("#scenario", "consent-required");
    assert.equal(await page.locator("#attempt-status").count(), 0);
    // IN_DOUBT and CONFLICT remain explicit and do not dispatch.
    for (const status of ["IN_DOUBT", "CONFLICT"]) {
      await page.selectOption("#scenario", "computers-followup");
      await page.locator("#next-snapshot").click();
      await page.locator("#approve").click();
      await page.selectOption("#fixture-decision-result", status);
      await page.locator("#resolve-fixture-decision").click();
      assert.match(
        await page.locator("#attempt-status").innerText(),
        new RegExp(status),
      );
      assert.equal(await page.locator("#approve").isDisabled(), true);
      if (status === "IN_DOUBT") {
        const old = await page.locator("#attempt-status").innerText();
        await page.locator("#retry-decision").click();
        assert.equal(await page.locator("#attempt-status").innerText(), old);
      }
    }
    const type = await page.evaluate(() => ({
      body: getComputedStyle(document.body).fontSize,
      meta: getComputedStyle(document.querySelector(".meta")).fontSize,
      logo: document.querySelector("#identity img").width,
    }));
    assert.deepEqual(type, { body: "16px", meta: "14px", logo: 40 });
    // Capability reads time out without leaving a stale image behind.
    const timeout = await page.evaluate(async () => {
      const { createEvidenceView } = await import(
        "/examples/survey-console/evidence.mjs"
      );
      const { makeScenario } = await import(
        "/examples/survey-console/fixtures.mjs"
      );
      const h = document.createElement("div");
      document.body.append(h);
      const v = createEvidenceView(h, {
        read_evidence: () => new Promise(() => {}),
      });
      await v.select(makeScenario("people-lower-bound").snapshots[0], {
        kind: "frame",
        id: "f1",
      });
      const ok =
        h.textContent.includes("timed out") &&
        h.querySelectorAll("img").length === 0;
      v.dispose();
      h.remove();
      return ok;
    });
    assert.equal(timeout, true);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.evaluate(async () => {
      const { renderShell } = await import(
        "/examples/survey-console/shell.mjs"
      );
      const { makeScenario } = await import(
        "/examples/survey-console/fixtures.mjs"
      );
      const s = makeScenario("computers-followup").snapshots[0];
      s.request_text = "<img src=x onerror=alert(1)>";
      renderShell(
        document.getElementById("app"),
        {
          survey: s,
          events: [],
          needs_refresh: false,
          log_gaps: [],
          error: null,
        },
        {},
      );
    });
    assert.match(
      await page.locator("#request").innerText(),
      /<img src=x onerror=alert\(1\)>/,
    );
    assert.equal(await page.locator("#request img").count(), 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    fs.writeFileSync(
      path.join(output, "validation.json"),
      JSON.stringify(
        {
          status: "passed",
          stage: "all-eight-scenarios",
          scenario_count: 8,
          node_test_command: "node --test tests/survey_console/*.test.mjs",
          errors,
          external,
        },
        null,
        2,
      ),
    );
    console.log(
      "Survey console browser acceptance passed: 8 scenarios, evidence races, decision lifecycle, keyboard and responsive layout.",
    );
  } finally {
    await browser.close();
  }
}
main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
