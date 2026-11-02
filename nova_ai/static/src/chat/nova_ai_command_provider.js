/** @odoo-module **/
//
// Ask AI in the command palette. Any query longer than a couple of
// characters gets one top command per allowed agent: "Ask {agent}: …"
// which opens the drawer with the text prefilled. Dispatches a DOM
// event so the drawer (a systray island) needs no shared service.
//

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

const commandCategoryRegistry = registry.category("command_categories");
if (!commandCategoryRegistry.contains("ai")) {
    commandCategoryRegistry.add("ai", { namespace: "?" }, { sequence: 25 });
}

const commandProviderRegistry = registry.category("command_provider");
let askSeq = 0;
commandProviderRegistry.add("nova_ai", {
    namespace: "?",
    async provide(options) {
        const token = (askSeq = (askSeq || 0) + 1);
        const searchValue = ((options && options.searchValue) || "").trim();
        if (searchValue.length < 2) {
            return [];
        }
        let agents = [];
        try {
            agents = (((await rpc("/nova/ai/agents", {})) || {}).agents || []);
        } catch {
            agents = [];
        }
        if (token !== askSeq) {
            return [];
        }
        return agents.slice(0, 3).map((agent) => ({
            action() {
                try {
                    window.dispatchEvent(new CustomEvent("nova:ai-open", {
                        detail: { message: searchValue, agentId: agent.id },
                    }));
                } catch {
                    // drawer stays shut
                }
            },
            category: "ai",
            name: `Ask ${agent.name}: ${searchValue}`,
        }));
    },
});
