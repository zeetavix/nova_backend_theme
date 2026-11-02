// Part of Nova. See LICENSE file for full copyright and licensing details.
// Clean-room Gantt data model. One search_read per load; lanes are grouped
// client-side by section/row fields, bars span day columns between the
// record's start/stop dates (datetime values are bucketed by local day).
import { Domain } from "@web/core/domain";
import { serializeDate } from "@web/core/l10n/dates";
import { localization } from "@web/core/l10n/localization";
import { _t } from "@web/core/l10n/translation";
import { KeepLast, Mutex } from "@web/core/utils/concurrency";
import { useService } from "@web/core/utils/hooks";
import { Model } from "@web/model/model";
import { browser } from "@web/core/browser/browser";
import { signal } from "@odoo/owl";

const { DateTime, Interval } = luxon;

// ---------------------------------------------------------------------------
// Navigation (mirrors the grid: anchor + span windows)
// ---------------------------------------------------------------------------

export class NovaGanttNavigation {
    constructor(anchor, model) {
        this.anchor = anchor;
        this.model = model;
    }
    get range() {
        return this.model.activeRange;
    }
    get periodStart() {
        if (this.range.span === "week") {
            const firstDay = localization.weekStart;
            const delta = (this.anchor.weekday - firstDay + 7) % 7;
            return this.anchor.minus({ days: delta }).startOf("day");
        }
        return this.anchor.startOf(this.range.span);
    }
    get periodEnd() {
        if (this.range.span === "week") {
            return this.periodStart.plus({ weeks: 1 }).minus({ milliseconds: 1 });
        }
        return this.anchor.endOf(this.range.span);
    }
    get interval() {
        return Interval.fromDateTimes(this.periodStart, this.periodEnd);
    }
    get label() {
        const start = this.periodStart;
        const end = this.periodEnd;
        const opts = { month: "short", day: "numeric" };
        const locale = this.anchor.locale;
        const fmt = (d) => d.setLocale(locale).toFormat("MMM d");
        if (this.range.span === "month") {
            return start.setLocale(locale).toFormat("MMMM yyyy");
        }
        if (start.hasSame(end, "month")) {
            return `${fmt(start)} – ${end.setLocale(locale).toFormat("d, yyyy")}`;
        }
        return `${fmt(start)} – ${fmt(end)}`;
    }
}

// ---------------------------------------------------------------------------
// Bars / lanes / columns
// ---------------------------------------------------------------------------

export class NovaGanttColumn {
    constructor(model, start, stop, key) {
        this.model = model;
        this.start = start;
        this.stop = stop;
        this.id = model.columnId++;
        this.headerTop = "";
        this.headerSub = "";
        const isWeekDay = start.weekday < 6;
        this.isWeekDay = isWeekDay;
        this.isToday = start.startOf("day").equals(model.today.startOf("day"));
        this.title = "";
    }
}

export class NovaGanttLane {
    constructor(data, domain, values, model, section) {
        this.data = data;
        this._domain = domain;
        this.values = values;
        this.model = model;
        this.section = section;
        this.id = model.rowId++;
        this.bars = [];
    }
    get key() {
        return this._key;
    }
    set key(v) {
        this._key = v;
    }
    get domain() {
        if (this.section && !this.section.isFake) {
            return Domain.and([this.section.domain, this._domain]);
        }
        return this._domain;
    }
    get title() {
        const parts = [];
        for (const rowField of this.model.rowFields) {
            let label = this.values[rowField.name];
            const info = this.model.fieldsInfo[rowField.name];
            if (!info) {
                continue;
            }
            if (info.type === "many2one") {
                label = Array.isArray(label) ? label[1] : label;
            }
            if (label || label === 0) {
                parts.push(String(label));
            }
        }
        return parts.join(" / ") || _t("No Group");
    }
    get count() {
        return this.bars.length;
    }
}

export class NovaGanttSection {
    constructor(data, domain, values, model) {
        this.data = data;
        this.domain = domain;
        this.values = values;
        this.model = model;
        this.id = model.rowId++;
        this.isFake = !model.sectionField;
        this.lanes = [];
        const fname = model.sectionField?.name;
        this._sectionFieldName = fname;
        const raw = fname ? values[fname] : null;
        const info = fname ? model.fieldsInfo[fname] : null;
        this.title =
            info?.type === "many2one" && Array.isArray(raw)
                ? raw[1] || _t("None")
                : raw || _t("None");
    }
}

export class NovaGanttBar {
    constructor(model, lane, record) {
        this.model = model;
        this.lane = lane;
        this.record = record;
        this.id = record.id;
        this.saving = signal(false);
    }
    get label() {
        return this.record.display_name || `#${this.record.id}`;
    }
    get start() {
        return this.model.toDay(this.record[this.model.dateStartField]);
    }
    get stop() {
        const raw = this.record[this.model.dateStopField];
        return raw ? this.model.toDay(raw) : this.start;
    }
    get days() {
        return Math.max(1, Math.round(this.stop.diff(this.start, "days").days) + 1);
    }
}

// ---------------------------------------------------------------------------
// Model
// ---------------------------------------------------------------------------

export class NovaGanttModel extends Model {
    static Lane = NovaGanttLane;
    static Section = NovaGanttSection;
    static Bar = NovaGanttBar;
    static Column = NovaGanttColumn;
    static Navigation = NovaGanttNavigation;

    setup(params, services) {
        this.notificationService = services.notification;
        this.actionService = services.action;
        this.keepLast = new KeepLast();
        this.mutex = new Mutex();
        this.defaultSectionField = params.sectionField;
        this.defaultRowFields = params.rowFields || [];
        this.sectionField = undefined;
        this.rowFields = [];
        this.searchParams = {};
        this.dateStartField = params.dateStartField;
        this.dateStopField = params.dateStopField;
        this.columnFieldType = params.columnFieldType || "";
        this.kpiUnit = params.kpiUnit || "";
        this.resModel = params.resModel;
        this.fieldsInfo = params.fieldsInfo;
        this.ranges = params.ranges || {};
        this.activeRangeName = params.activeRangeName;
        this.data = undefined;
        this.record = undefined;
        this.defaultAnchor = params.defaultAnchor || DateTime.local().startOf("day");
        this.activeRange = this.ranges[this._storedRangeName(params)] || this.ranges[this.activeRangeName] || Object.values(this.ranges)[0];
        this.navigation = new this.constructor.Navigation(this.defaultAnchor, this);
        this.unavailable = {};
        this._showWeekend = true;
        this._resetIds();
    }

    load(params = {}) {
        const searchParams = { ...this.searchParams, ...params };
        this.searchParams = searchParams;
        // Lanes follow the arch defaults: search-view Group By never
        // restructures them (same rule as the grid engine).
        const invisible = (f) => f.invisible === "True" || f.invisible === "1";
        this.sectionField = undefined;
        if (this.defaultSectionField && !invisible(this.defaultSectionField)) {
            this.sectionField = this.defaultSectionField;
        }
        this.rowFields = (this.defaultRowFields || []).filter((r) => !invisible(r));
        return this.keepLast.add(this.loadData());
    }

    get today() {
        return DateTime.local().startOf("day");
    }
    get lanesArray() {
        return this.data?.lanes || [];
    }
    get sectionsArray() {
        return this.data?.sections || [];
    }
    get columnsArray() {
        return this.data?.columns || [];
    }
    get barsArray() {
        return this.data?.bars || [];
    }
    hasData() {
        return (this.barsArray.length || 0) > 0;
    }
    isReady() {
        return !!this.data;
    }
    get showWeekend() {
        return this._showWeekend !== false;
    }
    set showWeekend(v) {
        this._showWeekend = v;
    }

    _storedRangeName(params) {
        try {
            return browser.localStorage.getItem(this.storageKey) || params.activeRangeName;
        } catch {
            return params.activeRangeName;
        }
    }
    get storageKey() {
        return `nova_gantt_range_${this.env.config.viewId}`;
    }
    _resetIds() {
        this.columnId = 0;
        this.rowId = 0;
    }

    async setRange(name) {
        this.activeRange = this.ranges[name];
        try {
            browser.localStorage.setItem(this.storageKey, name);
        } catch {}
        await this.reload();
    }
    async moveAnchor(direction) {
        const span = this.activeRange.span;
        this.navigation.anchor =
            direction === "forward"
                ? this.navigation.anchor.plus({ [span]: 1 })
                : this.navigation.anchor.minus({ [span]: 1 });
        await this.reload();
    }
    async goToday() {
        this.navigation.anchor = this.today;
        await this.reload();
    }
    toggleWeekend() {
        this.showWeekend = !this.showWeekend;
        this.notify();
    }

    navigationDomain() {
        const { periodStart, periodEnd } = this.navigation;
        if (this.columnFieldType === "datetime") {
            // Datetime bounds compare against full timestamps: a bare date
            // is midnight, so the upper bound must be exclusive-next-day or
            // same-day afternoon records would fall outside the window.
            return new Domain([
                "&",
                [this.dateStartField, "<", serializeDate(periodEnd.plus({ days: 1 }))],
                "|",
                [this.dateStopField, ">=", serializeDate(periodStart)],
                [this.dateStopField, "=", false],
            ]);
        }
        return new Domain([
            "&",
            [this.dateStartField, "<=", serializeDate(periodEnd)],
            "|",
            [this.dateStopField, ">=", serializeDate(periodStart)],
            [this.dateStopField, "=", false],
        ]);
    }

    toDay(value) {
        if (!value) {
            return null;
        }
        if (typeof value === "string" && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(value)) {
            return DateTime.fromSQL(value, { zone: "utc" }).toLocal().startOf("day");
        }
        const dt = value instanceof DateTime ? value : DateTime.fromISO(String(value));
        return dt.isValid ? dt.startOf("day") : null;
    }
    serializeDay(day) {
        return serializeDate(day);
    }

    fetchFields() {
        const fields = new Set(["display_name", this.dateStartField, this.dateStopField]);
        if (this.sectionField) {
            fields.add(this.sectionField.name);
        }
        for (const r of this.rowFields) {
            fields.add(r.name);
        }
        return [...fields].filter(Boolean);
    }

    async reload(params = {}) {
        const searchParams = { ...this.searchParams, ...params };
        this.searchParams = searchParams;
        await this.keepLast.add(this.loadData());
        this.notify();
    }

    async loadData() {
        this._resetIds();
        const data = { columns: [], columnsKeyToId: {}, lanes: [], lanesKeyToId: {}, sections: [], sectionsKeyToId: {}, bars: [], items: [] };
        this._buildDateColumns(data);
        const domain = Domain.and([this.searchParams.domain || [], this.navigationDomain()]).toList({});
        const records = await this.orm.call(this.resModel, "nova_gantt_fetch", [domain, this.fetchFields()], {});
        this._buildLanes(data, records || []);
        this.data = data;
        return data;
    }

    _buildDateColumns(data) {
        const next = (d) => d.plus({ days: 1 });
        for (let current = this.navigation.periodStart.startOf("day"); current < this.navigation.periodEnd; current = next(current)) {
            const key = serializeDate(current);
            const column = new this.constructor.Column(this, current, next(current), key);
            if (this.activeRange.step !== "day") {
                column._headerTop = "";
                column._headerSub = String(current.day);
            } else {
                column._headerTop = current.setLocale(current.locale).toFormat("EEE").toUpperCase();
                column._headerSub = String(current.day);
            }
            column.title = current.toFormat("EEE MMM d");
            data.columns[column.id] = column;
            data.columnsKeyToId[key] = column.id;
        }
    }

    _laneKey(record) {
        const parts = [];
        if (this.sectionField) {
            parts.push(`S${this._fieldKey(record[this.sectionField.name])}`);
        } else {
            parts.push("Sfalse");
        }
        for (const rowField of this.rowFields) {
            parts.push(`${String(this._fieldKey(record[rowField.name]))}`);
        }
        return parts.join("|");
    }
    _fieldKey(value) {
        if (Array.isArray(value)) {
            return value[0];
        }
        return value ?? false;
    }
    _fieldLabel(record, fname) {
        const value = record[fname];
        const info = this.fieldsInfo[fname];
        if (info?.type === "many2one") {
            return Array.isArray(value) ? value[1] : value;
        }
        return value;
    }

    _buildLanes(data, records) {
        for (const record of records) {
            if (!record[this.dateStartField]) {
                continue;
            }
            const key = this._laneKey(record);
            let lane = data.lanesKeyToId[key] !== undefined ? data.lanes[data.lanesKeyToId[key]] : null;
            if (!lane) {
                const domain = this._laneDomain(record);
                const values = {};
                if (this.sectionField) {
                    values[this.sectionField.name] = record[this.sectionField.name];
                }
                for (const rowField of this.rowFields) {
                    values[rowField.name] = record[rowField.name];
                }
                let section = null;
                if (this.sectionField) {
                    const sectionKey = `S${String(this._fieldKey(record[this.sectionField.name]))}`;
                    if (data.sectionsKeyToId[sectionKey] === undefined) {
                        section = new this.constructor.Section(data, new Domain([[this.sectionField.name, "=", this._fieldKey(record[this.sectionField.name])]]), { [this.sectionField.name]: record[this.sectionField.name] }, this);
                        data.sectionsKeyToId[sectionKey] = data.sections.length;
                        data.sections.push(section);
                    } else {
                        section = data.sections[data.sectionsKeyToId[sectionKey]];
                    }
                } else if (!data.sections.length) {
                    section = new this.constructor.Section(data, new Domain(), {}, this);
                    section.isFake = true;
                    data.sectionsKeyToId["Sfalse"] = data.sections.length;
                    data.sections.push(section);
                } else {
                    section = data.sections[0];
                }
                lane = new this.constructor.Lane(data, domain, values, this, section);
                lane.key = key;
                data.lanesKeyToId[key] = data.lanes.length;
                data.lanes.push(lane);
                if (section && !section.isFake) {
                    section.lanes.push(lane);
                }
            }
            const bar = new this.constructor.Bar(this, lane, record);
            lane.bars.push(bar);
            data.bars.push(bar);
            data.items.push(bar);
        }
        // Lane titles for single-row lanes fall back to the record name when
        // the arch declares no row fields (e.g. portfolio lanes per manager
        // group several projects — the section title carries the meaning).
        data.sections.sort((a, b) => String(a.title).localeCompare(String(b.title)));
        data.lanes.sort((a, b) => String(a.title || "").localeCompare(String(b.title || "")));
    }

    _laneDomain(record) {
        let domain = new Domain([]);
        const and = (leaf) => {
            domain = Domain.and([domain, new Domain([leaf])]);
        };
        if (this.sectionField) {
            and([this.sectionField.name, "=", this._fieldKey(record[this.sectionField.name])]);
        }
        for (const rowField of this.rowFields) {
            and([rowField.name, "=", this._fieldKey(record[rowField.name])]);
        }
        return domain;
    }

    // ---- KPI strip (hero numbers above the timeline) ----
    get totalBars() {
        return this.barsArray.length;
    }
    get startingCount() {
        const { periodStart, periodEnd } = this.navigation;
        return this.barsArray.filter((b) => b.start && b.start >= periodStart.startOf("day") && b.start <= periodEnd).length;
    }
    get endingCount() {
        const { periodStart, periodEnd } = this.navigation;
        return this.barsArray.filter((b) => b.stop && b.stop >= periodStart.startOf("day") && b.stop <= periodEnd).length;
    }
    get avgDays() {
        const bars = this.barsArray;
        if (!bars.length) {
            return 0;
        }
        return bars.reduce((s, b) => s + b.days, 0) / bars.length;
    }
    fmt(v) {
        const n = Number(v || 0);
        return n ? String(Math.round(n * 100) / 100) : "0";
    }

    async moveBar(bar, newStart, newStop) {
        const stop = newStop || newStart;
        let s;
        let e;
        if (this.columnFieldType === "datetime") {
            // Preserve the original time-of-day: shift the stored UTC
            // timestamps by whole days instead of rewriting them at midnight.
            const shift = (origRaw, refDay, newDay) => {
                const dDays = Math.round(newDay.diff(refDay, "days").days);
                let dt;
                if (typeof origRaw === "string" && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(origRaw)) {
                    dt = DateTime.fromSQL(origRaw, { zone: "utc" });
                } else {
                    dt = DateTime.fromISO(String(origRaw), { zone: "utc" });
                }
                if (!dt.isValid) {
                    dt = refDay;
                }
                return dt.plus({ days: dDays }).toFormat("yyyy-MM-dd HH:mm:ss");
            };
            s = shift(bar.record[this.dateStartField], bar.start, newStart);
            const stopRaw = bar.record[this.dateStopField] || bar.record[this.dateStartField];
            e = shift(stopRaw, bar.stop, stop);
        } else {
            s = this.serializeDay(newStart);
            e = this.serializeDay(stop);
        }
        try {
            await this.orm.call(
                this.resModel,
                "nova_gantt_update_dates",
                [[["id", "=", bar.id]], this.dateStartField, this.dateStopField, s, e],
                {}
            );
        } catch (err) {
            const message = (err && err.data && err.data.message) || err;
            this.notificationService.add(String((message && message.message) || message || "Could not save").slice(0, 300), { type: "warning" });
        }
        await this.reload();
    }
}
