'use strict';

// One session-long resampler. Fractional source position survives render blocks.
class PCMInputProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;
    this.inputIndex = 0;
    this.nextPosition = 0;
    this.previous = 0;
    this.frame = new Int16Array(1600); // 100 ms at the transport sample rate.
    this.used = 0;
    this.meterSamples = 0;
    this.meterSquares = 0;
    this.wave = new Float32Array(64);
    this.waveIndex = 0;
  }

  pushSample(value) {
    const clipped = Math.max(-1, Math.min(1, value));
    this.frame[this.used++] = Math.round(clipped * (clipped < 0 ? 32768 : 32767));
    if (this.used === this.frame.length) {
      // Explicit little-endian encoding, independent of host endianness.
      const buffer = new ArrayBuffer(this.frame.length * 2);
      const view = new DataView(buffer);
      for (let i = 0; i < this.frame.length; i++) view.setInt16(i * 2, this.frame[i], true);
      this.port.postMessage({ type: 'pcm', buffer }, [buffer]);
      this.used = 0;
    }
  }

  process(inputs, outputs) {
    // Keep the capture graph running, but never route microphone sidetone.
    for (const output of outputs) for (const channel of output) channel.fill(0);
    const channels = inputs[0];
    if (!channels || !channels.length || !channels[0].length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i] || 0;
      value /= channels.length;
      this.meterSquares += value * value;
      this.meterSamples++;
      if (i % 2 === 0) this.wave[this.waveIndex++ % this.wave.length] = value;
      while (this.nextPosition <= this.inputIndex) {
        const fraction = this.nextPosition - (this.inputIndex - 1);
        this.pushSample(this.inputIndex === 0 ? value : this.previous + (value - this.previous) * fraction);
        this.nextPosition += this.step;
      }
      this.previous = value;
      this.inputIndex++;
    }
    if (this.meterSamples >= sampleRate / 25) {
      this.port.postMessage({ type: 'meter', rms: Math.sqrt(this.meterSquares / this.meterSamples), wave: Array.from(this.wave) });
      this.meterSamples = 0;
      this.meterSquares = 0;
    }
    return true;
  }
}
registerProcessor('pcm-input', PCMInputProcessor);
