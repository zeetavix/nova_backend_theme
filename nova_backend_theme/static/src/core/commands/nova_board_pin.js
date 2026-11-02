/** @odoo-module **/
//
// Nova board pin — "Pin view to dashboard" in the command palette.
// Same context recipe as stock add_to_board (domain + pre-favorite values),
// same route, same toasts. Hidden unless: a list/graph-type act_window is
// open, the board cog item exists (board installed), and a live searchModel
// was stashed by WithSearch.
//

import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { onWillUnmount } from "@odoo/owl";
import { WithSearch } from "@web/search/with_search/with_search";
import { getBoardContext, setBoardContext } from "@nova_backend_theme/core/commands/nova_board_context";

try {
    patch(WithSearch.prototype, {
        setup() {
            super.setup();
            try {
                let displayName = "";
                try {
                    displayName = this.env?.config?.getDisplayName?.() || "";
                } catch {
                    displayName = "";
                }
                setBoardContext({ searchModel: this.searchModel, displayName });
            } catch {
                setBoardContext(null);
            }
            // Clear only our own stash so a departed search view never
            // serves a stale model to the palette.
            onWillUnmount(() => {
                try {
                    if (getBoardContext()?.searchModel === this.searchModel) {
                        setBoardContext(null);
                    }
                } catch {
                    // stash stays put
                }
            });
        },
    });
} catch (e) {
    // eslint-disable-next-line no-console
    console.error("[nova] board context patch failed:", e);
}

function boardAvailable() {
    try {
        return registry.category("cogMenu").contains("add-to-board");
    } catch {
        return false;
    }
}

function pinnableController(action) {
    try {
        const controller = action && action.currentController;
        if (!controller || !controller.config) {
            return null;
        }
        const { actionType, actionId } = controller.config;
        const viewType = controller.props?.type;
        if (actionType !== "ir.actions.act_window" || !actionId || !viewType || viewType === "form") {
            return null;
        }
        return controller;
    } catch {
        return null;
    }
}

const commandProviderRegistry = registry.category("command_provider");
commandProviderRegistry.add("nova_board_pin", {
    namespace: "/",
    async provide() {
        const action = useService("action");
        const notification = useService("notification");
        const result = [];
        try {
            const controller = pinnableController(action);
            const ctx = getBoardContext();
            if (!controller || !ctx || !ctx.searchModel || !boardAvailable()) {
                return result;
            }
            let name = "";
            try {
                name = String(ctx.displayName || controller.displayName || "");
            } catch {
                name = "";
            }
            result.push({
                action: async () => {
                    try {
                        const searchModel = getBoardContext()?.searchModel;
                        if (!searchModel) {
                            return;
                        }
                        const { domain, globalContext } = searchModel;
                        const { context, groupBys, orderBy } = searchModel.getPreFavoriteValues();
                        const contextToSave = {
                            ...Object.fromEntries(
                                Object.entries(globalContext || {}).filter(
                                    (entry) => !entry[0].startsWith("search_default_")
                                )
                            ),
                            ...(context || {}),
                            orderedBy: orderBy,
                            group_by: groupBys,
                            dashboard_merge_domains_contexts: false,
                        };
                        const ok = await rpc("/board/add_to_dashboard", {
                            action_id: controller.config.actionId || false,
                            context_to_save: contextToSave,
                            domain: domain || [],
                            name,
                            view_mode: controller.props.type,
                        });
                        if (ok) {
                            notification.add(
                                _t("Please refresh your browser for the changes to take effect."),
                                { title: _t("%s added to dashboard", name), type: "warning" }
                            );
                        } else {
                            notification.add(_t("Could not add filter to dashboard"), {
                                type: "danger",
                            });
                        }
                    } catch {
                        // palette stays open on failure
                    }
                },
                category: "starred",
                name: _t("Pin view to dashboard"),
            });
        } catch {
            // pin item stays hidden
        }
        return result;
    },
});
