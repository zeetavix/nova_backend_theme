// Part of Nova. See LICENSE file for full copyright and licensing details.
import { registry } from "@web/core/registry";
import { NovaGanttArchParser } from "@nova_gantt/views/nova_gantt_arch_parser";
import { NovaGanttController } from "@nova_gantt/views/nova_gantt_controller";
import { NovaGanttModel } from "@nova_gantt/views/nova_gantt_model";
import { NovaGanttRenderer } from "@nova_gantt/views/nova_gantt_renderer";

export const novaGanttView = {
    type: "gantt",
    ArchParser: NovaGanttArchParser,
    Controller: NovaGanttController,
    Model: NovaGanttModel,
    Renderer: NovaGanttRenderer,
    buttonTemplate: "nova_gantt.GanttView.Buttons",

    props: (genericProps, view) => {
        const { ArchParser, Model, Renderer, buttonTemplate } = view;
        const { arch, relatedModels, resModel } = genericProps;
        return {
            ...genericProps,
            archInfo: new ArchParser().parse(arch, relatedModels, resModel),
            buttonTemplate: genericProps.buttonTemplate || buttonTemplate,
            Model,
            Renderer,
        };
    },
};

registry.category("views").add("gantt", novaGanttView);
