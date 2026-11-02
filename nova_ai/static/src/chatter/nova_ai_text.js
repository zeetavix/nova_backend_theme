/** @odoo-module **/
//
// Tiny text helpers shared by Nova's chatter surfaces (message actions,
// inline draft). Plain-text extraction is regex-light on purpose: it
// runs on already-trusted mail bodies, never on model output HTML.
//

/** HTML (mail body) → readable plain text. */
export function plainTextFromHtml(html) {
    return String(html || "")
        .replace(/<[^>]*>/g, " ")
        .replace(/&nbsp;/g, " ")
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/\s+/g, " ")
        .trim();
}

/**
 * Markdown → plain prose for the composer. The completion route asks
 * for markdown (the editor wants it); the message composer wants text.
 * Code fences collapse, emphasis markers drop, list markers become
 * bullets, headings lose their hashes.
 */
export function stripMarkdown(md) {
    let text = String(md || "");
    text = text.replace(/```[\s\S]*?```/g, (block) =>
        block.replace(/```[a-zA-Z]*\n?/g, "").replace(/```/g, "").trimEnd()
    );
    text = text.replace(/\*\*([^*]+)\*\*/g, "$1");
    text = text.replace(/\*([^*]+)\*/g, "$1");
    text = text.replace(/^#{1,6}\s+/gm, "");
    text = text.replace(/^\s*[-*+]\s+/gm, "• ");
    text = text.replace(/`([^`]+)`/g, "$1");
    return text.replace(/[ \t]+\n/g, "\n").trim();
}
