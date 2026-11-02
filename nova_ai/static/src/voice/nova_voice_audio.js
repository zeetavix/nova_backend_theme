/** @odoo-module **/
//
// Spoken replies: what to say and how to say it fast. Ported from the
// zeetavix_ai_local voice experiment, whose measurements are kept in the
// comments because they are the reason for each choice.

// Break a reply so synthesis and playback overlap: a short first chunk
// sets time-to-first-word (measured 6.30s -> 5.72s just by not packing
// chunk one), later chunks pack tight to save round trips.
export function splitForSpeech(text, { maxChars = 180, firstChars = 60 } = {}) {
    const sentences = String(text || "").match(/[^.!?]+[.!?]*\s*/g) || [text];
    const chunks = [];
    let current = "";
    let limit = firstChars;
    for (const sentence of sentences) {
        if (current && (current + sentence).length > limit) {
            chunks.push(current.trim());
            current = sentence;
            limit = maxChars;
        } else {
            current += sentence;
        }
    }
    if (current.trim()) {
        chunks.push(current.trim());
    }
    return chunks.filter(Boolean);
}

// A reply worth listening to: the headline, a few records, then a count.
// Reading a card list in full is a minute of "Customer Administrator,
// order date …, total …" while the screen already shows the detail.
export function spokenSummaryFromHtml(html, { maxBlocks = 4 } = {}) {
    // DOMParser, not innerHTML on a detached div: the document it builds
    // is inert, so nothing in a reply can load or run while reading words.
    let holder = null;
    try {
        const parsed = new DOMParser().parseFromString(String(html || ""), "text/html");
        holder = parsed.body;
    } catch {
        return String(html || "").replace(/\s+/g, " ").trim().slice(0, 600);
    }
    const blocks = [];
    const roots = holder.children.length ? holder.children : [holder];
    for (const node of roots) {
        const text = (node.textContent || "").replace(/\s+/g, " ").trim();
        if (text) {
            blocks.push(text);
        }
    }
    if (!blocks.length) {
        return (holder.textContent || "").replace(/\s+/g, " ").trim().slice(0, 600);
    }
    const spoken = blocks.slice(0, maxBlocks);
    const remaining = blocks.length - spoken.length;
    if (remaining > 0) {
        spoken.push(`and ${remaining} more on screen.`);
    }
    return spoken.join(". ").slice(0, 1200);
}

// Play base64 WAV through an <audio> element. Resolves on end, error,
// or interrupt() — losing speech must never end the conversation.
export function playWavBase64(base64, hooks = {}) {
    return new Promise((resolve) => {
        let done = false;
        const finish = () => {
            if (!done) {
                done = true;
                resolve();
            }
        };
        try {
            const audio = new Audio("data:audio/wav;base64," + base64);
            if (hooks.ref) {
                hooks.ref(audio);
            }
            audio.onended = finish;
            audio.onerror = finish;
            audio.play().catch(finish);
        } catch {
            finish();
        }
    });
}
