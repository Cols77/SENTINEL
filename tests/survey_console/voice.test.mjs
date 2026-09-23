import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createVoiceCommandController,
  cancelOnLostCapture,
  deriveRecognitionLanguage,
  isExactLandPhrase,
} from '../../examples/survey-console/voice.mjs';

function recognizerHarness() {
  const recognizer = {
    started: 0,
    stopped: 0,
    start() { this.started += 1; },
    stop() { this.stopped += 1; },
  };
  return recognizer;
}

test('voice derives English regional recognition language and exact phrase normalization', () => {
  assert.equal(deriveRecognitionLanguage('en-AU'), 'en-AU');
  assert.equal(deriveRecognitionLanguage('en-GB'), 'en-GB');
  assert.equal(deriveRecognitionLanguage('fr-FR'), 'en-US');
  assert.equal(deriveRecognitionLanguage('en', 'en-AU'), 'en-AU');
  assert.equal(deriveRecognitionLanguage('fr-FR', 'en-GB'), 'en-GB');
  assert.equal(isExactLandPhrase('  SENTINEL, land now! '), true);
  assert.equal(isExactLandPhrase('Sentinel land now please'), false);
  assert.equal(isExactLandPhrase('Sentinel land now 🚁'), false);
});

test('voice hold release submits one final transcript and interim text', () => {
  const recognizer = recognizerHarness();
  const interim = [];
  const submitted = [];
  const controller = createVoiceCommandController({
    recognizerFactory: () => recognizer,
    onInterim: text => interim.push(text),
    onSubmit: text => submitted.push(text),
    locale: 'en-AU',
  });
  controller.begin('typed text');
  recognizer.onresult({results: [{0: {transcript: 'status now'}, isFinal: false}]});
  recognizer.onresult({results: [{0: {transcript: 'status now'}, isFinal: true}]});
  controller.release();
  recognizer.onend();
  recognizer.onend();
  assert.equal(recognizer.lang, 'en-AU');
  assert.deepEqual(interim, ['status now']);
  assert.deepEqual(submitted, ['status now']);
  assert.equal(recognizer.started, 1);
  assert.equal(recognizer.stopped, 1);
});

test('release waits for a late final result until recognition ends, exactly once', () => {
  const recognizer = recognizerHarness();
  const submitted = [];
  const listening = [];
  const controller = createVoiceCommandController({
    recognizerFactory: () => recognizer,
    onSubmit: text => submitted.push(text),
    onListeningChange: value => listening.push(value),
  });
  controller.begin();
  controller.release();
  recognizer.onresult({results: [{0: {transcript: 'status now'}, isFinal: true}]});
  recognizer.onend();
  recognizer.onend();
  controller.release();
  assert.deepEqual(submitted, ['status now']);
  assert.deepEqual(listening, [true, false]);
});

test('recognition ending before release waits for release and finalizes once', () => {
  const recognizer = recognizerHarness();
  const submitted = [];
  const controller = createVoiceCommandController({recognizerFactory: () => recognizer, onSubmit: text => submitted.push(text)});
  controller.begin();
  recognizer.onend();
  recognizer.onresult({results: [{0: {transcript: 'status now'}, isFinal: true}]});
  controller.release();
  recognizer.onend();
  assert.deepEqual(submitted, ['status now']);
});

test('cancel is distinct from release and suppresses a final result', () => {
  for (const cancel of ['pointercancel', 'lostpointercapture', 'escape']) {
    const recognizer = recognizerHarness();
    const submitted = [];
    const listening = [];
    const controller = createVoiceCommandController({
      recognizerFactory: () => recognizer,
      onSubmit: text => submitted.push(text),
      onListeningChange: value => listening.push(value),
    });
    controller.begin('keep this');
    recognizer.onresult({results: [{0: {transcript: 'status now'}, isFinal: true}]});
    controller.cancel();
    recognizer.onend();
    assert.deepEqual(submitted, [], cancel);
    assert.equal(controller.isHeld, false, cancel);
    assert.deepEqual(listening, [true, false], cancel);
  }
});

test('voice cancellation, denial, errors and no final preserve text without submission', () => {
  for (const outcome of ['cancel', 'denial', 'error', 'no-final']) {
    const recognizer = recognizerHarness();
    const submitted = [];
    const restored = [];
    const controller = createVoiceCommandController({
      recognizerFactory: () => recognizer,
      onSubmit: text => submitted.push(text),
      onText: text => restored.push(text),
    });
    controller.begin('keep this');
    if (outcome === 'denial') recognizer.onerror({error: 'not-allowed'});
    else if (outcome === 'error') recognizer.onerror({error: 'network'});
    else if (outcome === 'cancel') controller.cancel();
    else { controller.release(); recognizer.onend(); }
    assert.deepEqual(submitted, [], outcome);
    assert.equal(restored.at(-1), 'keep this', outcome);
  }
});

test('exact land starts a cancellable countdown and executes once only when legal', () => {
  let callback;
  let cancelled = 0;
  let landCalls = 0;
  const clock = {
    schedule(fn, ms) { assert.equal(ms, 3000); callback = fn; return 1; },
    cancel() { cancelled += 1; },
  };
  const legal = () => ({
    safety: {land: {available: true}},
    legal_actions: [{action: 'LAND', enabled: true}],
  });
  const recognizer = recognizerHarness();
  const controller = createVoiceCommandController({
    recognizerFactory: () => recognizer,
    onLand: () => { landCalls += 1; },
    getSnapshot: legal,
    clock,
  });
  controller.begin();
  recognizer.onresult({results: [{0: {transcript: 'Sentinel, land now'}, isFinal: true}]});
  controller.release();
  recognizer.onend();
  assert.equal(landCalls, 0);
  callback(); callback();
  assert.equal(landCalls, 1);
  controller.cancelCountdown();
  assert.equal(cancelled, 0);
});

test('lost capture after normal release cannot cancel the valid Land countdown', () => {
  let callback;
  let landCalls = 0;
  const recognizer = recognizerHarness();
  const controller = createVoiceCommandController({
    recognizerFactory: () => recognizer,
    getSnapshot: () => ({safety: {land: {available: true}}, legal_actions: [{action: 'LAND', enabled: true}]}),
    onLand: () => { landCalls += 1; },
    clock: {schedule: fn => { callback = fn; return 1; }, cancel() {}},
  });
  controller.begin();
  recognizer.onresult({results: [{0: {transcript: 'Sentinel land now'}, isFinal: true}]});
  controller.release();
  recognizer.onend();
  // This is the view's lostpointercapture path after pointerup: no held gesture remains.
  if (controller.isHeld) controller.cancel();
  callback();
  assert.equal(landCalls, 1);
});

test('lost capture cancels only a still-held gesture', () => {
  let cancels = 0;
  const held = {isHeld: true, cancel: () => { cancels += 1; }};
  const released = {isHeld: false, cancel: () => { cancels += 1; }};
  cancelOnLostCapture(held);
  cancelOnLostCapture(released);
  assert.equal(cancels, 1);
});

test('cancel after release invalidates late recognition and restores the composer', () => {
  const recognizer = recognizerHarness();
  const submitted = [];
  const text = [];
  const controller = createVoiceCommandController({
    recognizerFactory: () => recognizer,
    onSubmit: value => submitted.push(value),
    onText: value => text.push(value),
  });
  controller.begin('keep this');
  controller.release();
  controller.cancel();
  recognizer.onresult({results: [{0: {transcript: 'late command'}, isFinal: true}]});
  recognizer.onend();
  assert.deepEqual(submitted, []);
  assert.equal(text.at(-1), 'keep this');
});

test('dispose after release invalidates late recognition and never submits', () => {
  const recognizer = recognizerHarness();
  const submitted = [];
  const controller = createVoiceCommandController({
    recognizerFactory: () => recognizer,
    onSubmit: value => submitted.push(value),
  });
  controller.begin();
  controller.release();
  controller.dispose();
  recognizer.onresult({results: [{0: {transcript: 'late command'}, isFinal: true}]});
  recognizer.onend();
  assert.deepEqual(submitted, []);
});

test('exact land is refused safely when legality changes before execution', () => {
  let callback;
  let landCalls = 0;
  const snapshot = {safety: {land: {available: true}}, legal_actions: [{action: 'LAND', enabled: true}]};
  const controller = createVoiceCommandController({
    recognizerFactory: recognizerHarness,
    getSnapshot: () => snapshot,
    onLand: () => { landCalls += 1; },
    clock: {schedule: fn => { callback = fn; return 1; }, cancel() {}},
  });
  controller.begin();
  controller.recognizer.onresult({results: [{0: {transcript: 'Sentinel land now'}, isFinal: true}]});
  controller.release();
  controller.recognizer.onend();
  snapshot.legal_actions[0].enabled = false;
  callback();
  assert.equal(landCalls, 0);
});

test('approval, emergency and other non-exact phrases use chat only', () => {
  const recognizer = recognizerHarness();
  const chat = [];
  const land = [];
  const controller = createVoiceCommandController({
    recognizerFactory: () => recognizer,
    onSubmit: text => chat.push(text),
    onLand: () => land.push('land'),
  });
  for (const phrase of ['approve the mission', 'emergency stop motors', 'Sentinel land now please']) {
    controller.begin();
    recognizer.onresult({results: [{0: {transcript: phrase}, isFinal: true}]});
    controller.release();
    recognizer.onend();
  }
  assert.deepEqual(chat, ['approve the mission', 'emergency stop motors', 'Sentinel land now please']);
  assert.deepEqual(land, []);
});
