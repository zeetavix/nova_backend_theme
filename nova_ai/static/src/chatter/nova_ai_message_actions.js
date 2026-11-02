/** @odoo-module **/
//
// Message "⋮" action: Draft reply with Nova. Sits right under Reply.
// Runs the one-shot completion (no session, no tools, no cards) with
// the quoted message as material, then writes the draft into the
// side-chatter composer through the composer MODEL — pressing Send
// stays the user's job, and nothing is ever auto-sent.
//
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { registerMessageAction } from "@mail/core/common/message_actions";
import { plainTextFromHtml, stripMarkdown } from "@nova_ai/chatter/nova_ai_text";

registerMessageAction("nova-ai-draft-reply", {
    // Record chatter only (not Discuss, not the messaging systray):
    // the draft lands in the form composer via env.inChatter.
    condition: ({ message, owner, thread }) =>
        !owner.env.inMessagingMenu &&
        Boolean(owner.env.inChatter) &&
        Boolean(thread) &&
        Boolean(message.body),
    icon: "wand_stars",
    name: _t("Draft reply with Nova"),
    onSelected: async ({ message, owner, thread }) => {
        const notify = (text, type) => {
            try {
                owner.env.services.notification.add(text, { type: type || "info" });
            } catch {
                // notification stays quiet
            }
        };
        const author = String(message.authorName || "").trim();
        const body = plainTextFromHtml(message.body).slice(0, 2000);
        if (!body) {
            return;
        }
        notify(_t("Nova is drafting a reply…"), "info");
        let reply = "";
        try {
            const quoted = author
                ? "From " + author + ":\n" + body
                : body;
            const res = await rpc("/nova/ai/complete", {
                message: _t(
                    "Draft a short, polite reply to the quoted message. "
                    + "Plain prose only — no markdown, no subject line, "
                    + "no sign-off, no placeholders."
                ),
                selection: quoted,
                context: {},
            });
            if (res.error || !res.reply) {
                notify(
                    res.error || _t("Nova could not draft a reply."),
                    "warning"
                );
                return;
            }
            reply = stripMarkdown(res.reply);
        } catch {
            notify(_t("Could not reach Nova."), "warning");
            return;
        }
        if (!reply) {
            return;
        }
        try {
            const composer = thread.composer;
            // Open first so the draft is visible the moment it lands.
            owner.env.inChatter.toggleComposer("message", { force: true });
            const current = String(composer.composerText || "").trim();
            composer.composerText = current
                ? current + "\n\n" + reply
                : reply;
            composer.restoredFromFullComposer = false;
            if (!composer.isFocused) {
                composer.autofocus++;
            }
            notify(
                _t("Draft ready in the composer — review and send."),
                "success"
            );
        } catch {
            // Composer model failed: show the text so nothing is lost.
            notify(reply, "info");
        }
    },
    sequence: 22,
});
