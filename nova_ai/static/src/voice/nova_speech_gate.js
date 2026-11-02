/** @odoo-module **/
//
// Block-by-block speech detection with a measured noise floor. Ported from
// the zeetavix_ai_local voice experiment, whose comments below record what
// failed and why: a fixed threshold works on the machine it was tuned on
// and fails everywhere else, and calibrating on the first blocks calibrates
// on the speaker's own voice (prompt speakers get cut off worst).
//
// Kept dependency-free so it runs under plain node for unit checks.

export class NovaSpeechGate {
    // The loudest silence is ever assumed to be. Above this, a "floor"
    // reading is likelier speech than a quiet room.
    static QUIET_CEILING = 0.03;

    constructor({ silenceMs = 1500, minSpeechMs = 400, startDebounceMs = 200 } = {}) {
        this.silenceMs = silenceMs;
        this.minSpeechMs = minSpeechMs;
        this.startDebounceMs = startDebounceMs;
        this.window = [];
        // ~10 s at 128 ms per block: a normal sentence contains a gap
        // somewhere, which is what the floor is looking for.
        this.windowBlocks = 80;
        this.speaking = false;
        this.silentFor = 0;
        this.loudFor = 0;
        this.spokenMs = 0;
        this.elapsed = 0;
    }

    // How loud silence is in this room: the quietest recent block, never
    // taken as louder than QUIET_CEILING (a window of pure speech must
    // fail towards recording noise, never towards muting the speaker).
    get floor() {
        if (!this.window.length) {
            return 0.004;
        }
        return Math.min(Math.min(...this.window), NovaSpeechGate.QUIET_CEILING);
    }

    get startThreshold() {
        const floor = this.floor;
        return Math.max(floor * 3.5, floor + 0.01, 0.015);
    }

    get continueThreshold() {
        const floor = this.floor;
        return Math.max(floor * 1.8, floor + 0.004, 0.007);
    }

    // @returns {"quiet"|"started"|"speaking"|"ended"} what this block means.
    push(rms, blockMs) {
        this.elapsed += blockMs;
        this.window.push(rms);
        if (this.window.length > this.windowBlocks) {
            this.window.shift();
        }
        if (!this.speaking) {
            if (rms > this.startThreshold) {
                // A couple of loud blocks before believing it, so a door
                // closing does not start a recording.
                this.loudFor += blockMs;
                if (this.loudFor >= this.startDebounceMs) {
                    this.speaking = true;
                    this.silentFor = 0;
                    return "started";
                }
            } else {
                this.loudFor = 0;
            }
            return "quiet";
        }
        if (rms > this.continueThreshold) {
            this.silentFor = 0;
            this.spokenMs += blockMs;
            return "speaking";
        }
        this.silentFor += blockMs;
        // A pause ends the turn only once enough was actually said: half a
        // second of noise is a cough or a chair, not a sentence.
        if (this.silentFor >= this.silenceMs && this.spokenMs >= this.minSpeechMs) {
            return "ended";
        }
        return "speaking";
    }

    // Was there enough speech to be worth sending anywhere?
    get hasSpeech() {
        return this.speaking && this.spokenMs >= this.minSpeechMs;
    }
}
