'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const MAX_ENTRIES = 200;
  const MAX_BUFFERED = 256 * 1024;
  const SAMPLE_RATE = 16000;
  const MAX_AUDIO_SAMPLES = SAMPLE_RATE * 300;
  const JITTER_SECONDS = 0.06;
  const controls = Array.from(document.querySelectorAll('[data-action]'));
  const runtime = {
    active: false, generation: 0, socket: null, context: null, stream: null,
    source: null, worklet: null, timer: null, connectTimer: null, started: 0,
    listen: false, wakeListening: false, muted: false, state: null, stage: 'offline', audio: null,
    output: null, nextAudioTime: 0, ignoreCancelled: false, protocol: null,
    playback: new Set(), drops: 0, lastDropWarning: 0, underruns: 0, stopping: false,
  };

  const text = (value, limit = 6000) => typeof value === 'string' ? value.slice(0, limit) : '';
  const number = (value) => typeof value === 'number' && Number.isFinite(value) ? value : null;
  const seconds = (value) => number(value) === null ? '—' : `${Math.max(0, value).toFixed(2)} s`;
  const speakerLabel = (value) => /^(?:S\d{1,3}|[A-Z]|PENDING)$/.test(String(value)) ? String(value) : 'Unassigned';
  const time = (value) => {
    const s = Math.max(0, Math.floor(number(value) ?? 0));
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  };
  const elapsed = () => runtime.started ? (performance.now() - runtime.started) / 1000 : 0;
  const human = (value) => text(value, 120).replaceAll('_', ' ') || '—';
  const make = (tag, className, value) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (value !== undefined) el.textContent = value;
    return el;
  };

  function appendBounded(container, entry) {
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 70;
    const empty = container.querySelector('.empty');
    if (empty) empty.remove();
    container.append(entry);
    while (container.children.length > MAX_ENTRIES) container.firstElementChild.remove();
    if (nearBottom) container.scrollTop = container.scrollHeight;
  }

  function log(message, kind = '', at = elapsed()) {
    const row = make('div', `event ${kind}`);
    row.append(make('time', '', time(at)), make('span', '', text(message, 1800)));
    appendBounded($('events'), row);
  }

  function showError(message) {
    $('error-banner').textContent = message;
    $('error-banner').hidden = false;
    log(message, 'error');
  }

  function status(message) { $('status-message').textContent = text(message); }
  function send(payload) {
    if (runtime.socket?.readyState !== WebSocket.OPEN) return false;
    try { runtime.socket.send(JSON.stringify(payload)); return true; }
    catch { showError('Could not send to the server. The session was released.'); release('Connection lost. Start a fresh session.'); return false; }
  }

  function renderControls() {
    const state = runtime.state;
    const connected = runtime.active && runtime.socket?.readyState === WebSocket.OPEN;
    $('start').disabled = runtime.active;
    $('language').disabled = runtime.active;
    $('mode').disabled = runtime.active;
    $('stop').disabled = !runtime.active;
    $('mute').disabled = !runtime.active || !runtime.stream;
    $('mute').textContent = runtime.muted ? 'Unmute mic' : 'Mute mic';
    $('mute').setAttribute('aria-pressed', String(runtime.muted));
    for (const button of controls) {
      const action = button.dataset.action;
      const urgent = ['pause', 'previous', 'next', 'repeat'].includes(action);
      button.disabled = !connected || (!urgent && (!state || state.busy === true))
        || state?.mode === 'assemblyai-proof'
        || ($('mode').value === 'assemblyai-english' && state?.provider_ready !== true)
        || (state?.opening_pending === true && !['pause', 'resume'].includes(action))
        || (state?.source_ready === false && !['pause', 'resume'].includes(action))
        || (!state && action !== 'pause')
        || (action === 'resume' && state?.paused !== true)
        || (action === 'pause' && state?.paused === true);
    }
    const micLive = runtime.active && Boolean(runtime.stream);
    const passing = micLive && (runtime.listen || runtime.wakeListening) && !runtime.muted;
    $('gate').textContent = !micLive ? 'Input closed' : runtime.muted ? 'Muted · sending silence' : passing ? runtime.wakeListening ? 'Listening for “William”' : 'Sending microphone' : 'Server gate · sending silence';
    $('wake-hint').hidden = !runtime.wakeListening;
    $('wake-hint').textContent = runtime.muted ? 'Mic is muted. Unmute to say “William” and interrupt reading, or tap Pause.' : 'Say “William” to interrupt reading.';
    $('gate').classList.toggle('active', passing);
    $('mic-label').textContent = micLive ? 'LOCAL MIC · LIVE' : 'MIC OFF';
    $('pipe-mic').classList.toggle('active', micLive);
    $('pipe-stt').classList.toggle('active', passing);
    $('pipe-model').classList.toggle('active', Boolean(runtime.active && state?.busy && !['synthesizing', 'speaking'].includes(runtime.stage)));
    $('pipe-tts').classList.toggle('active', ['synthesizing', 'speaking'].includes(runtime.stage));
    $('connection').textContent = human(runtime.stage);
    $('connection').classList.toggle('active', runtime.active && runtime.stage === 'listening');
  }

  function drawMeter(rms = 0, wave = []) {
    const level = Math.max(0, Math.min(100, rms * 300));
    $('level-fill').style.width = `${level}%`;
    $('level-meter').setAttribute('aria-valuenow', String(Math.round(level)));
    $('level-label').textContent = rms > 0.00001 ? `${(20 * Math.log10(rms)).toFixed(0)} dBFS` : '−∞ dBFS';
    const canvas = $('waveform');
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const { width, height } = canvas;
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = '#DDDFE4'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, height / 2); ctx.lineTo(width, height / 2); ctx.stroke();
    ctx.strokeStyle = runtime.muted ? '#8E93A4' : '#E42535'; ctx.lineWidth = 2;
    ctx.beginPath();
    const values = wave.length > 1 ? wave : [0, 0];
    values.forEach((v, i) => {
      const x = i * width / (values.length - 1);
      const y = height / 2 - Math.max(-1, Math.min(1, v * 2.5)) * height * 0.4;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  function cancelPlayback() {
    runtime.audio = null;
    runtime.output = null;
    runtime.nextAudioTime = 0;
    runtime.ignoreCancelled = true;
    for (const source of runtime.playback) {
      source.onended = null; // Cancellation must never acknowledge successful playout.
      try { source.stop(); } catch { /* Already stopped. */ }
      source.disconnect();
    }
    runtime.playback.clear();
    $('stat-playback').textContent = '0.00 s';
  }

  function release(message = 'Stopped. Microphone released; start again for a fresh session.') {
    runtime.generation++;
    runtime.active = false;
    runtime.stopping = false;
    clearInterval(runtime.timer); clearTimeout(runtime.connectTimer);
    runtime.timer = null; runtime.connectTimer = null;
    cancelPlayback();
    const socket = runtime.socket;
    runtime.socket = null;
    if (socket) {
      socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
      try { socket.close(1000, 'Session released'); } catch { /* Already closed. */ }
    }
    if (runtime.worklet) { runtime.worklet.port.onmessage = null; runtime.worklet.disconnect(); runtime.worklet.port.close(); }
    runtime.source?.disconnect();
    runtime.stream?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    const context = runtime.context;
    runtime.context = null;
    if (context) { context.onstatechange = null; context.close().catch(() => {}); }
    runtime.stream = runtime.source = runtime.worklet = null;
    runtime.state = null; runtime.listen = false; runtime.wakeListening = false; runtime.muted = false;
    runtime.protocol = null; runtime.underruns = 0;
    runtime.stage = 'offline'; runtime.started = 0; runtime.drops = 0; runtime.lastDropWarning = 0;
    $('phase').textContent = 'Not started'; $('lesson-step').textContent = '—'; $('bible').textContent = '—';
    $('prompt-key').textContent = '—'; $('current-prompt').textContent = 'Session is off. Start again to introduce the group.';
    $('question-key').textContent = '—'; $('latest-response').textContent = 'Microphone and playback are off.';
    $('model-label').textContent = 'Not connected'; $('pipe-model').textContent = '03 · Awaiting facilitator';
    $('decision-provider').textContent = 'AWAITING SESSION';
    $('stat-started').textContent = '—'; $('stat-underruns').textContent = '0';
    $('roster').replaceChildren(make('p', 'empty', 'No active participants. Session voice enrollment has been released.'));
    $('roster-count').textContent = '0 people'; $('pending-name').hidden = true;
    $('interim').textContent = 'Microphone is off.';
    $('elapsed').textContent = '00:00'; $('audio-format').textContent = 'Mono PCM · 16 kHz · 100 ms frames';
    for (const id of ['first', 'synthesis', 'audio', 'decision', 'input', 'queue']) $(`stat-${id}`).textContent = '—';
    $('stat-buffer').textContent = '0 KB'; $('stat-drops').textContent = '0 frames';
    $('network-warning').hidden = true;
    status(message); drawMeter(); renderControls();
  }

  function stop() {
    if (!runtime.active || runtime.stopping) return;
    send({ type: 'control', action: 'stop' });
    if (runtime.state?.mode?.startsWith('assemblyai-')) {
      runtime.stopping = true;
      runtime.listen = runtime.wakeListening = false;
      runtime.worklet.port.onmessage = null;
      runtime.stream.getTracks().forEach((track) => { track.onended = null; track.stop(); });
      runtime.stream = null;
      runtime.context.onstatechange = null;
      runtime.context.close().catch(() => {});
      runtime.stage = 'stopping';
      log('Microphone released. Waiting briefly for final speaker revisions and provider Termination.');
      clearTimeout(runtime.connectTimer);
      runtime.connectTimer = setTimeout(() => release('Stopped. Final provider acknowledgement unavailable.'), 6000);
      renderControls();
      return;
    }
    log('Stopped by user. Microphone, playback, and transport released.');
    release();
  }

  async function start() {
    if (runtime.active) return;
    if ($('mode').value === 'assemblyai-proof') {
      for (const id of ['events', 'transcript', 'decisions']) $(id).replaceChildren(make('p', 'empty', 'New proof session.'));
    }
    $('error-banner').hidden = true;
    if (!window.isSecureContext) { showError('Microphone access requires HTTPS. Open the HTTPS Tailscale address, not a plain HTTP IP address.'); return; }
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext || !window.AudioWorkletNode) {
      showError('This browser does not support the required microphone and AudioWorklet APIs. Use a current Safari or Chrome browser over HTTPS.'); return;
    }
    runtime.active = true; runtime.stage = 'connecting'; runtime.started = performance.now();
    runtime.ignoreCancelled = false;
    const generation = ++runtime.generation;
    status('Requesting microphone permission and unlocking audio…'); renderControls();
    let stream = null;
    try {
      // Resume directly in the Start gesture; do not wait for getUserMedia first.
      const context = new AudioContext({ latencyHint: 'interactive' });
      runtime.context = context;
      const resumed = context.resume();
      // Attach a rejection handler immediately, even if the permission prompt stays open.
      const resumedResult = resumed.then(() => null, (error) => error);
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
      if (generation !== runtime.generation) { stream.getTracks().forEach((track) => track.stop()); return; }
      runtime.stream = stream; renderControls();
      const resumeError = await resumedResult;
      if (resumeError) throw resumeError;
      if (generation !== runtime.generation) return;
      if (context.state !== 'running') throw new Error('Audio playback did not unlock. Keep the page foreground and tap Start again.');
      await context.audioWorklet.addModule(new URL('pcm-worklet.js', location.href));
      if (generation !== runtime.generation) return;
      runtime.source = context.createMediaStreamSource(stream);
      runtime.worklet = new AudioWorkletNode(context, 'pcm-input', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
      runtime.source.connect(runtime.worklet); runtime.worklet.connect(context.destination);
      runtime.worklet.port.onmessage = ({ data }) => {
        if (generation !== runtime.generation) return;
        if (data.type === 'meter') { drawMeter(data.rms, data.wave); return; }
        if (data.type !== 'pcm' || runtime.socket?.readyState !== WebSocket.OPEN) return;
        if (runtime.socket.bufferedAmount + data.buffer.byteLength > MAX_BUFFERED) {
          runtime.drops++;
          $('stat-drops').textContent = `${runtime.drops} frames`;
          $('network-warning').textContent = 'Network backpressure: dropping microphone frames instead of building an audio backlog. Try a stronger connection.';
          $('network-warning').hidden = false;
          if (elapsed() - runtime.lastDropWarning > 3 || runtime.drops === 1) {
            log('Audio dropped: WebSocket send buffer exceeded the 256 KB limit.', 'warn'); runtime.lastDropWarning = elapsed();
          }
          return;
        }
        if (!(runtime.listen || runtime.wakeListening) || runtime.muted) new Uint8Array(data.buffer).fill(0);
        try { runtime.socket.send(data.buffer); }
        catch { showError('Audio transport failed. Microphone released.'); release('Connection lost. Start a fresh session.'); }
      };
      for (const track of stream.getAudioTracks()) track.onended = () => {
        if (generation !== runtime.generation) return;
        showError('Microphone access ended. Check your device permissions and start again.'); release('Microphone disconnected.');
      };
      context.onstatechange = () => {
        if (generation !== runtime.generation || !runtime.active) return;
        if (context.state !== 'running') {
          showError('Browser audio was suspended or interrupted. Session released to avoid stalled playback. Keep the page foreground and tap Start again.');
          release('Audio interrupted. Start a fresh session.');
        }
      };
      $('audio-format').textContent = `${context.sampleRate.toLocaleString()} Hz capture → 16 kHz mono · 100 ms frames`;
      const url = new URL('ws', location.href);
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(url); socket.binaryType = 'arraybuffer'; runtime.socket = socket;
      status('Microphone ready. Connecting to the session server…');
      runtime.connectTimer = setTimeout(() => {
        if (generation !== runtime.generation) return;
        showError('The voice connection timed out. Check Tailscale and provider connectivity.'); release('Connection timed out.');
      }, 45000);
      socket.onopen = () => {
        if (generation !== runtime.generation) return;
        send({ type: 'start', mode: $('mode').value, language: $('language').value });
        log(`Session requested: ${$('mode').value === 'assemblyai-proof' ? 'one-mic EN/TR ASR proof' : $('language').value === 'es' ? 'Spanish study' : 'English study'}.`);
        status('Connected. Starting Speechmatics and the facilitator…'); renderControls();
      };
      socket.onmessage = ({ data }) => {
        if (generation !== runtime.generation) return;
        if (data instanceof ArrayBuffer) { playPCM(data, generation); return; }
        try {
          const event = JSON.parse(data);
          if (!event || typeof event !== 'object' || typeof event.type !== 'string') throw new Error('Invalid event');
          if (runtime.audio && event.type !== 'cancel_audio') throw new Error('PCM descriptor must be followed immediately by binary audio');
          handleEvent(event);
        } catch {
          showError('The server sent an unreadable event. Session released.'); release('Protocol error.');
        }
      };
      socket.onerror = () => {
        if (generation !== runtime.generation) return;
        showError('WebSocket connection failed. Check the HTTPS Tailscale address and server availability.'); release('Connection failed.');
      };
      socket.onclose = ({ code }) => {
        if (generation !== runtime.generation) return;
        showError(`Server connection closed (code ${code}). The microphone has been released. Restart introductions in a new session.`);
        release('Disconnected. No automatic reconnection.');
      };
      runtime.timer = setInterval(() => {
        $('elapsed').textContent = time(elapsed());
        $('stat-buffer').textContent = `${((runtime.socket?.bufferedAmount || 0) / 1024).toFixed(1)} KB`;
        $('stat-playback').textContent = seconds(runtime.context ? Math.max(0, runtime.nextAudioTime - runtime.context.currentTime) : 0);
      }, 500);
    } catch (error) {
      if (generation !== runtime.generation) { stream?.getTracks().forEach((track) => track.stop()); return; }
      const messages = {
        NotAllowedError: 'Microphone permission was denied. Allow microphone access in your browser’s site settings, then tap Start again.',
        NotFoundError: 'No microphone was found. Connect or enable a microphone, then try again.',
        NotReadableError: 'The microphone could not be opened. Another app may be using it.',
      };
      showError(messages[error.name] || `Could not start audio: ${text(error.message, 350) || 'unknown browser error'}`);
      release('Could not start. Microphone and connection released.');
    }
  }

  function beginOutput(event, legacy = false) {
    if (runtime.output || runtime.audio || runtime.playback.size) throw new Error('Overlapping audio stream');
    if (typeof event.id !== 'string' || !event.id || event.id.length > 200
        || event.format !== 'pcm_s16le' || event.channels !== 1 || event.sample_rate !== SAMPLE_RATE) {
      throw new Error('Invalid PCM stream format');
    }
    runtime.ignoreCancelled = false;
    runtime.nextAudioTime = 0;
    runtime.output = { id: event.id, legacy, samples: 0, chunks: 0, pending: 0,
      ended: false, acknowledged: false, started: false, receivedAt: performance.now() };
    $('stat-started').textContent = '—';
    if (legacy) describeChunk(event);
  }

  function describeChunk(event) {
    if (!runtime.output && runtime.ignoreCancelled) return; // Cancelled stream tail.
    const output = runtime.output;
    if (!output || runtime.audio || output.ended || event.id !== output.id
        || !Number.isSafeInteger(event.samples) || event.samples < 1
        || output.samples + event.samples > MAX_AUDIO_SAMPLES || output.chunks >= 10000) {
      throw new Error('Invalid PCM chunk descriptor');
    }
    runtime.audio = { id: event.id, samples: event.samples };
  }

  function completeOutput(output) {
    // End may arrive before or after the final onended callback. Both use this
    // guard; a cancellation detaches the stream, and can never produce an ack.
    if (runtime.output !== output || !runtime.active || !output.ended || output.pending || output.acknowledged) return;
    output.acknowledged = true;
    runtime.output = null;
    runtime.nextAudioTime = 0;
    $('stat-playback').textContent = '0.00 s';
    if (send({ type: 'played', id: output.id })) log('All output chunks finished; server notified once.');
  }

  function endOutput(event) {
    if (!runtime.output && runtime.ignoreCancelled) return;
    const output = runtime.output;
    if (!output || runtime.audio || output.ended || event.id !== output.id
        || !Number.isSafeInteger(event.total_samples) || event.total_samples < 1
        || event.total_samples !== output.samples) throw new Error('Invalid or empty PCM stream end');
    output.ended = true;
    completeOutput(output);
  }

  function playPCM(buffer, generation) {
    const descriptor = runtime.audio;
    runtime.audio = null;
    if (!descriptor && runtime.ignoreCancelled && !runtime.output) return;
    const output = runtime.output;
    try {
      if (!runtime.context || runtime.context.state !== 'running') throw new Error('Browser audio is not running.');
      if (!descriptor || !output || descriptor.id !== output.id || output.ended
          || buffer.byteLength !== descriptor.samples * 2) throw new Error('Unexpected or incomplete PCM chunk.');
      const context = runtime.context;
      const audio = context.createBuffer(1, descriptor.samples, SAMPLE_RATE);
      const values = audio.getChannelData(0); const view = new DataView(buffer);
      for (let i = 0; i < values.length; i++) values[i] = view.getInt16(i * 2, true) / 32768;
      const source = context.createBufferSource(); source.buffer = audio;
      source.connect(context.destination); runtime.playback.add(source);
      output.samples += descriptor.samples; output.chunks++; output.pending++;
      if (output.started && runtime.nextAudioTime < context.currentTime) {
        runtime.underruns++;
        $('stat-underruns').textContent = String(runtime.underruns);
      }
      const scheduledAt = Math.max(runtime.nextAudioTime, context.currentTime + JITTER_SECONDS);
      runtime.nextAudioTime = scheduledAt + audio.duration;
      source.onended = () => {
        runtime.playback.delete(source); source.disconnect();
        if (generation !== runtime.generation || runtime.output !== output) return;
        output.pending--;
        completeOutput(output);
      };
      source.start(scheduledAt);
      $('stat-playback').textContent = seconds(runtime.nextAudioTime - context.currentTime);
      if (!output.started) {
        output.started = true;
        const estimate = (performance.now() - output.receivedAt) / 1000 + Math.max(0, scheduledAt - context.currentTime);
        $('stat-started').textContent = seconds(estimate);
        // Browser scheduling estimate, not a measurement of acoustic onset.
        if (!output.legacy) send({ type: 'playback_started', id: output.id });
        log(`First PCM chunk scheduled (${seconds(estimate)} after stream header, browser estimate).`);
      }
      if (output.legacy) { output.ended = true; completeOutput(output); }
    } catch (error) {
      if (output) send({ type: 'playback_error', id: output.id });
      showError(`Playback failed: ${text(error.message, 300)} Session released; no successful playout was acknowledged.`);
      release('Playback failed. Start again to unlock audio.');
    }
  }

  function renderState(state) {
    runtime.state = state; runtime.listen = state.listen === true;
    if (state.mode === 'assemblyai-english') {
      $('pipe-stt').textContent = '02 · AssemblyAI';
      $('pipe-model').textContent = '03 · Codex session';
      $('model-label').textContent = 'Active Codex session · human name recognition unverified';
      $('decision-provider').textContent = 'CODEX SESSION';
    }
    if (state.mode === 'assemblyai-proof') {
      $('pipe-stt').textContent = '02 · AssemblyAI';
      $('pipe-model').textContent = '03 · ASR proof only';
      $('model-label').textContent = 'Universal-3.6 Pro · human identity UNVERIFIED';
      $('decision-provider').textContent = 'NO FACILITATION';
    }
    $('phase').textContent = human(state.phase);
    const total = Array.isArray(state.steps) ? state.steps.length : 0;
    $('lesson-step').textContent = text(state.question_key, 80) || (total && number(state.index) !== null ? `${Math.max(0, state.index + 1)} / ${total}` : '—');
    $('bible').textContent = [text(state.bible, 50), human(state.scripture_mode)].filter((v) => v && v !== '—').join(' · ') || '—';
    // prompt events are the exact currently spoken message; state adds question context.
    if (!$('prompt-key').textContent || $('prompt-key').textContent === '—') {
      if (state.current_question) $('current-prompt').textContent = text(state.current_question);
    }
    $('stat-queue').textContent = String(number(state.queue_depth) ?? '—');
    const roster = Array.isArray(state.roster) ? state.roster.slice(0, 100) : [];
    $('roster-count').textContent = `${roster.length} ${roster.length === 1 ? 'person' : 'people'}`;
    $('roster').replaceChildren();
    for (const person of roster) {
      const row = make('div', 'person');
      const name = text(person.name, 120) || 'Unnamed participant';
      const details = make('div');
      details.append(make('p', 'person-name', name), make('p', 'person-meta', `${speakerLabel(person.speaker)} · ${seconds(person.speech_seconds)} speech · Enrolled: ${person.voice_enrolled === true ? 'yes' : 'no'}`));
      row.append(make('div', 'avatar', Array.from(name)[0]?.toUpperCase() || '?'), details); $('roster').append(row);
    }
    if (!roster.length) $('roster').append(make('p', 'empty', 'Introduce yourselves one at a time. Say your name and what you’re thankful for.'));
    $('pending-name').hidden = !state.pending;
    if (state.pending) $('pending-name').textContent = `Confirming ${text(state.pending.name, 120) || 'a name'} (${speakerLabel(state.pending.speaker)}). The same person can confirm or correct their name naturally. Human voice recognition remains unverified.`;
    renderControls();
  }

  function transcript(event) {
    const segments = Array.isArray(event.speakers) ? event.speakers.slice(0, 100) : [];
    const speech = text(event.text);
    const languageConfidence = number(event.language_confidence);
    const asrFields = event.turn_order !== undefined ? `Speaker label: ${text(event.speaker_label, 30) || 'unassigned'} · Language code: ${text(event.language_code, 20) || 'unknown'} (${languageConfidence === null ? 'confidence unavailable' : `${Math.round(languageConfidence * 100)}%`}) · Name: ${text(event.verified_name, 60) || 'UNVERIFIED'}` : '';
    if (event.final !== true) { $('interim').textContent = speech ? `Interim · ${asrFields} · ${speech}` : 'Waiting for speech…'; return; }
    $('interim').textContent = 'Waiting for the next utterance…';
    const row = make('article', 'feed-entry');
    const meta = make('div', 'entry-meta');
    const labels = [...new Set(segments.map((s) => speakerLabel(s.speaker)))];
    meta.append(make('span', '', time(event.at)), make('span', '', labels.join(', ') || 'Unassigned'), make('span', event.ignored ? 'ignored' : 'intent', event.ignored ? 'FINAL · IGNORED' : 'FINAL'));
    row.append(meta, make('p', 'entry-text', speech));
    if (asrFields) row.append(make('p', 'entry-detail', `${asrFields}${event.revision ? ' · SPEAKER REVISION; earlier attribution revoked' : ''}`));
    for (const segment of segments) {
      const confidence = number(segment.confidence);
      row.append(make('p', 'entry-detail', `${speakerLabel(segment.speaker)} · ${confidence === null ? 'confidence unavailable' : `${Math.round(confidence * 100)}% confidence`}${number(segment.start) !== null ? ` · ${seconds(segment.start)}–${seconds(segment.end)}` : ''}${segments.length > 1 ? ` · ${text(segment.text)}` : ''}`));
    }
    const order = Number.isSafeInteger(event.turn_order) ? event.turn_order : null;
    const existing = order === null ? null : $('transcript').querySelector(`[data-turn-order="${order}"]`);
    if (order !== null) row.dataset.turnOrder = String(order);
    if (existing) existing.replaceWith(row);
    else appendBounded($('transcript'), row);
    if (!existing) log(`Final transcript${event.ignored ? ' (ignored)' : ''}: ${speech}`, '', event.at);
  }

  function decision(event) {
    const row = make('article', 'feed-entry'); const meta = make('div', 'entry-meta');
    meta.append(make('span', '', time(event.at)), make('strong', 'intent', human(event.intent)), make('span', '', event.source === 'button' ? 'BUTTON' : 'VOICE'));
    row.append(meta, make('p', 'entry-text', `${human(event.phase_before)} → ${human(event.phase_after)}`));
    if (event.input) row.append(make('p', 'entry-detail', `Input: ${text(event.input)}`));
    if (event.reason) row.append(make('p', 'entry-detail', text(event.reason, 1000)));
    if (Array.isArray(event.prompts) && event.prompts.length) row.append(make('p', 'entry-detail', `Prompts: ${event.prompts.map((v) => text(v, 80)).join(', ')}`));
    appendBounded($('decisions'), row);
    $('stat-decision').textContent = number(event.elapsed_ms) === null ? '—' : `${event.elapsed_ms.toFixed(1)} ms`;
    log(`Intent: ${human(event.intent)}; ${human(event.phase_before)} → ${human(event.phase_after)}. ${text(event.reason, 500)}`, '', event.at);
  }

  function handleEvent(event) {
    // Explicit display allowlist: never stringify arbitrary server state, voice IDs, or credentials.
    switch (event.type) {
      case 'hello':
        if (event.protocol !== 1) { showError('Unsupported server protocol. Refresh after checking the deployment.'); release('Protocol mismatch.'); }
        else log('Connected to DBS protocol 1.', '', event.at);
        break;
      case 'status':
        runtime.stage = text(event.stage, 40); status(event.message);
        if (['listening', 'synthesizing', 'speaking', 'paused'].includes(runtime.stage)) { clearTimeout(runtime.connectTimer); runtime.connectTimer = null; }
        log(`${human(event.stage)}: ${text(event.message, 700)}`, event.stage === 'error' ? 'error' : '', event.at); renderControls();
        if (event.stage === 'ended') release(text(event.message) || 'Session ended.');
        break;
      case 'state': renderState(event); break;
      case 'transcript': transcript(event); break;
      case 'provider_termination':
        log(`AssemblyAI Termination acknowledged: ${seconds(event.audio_duration_seconds)} audio; ${seconds(event.session_duration_seconds)} connected.`, '', event.at); break;
      case 'decision': decision(event); break;
      case 'prompt':
        $('prompt-key').textContent = text(event.key, 100) || '—'; $('current-prompt').textContent = text(event.text);
        log(`Prompt ${text(event.key, 100)}: ${text(event.text, 1200)}`, '', event.at); break;
      case 'tts':
        $('stat-first').textContent = seconds(event.first_audio_seconds);
        $('stat-synthesis').textContent = seconds(event.synthesis_seconds); $('stat-audio').textContent = seconds(event.audio_seconds);
        log(`TTS: ${text(event.provider, 40)} / ${text(event.model, 80)} (${text(event.language, 20)}); first byte ${seconds(event.first_audio_seconds)}, synthesis ${seconds(event.synthesis_seconds)}.`, '', event.at); break;
      case 'metrics':
        $('stat-input').textContent = `${seconds(event.input_seconds)} · ${number(event.received_bytes) === null ? '—' : `${(event.received_bytes / 1024).toFixed(0)} KB`}`;
        $('stat-queue').textContent = String(number(event.queue_depth) ?? '—'); break;
      case 'scripture_source': log(`Scripture: ${text(event.edition_title, 120)} · ${text(event.publisher, 120)} · ${text(event.reference, 100)} · ${text(event.copyright, 2500)}`, '', event.at); break;
      case 'audio': beginOutput(event, true); break;
      case 'audio_begin': beginOutput(event); break;
      case 'audio_chunk': describeChunk(event); break;
      case 'audio_end': endOutput(event); break;
      case 'cancel_audio': cancelPlayback(); log('Playback cancelled; no completion acknowledgement sent.', '', event.at); break;
      case 'error':
        showError(`${text(event.code, 80) || 'Server error'}: ${text(event.message, 1200)}`);
        if (event.fatal) release('Server error. Microphone released.'); break;
      case 'ended': log(text(event.message) || 'Session ended.', '', event.at); release(text(event.message) || 'Session ended. Microphone released.'); break;
      default: log('Unrecognized server event omitted from debug history.', 'warn', event.at);
    }
  }

  function renderGuide() {
    const proof = $('mode').value === 'assemblyai-proof';
    $('proof-notice').hidden = !proof;
    const english = $('mode').value === 'assemblyai-english';
    $('language').querySelector('[value=en]').textContent = english ? 'English · NIV' : 'English · NLT';
    $('language').disabled = runtime.active || english;
    if (english) $('language').value = 'en';
    if (english) {
      $('provider-description').textContent = 'Speech goes to AssemblyAI; spoken prompts go to ElevenLabs. William uses this active Codex session for facilitation. Scripture comes from the official YouVersion Platform API. Speech stays in session memory. Normal provider retention applies.';
      $('language-note').textContent = 'English only · NIV (YouVersion version 111, Biblica) · one room microphone.';
      $('quick-guide').replaceChildren();
      for (const phrase of ['Introduce yourself and share what you are thankful for.', 'Ask naturally to continue, go back, repeat, pause or read the passage.', 'Use Pause to interrupt reading; Resume continues from the saved place.']) $('quick-guide').append(make('p', 'small', phrase));
      return;
    }
    $('provider-description').textContent = proof
      ? 'Microphone speech goes to AssemblyAI for transcription and speaker labels. Human identity is UNVERIFIED. This app keeps speech in page/server memory and does not save audio or transcripts automatically. Normal provider retention applies. This proof has no translation or spoken facilitation.'
      : 'Microphone speech goes to Speechmatics for session speaker recognition. Spoken output, including names, goes to ElevenLabs. This browser path uses rules; participant turns are not sent to a model API. Normal provider retention applies. Audio and transcripts are not saved automatically.';
    if (proof) {
      $('language-note').textContent = 'EN/TR language bias with code-switching · one shared mic · 180-second limit · no Scripture in this proof.';
      $('quick-guide').replaceChildren();
      for (const phrase of ['Person 1: introduce your name and speak a few English sentences.', 'Person 2: introduce your name and speak a few Turkish sentences.', 'Alternate at least two more turns each; use a switch within one sentence.', 'Test a short turn and overlap, then Stop. Labels may be revised.']) {
        $('quick-guide').append(make('p', 'small', phrase));
      }
      return;
    }
    const spanish = $('language').value === 'es';
    $('guide-locale').textContent = spanish ? 'ES' : 'EN';
    $('language-note').textContent = spanish ? 'Génesis 1:1–25 · NVI. A participant reads unless an authorized passage is configured.' : 'Genesis 1:1–25 · English NLT passage.';
    const rows = spanish ? [
      ['Presentarse', '“Me llamo Josh y estoy agradecido por…”'], ['Confirmar', 'La misma persona dice “Sí” o “No”.'], ['Grupo', '“William, ya estamos todos.”'], ['Avanzar', '“William, siguiente pregunta.” Luego confirma.'], ['Repetir', '“William, repite.”'], ['Pasaje', '“William, lee el pasaje otra vez.”'], ['Pausar', '“William, pausa.” / “William, continúa.”'], ['Terminar', '“William, detente.”'],
    ] : [
      ['Introduce', '“My name is Josh, and I’m thankful for…”'], ['Confirm', 'The same person says “Yes” or “No”.'], ['Group', '“William, everyone is here.”'], ['Continue', '“William, next question.” Then confirm.'], ['Repeat', '“William, repeat.”'], ['Passage', '“William, read the passage again.”'], ['Pause', '“William, pause.” / “William, resume.”'], ['Finish', '“William, stop.”'],
    ];
    $('quick-guide').replaceChildren();
    for (const [label, phrase] of rows) { const row = make('div', 'guide-row'); row.append(make('span', '', label), make('strong', '', phrase)); $('quick-guide').append(row); }
    $('quick-guide').append(make('p', 'small muted guide-note', spanish ? 'Hablen de uno en uno, unos 20 segundos al presentarse. Se necesitan al menos 5 segundos de voz reconocida. El silencio no avanza la lección.' : 'Speak one at a time. Aim for about 20 seconds when introducing yourself; at least 5 seconds of recognized speech are needed. Silence never advances the lesson.'));
  }

  $('language').addEventListener('change', renderGuide);
  $('mode').addEventListener('change', renderGuide);
  $('start').addEventListener('click', start);
  $('stop').addEventListener('click', stop);
  $('mute').addEventListener('click', () => { runtime.muted = !runtime.muted; log(runtime.muted ? 'Mic muted: only silence is sent; local meter remains live.' : 'Mic unmuted: server listen gate still applies.'); renderControls(); });
  for (const button of controls) button.addEventListener('click', () => {
    if (button.disabled) return;
    if (send({ type: 'control', action: button.dataset.action })) log(`Button requested: ${human(button.dataset.action)}. Server flow confirms any transition.`);
  });
  $('clear-debug').addEventListener('click', () => {
    for (const id of ['events', 'transcript', 'decisions']) $(id).replaceChildren(make('p', 'empty', 'Debug history cleared.'));
    $('interim').textContent = runtime.active ? 'Waiting for speech…' : 'Microphone is off.';
    $('error-banner').hidden = true;
  });
  window.addEventListener('pagehide', () => { if (runtime.active) stop(); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && runtime.active) log('Page backgrounded. Mobile browsers may suspend audio; keep this page foreground.', 'warn');
  });
  renderGuide(); renderControls(); drawMeter();
})();
