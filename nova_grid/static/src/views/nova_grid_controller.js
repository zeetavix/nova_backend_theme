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

export class NovaGridController extends Component {
    static template = "nova_grid.GridView";
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
            columnFieldName: archInfo.columnFieldName,
            columnFieldType: archInfo.columnFieldType,
            measureField: archInfo.measureField,
            readonlyField: archInfo.readonlyField,
            ranges: archInfo.ranges,
            activeRangeName: archInfo.activeRangeName,
            heatmapScale: archInfo.heatmapScale,
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
        return this.model.columnFieldIsDate;
    }
    // ---- KPI strip (hero numbers above the grid) ----
    fmt(v) {
        const n = Number(v || 0);
        return n ? String(Math.round(n * 100) / 100) : "0";
    }
    get weekTotal() {
        return this.model.grandTotal || 0;
    }
    get todayTotal() {
        const col = (this.model.columnsArray || []).find((c) => c.isToday);
        return col ? col.total : 0;
    }
    get activeDays() {
        return (this.model.columnsArray || []).filter((c) => c.total > 0).length;
    }
    get dailyAvg() {
        return this.activeDays ? this.weekTotal / this.activeDays : 0;
    }
    get capacityTarget() {
        const configured = parseFloat(this.props.archInfo?.kpiCapacityTarget || 0);
        if (configured > 0) {
            return configured;
        }
        // Legacy timesheet behaviour when the arch sets no target.
        return this.model.measureField?.name === "unit_amount" ? 40 : 0;
    }
    get showCapacity() {
        return this.capacityTarget > 0;
    }
    get unitSuffix() {
        const configured = this.props.archInfo?.kpiUnit;
        if (configured !== undefined && configured !== "") {
            return configured;
        }
        return this.model.measureField?.name === "unit_amount" ? "h" : "";
    }
    get capacityPct() {
        return this.capacityTarget
            ? Math.min(100, Math.round((this.weekTotal / this.capacityTarget) * 100))
            : 0;
    }
    get capacityLabel() {
        const configured = this.props.archInfo?.kpiCapacityLabel;
        if (configured) {
            return configured;
        }
        return `of ${this.capacityTarget}${this.unitSuffix} target`;
    }
    get capacityRing() {
        const c = 2 * Math.PI * 25;
        return {
            dash: c.toFixed(1),
            offset: (c * (1 - this.capacityPct / 100)).toFixed(1),
        };
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
    async onCopyPrevious() {
        await this.model.copyPreviousPeriod();
    }
    csvText(value) {
        // Quote + neutralise spreadsheet formula injection: project/task
        // names are user-controlled and may start with = + - @ | %.
        let text = String(value ?? "").replace(/\n/g, " ");
        if (/^[=+\-@|\t\r%]/.test(text)) {
            text = `'${text}`;
        }
        return `"${text.replace(/"/g, '""')}"`;
    }
    onExportCsv() {
        const model = this.model;
        const cols = model.columnsArray;
        const header = ["Row", ...cols.map((c) => this.csvText(c.title)), "Total"];
        const lines = [header.join(",")];
        for (const item of model.itemsArray) {
            if (item.isSection && !item.isFake) {
                lines.push([this.csvText(item.title), ...cols.map(() => ""), ""].join(","));
                continue;
            }
            if (item.isSection) {
                continue;
            }
            const cells = cols.map((c) => {
                const cell = item.cells[c.id];
                return cell ? Number(cell.value || 0).toFixed(2) : "0.00";
            });
            const total = cells.reduce((s, v) => s + parseFloat(v), 0).toFixed(2);
            lines.push([this.csvText(item.title), ...cells, total].join(","));
        }
        const blob = new Blob([lines.join("\n")], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `nova_grid_${model.resModel}_${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    }
}
