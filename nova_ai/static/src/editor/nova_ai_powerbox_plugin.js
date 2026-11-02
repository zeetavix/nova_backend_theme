import { _t } from "@web/core/l10n/translation";
import { Plugin } from "@html_editor/plugin";
import { isHtmlContentSupported } from "@html_editor/core/selection_plugin";
import { withSequence } from "@html_editor/utils/resource";
import { MAIN_PLUGINS } from "@html_editor/plugin_sets";
import { user } from "@web/core/user";
import { NovaAiPromptDialog } from "./nova_ai_prompt_dialog";

export class NovaAiPowerboxPlugin extends Plugin {
    static id = "novaAiPowerbox";
    static dependencies = ["dom", "history", "selection"];

    resources = {
        powerbox_categories: withSequence(75, {
            id: "nova_ai",
            name: _t("Nova AI"),
        }),
        user_commands: [
            {
                id: "novaAiInsert",
                title: _t("Ask Nova AI"),
                description: _t("Generate or rewrite content with AI"),
                icon: "wand_stars",
                run: this.openDialog.bind(this),
                isAvailable: (selection) =>
                    isHtmlContentSupported(selection) && user.isInternalUser,
            },
        ],
        powerbox_items: withSequence(1, {
            categoryId: "nova_ai",
            commandId: "novaAiInsert",
            keywords: [_t("AI"), _t("nova"), _t("write"), _t("rewrite"), _t("generate")],
        }),
        power_buttons: withSequence(30, {
            commandId: "novaAiInsert",
            icon: "wand_stars",
        }),
    };

    async openDialog() {
        const selection = this.dependencies.selection.getEditableSelection();
        const selectedText = selection ? selection.toString().trim() : "";
        let recordInfo = {};
        try {
            const info = this.config.getRecordInfo
                ? this.config.getRecordInfo()
                : {};
            recordInfo = { model: info.resModel, res_id: info.resId };
        } catch {
            // no record info — standalone editor
        }
        this.services.dialog.add(NovaAiPromptDialog, {
            selection: selectedText,
            recordInfo: recordInfo,
            insert: (content) => {
                // dom.insert treats strings as plain text; parse the
                // markdown-rendered HTML into a fragment so tags become
                // elements, not visible markup.
                const tpl = this.document.createElement("template");
                tpl.innerHTML = String(content);
                this.dependencies.dom.insert(tpl.content);
                this.dependencies.history.commit();
            },
        });
    }
}

MAIN_PLUGINS.push(NovaAiPowerboxPlugin);
