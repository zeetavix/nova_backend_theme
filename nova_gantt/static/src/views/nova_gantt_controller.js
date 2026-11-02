// Part of Nova. See LICENSE file for full copyright and licensing details.
import { Component, signal } from "@odoo/owl";
import { Layout } from "@web/search/layout";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { useSearchBarToggler } from "@web/search/search_bar/search_bar_toggler";
import { CogMenu } from "@web/search/cog_menu/cog_menu";
import { ActionHelper } from "@web/views/action_helper";
import { useModelWithSampleData } from "@web/model/model";
import { standardViewProps } from "@web/views/standard_view_props";
import { useSetupAction } from "@web/search/action_hook";

export class NovaGanttController extends Component {
    static template = "nova_gantt.GanttView";
    static components = { Layout, SearchBar, CogMenu, ActionHelper };
    static props = {
        ...standardViewProps,
        Model: Function,
        Renderer: Function,
        buttonTemplate: String,
        archInfo: Object,
    };

    rootRef = signal.ref();

    setup() {
        this.model = useModelWithSampleData(this.props.Model, this.modelParams);
        useSetupAction({ rootRef: this.rootRef, getLocalState: () => ({}) });
        this.searchBarToggler = useSearchBarToggler();
    }

    get modelParams() {
        const archInfo = this.props.archInfo;
        return {
            sectionField: archInfo.sectionField,
            rowFields: archInfo.rowFields,
            dateStartField: archInfo.dateStartField,
            dateStopField: archInfo.dateStopField,
            columnFieldType: archInfo.columnFieldType,
            kpiUnit: archInfo.kpiUnit,
            ranges: archInfo.ranges,
            activeRangeName: archInfo.activeRangeName,
            resModel: this.props.resModel,
            fieldsInfo: this.props.fields,
            defaultAnchor: undefined,
        };
    }

    get rendererProps() {
        return {
            archInfo: this.props.archInfo,
            model: this.model,
        };
    }

    get showEmpty() {
        return this.model.isReady() && !this.model.hasData();
    }

    get showContent() {
        return this.model.isReady() && !this.showEmpty;
    }

    get rangeEntries() {
        return Object.values(this.props.archInfo.ranges || {});
    }

    get activeRangeName() {
        return this.model.activeRange?.name;
    }

    get showCopyPrevious() {
        return false;
    }

    // ---- KPI strip (hero numbers above the timeline) ----
    fmt(v) {
        const n = Number(v || 0);
        return n ? String(Math.round(n * 100) / 100) : "0";
    }
    get unitSuffix() {
        return this.props.archInfo?.kpiUnit || "";
    }

    async onRangeChange(name) {
        await this.model.setRange(name);
    }
    async onPrev() {
        await this.model.moveAnchor("backward");
    }
    async onNext() {
        await this.model.moveAnchor("forward");
    }
    async onToday() {
        await this.model.goToday();
    }
    onToggleWeekend() {
        this.model.showWeekend = !this.model.showWeekend;
        this.model.notify();
    }
    csvText(value) {
        // Quote + neutralise spreadsheet formula injection: record names are
        // user-controlled and may start with = + - @ | % or a quote char.
        let text = String(value ?? "").replace(/\n/g, " ");
        if (/^[=+\-@|\t\r%]/.test(text)) {
            text = `'${text}`;
        }
        return `"${text.replace(/"/g, '""')}"`;
    }
    onExportCsv() {
        const model = this.model;
        const header = ["Lane", "Item", "Start", "Stop", "Days"].map((h) => this.csvText(h));
        const lines = [header.join(",")];
        for (const bar of model.barsArray) {
            const lane = bar.lane ? (bar.lane.section && !bar.lane.section.isFake ? `${bar.lane.section.title} / ` : "") + (bar.lane.title || "") : "";
            lines.push(
                [
                    this.csvText(lane),
                    this.csvText(bar.label),
                    this.csvText(model.serializeDay(bar.start)),
                    this.csvText(model.serializeDay(bar.stop)),
                    bar.days,
                ].join(",")
            );
        }
        const blob = new Blob([lines.join("\n")], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `nova_gantt_${model.resModel}_${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    }
}
