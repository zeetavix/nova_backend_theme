/** @odoo-module **/
//
// One spoken turn on a microphone that stays open. Ported from the
// zeetavix_ai_local voice experiment: the first build opened and closed
// the device per turn, and whatever was said during the reopen gap —
// exactly when someone speaks after a wake phrase — was lost. Now the
// device opens once per voice-mode session and turns only gate blocks.

import { NovaSpeechGate } from "@nova_ai/voice/nova_speech_gate";

const WORKLET_NAME = "nova-pcm16-16k";

export class NovaVoiceRecorder {
    constructor({ onLevel } = {}) {
        this.onLevel = onLevel || (() => {});
        this.state = "inactive";
        this._collect = null;
        this._abort = null;
    }

    // What the browser reports, translated once into plain language.
    static describeMicError(error) {
        const name = (error && error.name) || "";
        if (name === "NotAllowedError" || name === "SecurityError") {
            return "The browser blocked microphone access. Allow the microphone for this site and try again.";
        }
        if (name === "NotFoundError" || name === "DevicesNotFoundError") {
            return "No microphone was found on this device.";
        }
        if (name === "NotReadableError") {
            return "The microphone is in use by another application.";
        }
        return "The microphone could not be started: " + ((error && error.message) || name || "unknown error");
    }

    // Why there is no button at all, told apart: on plain http
    // getUserMedia is missing (not denied) — the commonest deployment
    // failure, and it needs its own message.
    static unavailableReason() {
        try {
            if (typeof navigator === "undefined" || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                if (typeof window !== "undefined" && !window.isSecureContext) {
                    return "Microphone access needs a secure connection. Open Odoo over https (or on localhost) to talk to the assistant.";
                }
                return "This browser does not support microphone recording.";
            }
        } catch {
            return "This browser does not support microphone recording.";
        }
        return null;
    }

    // Open once per voice-mode session. Starting a turn only gates
    // blocks, which costs nothing and drops no words.
    async open() {
        if (this.audioContext) {
            return;
        }
        this.stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            },
        });
        this.audioContext = new AudioContext();
        await this.audioContext.audioWorklet.addModule(
            "/nova_ai/static/src/voice/nova_pcm16_processor.js");
        if (this.audioContext.state === "suspended") {
            await this.audioContext.resume();
        }
        const source = this.audioContext.createMediaStreamSource(this.stream);
        this.processor = new AudioWorkletNode(this.audioContext, WORKLET_NAME);
        source.connect(this.processor);
        // Never to destination: mic-to-speakers is a feedback loop, and
        // the worklet runs regardless.
        this.processor.port.onmessage = (event) => {
            const data = (event && event.data) || {};
            try {
                this.onLevel(data.rms || 0);
            } catch {
                // meter stays put
            }
            if (this._collect) {
                this._collect(data.samples, data.rms || 0);
            }
        };
    }

    // @returns {Promise<Blob|null>} 16 kHz mono WAV, or null if nobody spoke.
    async record({ maxMs = 30000, silenceMs = 1500, noSpeechMs = 8000 } = {}) {
        await this.open();
        this.state = "recording";
        return new Promise((resolve) => {
            const blocks = [];
            const gate = new NovaSpeechGate({ silenceMs: silenceMs });
            let elapsed = 0;
            let settled = false;
            const finish = (result) => {
                if (this.state !== "recording" || settled) {
                    return;
                }
                settled = true;
                this.state = "idle";
                this._collect = null;
                this._abort = null;
                try {
                    clearTimeout(deadline);
                } catch (ignored) {}
                resolve(result);
            };
            const finishWithTail = () => {
                // Flush the worklet's partial tail block first so the
                // final word is not cut off, then finish on next tick.
                try {
                    if (this.processor) {
                        this.processor.port.postMessage({ flush: true });
                    }
                } catch (ignored) {}
                setTimeout(() => finish(this.toWav(blocks)), 150);
            };
            // Wall-clock deadline: block counting alone cannot end the
            // turn if the worklet stalls and stops delivering blocks.
            const deadline = setTimeout(() => {
                finish(gate.hasSpeech ? this.toWav(blocks) : null);
            }, Math.max(maxMs, 1000));
            this._abort = () => finish(null);
            this._collect = (samples, rms) => {
                const blockMs = ((samples && samples.length) || 0) / 16000 * 1000;
                elapsed += blockMs;
                if (samples) {
                    blocks.push(samples);
                }
                const verdict = gate.push(rms || 0, blockMs);
                if (verdict === "ended") {
                    finishWithTail();
                    return;
                }
                if (!gate.speaking && elapsed >= noSpeechMs) {
                    finish(null);
                    return;
                }
                if (elapsed >= maxMs) {
                    if (gate.hasSpeech) {
                        finishWithTail();
                    } else {
                        finish(null);
                    }
                }
            };
        });
    }

    abort() {
        try {
            if (this._abort) {
                this._abort();
            }
        } catch {
            // recording stays put
        }
    }

    // Close the device when voice mode ends — this turns the browser's
    // recording indicator off. Never per turn.
    release() {
        this._collect = null;
        this._abort = null;
        try {
            if (this.processor) {
                this.processor.port.onmessage = null;
                this.processor = null;
            }
        } catch {
            // processor stays put
        }
        try {
            if (this.stream) {
                for (const track of this.stream.getTracks()) {
                    try {
                        track.stop();
                    } catch (ignored) {}
                }
            }
        } catch {
            // mic stays put
        }
        this.stream = null;
        try {
            if (this.audioContext && this.audioContext.state !== "closed") {
                this.audioContext.close();
            }
        } catch {
            // context stays put
        }
        this.audioContext = null;
        this.state = "inactive";
    }

    // Raw PCM wrapped in 44 bytes of WAV header: the server needs no
    // ffmpeg to read it, which a Windows host cannot be assumed to have.
    toWav(blocks) {
        let length = 0;
        for (const block of blocks) {
            length += block.length;
        }
        if (!length) {
            return null;
        }
        const buffer = new ArrayBuffer(44 + length * 2);
        const view = new DataView(buffer);
        const writeText = (offset, text) => {
            for (let i = 0; i < text.length; i++) {
                view.setUint8(offset + i, text.charCodeAt(i));
            }
        };
        writeText(0, "RIFF");
        view.setUint32(4, 36 + length * 2, true);
        writeText(8, "WAVE");
        writeText(12, "fmt ");
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);
        view.setUint16(22, 1, true);
        view.setUint32(24, 16000, true);
        view.setUint32(28, 16000 * 2, true);
        view.setUint16(32, 2, true);
        view.setUint16(34, 16, true);
        writeText(36, "data");
        view.setUint32(40, length * 2, true);
        let offset = 44;
        for (const block of blocks) {
            for (let i = 0; i < block.length; i++) {
                view.setInt16(offset, block[i], true);
                offset += 2;
            }
        }
        return new Blob([buffer], { type: "audio/wav" });
    }
}
