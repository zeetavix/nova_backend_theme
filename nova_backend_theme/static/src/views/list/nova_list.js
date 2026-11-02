/** @odoo-module **/
//
// Nova dialog drill-down — records open from lists that live inside a
// dialog (home KPI tiles, wizards with record lists).
//
// Why: ListController.openRecord delegates to props.selectRecord, and
// stock wires that to openFormView, which is a deliberate no-op when
// the action target is "new" (web action_plugin). So row clicks in ANY
// dialog list silently die — the list looks alive but nothing opens.
// This patch restores the expected behavior only where stock dead-ends:
// inside a modal, on an editable-capable (non-readonly) list, without a
// custom arch openAction. Everything else (selection dialogs, arch
// actions, main-view lists) keeps the exact stock path. The record form
// opens stacked on top via a new dialog, so the calling dialog stays
// put underneath.
//
// Clean-room: only public component + service APIs are used.
//

import { patch } from "@web/core/utils/patch";
import { ListController } from "@web/views/list/list_controller";

function rootElement(component) {
    try {
        if (component && typeof component.rootRef === "function") {
            const el = component.rootRef();
            if (el && typeof el.closest === "function") {
                return el;
            }
        }
    } catch {
        // rootRef stays unread
    }
    try {
        if (component && component.el && typeof component.el.closest === "function") {
            return component.el;
        }
    } catch {
        // el stays unread
    }
    return null;
}

function isInsideDialog(component) {
    try {
        const el = rootElement(component);
        if (el && el.closest(".o_dialog, .modal")) {
            return true;
        }
    } catch {
        // fall through to the env check below
    }
    return false;
}

patch(ListController.prototype, {
    async openRecord(record, options = {}) {
        const { force = false, newWindow = false } = options || {};
        let inDialog = isInsideDialog(this);
        let readonly = false;
        let hasArchAction = false;
        try {
            readonly = !!this.props.readonly;
        } catch {
            // props stay unread
        }
        try {
            hasArchAction = !!(this.props.allowOpenAction && this.archInfo.openAction);
        } catch {
            // arch stays unread
        }
        if (inDialog && !readonly && !hasArchAction && record && record.resId) {
            // Mirror stock's dirty-save before leaving the row.
            try {
                const dirty = await record.isDirty();
                if (dirty) {
                    await record.save();
                }
            } catch {
                // save best-effort only; the open must still happen
            }
            let activeIds = [];
            try {
                activeIds = (this.model.root.records || []).map((r) => r.resId);
            } catch {
                // pager list stays empty
            }
            let context = {};
            try {
                context = (record && record.context) || this.props.context || {};
            } catch {
                // context stays empty
            }
            let title = "";
            try {
                // RelationalRecord keeps field values under .data. Lists
                // rarely fetch display_name itself, so fall back to the
                // common name field before leaving the title empty (an
                // empty name renders as a bare "Odoo" dialog title).
                const data = (record && record.data) || {};
                title = data.display_name || data.name || "";
            } catch {
                // title stays empty
            }
            try {
                return await this.actionService.doAction(
                    {
                        type: "ir.actions.act_window",
                        name: title,
                        res_model: record.resModel,
                        res_id: record.resId,
                        resIds: activeIds,
                        views: [[false, "form"]],
                        target: "new",
                        context,
                    },
                    { newWindow }
                );
            } catch {
                // fall through to stock on doAction failure
            }
        }
        return super.openRecord(record, options);
    },
});
