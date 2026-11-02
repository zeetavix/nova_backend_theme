// Part of Nova. See LICENSE file for full copyright and licensing details.
// Nova grid renderer: sticky first column, heatmap cells, totals, keyboard nav.
import { Component, onMounted, onWillUnmount, proxy, signal } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class NovaGridRenderer extends Component {
    static template = "nova_grid.GridRenderer";
    static props = { archInfo: Object, model: Object };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.ui = proxy({ editingKey: null, draft: "" });
        this.collapsed = proxy({});
        this.tableRef = signal.ref();
        onMounted(() => {
            const el = this.tableRef.el;
            if (!el) {
                return;
            }
            this._onGridScroll = () => {
                el.classList.toggle("o_scrolled_x", el.scrollLeft > 0);
                el.classList.toggle("o_scrolled_y", el.scrollTop > 0);
            };
            el.addEventListener("scroll", this._onGridScroll, { passive: true });
            this._onGridScroll();
        });
        onWillUnmount(() => {
            this.tableRef.el?.removeEventListener("scroll", this._onGridScroll);
        });
    }

    get model() {
        return this.props.model;
    }
    get archInfo() {
        return this.props.archInfo;
    }
    get columns() {
        const cols = this.model.columnsArray || [];
        return this.model.showWeekend ? cols : cols.filter((c) => c.isWeekDay !== false);
    }
    get items() {
        return this.model.itemsArray || [];
    }
    isCollapsed(item) {
        return !!this.collapsed[item.id];
    }
    toggleSection(item) {
        this.collapsed[item.id] = !this.collapsed[item.id];
    }
    get visibleItems() {
        // Section headers always render; collapsing hides their rows.
        const out = [];
        for (const item of this.items) {
            if (item.isSection && !item.isFake) {
                out.push(item);
                continue;
            }
            if (item.isSection) {
                continue;
            }
            const section = item.section;
            if (section && !section.isFake && this.isCollapsed(section)) {
                continue;
            }
            out.push(item);
        }
        return out;
    }
    get showLineTotal() {
        return !this.archInfo.hideLineTotal;
    }
    get showColumnTotal() {
        return !this.archInfo.hideColumnTotal;
    }
    get grandTotal() {
        return this.model.grandTotal || 0;
    }
    get rowHeaderTitle() {
        const fieldsInfo = this.model.fieldsInfo || {};
        const labels = (this.archInfo.rowFields || [])
            .map((f) => fieldsInfo[f.name]?.string)
            .filter(Boolean);
        if (this.archInfo.sectionField) {
            const sectionLabel = fieldsInfo[this.archInfo.sectionField.name]?.string;
            if (sectionLabel) {
                labels.unshift(sectionLabel);
            }
        }
        return labels.join(" / ") || "Row";
    }

    rowCells(row) {
        return this.columns.map((c) => row.cells[c.id]).filter(Boolean);
    }

    cellKey(row, col) {
        return `${row.id}:${col.id}`;
    }

    isEditing(row, col) {
        return this.ui.editingKey === this.cellKey(row, col);
    }

    startEdit(row, col) {
        const cell = row.cells[col.id];
        if (!cell || cell.readonly || !this.archInfo.editable) {
            return;
        }
        this.ui.editingKey = this.cellKey(row, col);
        this.ui.draft = this.formatCell(cell.value);
    }

    formatCell(value) {
        const v = Number(value || 0);
        if (!v) {
            return "";
        }
        return String(Math.round(v * 100) / 100);
    }

    parseDraft(text) {
        if (text == null) {
            return 0;
        }
        const t = String(text).trim().replace(",", ".");
        if (!t) {
            return 0;
        }
        // Quick entry: "1:30" / "1h30" -> 1.5
        const hm = t.match(/^(-?\d+)[h:](\d{1,2})$/);
        if (hm) {
            const sign = hm[1].startsWith("-") ? -1 : 1;
            return sign * (Math.abs(parseInt(hm[1], 10)) + parseInt(hm[2], 10) / 60);
        }
        const n = Number(t);
        return Number.isFinite(n) ? n : NaN;
    }

    async commitEdit(row, col) {
        const key = this.cellKey(row, col);
        if (this.ui.editingKey !== key) {
            return;
        }
        this.ui.editingKey = null;
        const parsed = this.parseDraft(this.ui.draft);
        if (Number.isNaN(parsed)) {
            return;
        }
        await row.cells[col.id]?.update(parsed);
    }

    cancelEdit() {
        this.ui.editingKey = null;
    }

    onCellKeydown(ev, row, col) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.commitEdit(row, col);
            this.focusNeighbour(row, col, 1);
        } else if (ev.key === "Escape") {
            this.cancelEdit();
        } else if (ev.key === "Tab") {
            ev.preventDefault();
            this.commitEdit(row, col);
            this.focusNeighbour(row, col, ev.shiftKey ? -1 : 1);
        }
    }

    focusNeighbour(row, col, dx) {
        const cols = this.columns;
        const idx = cols.findIndex((c) => c.id === col.id);
        const next = cols[idx + dx];
        if (!next) {
            return;
        }
        requestAnimationFrame(() => {
            const el = this.tableRef.el?.querySelector(
                `[data-cell="${row.id}:${next.id}"] input, [data-cell="${row.id}:${next.id}"] button`
            );
            el?.focus?.();
        });
    }

    heatStyle(cell) {
        if (!this.archInfo.showHeatmap || !cell.value) {
            return "";
        }
        const intensity = Math.min(1, Number(cell.value) / (this.archInfo.heatmapScale || 8));
        // Expose the intensity only; the palette (and its dark-mode damping)
        // lives in nova_grid.scss / nova_grid.dark.scss.
        const pct = Math.round(10 + intensity * 22);
        return `--ng-heat: ${pct}%;`;
    }
    avatarInitial(item) {
        const title = (item.title || "").trim();
        return title ? [...title][0].toUpperCase() : "–";
    }
    avatarStyle(item) {
        const title = item.title || "?";
        let hash = 0;
        for (const ch of title) {
            hash = (hash * 31 + ch.codePointAt(0)) % 360;
        }
        // Pastel circle that reads on both light and dark surfaces.
        return `background: hsl(${hash}, 60%, 86%); color: hsl(${hash}, 45%, 32%);`;
    }

    async addRow() {
        try {
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: this.model.resModel,
                views: [[false, "form"]],
                view_mode: "form",
                target: "new",
                context: this.model.searchParams.context,
            });
        } catch {
            // Dialogs are unavailable in some contexts; a reload still helps.
        }
        await this.model.reload();
    }

    fmtTotal(v) {
        const n = Number(v || 0);
        return n ? String(Math.round(n * 100) / 100) : "–";
    }
    sectionTotal(item) {
        const total = this.fmtTotal(item.getTotal(this.model.showWeekend));
        const unit = this.model.measureField?.string || "";
        return unit && unit.length <= 6 ? `${total} ${unit}` : total;
    }
    isOvertime(cell) {
        // Day-level overload marker (never on money/count grids).
        // Timesheets keep the legacy rule (unit_amount > 8); other grids
        // opt in explicitly with overtime_at="8".
        const at = this.archInfo?.overtimeAt
            || (this.model.measureField?.name === "unit_amount" ? 8 : 0);
        return at > 0 && Number(cell.value) > at;
    }
}
