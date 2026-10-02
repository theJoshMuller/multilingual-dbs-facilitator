'use strict';
// Offline UI protocol checks: no browser, microphone, speech, or model providers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function client() {
  class Element {
    constructor() {
      this.children = []; this.textContent = ''; this.value = ''; this.checked = true;
      this.dataset = {}; this.style = {}; this.classList = { toggle() {} };
      this.scrollHeight = this.scrollTop = this.clientHeight = 0;
    }
    addEventListener() {}
    setAttribute() {}
    querySelector() { return null; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    getContext() { return { clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {} }; }
  }
  const root = path.resolve(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map((m) => [m[1], new Element()]));
  const controls = [...html.matchAll(/data-action="([^"]+)"/g)].map((m) => {
    const el = new Element(); el.dataset.action = m[1]; return el;
  });
  elements.get('language').value = 'en';
  if (elements.has('mode')) elements.get('mode').value = 'study';
  const document = {
    getElementById: (id) => elements.get(id),
    querySelectorAll: () => controls,
    createElement: () => new Element(),
    addEventListener() {},
  };
  const window = { addEventListener() {} };
  const context = vm.createContext({ document, window, performance, clearTimeout, clearInterval, WebSocket: { OPEN: 1 } });
  const source = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8');
  assert.ok(source.endsWith('})();\n'));
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'window.fixture = { runtime, handleEvent, playPCM }; })();'), context);
  const fixture = window.fixture;
  fixture.runtime.active = true;
  fixture.runtime.socket = { readyState: 1 };
  return { ...fixture, elements, controls };
}

const study = { type: 'state', phase: 'lesson', index: 1, steps: ['f.001', 'f.002'],
  current_question: 'The full original study question?', question_key: 'f.002',
  parser: 'openrouter', model: 'offline/fixture', facilitation_mode: 'generative',
  roster: [], paused: false, busy: false, listen: true };

test('generated procedural speech preserves the current canonical question', () => {
  const { handleEvent, elements } = client();
  handleEvent(study);
  handleEvent({ type: 'prompt', key: 'assistant', origin: 'generated', text: 'Anyone can share or pass.' });
  assert.equal(elements.get('current-prompt').textContent, study.current_question);
  assert.equal(elements.get('latest-response').textContent, 'Anyone can share or pass.');
  assert.match(elements.get('prompt-key').textContent, /generated/);
  handleEvent({ ...study, current_question: 'The next original question?', question_key: 'f.003' });
  assert.equal(elements.get('current-prompt').textContent, 'The next original question?');
  assert.equal(elements.get('question-key').textContent, 'f.003');
});

test('provider metadata and navigation reflect generative versus rules modes', () => {
  const { handleEvent, elements, controls } = client();
  handleEvent(study);
  assert.match(elements.get('model-label').textContent, /openrouter \/ offline\/fixture/);
  assert.equal(controls.find((el) => el.dataset.action === 'previous').disabled, false);
  handleEvent({ ...study, parser: 'rules', model: '', facilitation_mode: 'rules' });
  assert.equal(controls.find((el) => el.dataset.action === 'previous').disabled, true);
  assert.match(elements.get('control-note').textContent, /then confirm aloud/);
});

test('sessions without study metadata retain their own facilitator diagnostics', () => {
  const { handleEvent, elements } = client();
  elements.get('model-label').textContent = 'ASR only';
  handleEvent({ type: 'state', phase: 'asr_proof', mode: 'assemblyai-proof',
    current_question: 'Recognition proof', question_key: 'ASR ONLY', roster: [] });
  assert.match(elements.get('model-label').textContent, /ASR|UNVERIFIED/);
  assert.equal(elements.get('question-key').textContent, 'ASR ONLY');
});

test('buffered opening audio keeps the session active and acknowledges completed playback once', () => {
  const { handleEvent, playPCM, runtime } = client();
  const sent = [];
  const sources = [];
  runtime.socket.send = (payload) => sent.push(JSON.parse(payload));
  runtime.context = {
    state: 'running', currentTime: 0, destination: {}, close: () => Promise.resolve(),
    createBuffer: (_channels, samples, rate) => ({
      duration: samples / rate, getChannelData: () => new Float32Array(samples),
    }),
    createBufferSource: () => ({ connect() {}, disconnect() {}, stop() {},
      start() { sources.push(this); },
    }),
  };
  handleEvent({ type: 'audio', id: 'offline-opening', samples: 320,
    sample_rate: 16000, format: 'pcm_s16le', channels: 1 });
  playPCM(new ArrayBuffer(640), runtime.generation);
  assert.equal(runtime.active, true, 'Valid opening audio must not end the session');
  assert.equal(sources.length, 1);
  assert.equal(sent.length, 0, 'Do not acknowledge merely receiving audio');
  sources[0].onended();
  assert.deepEqual(sent, [{ type: 'played', id: 'offline-opening' }]);
  sources[0].onended();
  assert.equal(sent.length, 1);
});
