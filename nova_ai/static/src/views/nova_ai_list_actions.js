/** @odoo-module **/
//
// List-row AI actions — "Ask Nova" entries in the list Action menu,
// active only for models the deployment allows agents to read (the
// same allow-list the tools enforce server-side, fetched once).
//
// Clicking an entry opens the drawer pinned to the first selected row
// with the prompt prefilled. Nothing is sent until the user hits send.
//
import { patch } from "@web/core/utils/patch";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";
import { ListController } from "@web/views/list/list_controller";

let allowedModels = null;
let fetching = null;

async function loadAllowedModels() {
    if (allowedModels) {
        return allowedModels;
    }
    if (!fetching) {
        fetching = rpc("/nova/ai/agents", {})
            .then((res) => {
                allowedModels = new Set((res && res.models) || []);
                return allowedModels;
            })
            .catch(() => {
                allowedModels = new Set();
                return allowedModels;
            })
            .finally(() => {
                fetching = null;
            });
    }
    return fetching;
}

function askNova(model, resId, message) {
    try {
        window.dispatchEvent(new CustomEvent("nova:ai-open", {
            detail: {
                message,
                context: { model, res_id: resId },
            },
        }));
    } catch {
        // drawer stays shut
    }
}

let listPatched = false;
if (!listPatched) {
    listPatched = true;
    try {
        patch(ListController.prototype, {
            setup() {
                super.setup(...arguments);
                this._novaAiModels = new Set();
                loadAllowedModels().then((models) => {
                    try {
                        this._novaAiModels = models;
                    } catch {
                        // items stay unavailable
                    }
                });
            },

            _novaAiFirstSelected() {
                try {
                    const selection = this.model.root.selection;
                    return selection.length ? selection[0].resId : 0;
                } catch {
                    return 0;
                }
            },

            getStaticActionMenuItems() {
                const items = super.getStaticActionMenuItems(...arguments);
                const self = this;
                const eligible = () =>
                    !!self.props.resModel &&
                    self._novaAiModels &&
                    self._novaAiModels.has(self.props.resModel) &&
                    !!self._novaAiFirstSelected();
                return {
                    ...items,
                    nova_ai_summarize: {
                        isAvailable: eligible,
                        sequence: 60,
                        icon: "sparkles",
                        description: _t("Ask Nova: summarize"),
                        callback: () => askNova(
                            self.props.resModel,
                            self._novaAiFirstSelected(),
                            _t("Summarize this record.")),
                    },
                    nova_ai_next: {
                        isAvailable: eligible,
                        sequence: 61,
                        icon: "arrow_forward",
                        description: _t("Ask Nova: next steps"),
                        callback: () => askNova(
                            self.props.resModel,
                            self._novaAiFirstSelected(),
                            _t("Suggest concrete next steps for this record.")),
                    },
                };
            },
        });
    } catch (e) {
        // eslint-disable-next-line no-console
        console.error("[nova_ai] list action patch failed:", e);
    }
}
