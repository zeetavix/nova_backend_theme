// Microphone audio, prepared for whisper and for deciding when someone stopped
// talking. Adapted from the zeetavix_ai_local voice experiment (same design,
// rewritten for the Nova drawer): mono mix, 16 kHz resample, RMS per block,
// all off the main thread. IMPORTANT: this file is fetched by URL from an
// AudioContext and must stay OUT of the JS bundle (see __manifest__.py).
class NovaPcm16Processor extends AudioWorkletProcessor {
    constructor() {
        super();
        // 2048 samples at 16 kHz is 128 ms: prompt end-of-speech without
        // waking the main thread hundreds of times a second.
        this.blockSize = 2048;
        this.buffer = new Int16Array(this.blockSize);
        this.offset = 0;
        // Non-integer resample ratio: keep the fractional read position
        // across blocks, or every 128 ms boundary introduces a click.
        this.readPosition = 0;
        // A turn's tail is usually a partial block; without this it is
        // cut off (up to 128 ms of the final word).
        this.port.onmessage = (event) => {
            if (event && event.data && event.data.flush && this.offset > 0) {
                this.flush();
            }
        };
    }

    process(inputs) {
        const input = inputs[0];
        if (!input || input.length === 0 || !input[0] || input[0].length === 0) {
            return true;
        }
        const mono = this.toMono(input);
        // eslint-disable-next-line no-undef
        const ratio = sampleRate / 16000;
        while (this.readPosition < mono.length) {
            const sample = mono[Math.floor(this.readPosition)];
            const clamped = Math.max(-1, Math.min(1, sample));
            this.buffer[this.offset++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
            this.readPosition += ratio;
            if (this.offset >= this.blockSize) {
                this.flush();
            }
        }
        this.readPosition -= mono.length;
        return true;
    }

    flush() {
        const block = this.buffer.slice(0, this.offset);
        // RMS, not peak: a single click must not read as speech.
        let sumOfSquares = 0;
        for (let i = 0; i < block.length; i++) {
            const normalised = block[i] / 0x8000;
            sumOfSquares += normalised * normalised;
        }
        const rms = block.length ? Math.sqrt(sumOfSquares / block.length) : 0;
        this.port.postMessage({ samples: block, rms }, [block.buffer]);
        this.buffer = new Int16Array(this.blockSize);
        this.offset = 0;
    }

    toMono(channels) {
        if (channels.length === 1) {
            return channels[0];
        }
        const mono = new Float32Array(channels[0].length);
        for (let i = 0; i < mono.length; i++) {
            let total = 0;
            for (let c = 0; c < channels.length; c++) {
                total += channels[c][i];
            }
            mono[i] = total / channels.length;
        }
        return mono;
    }
}

registerProcessor("nova-pcm16-16k", NovaPcm16Processor);
