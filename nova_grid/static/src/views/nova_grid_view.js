// Part of Nova. See LICENSE file for full copyright and licensing details.
import { registry } from "@web/core/registry";
import { NovaGridArchParser } from "@nova_grid/views/nova_grid_arch_parser";
import { NovaGridController } from "@nova_grid/views/nova_grid_controller";
import { NovaGridModel } from "@nova_grid/views/nova_grid_model";
import { NovaGridRenderer } from "@nova_grid/views/nova_grid_renderer";

export const novaGridView = {
    type: "grid",
    ArchParser: NovaGridArchParser,
    Controller: NovaGridController,
    Model: NovaGridModel,
    Renderer: NovaGridRenderer,
    buttonTemplate: "nova_grid.GridView.Buttons",

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

registry.category("views").add("grid", novaGridView);
