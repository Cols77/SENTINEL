const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const path = require('node:path');

const suppliedBase = process.argv[2];
let base = suppliedBase;
let server = null;
let dataDir = null;

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function waitFor(check, message, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await delay(100);
  }
  throw new Error(message);
}

async function startHost(scenario = null, mode = 'rehearsal') {
  if (suppliedBase) {
    base = suppliedBase;
    return;
  }
  dataDir = fs.mkdtempSync(path.resolve('artifacts/mission-control-test-'));
  const net = require('node:net');
  const probe = net.createServer();
  await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  base = `http://127.0.0.1:${port}`;
  const python = process.platform === 'win32'
    ? path.resolve('.venv/Scripts/python.exe')
    : path.resolve('.venv/bin/python');
  const args = ['-m', 'sentinel.ui', '--data-dir', dataDir, '--port', String(port)];
  if (mode === 'live') args.push('--mode', 'live');
  if (scenario) args.push('--scenario', path.resolve(scenario));
  server = spawn(python, args, { stdio: 'ignore' });
  await waitFor(async () => {
    try { return (await fetch(`${base}/api/v1/session`)).ok; }
    catch { return false; }
  }, `Test host did not start for ${mode === 'live' ? 'bare Live' : scenario || 'clean scenario'}`);
}

async function stopHost() {
  if (server) {
    const owned = server;
    let exited = owned.exitCode !== null;
    const exit = new Promise(resolve => owned.once('exit', () => { exited = true; resolve(); }));
    if (!exited) owned.kill('SIGTERM');
    await Promise.race([exit, delay(5000)]);
    if (!exited) {
      owned.kill('SIGKILL');
      await Promise.race([exit, delay(5000)]);
    }
    server = null;
  }
  if (dataDir) {
    fs.rmSync(dataDir, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
    dataDir = null;
  }
  if (!suppliedBase) base = null;
}

async function currentSnapshot() {
  const session = await (await fetch(`${base}/api/v1/session`)).json();
  return (await fetch(`${base}/api/v1/runs/${session.active_run_id}/snapshot`)).json();
}

async function requestMission(page) {
  const missionState = page.locator('#mission-state');
  await missionState.waitFor();
  await waitFor(async () => !(await missionState.innerText()).startsWith('Connecting'),
    'Browser did not connect to the mission runtime');
  await page.getByRole('textbox', { name: 'Mission request' }).fill('count the computers');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await waitFor(async () => (await currentSnapshot()).run.request_text === 'count the computers',
    'Mission request did not reach the runtime');
  const approve = page.getByRole('button', { name: 'Approve flight', exact: true });
  await approve.waitFor();
  try {
    await waitFor(() => approve.isEnabled(), 'Flight proposal did not become approvable');
  } catch (error) {
    const snapshot = await currentSnapshot();
    error.message += `: ${JSON.stringify({ run: snapshot.run, proposal: snapshot.proposal, legal_actions: snapshot.legal_actions })}`;
    throw error;
  }
}

async function installFakeSpeech(page) {
  await page.addInitScript(() => {
    globalThis.__speechInstances = [];
    class FakeSpeechRecognition {
      constructor() { globalThis.__speechInstances.push(this); }
      start() { this.started = (this.started || 0) + 1; }
      stop() { this.stopped = (this.stopped || 0) + 1; }
      abort() { this.aborted = (this.aborted || 0) + 1; }
      emit(text, isFinal) {
        this.onresult?.({ results: [{ 0: { transcript: text }, isFinal }] });
      }
      end() { this.onend?.(); }
      fail(error) { this.onerror?.({ error }); }
    }
    globalThis.SpeechRecognition = FakeSpeechRecognition;
  });
}

async function verifyVoiceIntegration(page, chatRequests) {
  const missionState = page.locator('#mission-state');
  await missionState.waitFor();
  await waitFor(async () => !(await missionState.innerText()).startsWith('Connecting'),
    'Browser did not connect before voice input');
  const input = page.getByRole('textbox', { name: 'Mission request' });
  const mic = page.getByRole('button', {
    name: 'Hold to talk. Audio may be processed by the browser speech service.',
  });
  await mic.waitFor();
  assert.equal(await page.evaluate(() => typeof globalThis.SpeechRecognition), 'function');
  await input.fill('preserve this draft');
  await mic.hover();
  await page.mouse.down();
  assert.equal(await page.evaluate(() => globalThis.__speechInstances.length), 1);
  await page.evaluate(() => globalThis.__speechInstances.at(-1).emit('status', false));
  assert.equal(await input.inputValue(), 'status');
  await page.evaluate(() => globalThis.__speechInstances.at(-1).emit('status', true));
  await page.mouse.up();
  await page.evaluate(() => {
    const recognizer = globalThis.__speechInstances.at(-1);
    recognizer.end();
    recognizer.end();
  });
  await waitFor(() => chatRequests.filter(request => request.text === 'status').length === 1,
    'Voice status transcript was not submitted exactly once');
  await waitFor(async () => (await input.inputValue()) === '', 'Voice acknowledgement did not clear the composer');

  const requestCount = chatRequests.length;
  await input.fill('keep after denial');
  await mic.hover();
  await page.mouse.down();
  await page.evaluate(() => globalThis.__speechInstances.at(-1).fail('not-allowed'));
  await page.mouse.up();
  assert.equal(await input.inputValue(), 'keep after denial');
  assert.equal(chatRequests.length, requestCount);

  const componentChecks = await page.evaluate(async () => {
    const { createVoiceView } = await import('/console/voice.mjs');
    const legal = { safety: { land: { available: true } }, legal_actions: [{ action: 'LAND', enabled: true }] };
    const host = document.createElement('div');
    const input = document.createElement('textarea');
    document.body.append(host, input);
    let recognizer;
    let timer;
    let timerCancelled = false;
    let landCalls = 0;
    const chat = [];
    const view = createVoiceView(
      host,
      input,
      () => legal,
      { submit: text => chat.push(text) },
      { request: () => { landCalls += 1; } },
      {
        recognizerFactory: () => (recognizer = { start() {}, stop() {}, abort() {} }),
        clock: {
          schedule(callback, milliseconds) {
            timerCancelled = false;
            timer = () => { if (!timerCancelled) callback(); };
            return milliseconds;
          },
          cancel() { timerCancelled = true; },
        },
      },
    );
    const speak = phrase => {
      view.controller.begin(input.value);
      recognizer.onresult({ results: [{ 0: { transcript: phrase }, isFinal: true }] });
      view.controller.release();
      recognizer.onend();
    };
    speak('Sentinel, land now');
    const countdownVisible = host.textContent.includes('Controlled Land starts in 3 seconds');
    host.querySelector('#voice-cancel').click();
    timer();
    const cancelled = landCalls === 0 && host.textContent.includes('Controlled Land cancelled');
    speak('Sentinel, land now');
    timer();
    const completed = landCalls === 1;
    speak('approve the proposal');
    speak('emergency stop motors');
    const bounded = landCalls === 1 && chat.join('|') === 'approve the proposal|emergency stop motors';
    view.dispose();
    host.remove();
    input.remove();

    const unsupportedHost = document.createElement('div');
    const unsupportedInput = document.createElement('textarea');
    unsupportedInput.value = 'unchanged';
    document.body.append(unsupportedHost, unsupportedInput);
    const unsupported = createVoiceView(
      unsupportedHost,
      unsupportedInput,
      () => legal,
      { submit() { throw new Error('unsupported voice submitted'); } },
      { request() { throw new Error('unsupported voice landed'); } },
      { recognizerFactory: null },
    );
    const unavailable = unsupportedHost.textContent.includes('Voice input unavailable')
      && unsupportedInput.value === 'unchanged';
    unsupported.dispose();
    unsupportedHost.remove();
    unsupportedInput.remove();
    return { countdownVisible, cancelled, completed, bounded, unavailable };
  });
  assert.deepEqual(componentChecks, {
    countdownVisible: true,
    cancelled: true,
    completed: true,
    bounded: true,
    unavailable: true,
  });
}

async function verifyBareLiveScenario(browser, errors, external) {
  await startHost(null, 'live');
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(5000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (!request.url().startsWith(`${base}/`) && !request.url().startsWith('blob:')) external.push(request.url());
  });
  try {
    await page.goto(`${base}/console/`);
    await page.getByText('LIVE · UNARMED · NOT_READY', { exact: true }).first().waitFor();
    const panel = page.locator('#live-readiness');
    assert.equal(await panel.isVisible(), true);
    assert.equal(await panel.locator('.readiness-checks > li').count(), 4);
    assert.match(await panel.locator('.readiness-command').innerText(), /--site <SITE_JSON>/);
    assert.doesNotMatch(await panel.innerText(), /API_KEY=/);

    const input = page.getByRole('textbox', { name: 'Mission request' });
    await input.fill('status');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await page.getByText(/Snapshot \d+: request\./).waitFor();
    await input.fill('count the computers');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await page.getByText(/Planning is unavailable \(LIVE_PLANNING_CONFIG_REQUIRED\)/).waitFor();

    const snapshot = await currentSnapshot();
    assert.equal(snapshot.proposal, null);
    assert.deepEqual(snapshot.mission.route, []);
    assert.equal(snapshot.readiness.state, 'NOT_READY');
    for (const id of ['land-now', 'emergency-stop']) {
      const control = page.locator(`#${id}`);
      assert.equal(await control.isDisabled(), true);
      const describedBy = await control.getAttribute('aria-describedby');
      assert.ok(describedBy);
      assert.ok(await page.locator(`#${describedBy}`).count());
    }
    for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    }
  } finally {
    await page.close();
    await stopHost();
  }
}

async function verifyCleanScenario(browser, errors, external) {
  await startHost();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(5000);
  const chatRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (!request.url().startsWith(`${base}/`) && !request.url().startsWith('blob:')) external.push(request.url());
    if (request.method() === 'POST' && request.url().endsWith('/chat')) {
      try { chatRequests.push(JSON.parse(request.postData())); } catch { /* invalid payload is caught by the host */ }
    }
  });
  await installFakeSpeech(page);
  try {
    await page.goto(`${base}/console/`);
    await verifyVoiceIntegration(page, chatRequests);
    const independentSafety = await page.evaluate(async () => {
      const { createSafetyView } = await import('/console/safety.mjs');
      const host = document.createElement('div');
      document.body.append(host);
      const snapshot = { run: { id: 'r' }, session: { id: 's', mode: 'REHEARSAL' }, safety: { land: { available: true }, emergency: { available: true } } };
      let release;
      const blocked = new Promise(resolve => { release = resolve; });
      const view = createSafetyView(host, { request_land: () => blocked, trigger_emergency: async () => ({ status: 'ACKNOWLEDGED' }) }, () => snapshot);
      view.render(snapshot);
      host.querySelector('#land-now').click();
      view.render(snapshot);
      const available = !host.querySelector('#emergency-stop').disabled;
      release({ status: 'ACKNOWLEDGED' });
      view.dispose();
      host.remove();
      return available;
    });
    assert.equal(independentSafety, true, 'Emergency must remain available during a pending Land request');
    await requestMission(page);
    const proposed = await currentSnapshot();
    assert.equal(proposed.schema_version, 3);
    await page.reload();
    await page.locator('#localization .pose-stale').waitFor({ timeout: 10000 });
    await page.locator('#localization .pose-fresh').waitFor({ timeout: 10000 });
    assert.equal(await page.getByRole('button', { name: 'Approve flight', exact: true }).isEnabled(), true);
    await page.locator('#localization svg polygon').first().waitFor();
    await page.route('**/decisions', async route => { await route.fetch(); await route.abort(); }, { times: 1 });
    await page.getByRole('button', { name: 'Approve flight', exact: true }).click();
    await page.locator('#accepted-report').waitFor({ timeout: 15000 });
    await page.reload();
    await page.locator('#accepted-report').waitFor();
    assert.match(await page.locator('#accepted-report').innerText(), /1 computers/);
    await page.locator('[data-frame-id]').first().click();
    await page.locator('#evidence img').waitFor();
    const snapshot = await currentSnapshot();
    assert.equal(snapshot.results.counts.find(count => count.class_name === 'computer').confirmed, 1);
    assert.equal(snapshot.report.status, 'ACCEPTED');
    assert.equal(snapshot.mission.flights.used, 1);
    assert.ok(snapshot.mission.route.some(stop => stop.coverage === 'OK'));
    assert.ok(snapshot.evidence.frames.every(frame => ['ANALYSED', 'FAILED'].includes(frame.detection_status)));
    assert.equal(snapshot.report.run_id, snapshot.run.id);
    const sightingIds = new Set(snapshot.evidence.sightings.map(sighting => sighting.id));
    const frameIds = new Set(snapshot.evidence.frames.map(frame => frame.id));
    assert.ok(snapshot.results.counts.flatMap(count => count.sighting_ids).every(id => sightingIds.has(id)));
    assert.ok(snapshot.evidence.sightings.every(sighting => frameIds.has(sighting.frame_id)));
    const evidenceLabels = await page.evaluate(async () => {
      const { createEvidenceView } = await import('/console/evidence.mjs');
      const { createHttpClient } = await import('/console/http-client.mjs');
      const { toSurveyView } = await import('/console/view-model.mjs');
      const client = createHttpClient();
      const session = await client.session();
      const survey = await client.read_survey(session.active_run_id);
      const viewSnapshot = toSurveyView(survey);
      viewSnapshot.mode = 'LIVE';
      viewSnapshot.source = 'LIVE';
      viewSnapshot.frames.forEach(frame => { frame.source_kind = 'LIVE'; });
      const host = document.createElement('div');
      document.body.append(host);
      const view = createEvidenceView(host, client);
      await view.select(viewSnapshot, { kind: 'frame', id: viewSnapshot.frames[0].id });
      const result = { alt: host.querySelector('img')?.alt, text: host.textContent };
      view.dispose();
      host.remove();
      return result;
    });
    assert.match(evidenceLabels.alt, /LIVE capture/);
    assert.doesNotMatch(evidenceLabels.text, /Synthetic image only/);
    const poseRevocation = await page.evaluate(async () => {
      const { createLocalizationView } = await import('/console/localization.mjs');
      const { createHttpClient } = await import('/console/http-client.mjs');
      const client = createHttpClient();
      const session = await client.session();
      const survey = await client.read_survey(session.active_run_id);
      const host = document.createElement('div');
      document.body.append(host);
      const canvas = document.createElement('canvas');
      canvas.width = 1;
      canvas.height = 1;
      const bytes = Uint8Array.from(atob(canvas.toDataURL().split(',')[1]), char => char.charCodeAt(0));
      survey.localization.pose_evidence = { artifact_id: 'a', report_id: 'r', width_px: 1, height_px: 1, tags: [] };
      let release;
      const view = createLocalizationView(host, { read_pose_evidence: () => new Promise(resolve => { release = resolve; }) });
      const pending = view.render(survey);
      const revoked = structuredClone(survey);
      revoked.localization.pose_evidence = null;
      await view.render(revoked);
      release({ bytes, media_type: 'image/png' });
      await pending;
      const lateRemoved = !host.querySelector('img');
      const display = view.render(survey);
      release({ bytes, media_type: 'image/png' });
      await display;
      const initiallyVisible = Boolean(host.querySelector('img'));
      await view.render(revoked);
      const removed = !host.querySelector('img');
      view.dispose();
      host.remove();
      return { lateRemoved, initiallyVisible, removed };
    });
    assert.deepEqual(poseRevocation, { lateRemoved: true, initiallyVisible: true, removed: true });

    const emergency = page.locator('#emergency-stop');
    await emergency.focus();
    await page.keyboard.down('Space');
    await page.waitForTimeout(1250);
    await page.keyboard.up('Space');
    await page.getByText('Emergency: ACKNOWLEDGED · simulated transport', { exact: true }).waitFor();
    for (const viewport of [{ width: 1440, height: 900 }, { width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    }
    await page.emulateMedia({ reducedMotion: 'reduce' });
    fs.mkdirSync('artifacts/mission-control', { recursive: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: 'artifacts/mission-control/acceptance.png', fullPage: true });
  } finally {
    await page.close();
    await stopHost();
  }
}

async function verifyUncertainScenario(browser, errors, external) {
  await startHost('fixtures/mission-control/uncertain-followup.json');
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(5000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (!request.url().startsWith(`${base}/`) && !request.url().startsWith('blob:')) external.push(request.url());
  });
  try {
    await page.goto(`${base}/console/`);
    await requestMission(page);
    const first = await currentSnapshot();
    await page.getByRole('button', { name: 'Approve flight', exact: true }).click();
    let latest;
    try {
      await waitFor(async () => {
        latest = await currentSnapshot();
        return latest.proposal
          && latest.proposal.decision_version > first.proposal.decision_version
          && latest.run.status === 'ACTIVE'
          && latest.run.phase === 'APPROVAL';
      }, 'Uncertain scenario did not request an approved follow-up');
    } catch (error) {
      error.message += `: ${JSON.stringify(latest)}`;
      throw error;
    }
    const followUpApprove = page.getByRole('button', { name: 'Approve flight', exact: true });
    await followUpApprove.waitFor({ state: 'visible', timeout: 10000 });
    await waitFor(() => followUpApprove.isEnabled(), 'Follow-up proposal did not become approvable');
    await followUpApprove.click();
    try {
      await page.locator('#accepted-report').waitFor({ timeout: 15000 });
    } catch (error) {
      const snapshot = await currentSnapshot();
      error.message += `: ${JSON.stringify({ snapshot, attempt: await page.locator('#attempt-status').allTextContents() })}`;
      throw error;
    }
    const snapshot = await currentSnapshot();
    assert.equal(snapshot.mission.flights.used, 2);
    assert.equal(snapshot.report.status, 'ACCEPTED');
    assert.ok(snapshot.results.counts.some(count => count.class_name === 'computer'));
  } finally {
    await page.close();
    await stopHost();
  }
}

async function verifyMissingAnalysisScenario(browser, errors, external) {
  await startHost('fixtures/mission-control/missing-analysis.json');
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.setDefaultTimeout(5000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (!request.url().startsWith(`${base}/`) && !request.url().startsWith('blob:')) external.push(request.url());
  });
  try {
    await page.goto(`${base}/console/`);
    await requestMission(page);
    await page.getByRole('button', { name: 'Approve flight', exact: true }).click();
    await page.locator('#accepted-report').waitFor({ timeout: 15000 });
    const snapshot = await currentSnapshot();
    assert.equal(snapshot.report.status, 'ACCEPTED');
    assert.ok(snapshot.evidence.failures.length > 0);
    assert.ok(snapshot.evidence.frames.some(frame => frame.detection_status === 'FAILED'));
    assert.ok(snapshot.evidence.frames.some(frame => frame.detection_status === 'ANALYSED'));
    assert.equal(snapshot.results.counts.find(count => count.class_name === 'computer').semantics, 'UNKNOWN');
  } finally {
    await page.close();
    await stopHost();
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const external = [];
  try {
    await verifyCleanScenario(browser, errors, external);
    if (!suppliedBase) {
      await verifyBareLiveScenario(browser, errors, external);
      await verifyUncertainScenario(browser, errors, external);
      await verifyMissingAnalysisScenario(browser, errors, external);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('PASS real-host clean, bare-Live readiness, uncertain-follow-up and missing-analysis scenarios; voice, safety, evidence, schema v3, three viewports and local-only traffic');
  } finally {
    await browser.close();
    await stopHost();
  }
})().catch(async error => {
  console.error(error);
  await stopHost();
  process.exitCode = 1;
});
