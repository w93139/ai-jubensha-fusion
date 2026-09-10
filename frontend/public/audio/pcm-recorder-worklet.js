/* global AudioWorkletProcessor, registerProcessor, sampleRate */
// No recognition, network or storage. Output is silence; PCM is transferred only to this page.
class PlayPcmRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    if (!Number.isFinite(this.ratio) || this.ratio <= 0) throw new Error('Invalid audio rate');
    this.sum = 0;
    this.weight = 0;
    this.count = 0;
    this.used = 0;
    this.chunk = new Int16Array(1024);
    this.done = false;
    this.port.onmessage = ({ data }) => {
      if (data?.type === 'stop') {
        this.done = true;
        this.flush();
        this.port.postMessage({ type: 'stopped' });
      }
    };
  }

  flush() {
    if (!this.used) return;
    const samples = this.chunk.slice(0, this.used);
    this.port.postMessage({ type: 'data', samples: samples.buffer }, [samples.buffer]);
    this.used = 0;
  }

  process(inputs, outputs) {
    for (const output of outputs) for (const channel of output) channel.fill(0);
    if (this.done) return false;
    const channels = inputs[0];
    if (!channels?.length) return true;
    // Weighted interval averaging maintains phase across render blocks, including 44.1 kHz.
    // The requested 16 kHz AudioContext normally lets the browser resample the device first.
    for (let i = 0; i < channels[0].length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i];
      value /= channels.length;
      if (!Number.isFinite(value)) throw new Error('Invalid audio sample');
      let remaining = 1;
      while (remaining > 1e-9) {
        const take = Math.min(remaining, this.ratio - this.weight);
        this.sum += value * take;
        this.weight += take;
        remaining -= take;
        if (this.weight + 1e-9 < this.ratio) continue;
        const sample = Math.max(-1, Math.min(1, this.sum / this.ratio));
        this.chunk[this.used++] = Math.round(sample < 0 ? sample * 32768 : sample * 32767);
        this.sum = 0; this.weight = 0; this.count++;
        if (this.used === this.chunk.length) this.flush();
        if (this.count === 16000 * 60) {
          this.done = true; this.flush(); this.port.postMessage({ type: 'limit' }); return false;
        }
      }
    }
    return true;
  }
}

registerProcessor('play-pcm-recorder', PlayPcmRecorder);
