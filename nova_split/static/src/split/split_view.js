// Part of Nova. See LICENSE file for full copyright and licensing details.
import { registry } from "@web/core/registry";
import { RelationalModel } from "@web/model/relational_model/relational_model";
import { ListArchParser } from "@web/views/list/list_arch_parser";
import { ListRenderer } from "@web/views/list/list_renderer";
import { SplitController } from "@nova_split/split/split_controller";

// The server answers split arch requests with the action's own list arch
// (see nova_split.models.base), so the left pane parses exactly like a
// stock list view: same arch, same model, same renderer.
export const splitView = {
    type: "split",
    Controller: SplitController,
    buttonTemplate: "nova_split.SplitView.Buttons",
    canOrderByCount: true,

    props: (genericProps, view) => {
        const { ArchParser } = view;
        const { relatedModels, resModel } = genericProps;
        // In split view the list is for navigation; creating and editing
        // happen in the form panel. Inline edition would be cramped and
        // conflicts with row-click-to-preview, so it is always off here.
        // The arch is cloned first: the element may be shared with other
        // views of the same action.
        const arch =
            genericProps.arch && typeof genericProps.arch.cloneNode === "function"
                ? genericProps.arch.cloneNode(true)
                : genericProps.arch;
        if (arch && typeof arch.removeAttribute === "function") {
            arch.removeAttribute("editable");
        }
        const archInfo = new ArchParser().parse(arch, relatedModels, resModel);
        return {
            ...genericProps,
            arch,
            Model: RelationalModel,
            Renderer: ListRenderer,
            buttonTemplate: genericProps.buttonTemplate || view.buttonTemplate,
            archInfo,
        };
    },
    ArchParser: ListArchParser,
};

registry.category("views").add("split", splitView);
