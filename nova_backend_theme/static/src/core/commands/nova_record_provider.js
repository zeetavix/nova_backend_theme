/** @odoo-module **/
//
// Nova record provider — "#" namespace for the command palette.
// Searches records across allowlisted models via /nova/search and opens
// the form view. Clean-room; only public registries/services are used.
//
// Namespace note: stock mail owns "@" (discuss_command_palette.js), so
// Nova uses "#" — verified free of command_setup collisions in-tree.
// Every add is contains-guarded: a future collision hides this feature
// instead of crashing the whole bundle at startup.
//

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const commandCategoryRegistry = registry.category("command_categories");
if (!commandCategoryRegistry.contains("records")) {
    commandCategoryRegistry.add("records", { namespace: "#" }, { sequence: 30 });
}

const commandSetupRegistry = registry.category("command_setup");
if (!commandSetupRegistry.contains("#")) {
    commandSetupRegistry.add("#", {
        emptyMessage: _t("No record found"),
        name: _t("records"),
        placeholder: _t("Search records…"),
        debounceDelay: 250,
    });
}

const commandProviderRegistry = registry.category("command_provider");
let recordSeq = 0;
commandProviderRegistry.add("nova_records", {
    namespace: "#",
    async provide(options) {
        const action = useService("action");
        const token = (recordSeq = (recordSeq || 0) + 1);
        let results = [];
        try {
            const searchValue = (options && options.searchValue) || "";
            if (searchValue.trim().length >= 2) {
                const res = await rpc("/nova/search", { q: searchValue, limit: 10 });
                if (token !== recordSeq) {
                    return [];
                }
                results = (res && res.results) || [];
            }
        } catch {
            results = [];
        }
        return results.map((rec) => ({
            action() {
                try {
                    action.doAction({
                        type: "ir.actions.act_window",
                        res_model: rec.model,
                        res_id: rec.res_id,
                        views: [[false, "form"]],
                        target: "current",
                    });
                } catch {
                    // stay in the palette on failure
                }
            },
            category: "records",
            name: `${rec.display_name} (${rec.model_label})`,
        }));
    },
});
