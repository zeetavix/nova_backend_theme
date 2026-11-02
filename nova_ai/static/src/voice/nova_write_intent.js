/** @odoo-module **/
//
// Would this spoken sentence change the database, or is it a question?
// Ported from the zeetavix_ai_local voice experiment: testing for a write
// verb ANYWHERE holds every question containing "new" or "set" ("how many
// new orders" never sends itself). What matters is the SHAPE — questions
// open with question words or fetch verbs, instructions lead with the verb
// — after politeness wrappers ("can you…", "i want to…") are stripped.

const WRITE_VERBS = new Set([
    "create", "add", "make", "update", "change", "edit", "modify", "set",
    "rename", "delete", "remove", "confirm", "cancel", "post", "validate",
    "approve", "assign", "archive", "duplicate", "register", "send",
]);

const READ_OPENERS = new Set([
    "how", "what", "which", "who", "whom", "whose", "when", "where", "why",
    "show", "list", "find", "search", "tell", "give", "display", "get",
    "count", "do", "does", "did", "is", "are", "was", "were", "has", "have",
    "any", "there",
]);

const POLITE_PREFIXES = [
    ["can", "you"], ["could", "you"], ["would", "you"], ["will", "you"],
    ["please"], ["i", "want", "to"], ["i", "need", "to"], ["i", "would", "like", "to"],
    ["lets"], ["let", "us"], ["go", "ahead", "and"], ["now"], ["ok"], ["okay"],
];

function words(text) {
    return String(text || "")
        .toLowerCase()
        .replace(/['’]/g, "")
        .replace(/[^\p{L}\p{N}\s]/gu, " ")
        .split(/\s+/)
        .filter(Boolean);
}

function stripPolite(list) {
    let current = list;
    let changed = true;
    while (changed) {
        changed = false;
        for (const prefix of POLITE_PREFIXES) {
            if (prefix.every((word, index) => current[index] === word)) {
                current = current.slice(prefix.length);
                changed = true;
                break;
            }
        }
    }
    return current;
}

// @returns {boolean} true when a person should read the sentence before it
// runs; false means it is a question and can go straight away.
export function looksLikeWrite(text) {
    const stripped = stripPolite(words(text));
    if (!stripped.length) {
        return false;
    }
    if (READ_OPENERS.has(stripped[0])) {
        return false;
    }
    // Imperative leads with its verb, allowing an adverb or two.
    const head = stripped.slice(0, 4);
    return head.some((word) => WRITE_VERBS.has(word));
}
