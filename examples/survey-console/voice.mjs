import { el, button } from './dom.mjs';
import { isLandLegal } from './safety.mjs';

const speechCtor = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;

export function deriveRecognitionLanguage(
  locale = globalThis.document?.documentElement?.lang,
  browserLocale = globalThis.navigator?.language,
) {
  for (const candidate of [locale, browserLocale]) {
    const match = String(candidate || '').match(/^en[-_]([A-Za-z]{2,3}|\d{3})(?:-|$)/i);
    if (match) return `en-${match[1].toUpperCase()}`;
  }
  return 'en-US';
}

export function normalizeVoicePhrase(value) {
  return String(value ?? '')
    .toLowerCase()
    .trim()
    .replace(/[\p{P}]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function isExactLandPhrase(value) {
  return normalizeVoicePhrase(value) === 'sentinel land now';
}

export function cancelOnLostCapture(controller) {
  if (controller.isHeld) controller.cancel();
}

function transcriptFromResult(result) {
  return String(result?.[0]?.transcript ?? result?.transcript ?? '').trim();
}

export function createVoiceCommandController({
  recognizerFactory = speechCtor ? () => new speechCtor() : null,
  onSubmit = () => {},
  onText = () => {},
  onInterim = () => {},
  onStatus = () => {},
  onListeningChange = () => {},
  onLand = () => {},
  getSnapshot = () => null,
  locale = globalThis.document?.documentElement?.lang,
  browserLocale = globalThis.navigator?.language,
  clock = { schedule: setTimeout, cancel: clearTimeout },
} = {}) {
  let recognizer = null;
  let held = false;
  let active = false;
  let ended = false;
  let released = false;
  let finished = false;
  let failed = false;
  let originalText = '';
  let finalText = '';
  let countdown = null;
  let landTriggered = false;
  let disposed = false;

  const setHeld = value => {
    if (held === value) return;
    held = value;
    onListeningChange(value);
  };

  const restore = () => onText(originalText);
  const clearCountdown = () => {
    if (countdown !== null) clock.cancel(countdown);
    countdown = null;
  };
  const refuseLand = () => {
    clearCountdown();
    restore();
    onStatus('Controlled Land unavailable: current safety state does not permit LAND.');
  };
  const executeLand = () => {
    countdown = null;
    if (landTriggered) return;
    if (disposed || !isLandLegal(getSnapshot())) return refuseLand();
    landTriggered = true;
    onStatus('Controlled Land executing.');
    onLand();
  };
  const beginCountdown = () => {
    if (!isLandLegal(getSnapshot())) return refuseLand();
    onStatus('Controlled Land starts in 3 seconds. Press Escape or Cancel to abort.');
    countdown = clock.schedule(executeLand, 3000);
  };
  const finish = () => {
    if (finished) return;
    finished = true;
    active = false;
    setHeld(false);
    if (failed || !finalText) return restore();
    restore();
    if (isExactLandPhrase(finalText)) return beginCountdown();
    onText(finalText);
    onSubmit(finalText);
  };
  const setResult = event => {
    if (finished || failed || !active) return;
    for (const result of Array.from(event?.results ?? [])) {
      const text = transcriptFromResult(result);
      if (!text) continue;
      if (result.isFinal) finalText = text;
      else onInterim(text);
    }
  };
  const setup = () => {
    if (!recognizerFactory) {
      onStatus('Voice input unavailable in this browser. Use the text composer.');
      return null;
    }
    try {
      const next = recognizerFactory();
      next.lang = deriveRecognitionLanguage(locale, browserLocale);
      next.continuous = false;
      next.interimResults = true;
      next.maxAlternatives = 1;
      next.onresult = setResult;
      next.onerror = event => {
        failed = true;
        active = false;
        setHeld(false);
        onStatus(event?.error === 'not-allowed'
          ? 'Microphone permission denied. Use the text composer.'
          : 'Voice input failed. Your text composer is still available.');
        finish();
      };
      next.onend = () => {
        ended = true;
        setHeld(false);
        if (released) finish();
      };
      return next;
    } catch {
      onStatus('Microphone permission denied. Use the text composer.');
      return null;
    }
  };
  const begin = (text = '') => {
    if (disposed || active || countdown !== null) return false;
    originalText = String(text);
    finalText = '';
    finished = false;
    failed = false;
    active = false;
    ended = false;
    released = false;
    recognizer = setup();
    if (!recognizer) return false;
    active = true;
    setHeld(true);
    onStatus('Listening… release to send.');
    try { recognizer.start(); }
    catch {
      failed = true;
      active = false;
      setHeld(false);
      finish();
      onStatus('Microphone permission denied. Use the text composer.');
      return false;
    }
    return true;
  };
  const release = () => {
    if (!active || released) return;
    released = true;
    setHeld(false);
    try { recognizer?.stop(); } catch { /* recognizer already ended */ }
    if (ended) finish();
  };
  const cancel = () => {
    if (active) {
      active = false;
      released = true;
      failed = true;
      setHeld(false);
      try { recognizer?.abort?.(); } catch { /* already ended */ }
      try { recognizer?.stop?.(); } catch { /* already ended */ }
      finish();
    }
    clearCountdown();
    if (!disposed) onStatus('Voice command cancelled.');
  };
  const cancelCountdown = () => {
    if (countdown === null) return false;
    clearCountdown();
    restore();
    onStatus('Controlled Land cancelled.');
    return true;
  };
  const dispose = () => {
    disposed = true;
    cancel();
    clearCountdown();
  };
  return {
    begin, release, cancel, cancelCountdown, dispose,
    supported: Boolean(recognizerFactory),
    get recognizer() { return recognizer; },
    get isHeld() { return held; },
    get isCountingDown() { return countdown !== null; },
  };
}

export function createVoiceView(host, input, getSnapshot, chatController, landController, options = {}) {
  const status = el('span', { class: 'voice-status', role: 'status', 'aria-live': 'polite' });
  const mic = button('Hold to talk', null, {
    id: 'voice-talk',
    class: 'voice-control',
    'aria-label': 'Hold to talk. Audio may be processed by the browser speech service.',
    'aria-pressed': 'false',
  });
  const cancel = button('Cancel', null, { id: 'voice-cancel', class: 'voice-cancel', hidden: true });
  const setText = value => { input.value = value; };
  const controller = createVoiceCommandController({
    ...options,
    getSnapshot,
    onSubmit: text => chatController.submit(text),
    onText: setText,
    onInterim: text => setText(text),
    onStatus: text => { status.textContent = text; cancel.hidden = !text.includes('Controlled Land starts'); },
    onListeningChange: listening => mic.setAttribute('aria-pressed', String(listening)),
    onLand: () => landController.request(),
  });
  if (!controller.supported) status.textContent = 'Voice input unavailable in this browser. Use the text composer.';
  const start = event => {
    if (event.type === 'pointerdown' && event.button !== 0) return;
    if (event.type === 'pointerdown') mic.setPointerCapture?.(event.pointerId);
    if (controller.begin(input.value)) {
      mic.setAttribute('aria-pressed', 'true');
      cancel.hidden = false;
    }
  };
  const release = () => {
    controller.release();
    cancel.hidden = !controller.isCountingDown;
  };
  const cancelListening = () => {
    cancelOnLostCapture(controller);
    if (!controller.isCountingDown) cancel.hidden = true;
  };
  mic.addEventListener('pointerdown', start);
  mic.addEventListener('pointerup', release);
  mic.addEventListener('pointercancel', cancelListening);
  mic.addEventListener('lostpointercapture', cancelListening);
  mic.addEventListener('keydown', event => {
    if ([' ', 'Enter'].includes(event.key)) { event.preventDefault(); if (!event.repeat) start(event); }
  });
  mic.addEventListener('keyup', event => { if ([' ', 'Enter'].includes(event.key)) release(); });
  cancel.addEventListener('click', () => controller.cancelCountdown());
  const escape = event => { if (event.key === 'Escape') controller.cancelCountdown() || controller.cancel(); };
  document.addEventListener('keydown', escape);
  host.append(mic, cancel, status);
  return {
    controller,
    render() { cancel.hidden = !controller.isCountingDown; },
    dispose() {
      document.removeEventListener('keydown', escape);
      controller.dispose();
      mic.remove(); cancel.remove(); status.remove();
    },
  };
}
