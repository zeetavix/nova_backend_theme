// Part of Nova. See LICENSE file for full copyright and licensing details.
import { RelationalModel } from "@web/model/relational_model/relational_model";
import { FormCompiler } from "@web/views/form/form_compiler";
import { FormController } from "@web/views/form/form_controller";
import { FormRenderer } from "@web/views/form/form_renderer";
import { Component, onWillRender, onWillUnmount, t, useProps } from "@odoo/owl";
import { useEnv, useSubEnv } from "@web/owl2/utils";

/**
 * Form renderer for the side panel.
 *
 * Odoo decides the chatter position from the WINDOW size (full-width XXL
 * screens dock the chatter aside). Inside a docked panel that is wrong: the
 * panel is only a part of the window, so the aside lane squeezes the sheet.
 * The chatter goes aside only when the panel itself is wide enough (or when
 * the panel is expanded to full width, where the stock layout is correct).
 */
export class PanelFormRenderer extends FormRenderer {
    mailLayout(hasAttachmentContainer) {
        const layout = this.env.novaSplitLayout;
        const stockLayout = super.mailLayout;
        if (typeof stockLayout === "function" && layout && (layout.expanded || layout.wide)) {
            return stockLayout.call(this, hasAttachmentContainer);
        }
        return "BOTTOM_CHATTER";
    }
}

/**
 * Form controller for the side panel.
 *
 * - Publishes its model on the bridge after every root load, so the split
 *   controller can switch records with the same dirty-guard semantics as the
 *   form pager, without reaching into component instances.
 * - Never applies the full-window XXL layout class: that layout is a row
 *   flex that squeezes the sheet inside a docked panel.
 */
export class PanelFormController extends FormController {
    get className() {
        const result = super.className;
        delete result["o_xxl_form_view h-100"];
        // The stock View wrapper adds these classes to every form root; the
        // embedded controller bypasses that wrapper, so add them here or the
        // whole `.o_form_view` stylesheet (field grid, labels) never applies.
        result.o_form_view = true;
        result.o_view_controller = true;
        return result;
    }

    async onRootLoaded() {
        const holder = this.env.novaSplitPanel;
        if (holder && this.model) {
            holder.model = this.model;
            if (holder.onModelReady) {
                holder.onModelReady();
            }
        }
        await super.onRootLoaded();
    }
}

/**
 * Side panel hosting the form. It trims the surrounding action chrome
 * (breadcrumbs, view switcher) and record navigation (historyBack) so the
 * embedded form behaves like a docked preview instead of a full view:
 * deleting or discarding a new record closes the panel and refreshes the
 * list instead of leaving the whole action.
 */
export class SplitFormPanel extends Component {
    static template = "nova_split.SplitFormPanel";
    static components = { PanelFormController };
    props = useProps({
        arch: t.object(),
        archInfo: t.object(),
        bridge: t.object(),
        context: t.object(),
        display: t.object(),
        domain: t.array(),
        expanded: t.boolean(),
        fields: t.object(),
        groupBy: t.array(),
        info: t.object(),
        onHistoryBack: t.function(),
        onSave: t.function(),
        orderBy: t.array(),
        panelBreadcrumbs: t.array(),
        relatedModels: t.object(),
        resId: t.or([t.number(), t.boolean()]),
        resIds: t.array(),
        resModel: t.string(),
        searchMenuTypes: t.array(),
        useSampleModel: t.boolean(),
        viewId: t.or([t.number(), t.boolean()]),
        wide: t.boolean(),
    });

    setup() {
        this.FormModel = RelationalModel;
        this.FormRenderer = PanelFormRenderer;
        this.FormCompiler = FormCompiler;
        const parentConfig = useEnv().config;
        // The bridge must not outlive the form: after unmount the controller
        // drives the next record through a fresh mount instead.
        onWillUnmount(() => {
            this.props.bridge.model = null;
        });
        // Mutable holder so the embedded renderer sees expand/collapse and
        // width changes without remounting (sub-env values are fixed at
        // setup time).
        this.layoutHolder = { expanded: this.props.expanded, wide: this.props.wide };
        onWillRender(() => {
            this.layoutHolder.expanded = this.props.expanded;
            this.layoutHolder.wide = this.props.wide;
        });
        useSubEnv({
            novaSplitPanel: this.props.bridge,
            novaSplitLayout: this.layoutHolder,
            config: {
                ...parentConfig,
                breadcrumbs: this.props.panelBreadcrumbs,
                viewSwitcherEntries: [],
                // The embedded controller is a form: declare it so model
                // services (offline availability, pager) take the form path
                // instead of the multi-record one.
                viewType: "form",
                historyBack: () => this.props.onHistoryBack(),
            },
        });
    }
}
