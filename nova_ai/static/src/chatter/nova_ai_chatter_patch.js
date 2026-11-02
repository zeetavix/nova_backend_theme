/** @odoo-module **/
//
// Chatter AI entry — ambient agent actions that complete inline.
//
// The "AI" menu no longer opens the drawer: each action runs the agent
// turn headlessly on this record and applies the result where it
// belongs. Drafts land in the composer for the user to send (their job
// is pressing Send, not retyping); everything else opens a small
// dialog with the answer and a Copy button. Any failure falls back to
// opening the drawer prefilled, so the menu never dead-ends.
//
// Patch-only: no fork of mail, and the control degrades to nothing
// when nova_ai is absent.
//

import { Chatter } from "@mail/chatter/web_portal_project/chatter";
import { Component, markup, xml } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { rpc } from "@web/core/network/rpc";
import { patch } from "@web/core/utils/patch";
import { renderMarkdown } from "@nova_ai/chat/nova_ai_drawer";
import { stripMarkdown } from "@nova_ai/chatter/nova_ai_text";

const OPS = ["summarize", "translate", "draft", "next", "risk"];

const TITLES = {
    summarize: "Thread summary",
    translate: "Translation",
    draft: "Draft reply",
    next: "Suggested next steps",
    risk: "Risk flags",
};

class NovaAiResultDialog extends Component {
    // Mirrors the core debug-menu dialog shape (web.ClocReport):
    // title/footer through `this`, close via the injected prop.
    static template = xml`
        <Dialog title="this.dialogTitle" footer="true">
            <div class="nova_ai_result" t-out="this.props.html"/>
            <t t-set-slot="footer">
                <button class="btn btn-secondary" t-on-click="this.onCopy">Copy</button>
                <button class="btn btn-primary" t-on-click="this.props.close">Close</button>
            </t>
        </Dialog>`;
    static components = { Dialog };
    static props = ["title", "html", "copy", "close?"];

    get dialogTitle() {
        return this.props.title || "Ask Nova";
    }

    onCopy() {
        try {
            this.props.copy();
        } catch {
            // clipboard stays put
        }
    }
}

function chatterContext(chatter) {
    try {
        const thread = typeof chatter.thread === "function" ? chatter.thread() : null;
        const model = (thread && (thread.model || thread.resModel)) || "";
        const resId = Number((thread && (thread.id || thread.resId)) || 0);
        if (model && resId) {
            return { model: model, res_id: resId };
        }
    } catch {
        // context stays put
    }
    return {};
}

function openDrawerFallback(key, context) {
    const PROMPTS = {
        summarize: "Summarize this record's chatter thread in a few bullets.",
        translate: "Translate the most recent chatter message into English.",
        draft: "Draft a short follow-up reply for this record.",
        next: "Suggest concrete next steps for this record.",
        risk: "Flag risks on this record: overdue amounts, stalled states, missing data.",
    };
    try {
        window.dispatchEvent(new CustomEvent("nova:ai-open", {
            detail: { message: PROMPTS[key] || "", context: context },
        }));
    } catch {
        // drawer stays shut
    }
}

function notify(chatter, message, kind) {
    try {
        const service = chatter.env && chatter.env.services &&
            chatter.env.services.notification;
        if (service) {
            service.add(message, { type: kind || "info", sticky: false });
        }
    } catch {
        // silent
    }
}

let chatterPatched = false;
if (!chatterPatched) {
    chatterPatched = true;
    try {
        patch(Chatter.prototype, {
            async novaAiAsk(key) {
                if (!OPS.includes(key)) {
                    return;
                }
                // One turn at a time: double-clicks must not fire double
                // sessions and double model turns.
                if (this._novaAiBusy) {
                    return;
                }
                const context = chatterContext(this);
                if (!context.model) {
                    notify(this, "Open a record first, then ask Nova.",
                        "warning");
                    return;
                }
                this._novaAiBusy = true;
                notify(this, "Asking Nova…", "info");
                let answer = null;
                try {
                    answer = await rpc("/nova/ai/inline", {
                        op: key,
                        model: context.model,
                        res_id: context.res_id,
                    });
                } catch {
                    answer = null;
                } finally {
                    try {
                        this._novaAiBusy = false;
                    } catch {
                        // flag stays put
                    }
                }
                if (!answer || answer.error) {
                    if (answer && answer.error) {
                        notify(this, answer.error, "warning");
                    }
                    openDrawerFallback(key, context);
                    return;
                }
                const reply = (answer.reply || "").trim();
                if (!reply) {
                    openDrawerFallback(key, context);
                    return;
                }
                if (key === "draft") {
                    // The composer is plain(ish) text — strip markdown so
                    // asterisks and hashes never reach the message body.
                    this.novaAiFillComposer(stripMarkdown(reply));
                    return;
                }
                this.novaAiShowResult(
                    key, reply, Number(answer.pending || 0));
            },

            async novaAiFillComposer(text) {
                // The draft lands in the composer MODEL (thread.composer),
                // not by poking the textarea DOM: open the composer through
                // the chatter API, then assign composerText. Existing draft
                // text is appended to, never overwritten. Any failure falls
                // back to the result dialog so the text is never lost.
                try {
                    const thread =
                        typeof this.thread === "function"
                            ? this.thread()
                            : this.thread;
                    const composer = thread && thread.composer;
                    if (!composer) {
                        this.novaAiShowResult("draft", text, 0);
                        return;
                    }
                    await this.toggleComposer("message", { force: true });
                    const current = String(composer.composerText || "").trim();
                    composer.composerText = current
                        ? current + "\n\n" + text
                        : text;
                    composer.restoredFromFullComposer = false;
                    if (!composer.isFocused) {
                        composer.autofocus++;
                    }
                    notify(this, "Draft ready in the composer — review and send.", "success");
                } catch {
                    this.novaAiShowResult("draft", text, 0);
                }
            },

            novaAiShowResult(key, reply, pending) {
                let html = null;
                try {
                    html = markup(renderMarkdown(reply));
                } catch {
                    html = null;
                }
                if (!html) {
                    openDrawerFallback(key, chatterContext(this));
                    return;
                }
                if (pending > 0) {
                    try {
                        html = markup(
                            String(html) +
                            "<p><em>" + pending +
                            " approval(s) waiting in the Ask Nova history.</em></p>");
                    } catch {
                        // note stays put
                    }
                }
                const copy = async () => {
                    try {
                        if (navigator.clipboard && navigator.clipboard.writeText) {
                            await navigator.clipboard.writeText(reply);
                            notify(this, "Copied.", "success");
                        }
                    } catch {
                        // clipboard stays put
                    }
                };
                try {
                    const dialog = this.env.services.dialog;
                    dialog.add(NovaAiResultDialog, {
                        title: TITLES[key] || "Ask Nova",
                        html: html,
                        copy: copy,
                    });
                } catch {
                    openDrawerFallback(key, chatterContext(this));
                }
            },
        });
    } catch (e) {
        // eslint-disable-next-line no-console
        console.error("[nova_ai] chatter patch failed to apply:", e);
    }
}
