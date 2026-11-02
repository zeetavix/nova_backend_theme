/** @odoo-module **/
//
// Launcher digest — Ask Nova narrates the theme's own insight tiles.
// Additive patch: no fork of NovaHome, and the block simply stays
// absent when no agent is configured or the provider is busy.
//
import { signal } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { rpc } from "@web/core/network/rpc";
import { NovaHome } from "@nova_backend_theme/home/nova_home";

let digestPatched = false;
if (!digestPatched) {
    digestPatched = true;
    try {
        patch(NovaHome.prototype, {
            setup() {
                super.setup(...arguments);
                this._novaDigest = signal("");
                this._novaDigestAt = signal("");
                this._novaFigures = signal("");
                // Fetch once, after the tile load settles, and never
                // block the launcher on it.
                try {
                    const orig = this.loadInsights;
                    if (typeof orig === "function") {
                        this.loadInsights = async (...args) => {
                            const result = await orig.apply(this, args);
                            this.loadNovaDigest();
                            return result;
                        };
                    } else {
                        Promise.resolve().then(() => this.loadNovaDigest());
                    }
                } catch {
                    // digest stays empty; the launcher is unaffected
                }
            },

            async loadNovaDigest() {
                try {
                    const res = await rpc("/nova/ai/digest", {});
                    const text = (res && res.narrative) || "";
                    try {
                        this._novaDigest.set(text);
                        this._novaFigures.set((res && res.figures) || "");
                        this._novaDigestAt.set((res && res.at) || "");
                    } catch {
                        // signal stays put
                    }
                } catch {
                    try {
                        this._novaDigest.set("");
                    } catch {
                        // signal stays put
                    }
                }
            },

            get novaDigestText() {
                try {
                    return this._novaDigest() || "";
                } catch {
                    return "";
                }
            },

            get novaFigures() {
                try {
                    return this._novaFigures() || "";
                } catch {
                    return "";
                }
            },

            // Server ships "Label value · Label value · …" as one string;
            // chips need the parts. Plain text split — nothing HTML.
            get novaFigureChips() {
                try {
                    const raw = this._novaFigures() || "";
                    if (!raw) {
                        return [];
                    }
                    return raw
                        .split("·")
                        .map((s) => s.trim())
                        .filter(Boolean);
                } catch {
                    return [];
                }
            },

            // The launcher's "Ask Nova" pill and FAB open the real agent
            // (the drawer), not the old rule-based navigator: one name,
            // one brain.
            toggleAssistant() {
                try {
                    window.dispatchEvent(new CustomEvent("nova:ai-open", {
                        detail: {},
                    }));
                } catch {
                    if (super.toggleAssistant) {
                        return super.toggleAssistant(...arguments);
                    }
                }
            },
        });
    } catch (e) {
        // eslint-disable-next-line no-console
        console.error("[nova_ai] launcher digest patch failed:", e);
    }
}
