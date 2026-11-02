/** @odoo-module */
// Part of Nova. See LICENSE file for full copyright and licensing details.
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("nova_split_tour", {
    steps: () => [
        {
            trigger: ".o_list_renderer",
        },
        {
            trigger: ".o_switch_view.o_split",
            run: "click",
        },
        {
            trigger: ".o_nova_split_panel .o_form_sheet",
        },
        {
            trigger: ".o_nova_split_list .o_data_row:eq(1) .o_data_cell:first",
            run: "click",
        },
        {
            trigger: ".o_nova_split_list .o_data_row:eq(1).o_nova_split_active",
        },
        {
            // Clicking the row focuses the list pane (see SplitController),
            // so the arrow keys below go to the list, like real usage.
            trigger: ".o_nova_split_list",
            run: "press ArrowUp",
        },
        {
            trigger: ".o_nova_split_list .o_data_row:eq(0).o_nova_split_active",
        },
        {
            trigger: ".o_nova_split_close",
            run: "click",
        },
        {
            trigger: ".o_nova_split_toggle",
            run: "click",
        },
        {
            trigger: ".o_nova_split_panel .o_form_sheet",
        },
    ],
});
