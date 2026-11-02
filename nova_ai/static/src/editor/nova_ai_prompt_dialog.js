import { Component, signal, markup, xml } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { rpc } from "@web/core/network/rpc";
import { renderMarkdown } from "@nova_ai/chat/nova_ai_drawer";

const SUGGESTIONS = [
    "Make this more formal",
    "Make this more concise",
    "Fix grammar and spelling",
    "Expand with more detail",
    "Translate to Spanish",
];

export class NovaAiPromptDialog extends Component {
    static template = xml`
        <Dialog title="this.dialogTitle" footer="true" size="'md'">
            <div class="nova_ai_prompt_dialog">
                <t t-if="this.props.selection">
                    <div class="nova_ai_prompt_selection">
                        <div class="nova_ai_prompt_sel_label">Selected text</div>
                        <div class="nova_ai_prompt_sel_body" t-esc="this.props.selection"/>
                    </div>
                </t>
                <textarea class="form-control nova_ai_prompt_input"
                          t-att-value="this.state.prompt"
                          t-on-input="(ev) => this._prompt.set(ev.target.value)"
                          t-on-keydown="(ev) => this.onKeydown(ev)"
                          placeholder="Tell Nova what to write…"
                          rows="3"/>
                <div class="nova_ai_prompt_suggest">
                    <button t-foreach="this.suggestions"
                            t-as="sug"
                            t-key="sug"
                            type="button"
                            class="nova_ai_prompt_chip"
                            t-att-class="this.state.prompt === sug ? 'nova_ai_prompt_chip_on' : ''"
                            t-on-click="() => this.useSuggestion(sug)"
                            t-esc="sug"/>
                </div>
                <div class="nova_ai_prompt_actions">
                    <button class="btn btn-primary nova_ai_prompt_go"
                            t-on-click="onGenerate"
                            t-att-disabled="this.state.busy or !this.state.prompt.trim()">
                        <span t-if="this.state.busy" class="nova_ai_prompt_spin" aria-hidden="true"/>
                        <svg t-else="" class="nova_ai_glyph" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 2.5c.7 4.6 3 6.9 7.6 7.6-4.6.7-6.9 3-7.6 7.6-.7-4.6-3-6.9-7.6-7.6 4.6-.7 6.9-3 7.6-7.6Z" fill="currentColor"/><circle cx="19" cy="5.5" r="1.7" fill="currentColor"/><circle cx="5" cy="18.5" r="1.7" fill="currentColor"/></svg>
                        <t t-if="this.state.busy">Generating…</t>
                        <t t-else="">Generate</t>
                    </button>
                    <span t-if="this.state.busy" class="nova_ai_prompt_hint">This can take a few seconds</span>
                </div>
                <t t-if="this.state.error">
                    <div class="nova_ai_prompt_error" role="alert" t-esc="this.state.error"/>
                </t>
                <t t-if="this.state.reply">
                    <div class="nova_ai_prompt_preview">
                        <div class="nova_ai_prompt_prev_label">Preview</div>
                        <div class="nova_ai_prompt_prev_body" t-out="this.state.replyHtml"/>
                    </div>
                </t>
            </div>
            <t t-set-slot="footer">
                <button class="btn btn-secondary" t-on-click="this.props.close">Cancel</button>
                <button class="btn btn-primary"
                        t-on-click="this.onInsert"
                        t-att-disabled="!this.state.reply">
                    Insert
                </button>
            </t>
        </Dialog>
    `;
    static components = { Dialog };
    static props = ["selection", "recordInfo", "insert", "close?"];

    setup() {
        this._prompt = signal("");
        this._busy = signal(false);
        this._reply = signal("");
        this._replyHtml = signal("");
        this._error = signal("");
    }

    get dialogTitle() {
        return "Ask Nova AI";
    }

    get suggestions() {
        return SUGGESTIONS;
    }

    get state() {
        const read = (sig, fallback) => {
            try {
                const value = sig();
                return value === undefined ? fallback : value;
            } catch {
                return fallback;
            }
        };
        return {
            prompt: read(this._prompt, ""),
            busy: read(this._busy, false),
            reply: read(this._reply, ""),
            replyHtml: read(this._replyHtml, ""),
            error: read(this._error, ""),
        };
    }

    useSuggestion(text) {
        this._prompt.set(text);
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
            ev.preventDefault();
            this.onGenerate();
        }
    }

    async onGenerate() {
        const prompt = this.state.prompt.trim();
        if (!prompt || this.state.busy) {
            return;
        }
        this._busy.set(true);
        this._error.set("");
        this._reply.set("");
        this._replyHtml.set("");
        try {
            const result = await rpc("/nova/ai/complete", {
                message: prompt,
                selection: this.props.selection || "",
                context: this.props.recordInfo || {},
            });
            if (result.error) {
                this._error.set(result.error);
            } else {
                this._reply.set(result.reply || "");
                this._replyHtml.set(markup(renderMarkdown(result.reply || "")));
            }
        } catch {
            this._error.set("Could not reach Nova. Please try again.");
        } finally {
            this._busy.set(false);
        }
    }

    onInsert() {
        if (!this.state.reply) {
            return;
        }
        this.props.insert(this.state.replyHtml);
        this.props.close();
    }
}
