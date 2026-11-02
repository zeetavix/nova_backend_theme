// Part of Nova. See LICENSE file for full copyright and licensing details.
// Clean-room grid data model. Uses read_group (single RPC per load) and
// keeps row/section/column/cell objects with reactive totals.
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
// Cells / rows / columns
// ---------------------------------------------------------------------------

export class NovaGridCell {
    constructor(model, row, column, value = 0) {
        this.model = model;
        this.row = row;
        this.column = column;
        this._value = signal(value);
        this._readonly = false;
        this._saving = signal(false);
        column.registerCell(this);
    }
    get value() {
        return this._value();
    }
    set value(v) {
        this._value.set(v);
    }
    get saving() {
        return this._saving();
    }
    get readonly() {
        return this._readonly || this.column.readonly;
    }
    get domain() {
        return Domain.and([this.model.searchParams.domain, this.row.domain, this.column.domain]);
    }
    get context() {
        return {
            ...(this.model.searchParams.context || {}),
            ...this.row.sectionContext,
            ...this.row.context,
            ...this.column.context,
        };
    }
    get title() {
        const rowTitle =
            !this.row.section || this.row.section.isFake
                ? this.row.title
                : `${this.row.section.title} / ${this.row.title}`;
        return `${rowTitle} (${this.column.title})`;
    }
    get heatIntensity() {
        const scale = this.model.heatmapScale || 8;
        if (!this.value || scale <= 0) {
            return 0;
        }
        return Math.min(1, this.value / scale);
    }
    async update(value) {
        return this.model.mutex.exec(() => this._update(value));
    }
    async _update(value) {
        const clean = Math.max(0, Number(value) || 0);
        if (clean > 24) {
            this.model.notificationService.add(
                _t("A single day cannot exceed 24 hours."),
                { type: "warning" }
            );
            return;
        }
        const oldValue = this.value;
        const delta = clean - oldValue;
        if (!delta) {
            return;
        }
        this._saving.set(true);
        try {
            const result = await this.model.orm.call(
                this.model.resModel,
                "nova_grid_update_cell",
                [this.domain.toList({}), this.model.measureFieldName, delta],
                { context: this.context }
            );
            if (result) {
                this.model.actionService.doAction(result);
                return;
            }
            this.row.applyCellDelta(this.column, delta, this.model.data);
            this.model.notify();
        } finally {
            this._saving.set(false);
        }
    }
}

export class NovaGridRow {
    constructor(data, domain, valuePerFieldName, model, section, isExtra = false) {
        this._domain = domain;
        this.model = model;
        this.cells = {};
        this.valuePerFieldName = valuePerFieldName;
        this.id = model.rowId++;
        this.section = section;
        if (section) {
            section.attachRow(this);
        }
        this.total = 0;
        this.weekdayTotal = 0;
        this.isExtra = isExtra;
        this._buildCells(data);
    }
    get title() {
        const parts = [];
        for (const rowField of this.model.rowFields) {
            let title = this.valuePerFieldName[rowField.name];
            const info = this.model.fieldsInfo[rowField.name];
            if (!info) {
                continue;
            }
            if (info.type === "many2one") {
                title = title ? title[1] : parts.length ? "" : _t("None");
            } else if (info.type === "selection") {
                title = (info.selection || []).find(([k]) => k === title)?.[1];
            }
            if (title) {
                parts.push(title);
            }
        }
        return parts.join(" / ") || _t("No Group");
    }
    get domain() {
        if (this.section && !this.section.isFake) {
            return Domain.and([this.section.domain, this._domain]);
        }
        return this._domain;
    }
    get context() {
        const ctx = {};
        for (const [key, value] of Object.entries(this.valuePerFieldName)) {
            ctx[`default_${key}`] =
                this.model.fieldsInfo[key]?.type === "many2one" && value ? value[0] : value;
        }
        return ctx;
    }
    get sectionContext() {
        return {};
    }
    _buildCells(data) {
        for (const column of Object.values(data.columns)) {
            this.cells[column.id] = new this.model.constructor.Cell(this.model, this, column, 0);
        }
    }
    applyCellDelta(column, delta, data) {
        const cell = this.cells[column.id];
        if (!cell) {
            return;
        }
        cell.value = cell.value + delta;
        this.total += delta;
        if (column.isWeekDay !== false) {
            this.weekdayTotal += delta;
        }
        if (this.section) {
            this.section.absorbDelta(column, delta);
        }
        column.total += delta;
        if (this.isExtra && delta > 0) {
            this.isExtra = false;
        }
    }
    markCellReadonly(column, flag) {
        const cell = this.cells[column.id];
        if (cell) {
            cell._readonly = Boolean(flag);
        }
    }
    getTotal(showWeekend) {
        return showWeekend ? this.total : this.weekdayTotal;
    }
}

export class NovaGridSection extends NovaGridRow {
    constructor(...args) {
        super(...args);
        this.sectionId = this.model.sectionId++;
        this.rows = {};
        this.isSection = true;
        this.lastRow = null;
    }
    // The section field name is captured on the instance at build time so
    // titles/domains never depend on transient model state (which a later
    // load may replace while rows are on screen).
    get sectionFieldName() {
        return this._sectionFieldName || this.model.sectionField?.name;
    }
    get value() {
        return this.valuePerFieldName?.[this.sectionFieldName];
    }
    get domain() {
        const raw = this.value;
        const val = Array.isArray(raw) ? raw[0] : raw;
        if (this.isFake || !this.sectionFieldName) {
            return new Domain();
        }
        return new Domain([[this.sectionFieldName, "=", val]]);
    }
    get context() {
        if (this.isFake || !this.sectionFieldName) {
            return {};
        }
        const raw = this.value;
        const val = Array.isArray(raw) ? raw[0] : raw;
        return { [`default_${this.sectionFieldName}`]: val };
    }
    get sectionContext() {
        return this.context;
    }
    get title() {
        const raw = this.value;
        if (raw == null) {
            // Fake sections are never rendered; this is a last-resort label
            // so a section band can never appear blank.
            const fallback = this.model.fieldsInfo?.[this.sectionFieldName]?.string;
            return fallback || "—";
        }
        if (this.sectionFieldName && this.model.fieldsInfo[this.sectionFieldName]?.type === "many2one") {
            return (raw && raw[1]) || _t("None");
        }
        return String(raw);
    }
    get isFake() {
        return this.value == null;
    }
    attachRow(row) {
        this.rows[row.id] = row;
        this.lastRow = row;
    }
    absorbDelta(column, delta) {
        const cell = this.cells[column.id];
        if (cell) {
            cell.value = cell.value + delta;
        }
        this.total += delta;
        if (column.isWeekDay !== false) {
            this.weekdayTotal += delta;
        }
    }
}

export class NovaGridColumn {
    constructor(model, title, value, readonly = false) {
        this.model = model;
        this.title = title;
        this.value = value;
        this.cells = [];
        this.id = model.columnId++;
        this.total = 0;
        this.readonly = readonly;
        this.isWeekDay = true;
        this.isToday = false;
    }
    registerCell(cell) {
        this.cells.push(cell);
        this.total += cell.value;
    }
    get headerTop() {
        return "";
    }
    get headerSub() {
        return this.title;
    }
    get domain() {
        return new Domain([[this.model.columnFieldName, "=", this.value]]);
    }
    get context() {
        return { [`default_${this.model.columnFieldName}`]: this.value };
    }
}

export class NovaGridDateColumn extends NovaGridColumn {
    constructor(model, title, start, stop, isToday, isWeekDay, readonly = false) {
        super(model, title, start, readonly);
        this.stop = stop;
        this.isToday = isToday;
        this.isWeekDay = isWeekDay;
    }
    get headerTop() {
        return this._headerTop !== undefined ? this._headerTop : super.headerTop;
    }
    get headerSub() {
        return this._headerSub !== undefined ? this._headerSub : super.headerSub;
    }
    get domain() {
        return new Domain([
            "&",
            [this.model.columnFieldName, ">=", this.value],
            [this.model.columnFieldName, "<", this.stop],
        ]);
    }
}

export class NovaGridNavigation {
    constructor(anchor, model) {
        this.anchor = anchor;
        this.model = model;
    }
    get range() {
        return this.model.activeRange;
    }
    get periodStart() {
        if (this.range.span !== "week") {
            return this.anchor.startOf(this.range.span);
        }
        const firstDay = localization.weekStart;
        const delta = (this.anchor.weekday - firstDay + 7) % 7;
        return this.anchor.minus({ days: delta }).startOf("day");
    }
    get periodEnd() {
        if (this.range.span !== "week") {
            return this.anchor.endOf(this.range.span);
        }
        return this.periodStart.plus({ weeks: 1 }).minus({ milliseconds: 1 });
    }
    get interval() {
        return Interval.fromDateTimes(this.periodStart, this.periodEnd);
    }
    contains(date) {
        return this.interval.contains(date.startOf("day"));
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
// Model
// ---------------------------------------------------------------------------

export class NovaGridModel extends Model {
    static Cell = NovaGridCell;
    static Row = NovaGridRow;
    static Section = NovaGridSection;
    static Column = NovaGridColumn;
    static DateColumn = NovaGridDateColumn;
    static Navigation = NovaGridNavigation;

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
        this.resModel = params.resModel;
        this.fieldsInfo = params.fieldsInfo;
        this.columnFieldName = params.columnFieldName;
        this.columnFieldType =
            params.columnFieldType || this.fieldsInfo[params.columnFieldName]?.type || "";
        const columnType = this.columnFieldType;
        this.columnFieldIsDate = columnType === "date" || columnType === "datetime";
        this.measureField = params.measureField;
        this.readonlyField = params.readonlyField;
        this.ranges = params.ranges || {};
        this.activeRangeName = params.activeRangeName;
        this.heatmapScale = params.heatmapScale || 8;
        this.showWeekend = true;
        this.data = undefined;
        this.record = undefined;
        this.defaultAnchor = params.defaultAnchor || DateTime.local().startOf("day");
        this.activeRange = this.ranges[this._storedRangeName(params)] || this.ranges[this.activeRangeName] || Object.values(this.ranges)[0];
        this.navigation = new this.constructor.Navigation(this.defaultAnchor, this);
        this.unavailable = {};
        this._resetIds();
    }

    get today() {
        return DateTime.local().startOf("day");
    }
    get sectionsArray() {
        return Object.values(this.data?.sections || {});
    }
    get rowsArray() {
        return Object.values(this.data?.rows || {});
    }
    get columnsArray() {
        return Object.values(this.data?.columns || {});
    }
    get itemsArray() {
        return this.data?.items || [];
    }
    get measureFieldName() {
        return this.measureField?.name || "unit_amount";
    }
    get measureAggregate() {
        if (this.measureField?.aggregator) {
            return `${this.measureFieldName}:${this.measureField.aggregator}`;
        }
        return this.measureFieldName;
    }
    get storageKey() {
        return `nova_grid_range_${this.env.config.viewId}`;
    }
    get columnGroupBy() {
        if (this.columnFieldIsDate) {
            return `${this.columnFieldName}:${this.activeRange.step}`;
        }
        return this.columnFieldName;
    }
    get maxColumnTotal() {
        const totals = this.columnsArray.map((c) => c.total);
        return totals.length ? Math.max(...totals) : 0;
    }
    get grandTotal() {
        return this.columnsArray.reduce((s, c) => s + c.total, 0);
    }

    _storedRangeName(params) {
        try {
            return browser.localStorage.getItem(this.storageKey) || params.activeRangeName;
        } catch {
            return params.activeRangeName;
        }
    }
    _resetIds() {
        this.columnId = 0;
        this.rowId = 0;
        this.sectionId = 0;
    }
    hasData() {
        return this.sectionsArray.length > 0 && this.rowsArray.length > 0;
    }

    async setRange(rangeName) {
        this.activeRange = this.ranges[rangeName];
        try {
            browser.localStorage.setItem(this.storageKey, rangeName);
        } catch {}
        await this.reload();
    }
    async moveAnchor(direction) {
        // Step by the visible span (week/month/...), not by the column step.
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
        if (!this.columnFieldIsDate) {
            return Domain.TRUE;
        }
        const { periodStart, periodEnd } = this.navigation;
        if (this.columnFieldType === "datetime") {
            // Datetime columns: compare against day boundaries, otherwise
            // records later in the day would fall outside a "<= date" bound.
            return new Domain([
                "&",
                [this.columnFieldName, ">=", serializeDate(periodStart)],
                [this.columnFieldName, "<", serializeDate(periodEnd.plus({ days: 1 }))],
            ]);
        }
        return new Domain([
            "&",
            [this.columnFieldName, ">=", serializeDate(periodStart)],
            [this.columnFieldName, "<=", serializeDate(periodEnd)],
        ]);
    }

    async load(params = {}) {
        const searchParams = { ...this.searchParams, ...params };
        // Never group by the column field itself.
        searchParams.groupBy = (searchParams.groupBy || []).filter(
            (g) => !g.startsWith(this.columnFieldName)
        );
        // Grid rows are arch-defined: a matrix with inline editing cannot be
        // arbitrarily regrouped, so search-view Group By selections never
        // restructure it (they would silently replace the arch rows with
        // ad-hoc fields and break titles, domains and writes).
        let rowFields = [];
        let sectionField;
        const invisible = (f) => f.invisible === "True" || f.invisible === "1";
        if (this.defaultSectionField && !invisible(this.defaultSectionField)) {
            sectionField = this.defaultSectionField;
        }
        rowFields = this.defaultRowFields.filter((r) => !invisible(r));
        if (!rowFields.length && !sectionField) {
            rowFields = this.defaultRowFields.slice(0, 1);
        }
        const metaData = { searchParams, rowFields, sectionField };
        // Assign fields BEFORE loading: rows/sections read model.sectionField
        // while the matrix is built (e.g. fake-section detection).
        this.sectionField = sectionField;
        this.rowFields = rowFields;
        this.searchParams = searchParams;
        await this.keepLast.add(this.loadData(metaData));
        // Assign again AFTER loading to pick up data + record.
        Object.assign(this, metaData);
    }

    async reload(params = {}) {
        await this.load(params);
        this.useSampleModel = false;
        this.notify();
    }

    groupByFields(metaData) {
        const { rowFields, sectionField } = metaData;
        const fields = [this.columnGroupBy, ...rowFields.map((r) => r.name)];
        if (sectionField) {
            fields.push(sectionField.name);
        }
        return fields;
    }

    aggregates() {
        const aggs = [this.measureAggregate, "id:array_agg"];
        if (this.readonlyField) {
            aggs.push(`${this.readonlyField.name}:${this.readonlyField.aggregator || "sum"}`);
        }
        return aggs;
    }

    async loadData(metaData) {
        this._resetIds();
        Object.assign(metaData, await this._initialData(metaData));
        const { data } = metaData;
        const fetched = await this.fetchData(metaData);
        this._buildMatrix(fetched, metaData);
        await this._fetchUnavailability(metaData);
        this._flattenItems(data);
    }

    _flattenItems(data) {
        // Flatten render order: section header, then its rows.
        // Fake sections (no section field in the arch) are structural only
        // and must never render as clickable rows.
        data.items = [];
        for (const section of Object.values(data.sections)) {
            if (!section.isFake) {
                data.items.push(section);
            }
            for (const rowId of Object.keys(section.rows || {})) {
                const row = section.rows[rowId];
                if (row && !row.isSection) {
                    data.items.push(row);
                }
            }
        }
    }

    /**
     * Render previous-period row skeletons as addable ("new") rows.
     * Pure client-side: nothing is written until the user types a value
     * (which flows through the normal cell-update path).
     */
    async copyPreviousPeriod() {
        if (!this.columnFieldIsDate) {
            return;
        }
        const { periodStart, periodEnd } = this.navigation;
        const spanMs = periodEnd.toMillis() - periodStart.toMillis();
        const prevEnd = periodStart;
        const prevStart = periodStart.minus({ milliseconds: spanMs });
        const rowFields = this.rowFields.map((r) => r.name);
        const payload = await this.orm.call(
            this.resModel,
            "nova_grid_copy_previous",
            [
                (this.searchParams.domain || []).slice(),
                this.columnFieldName,
                serializeDate(periodStart),
                serializeDate(periodEnd),
                serializeDate(prevStart),
                serializeDate(prevEnd),
                rowFields,
            ],
            {}
        );
        this.addExtraRows(payload?.rows || []);
    }

    addExtraRows(rowSpecs) {
        if (!rowSpecs.length || !this.data) {
            return;
        }
        const metaData = { rowFields: this.rowFields, sectionField: this.sectionField };
        for (const spec of rowSpecs) {
            const domain = Domain.and([
                ...Object.entries(spec.values || {}).map(([fname, raw]) => [
                    [fname, "=", Array.isArray(raw) ? raw[0] : raw],
                ]),
            ]);
            this._ensureRow(this.data, spec.values || {}, domain, metaData, true);
        }
        this._flattenItems(this.data);
        this.notify();
    }

    async _initialData() {
        const data = {
            columnsKeyToId: {},
            columns: {},
            rows: {},
            rowsKeyToId: {},
            sections: {},
            sectionsKeyToId: {},
            items: [],
        };
        if (this.columnFieldIsDate) {
            this._buildDateColumns(data);
        } else {
            const info = this.fieldsInfo[this.columnFieldName];
            let records = [];
            if (info?.type === "selection") {
                const options = await this.orm.call("ir.model.fields", "get_field_selection", [
                    this.resModel,
                    this.columnFieldName,
                ]);
                records = options;
            } else if (info?.type === "many2one") {
                records = await this.orm.searchRead(
                    info.relation,
                    [],
                    ["display_name"]
                );
                records = records.map((r) => [r.id, r.display_name]);
            }
            for (const rec of records) {
                const [key, label] = rec;
                const column = new this.constructor.Column(this, label, key);
                data.columns[column.id] = column;
                data.columnsKeyToId[key] = column.id;
            }
        }
        return {
            data,
            record: { resModel: this.resModel, resIds: [] },
        };
    }

    _buildDateColumns(data) {
        const step = this.activeRange.step || "day";
        const next = (d) => d.plus({ [`${step}s`]: 1 });
        for (
            let current = this.navigation.periodStart;
            current < this.navigation.periodEnd;
            current = next(current)
        ) {
            const start = current;
            const stop = next(current);
            const key = serializeDate(start);
            const isWeekDay = start.weekday < 6;
            const column = new this.constructor.DateColumn(
                this,
                this._columnTitle(start),
                key,
                serializeDate(stop),
                start.startOf("day").equals(this.today.startOf("day")),
                isWeekDay
            );
            // Structured header (computed from the DateTime, not the serialized key):
            // weekday eyebrow ("MON") + day number ("19"). Month context comes
            // from the toolbar period label, so headers stay slim.
            if (step === "day") {
                column._headerTop = start.setLocale(start.locale).toFormat("EEE").toUpperCase();
                column._headerSub = String(start.day);
            }
            data.columns[column.id] = column;
            data.columnsKeyToId[key] = column.id;
        }
    }

    _columnTitle(date) {
        const step = this.activeRange.step;
        if (step === "month") {
            return date.setLocale(date.locale).toFormat("MMM yyyy");
        }
        const locale = date.locale;
        try {
            const parts = new Intl.DateTimeFormat(locale, {
                weekday: "short",
                month: "short",
                day: "numeric",
            }).formatToParts(date.toJSDate());
            const first = parts
                .slice(0, 3)
                .map((p) => p.value)
                .join("")
                .trim();
            const rest = parts
                .slice(3)
                .map((p) => p.value)
                .join("")
                .trim();
            return rest ? `${first}\n${rest}` : first;
        } catch {
            return date.toFormat("EEE\nMMM d");
        }
    }

    async fetchData(metaData) {
        const { searchParams } = metaData;
        return this.orm.formattedReadGroup(
            this.resModel,
            Domain.and([searchParams.domain, this.navigationDomain()]).toList({}),
            this.groupByFields(metaData),
            this.aggregates()
        );
    }

    async _fetchUnavailability() {
        if (!this.columnFieldIsDate) {
            return;
        }
        try {
            const result = await this.orm.call(
                this.resModel,
                "nova_grid_unavailability",
                [serializeDate(this.navigation.periodStart), serializeDate(this.navigation.periodEnd)],
                {}
            );
            this.unavailable = result || {};
        } catch {
            this.unavailable = {};
        }
    }

    _sectionKey(result, sectionField) {
        let value = result[sectionField.name];
        if (this.fieldsInfo[sectionField.name]?.type === "many2one") {
            value = value && value[0];
        }
        return `S${String(value)}`;
    }

    _rowKey(result, metaData) {
        const sectionPart = metaData.sectionField
            ? this._sectionKey(result, metaData.sectionField)
            : "Sfalse";
        let key = "";
        for (const rowField of metaData.rowFields) {
            let value = result[rowField.name];
            if (this.fieldsInfo[rowField.name]?.type === "many2one") {
                value = value && value[0];
            }
            key += `${String(value)}|`;
        }
        return `${sectionPart}@${key}`;
    }

    _rowDomainValues(result, rowFields) {
        let domain = new Domain();
        const values = {};
        for (const rowField of rowFields) {
            const raw = result[rowField.name];
            let value = raw;
            if (this.fieldsInfo[rowField.name]?.type === "many2one") {
                value = value && value[0];
            }
            values[rowField.name] = raw;
            domain = Domain.and([domain, [[rowField.name, "=", value]]]);
        }
        return { domain, values };
    }

    _fakeSection(data) {
        const section = new this.constructor.Section(data, new Domain(), null, this, null);
        section._sectionFieldName = this.sectionField?.name;
        data.sections[section.id] = section;
        data.sectionsKeyToId["Sfalse"] = section.id;
        data.rows[section.id] = section;
        data.rowsKeyToId["Sfalse@"] = section.id;
        return section;
    }

    _ensureRow(data, values, domain, metaData, isExtra = false) {
        const keyParts = metaData.rowFields.map((f) => {
            let v = values[f.name];
            if (this.fieldsInfo[f.name]?.type === "many2one" && Array.isArray(v)) {
                v = v[0];
            }
            return String(v);
        });
        const sectionKey = metaData.sectionField
            ? `S${String(
                (() => {
                    let v = values[metaData.sectionField.name];
                    return Array.isArray(v) ? v[0] : v;
                })()
            )}`
            : "Sfalse";
        const rowKey = `${sectionKey}@${keyParts.join("|")}|`;
        if (rowKey in data.rowsKeyToId) {
            return data.rows[data.rowsKeyToId[rowKey]];
        }
        let section;
        if (metaData.sectionField) {
            if (!(sectionKey in data.sectionsKeyToId)) {
                const sec = new this.constructor.Section(
                    data,
                    new Domain(),
                    { [metaData.sectionField.name]: values[metaData.sectionField.name] },
                    this,
                    null
                );
                sec._sectionFieldName = metaData.sectionField.name;
                data.sections[sec.id] = sec;
                data.sectionsKeyToId[sectionKey] = sec.id;
                data.rows[sec.id] = sec;
            }
            section = data.sections[data.sectionsKeyToId[sectionKey]];
        } else {
            section =
                data.sections[data.sectionsKeyToId["Sfalse"]] || this._fakeSection(data);
        }
        const row = new this.constructor.Row(data, domain, values, this, section, isExtra);
        data.rows[row.id] = row;
        data.rowsKeyToId[rowKey] = row.id;
        return row;
    }

    _buildMatrix(groups, metaData) {
        const { data, record, sectionField, rowFields } = metaData;
        let section = null;
        for (const group of groups) {
            if (!this.orm.isSample) {
                record.resIds.push(...(group["id:array_agg"] || []));
            }
            const rowKey = this._rowKey(group, metaData);
            if (sectionField) {
                const sectionKey = this._sectionKey(group, sectionField);
                if (!(sectionKey in data.sectionsKeyToId)) {
                    const sec = new this.constructor.Section(
                        data,
                        new Domain(),
                        { [sectionField.name]: group[sectionField.name] },
                        this,
                        null
                    );
                    sec._sectionFieldName = sectionField.name;
                    data.sections[sec.id] = sec;
                    data.sectionsKeyToId[sectionKey] = sec.id;
                    data.rows[sec.id] = sec;
                    data.rowsKeyToId[`${sectionKey}@`] = sec.id;
                }
                section = data.sections[data.sectionsKeyToId[sectionKey]];
            } else if (!Object.keys(data.sections).length) {
                section = this._fakeSection(data);
            }
            let row;
            if (!(rowKey in data.rowsKeyToId)) {
                const { domain, values } = this._rowDomainValues(group, rowFields);
                row = new this.constructor.Row(data, domain, values, this, section);
                data.rows[row.id] = row;
                data.rowsKeyToId[rowKey] = row.id;
            } else {
                row = data.rows[data.rowsKeyToId[rowKey]];
            }
            const colInfo = this.fieldsInfo[this.columnFieldName];
            const rawKey = group[this.columnGroupBy];
            let columnKey;
            if (this.columnFieldIsDate || colInfo?.type === "many2one") {
                // Group values arrive as [key, label] pairs; False means undated.
                // Datetime grouping returns keys with a time part
                // ("YYYY-MM-DD HH:MM:SS") while day columns are keyed by date.
                columnKey = Array.isArray(rawKey) ? rawKey[0] : undefined;
                if (typeof columnKey === "string" && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(columnKey)) {
                    columnKey = columnKey.slice(0, 10);
                }
            } else {
                columnKey = rawKey;
            }
            if (columnKey in data.columnsKeyToId) {
                const column = data.columns[data.columnsKeyToId[columnKey]];
                row.applyCellDelta(column, group[this.measureAggregate] || 0, data);
            }
        }
    }

}
