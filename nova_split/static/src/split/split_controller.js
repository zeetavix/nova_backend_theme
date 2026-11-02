// Part of Nova. See LICENSE file for full copyright and licensing details.
import { _t } from "@web/core/l10n/translation";
import { useBus, useService } from "@web/core/utils/hooks";
import { parseXML } from "@web/core/utils/xml";
import { FormArchParser } from "@web/views/form/form_arch_parser";
import { ListController } from "@web/views/list/list_controller";
import { onMounted, onPatched, onWillStart, onWillUnmount, proxy } from "@odoo/owl";
import { SplitFormPanel } from "@nova_split/split/split_form_panel";

const PANEL_WIDTH_KEY = "nova_split.width";
const PANEL_MIN_WIDTH = 340;
const PANEL_MAX_WIDTH = 1600;
const PANEL_DEFAULT_WIDTH = 560;

/**
 * Split view controller: a stock list on the left, a form panel on the
 * right. It inherits every list behaviour (search, pager, selection,
 * action menus, inline edition) and only redirects "open record" style
 * flows into the side panel instead of switching the whole view.
 */
export class SplitController extends ListController {
    static template = "nova_split.SplitView";
    static components = { ...ListController.components, SplitFormPanel };

    setup() {
        super.setup();
        this.viewService = useService("view");
        // Shared holder: the panel form controller publishes its model here
        // (see PanelFormController.onRootLoaded) so row clicks can switch
        // records through model.load, exactly like the form pager does.
        this.panelBridge = {
            model: null,
            onModelReady: () => this.updatePanelBreadcrumb(),
        };
        this.panelDisplay = { controlPanel: {}, searchPanel: false };
        this.panelInfo = { actionMenus: {} };
        // Breadcrumb trail shown inside the panel header. It always holds a
        // single entry with the current record name (mutated in place so the
        // proxied array stays reactive).
        this.panelBreadcrumbs = [];
        this.splitState = proxy({
            active: false,
            resId: null,
            resIds: [],
            formReady: false,
            width: this.readPanelWidth(),
            expanded: false,
            wide: false,
        });
        this.formSide = {};
        onWillStart(async () => {
            await this.loadFormSide();
            if (this.shouldAutoOpen()) {
                await this.openDefaultRecord();
            }
        });
        onMounted(() => {
            this.observeListRows();
            this.updateWideMode();
            this.highlightSelectedRow();
        });
        onPatched(() => {
            this.observeListRows();
            this.updateWideMode();
            this.highlightSelectedRow();
        });
        onWillUnmount(() => {
            this.highlightObserver?.disconnect();
            this.highlightObserverTarget = null;
        });
        useBus(this.uiService.bus, "resize", () => this.updateWideMode());
        // Follow the list: when filters, pagination or reloads change the
        // rows underneath the panel, show the first match (or the empty
        // state) instead of a stale record.
        useBus(this.model.bus, "update", () => this.reconcilePanelWithList());
    }

    async reconcilePanelWithList() {
        if (!this.splitState.active || !this.splitState.formReady || this.switchingPanel) {
            return;
        }
        const records = this.listRecords();
        if (this.splitState.resId === false) {
            return;
        }
        if (this.splitState.resId === null && records.length) {
            await this.selectPanelRecord(records[0].resId, this.listActiveIds());
            return;
        }
        if (!records.length) {
            if (this.splitState.resId !== null) {
                this.splitState.active = true;
                this.splitState.resId = null;
                this.splitState.resIds = [];
                this.updatePanelBreadcrumb();
            }
            return;
        }
        if (!this.findRecord(this.splitState.resId)) {
            await this.selectPanelRecord(records[0].resId, this.listActiveIds());
        }
    }

    // ------------------------------------------------------------------
    // configuration
    // ------------------------------------------------------------------

    readSplitConfig() {
        const fallback = { auto_open: true, default_width: PANEL_DEFAULT_WIDTH };
        const action = this.actionService.currentAction;
        if (!action || typeof action.nova_split_config !== "object") {
            return fallback;
        }
        return { ...fallback, ...action.nova_split_config };
    }

    readPanelWidth() {
        const actionId = this.env.config.actionId || "global";
        const candidates = [
            this.readStoredValue(`${PANEL_WIDTH_KEY}.${actionId}`),
            this.readStoredValue(PANEL_WIDTH_KEY),
        ];
        for (const raw of candidates) {
            const width = Number.parseInt(raw, 10);
            if (Number.isFinite(width)) {
                return this.clampWidth(width);
            }
        }
        return this.clampWidth(this.readSplitConfig().default_width);
    }

    readStoredValue(key) {
        try {
            return window.localStorage.getItem(key);
        } catch {
            return null;
        }
    }

    clampWidth(width) {
        if (!Number.isFinite(width)) {
            return PANEL_DEFAULT_WIDTH;
        }
        return Math.min(Math.max(width, PANEL_MIN_WIDTH), PANEL_MAX_WIDTH);
    }

    storePanelWidth(width) {
        const actionId = this.env.config.actionId || "global";
        try {
            window.localStorage.setItem(`${PANEL_WIDTH_KEY}.${actionId}`, String(width));
        } catch {
            // Storage may be unavailable (private mode, blocked cookies):
            // the width simply does not persist.
        }
    }

    shouldAutoOpen() {
        return (
            this.splitState.formReady &&
            !this.env.inDialog &&
            this.readSplitConfig().auto_open
        );
    }

    // ------------------------------------------------------------------
    // form side loading
    // ------------------------------------------------------------------

    async loadFormSide() {
        const views = this.env.config.views || [];
        const formEntry = views.find((view) => view[1] === "form");
        if (!formEntry || this.env.inDialog) {
            return;
        }
        let loaded;
        try {
            loaded = await this.viewService.loadViews(
                {
                    context: this.props.context,
                    resModel: this.props.resModel,
                    views: [[formEntry[0] || false, "form"]],
                },
                { actionId: this.env.config.actionId, loadActionMenus: true }
            );
        } catch {
            // The form side is optional: without it Split stays a plain
            // list (record clicks fall back to the stock form view).
            return;
        }
        const { fields, relatedModels, views: descriptions } = loaded;
        const formArch = parseXML(descriptions.form.arch);
        // Mirror View.loadView: honour create/edit/delete action flags.
        for (const action of ["create", "edit", "delete"]) {
            if (action in this.props.context && !this.props.context[action]) {
                formArch.setAttribute(action, "0");
            }
        }
        this.formSide = {
            arch: formArch,
            archInfo: new FormArchParser().parse(formArch, relatedModels, this.props.resModel),
            fields,
            relatedModels,
            viewId: descriptions.form.id,
        };
        this.panelInfo = { actionMenus: descriptions.form.actionMenus || {} };
        this.splitState.formReady = true;
    }

    // ------------------------------------------------------------------
    // panel record flows
    // ------------------------------------------------------------------

    listRecords() {
        return (this.model.root && this.model.root.records) || [];
    }

    listActiveIds() {
        return this.listRecords()
            .map((record) => record.resId)
            .filter((resId) => resId);
    }

    findRecord(resId) {
        return this.listRecords().find((record) => record.resId === resId);
    }

    async openDefaultRecord() {
        if (this.splitState.active || this.model.useSampleModel) {
            return;
        }
        const records = this.model.root.records;
        if (!records.length) {
            this.splitState.active = true;
            this.splitState.resId = null;
            this.splitState.resIds = [];
            return;
        }
        const preferred =
            (this.splitState.resId && this.findRecord(this.splitState.resId)) ||
            (this.props.resId && this.findRecord(this.props.resId)) ||
            null;
        await this.selectPanelRecord((preferred || records[0]).resId, this.listActiveIds());
    }

    /**
     * Show a record in the side panel. Dirty panel edits are saved first,
     * mirroring the form pager; a failed save keeps the current record.
     */
    async selectPanelRecord(resId, activeIds) {
        // Guard against overlapping switches: only the latest click wins.
        const loadId = (this.panelLoadId = (this.panelLoadId || 0) + 1);
        this.switchingPanel = true;
        try {
            return await this.selectPanelRecordNow(resId, activeIds, loadId);
        } finally {
            if (loadId === this.panelLoadId) {
                this.switchingPanel = false;
            }
        }
    }

    async selectPanelRecordNow(resId, activeIds, loadId) {
        const ids = activeIds || this.listActiveIds();
        // Keep the list's own inline edition consistent, like openRecord
        // does, but abort the switch when those edits cannot be saved.
        const clickedRecord = this.findRecord(resId);

        if (clickedRecord) {
            const listDirty = await clickedRecord.isDirty();
            if (listDirty) {
                const listSaved = await clickedRecord.save();
                if (!listSaved) {
                    return false;
                }
            }
        }
        if (this.model.root.editedRecord) {
            let left = false;
            try {
                left = await this.model.root.leaveEditMode();
            } catch {
                left = false;
            }
            if (left === false) {
                return false;
            }
        }
        await this.model.load();
        const formModel = this.panelBridge.model;
        if (formModel && formModel.root) {
            const dirty = await formModel.root.isDirty();
            if (dirty) {
                const saved = await formModel.root.save();
                if (!saved) {
                    return false;
                }
                await this.model.load();
            }
            await formModel.load({ resId, resIds: ids });
        }
        if (loadId !== this.panelLoadId) {
            return false;
        }
        this.splitState.active = true;
        this.splitState.resId = resId;
        this.splitState.resIds = ids;
        this.updatePanelBreadcrumb();
        this.pendingScrollToSelection = true;
        // Sync the highlight on the next frame as well: the state change
        // above may apply without a component patch reaching onPatched
        // (e.g. the rows themselves did not re-render), while the highlight
        // reads live DOM state and is safe to run any time.
        requestAnimationFrame(() => this.highlightSelectedRow());
        return true;
    }

    updatePanelBreadcrumb() {
        const formModel = this.panelBridge.model;
        let name = "";
        if (formModel && formModel.root) {
            const root = formModel.root;
            name = root.data.display_name || (root.isNew ? _t("New") : "");
        }
        this.panelBreadcrumbs.splice(0, this.panelBreadcrumbs.length, { name });
    }

    async createPanelRecord() {
        // The split list is never inline-editable (the arch flag is stripped
        // in split_view), so New always opens a blank form in the panel.
        this.switchingPanel = true;
        try {
            const formModel = this.panelBridge.model;
            if (formModel && formModel.root) {
                const dirty = await formModel.root.isDirty();
                if (dirty) {
                    const saved = await formModel.root.save();
                    if (!saved) {
                        return;
                    }
                    await this.model.load();
                }
                await formModel.load({ resId: false, resIds: this.splitState.resIds });
            }
            this.splitState.active = true;
            this.splitState.resId = false;
            this.updatePanelBreadcrumb();
        } finally {
            this.switchingPanel = false;
        }
    }

    async closePanel() {
        const formModel = this.panelBridge.model;
        if (formModel && formModel.root && (formModel.root.isNew || formModel.root.resId)) {
            const dirty = await formModel.root.isDirty();
            if (dirty) {
                const saved = await formModel.root.save();
                if (!saved) {
                    return false;
                }
                await this.model.load();
            }
        }
        this.splitState.active = false;
        this.env.config.setDisplayName(this.env.config.getDisplayName());
        return true;
    }

    togglePanel() {
        if (this.splitState.active) {
            this.closePanel();
        } else if (this.splitState.resId || this.splitState.resId === false) {
            this.selectPanelRecord(this.splitState.resId, this.splitState.resIds);
        } else {
            this.openDefaultRecord();
        }
    }

    // ------------------------------------------------------------------
    // list flow overrides
    // ------------------------------------------------------------------

    async openRecord(record, options = {}) {
        if (
            options.newWindow ||
            !this.splitState.formReady ||
            this.env.inDialog ||
            this.model.useSampleModel
        ) {
            return super.openRecord(record, options);
        }
        const selected = await this.selectPanelRecord(record.resId, this.listActiveIds());
        if (selected) {
            // Rows call preventDefault on mousedown, so a click never moves
            // focus into the list. Focus it here, after a deliberate row
            // click, so ArrowUp/ArrowDown navigate right away. Auto-open does
            // not come through here, so it never steals focus from a form.
            const list = this.rootRef && this.rootRef()?.querySelector(".o_nova_split_list");
            list?.focus({ preventScroll: true });
        }
        return selected;
    }

    async createRecord() {
        if (!this.splitState.formReady || this.env.inDialog) {
            return super.createRecord();
        }
        return this.createPanelRecord();
    }

    // ------------------------------------------------------------------
    // panel callbacks
    // ------------------------------------------------------------------

    async onPanelSaved(record) {
        await this.model.load();
        const resIds = this.listActiveIds();
        if (record.resId && !resIds.includes(record.resId)) {
            resIds.unshift(record.resId);
        }
        this.splitState.resIds = resIds;
        this.splitState.resId = record.resId;
        this.updatePanelBreadcrumb();
    }

    async onPanelHistoryBack() {
        // Covers record deletion and discarding a new record: the panel is
        // done, the list may have changed.
        this.splitState.active = false;
        await this.model.load();
        this.env.config.setDisplayName(this.env.config.getDisplayName());
    }

    onPageChange() {
        // The list scrolls inside its own pane (not .o_content), so page
        // changes reset the pane instead of the stock renderer query.
        const listPane = this.rootRef && this.rootRef()?.querySelector(".o_nova_split_list");
        if (listPane) {
            listPane.scrollTop = 0;
        } else {
            super.onPageChange();
        }
    }

    // ------------------------------------------------------------------
    // panel width (drives chatter placement inside the embedded form)
    // ------------------------------------------------------------------

    updateWideMode() {
        if (!this.splitState.active) {
            return;
        }
        const root = this.rootRef && this.rootRef();
        const panel = root && root.querySelector(".o_nova_split_panel");
        if (!panel) {
            return;
        }
        // Chatter docks aside only when the panel itself has room for it.
        const wide = panel.clientWidth >= 1100;
        if (wide !== this.splitState.wide) {
            this.splitState.wide = wide;
        }
    }

    // ------------------------------------------------------------------
    // resizer
    // ------------------------------------------------------------------

    toggleExpanded() {
        this.splitState.expanded = !this.splitState.expanded;
    }

    onResizerPointerDown(ev) {
        if (this.splitState.expanded) {
            return;
        }
        // Clicks on the close/expand buttons must not start a drag.
        if (ev.target.closest("button") || ev.isPrimary === false) {
            return;
        }
        ev.preventDefault();
        const divider = ev.currentTarget;
        const body = divider.closest(".o_nova_split_body");
        if (!body) {
            return;
        }
        try {
            divider.setPointerCapture(ev.pointerId);
        } catch {
            // Pointer capture is best-effort; window listeners below cover it.
        }
        // Coalesce to one width write per animation frame. Raw pointermove
        // fires far faster than the screen, and every write re-renders the
        // whole split tree (list + heavy form); without this the UI thread
        // saturates mid-drag and the screen appears frozen.
        let frame = 0;
        let lastX = ev.clientX;
        const apply = () => {
            frame = 0;
            const rect = body.getBoundingClientRect();
            this.splitState.width = this.clampWidth(Math.round(rect.right - lastX));
            // Width is bound to the style attribute, which Owl updates
            // fine-grained without a component patch: sync the DOM that
            // depends on panel size directly instead of via onPatched.
            this.updateWideMode();
            this.highlightSelectedRow();
        };
        const move = (moveEv) => {
            lastX = moveEv.clientX;
            if (!frame) {
                frame = requestAnimationFrame(apply);
            }
        };
        const up = () => {
            if (frame) {
                cancelAnimationFrame(frame);
                frame = 0;
            }
            this.updateWideMode();
            this.highlightSelectedRow();
            this.storePanelWidth(this.splitState.width);
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
            window.removeEventListener("pointercancel", up);
            window.removeEventListener("blur", up);
            divider.removeEventListener("lostpointercapture", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
        window.addEventListener("pointercancel", up);
        window.addEventListener("blur", up);
        divider.addEventListener("lostpointercapture", up);
    }

    /**
     * Master-detail navigation: with focus in the list, ArrowDown and
     * ArrowUp move the side panel to the next or previous record, so a user
     * can triage a whole list from the keyboard. Keys typed into fields,
     * open menus, or an inline row edit are left alone.
     */
    async onListKeyDown(ev) {
        if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") {
            return;
        }
        if (ev.defaultPrevented || ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) {
            return;
        }
        if (!this.splitState.active || this.splitState.expanded || this.env.inDialog) {
            return;
        }
        if (this.model.root.editedRecord) {
            return;
        }
        if (
            ev.target.closest(
                "input, textarea, select, [contenteditable='true'], .o_dropdown, .dropdown-menu, .o_nova_split_panel"
            )
        ) {
            return;
        }
        const records = this.listRecords();
        const index = records.findIndex((record) => record.resId === this.splitState.resId);
        const next = records[index + (ev.key === "ArrowDown" ? 1 : -1)];
        if (index < 0 || !next) {
            return;
        }
        ev.preventDefault();
        await this.selectPanelRecord(next.resId, this.listActiveIds());
    }

    /**
     * Keyboard resizing for the splitter (WAI-ARIA window-splitter pattern):
     * ArrowLeft widens the panel, ArrowRight narrows it, Shift steps larger,
     * Home restores the configured default width. Only the splitter itself
     * handles keys, not the close/expand buttons inside it.
     */
    onResizerKeyDown(ev) {
        if (ev.target !== ev.currentTarget || this.splitState.expanded) {
            return;
        }
        const step = ev.shiftKey ? 80 : 24;
        let next = null;
        if (ev.key === "ArrowLeft") {
            next = this.splitState.width + step;
        } else if (ev.key === "ArrowRight") {
            next = this.splitState.width - step;
        } else if (ev.key === "Home") {
            next = this.readSplitConfig().default_width;
        } else {
            return;
        }
        ev.preventDefault();
        this.splitState.width = this.clampWidth(next);
        this.storePanelWidth(this.splitState.width);
        this.updateWideMode();
        this.highlightSelectedRow();
    }

    onResizerDoubleClick() {
        this.splitState.width = this.clampWidth(this.readSplitConfig().default_width);
        this.storePanelWidth(this.splitState.width);
    }

    // ------------------------------------------------------------------
    // selection highlight
    // ------------------------------------------------------------------

    observeListRows() {
        // The highlight is plain DOM state on rows rendered by the core
        // list renderer, so any child-level re-render (reload, inline
        // patch) wipes it without notifying this controller. Re-apply it
        // whenever rows are added or removed, whatever caused the change.
        // Only childList is observed, so the class/attribute writes below
        // never re-trigger the observer.
        const root = this.rootRef && this.rootRef();
        const listEl = root && root.querySelector(".o_nova_split_list");
        if (!listEl) {
            return;
        }
        if (!this.highlightObserver) {
            this.highlightObserver = new MutationObserver(() => this.highlightSelectedRow());
        }
        if (this.highlightObserverTarget !== listEl) {
            this.highlightObserver.disconnect();
            this.highlightObserver.observe(listEl, { childList: true, subtree: true });
            this.highlightObserverTarget = listEl;
        }
    }

    highlightSelectedRow() {
        const root = this.rootRef && this.rootRef();
        if (!root || !this.splitState.active) {
            return;
        }
        root.querySelectorAll(".o_data_row.o_nova_split_active").forEach((row) => {
            row.classList.remove("o_nova_split_active");
            row.removeAttribute("aria-current");
        });
        const selected = this.listRecords().find(
            (record) => record.resId === this.splitState.resId
        );
        if (!selected) {
            return;
        }
        const row = root.querySelector(`.o_data_row[data-id="${CSS.escape(selected.id)}"]`);
        if (row) {
            row.classList.add("o_nova_split_active");
            row.setAttribute("aria-current", "true");
            if (this.pendingScrollToSelection) {
                row.scrollIntoView({ block: "nearest" });
            }
        }
        this.pendingScrollToSelection = false;
    }
}
