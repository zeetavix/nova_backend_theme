/** @odoo-module **/
//
// Tier badge — the capability chip the whole AI program hinges on.
// T1 outline / T2 soft fill / T3 solid, all on theme tokens: the
// handle for "what can this model actually do".
//
import { Component, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const TIER_META = {
    t1: { cls: "nova_tier-t1", label: "T1 Basic" },
    t2: { cls: "nova_tier-t2", label: "T2 Assistant" },
    t3: { cls: "nova_tier-t3", label: "T3 Agent" },
};

const TIER_TITLES = {
    t1: "Basic: text-only answers. Tools run server-side and every write needs your confirmation.",
    t2: "Assistant: reads your records and calls tools, always with confirmation.",
    t3: "Agent: plans multi-step work and may apply writes when auto-apply is on.",
};

export class NovaTierBadge extends Component {
    static template = "nova_ai.NovaTierBadge";
    props = useProps({ ...standardFieldProps });

    get value() {
        return this.props.record.data[this.props.name] || "";
    }

    get cls() {
        return (TIER_META[this.value] || {}).cls || "nova_tier-t1";
    }

    get label() {
        return (TIER_META[this.value] || {}).label || "—";
    }

    get title() {
        return TIER_TITLES[this.value] || "Capability not probed yet";
    }
}

registry.category("fields").add("nova_tier_badge", {
    component: NovaTierBadge,
});
