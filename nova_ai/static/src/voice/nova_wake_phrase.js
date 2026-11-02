/** @odoo-module **/
//
// Fuzzy wake-phrase matching. Ported from the zeetavix_ai_local voice
// experiment: whisper renders an unstressed opener ("hey odoo") differently
// almost every time ("Hey, Odo.", "hey adieu", "Hay Odo"), so exact
// matching rejects most real attempts. Edit distance with a consonant
// skeleton forgives mangled vowels while keeping other sentences out.

export function editDistance(left, right) {
    if (left === right) {
        return 0;
    }
    if (!left.length) {
        return right.length;
    }
    if (!right.length) {
        return left.length;
    }
    let previous = Array.from({ length: right.length + 1 }, (_, i) => i);
    let current = new Array(right.length + 1);
    for (let i = 0; i < left.length; i++) {
        current[0] = i + 1;
        for (let j = 0; j < right.length; j++) {
            const substitution = previous[j] + (left[i] === right[j] ? 0 : 1);
            current[j + 1] = Math.min(current[j] + 1, previous[j + 1] + 1, substitution);
        }
        [previous, current] = [current, previous];
    }
    return previous[right.length];
}

function normalise(text) {
    return String(text || "")
        .toLowerCase()
        // Apostrophes join rather than split: "O'Dooth" -> "odooth"
        // (two edits from "odoo"), not "o" + "dooth".
        .replace(/['’]/g, "")
        .replace(/[^\p{L}\p{N}\s]/gu, " ")
        .replace(/\s+/g, " ")
        .trim();
}

// Consonants with routine recogniser swaps folded: vowels are guessed,
// consonants are heard. "odoo"/"adieu"/"oduu" converge; "todo" stays out.
function skeleton(text) {
    return normalise(text)
        .replace(/[aeiouy]/g, "")
        .replace(/[cqk]/g, "k")
        .replace(/[sz]/g, "s")
        .replace(/[dt]/g, "d")
        .replace(/[fv]/g, "f")
        .replace(/\s+/g, "");
}

// @returns {{matched: boolean, rest: string, distance: number}} — rest is
// whatever followed the phrase ("hey nova, how many orders" in one breath).
export function matchWakePhrase(heard, phrase) {
    const words = normalise(heard).split(" ").filter(Boolean);
    const target = normalise(phrase).split(" ").filter(Boolean);
    if (!words.length || !target.length) {
        return { matched: false, rest: "", distance: Infinity };
    }
    const candidate = words.slice(0, target.length).join(" ");
    const wanted = target.join(" ");
    const distance = editDistance(candidate, wanted);
    // Roughly a third of the phrase may be wrong.
    const tolerance = Math.max(2, Math.floor(wanted.length / 3));
    const skeletonDistance = editDistance(skeleton(candidate), skeleton(wanted));
    const soundsRight = skeletonDistance <= 1 && distance <= Math.ceil(wanted.length / 2);
    if (distance > tolerance && !soundsRight) {
        return { matched: false, rest: "", distance };
    }
    // Remainder from the original text so capitals and digits ("S00042")
    // survive for the agent's own extraction.
    const spoken = String(heard || "").trim().split(/\s+/);
    const rest = spoken.slice(target.length).join(" ")
        .replace(/^[\s,.:;!?-]+/, "")
        .trim();
    return { matched: true, rest, distance };
}
