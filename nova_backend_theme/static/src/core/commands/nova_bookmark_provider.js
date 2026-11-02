/** @odoo-module **/
//
// Nova starred provider — "Starred" section in the command palette.
// Reads the shared bookmark store (populated by the launcher); resolves
// menus live so deleted items vanish. Clean-room original.
//

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { bookmarkState, setBookmarks } from "@nova_backend_theme/home/nova_bookmark_store";

const commandCategoryRegistry = registry.category("command_categories");
if (!commandCategoryRegistry.contains("starred")) {
    commandCategoryRegistry.add("starred", { namespace: "/" }, { sequence: 5 });
}

function currentFormRecord(action) {
    // Pin only a mono-record form: resModel + numeric resId on a form view,
    // never lists, client actions, or unsaved records. Dialogs render above
    // the stack, so the background form stays the pinnable one.
    try {
        const controller = action && action.currentController;
        if (!controller || !controller.props) {
            return null;
        }
        const props = controller.props;
        const resModel = props.resModel;
        const resId = Number(controller.currentState?.resId ?? props.resId);
        const viewType = controller.view?.type || props.type;
        if (!resModel || !resId || viewType !== "form") {
            return null;
        }
        if (resModel === "nova.bookmark") {
            return null;
        }
        let label = resModel;
        try {
            label = String(controller.displayName || resModel).slice(0, 120);
        } catch {
            label = resModel;
        }
        return { model: resModel, res_id: resId, display_name: label };
    } catch {
        return null;
    }
}

const commandProviderRegistry = registry.category("command_provider");
commandProviderRegistry.add("nova_starred", {
    namespace: "/",
    provide() {
        const menu = useService("menu");
        const action = useService("action");
        const orm = useService("orm");
        const notification = useService("notification");
        const result = [];
        let menuIds = [];
        let records = [];
        try {
            menuIds = bookmarkState.menus() || [];
        } catch {
            menuIds = [];
        }
        try {
            records = bookmarkState.records() || [];
        } catch {
            records = [];
        }
        for (const menuId of menuIds) {
            let node = null;
            try {
                node = menu.getMenu(Number(menuId));
            } catch {
                node = null;
            }
            if (!node || !node.actionID) {
                continue;
            }
            result.push({
                action() {
                    try {
                        menu.selectMenu(node);
                    } catch {
                        // stay in the palette on failure
                    }
                },
                category: "starred",
                name: node.name,
            });
        }
        for (const rec of records) {
            if (!rec || !rec.res_model || !rec.res_id) {
                continue;
            }
            result.push({
                action() {
                    try {
                        action.doAction({
                            type: "ir.actions.act_window",
                            res_model: rec.res_model,
                            res_id: rec.res_id,
                            views: [[false, "form"]],
                            target: "current",
                        });
                    } catch {
                        // stay in the palette on failure
                    }
                },
                category: "starred",
                name: `${rec.name || rec.res_model}`,
            });
        }
        // Pin/unpin the record under the palette — no new routes, reuses
        // toggle_record; hidden entirely when no form record is open.
        try {
            const current = currentFormRecord(action);
            if (current) {
                let pinned = false;
                try {
                    pinned = records.some(
                        (r) => r && r.res_model === current.model && Number(r.res_id) === current.res_id
                    );
                } catch {
                    pinned = false;
                }
                result.push({
                    action: async () => {
                        try {
                            const state = await orm.call("nova.bookmark", "toggle_record", [
                                current.model,
                                current.res_id,
                                current.display_name,
                            ]);
                            try {
                                setBookmarks(
                                    Array.isArray(state?.menus) ? state.menus : bookmarkState.menus(),
                                    Array.isArray(state?.records) ? state.records : bookmarkState.records()
                                );
                            } catch {
                                // store stays put
                            }
                            try {
                                window.dispatchEvent(
                                    new CustomEvent("nova:bookmarks-changed", { detail: state })
                                );
                            } catch {
                                // dock refreshes next visit
                            }
                            try {
                                notification.add(
                                    pinned ? _t("Removed from Nova dock.") : _t("Pinned to Nova dock."),
                                    { type: "success" }
                                );
                            } catch {
                                // toast optional
                            }
                        } catch {
                            // palette stays open on failure
                        }
                    },
                    category: "starred",
                    name: pinned ? _t("Unpin current record") : _t("Pin current record"),
                });
            }
        } catch {
            // pin item stays hidden
        }
        return result;
    },
});
