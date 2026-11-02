// Part of Nova. See LICENSE file for full copyright and licensing details.
// Nova Gantt renderer: sticky lane headers, day columns, draggable bars.
import { Component, onMounted, onWillUnmount, signal } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class NovaGanttRenderer extends Component {
    static template = "nova_gantt.GanttRenderer";
    static props = { archInfo: Object, model: Object };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.gridRef = signal.ref();
        this._drag = null;
        onMounted(() => {
            const el = this.gridRef.el;
            if (!el) {
                return;
            }
            this._onGanttScroll = () => {
                el.classList.toggle("o_scrolled_x", el.scrollLeft > 0);
                el.classList.toggle("o_scrolled_y", el.scrollTop > 0);
            };
            el.addEventListener("scroll", this._onGanttScroll, { passive: true });
            this._onGanttScroll();
        });
        onWillUnmount(() => {
            this.gridRef.el?.removeEventListener("scroll", this._onGanttScroll);
        });
    }

    get model() {
        return this.props.model;
    }
    get archInfo() {
        return this.props.archInfo;
    }

    get gridStyle() {
        const n = this.model.columnsArray.length || 1;
        return `grid-template-columns: 300px repeat(${n}, minmax(36px, 1fr));`;
    }

    rowHeaderTitle() {
        const parts = [];
        if (this.model.sectionField) {
            const info = this.model.fieldsInfo[this.model.sectionField.name];
            parts.push(info?.string || this.model.sectionField.name);
        }
        for (const rowField of this.model.rowFields) {
            const info = this.model.fieldsInfo[rowField.name];
            parts.push(info?.string || rowField.name);
        }
        return parts.join(" / ");
    }
    lanesOf(section) {
        if (section.isFake) {
            return this.model.lanesArray;
        }
        return section.lanes || [];
    }

    avatarInitial(section) {
        const title = String(section.title ?? "").trim();
        return title ? [...title][0].toUpperCase() : "–";
    }
    avatarStyle(section) {
        const title = String(section.title ?? "?");
        let hash = 0;
        for (const ch of title) {
            hash = (hash * 31 + ch.codePointAt(0)) % 360;
        }
        // Pastel circle that reads on both light and dark surfaces.
        return `background: hsl(${hash}, 60%, 86%); color: hsl(${hash}, 45%, 32%);`;
    }

    barTitle(bar) {
        const fmt = (d) => (d ? d.toFormat("MMM d, yyyy") : "–");
        return `${bar.label} (${fmt(bar.start)} → ${fmt(bar.stop)}, ${bar.days}d)`;
    }

    colIndexOf(day) {
        const cols = this.model.columnsArray;
        for (let i = 0; i < cols.length; i++) {
            if (cols[i].start.hasSame(day, "day")) {
                return i;
            }
        }
        return -1;
    }

    barStyle(bar) {
        const cols = this.model.columnsArray;
        const n = cols.length;
        if (!n) {
            return "display:none";
        }
        let si = this.colIndexOf(bar.start);
        let ei = this.colIndexOf(bar.stop);
        // Bars fully outside the window are hidden; partial ones are
        // clamped to the visible edge so long bars still read correctly.
        if (bar.stop < cols[0].start.startOf("day")) {
            return "display:none";
        }
        if (bar.start > cols[n - 1].start.startOf("day")) {
            return "display:none";
        }
        if (si < 0) {
            si = 0;
        }
        if (ei < 0 || ei >= n) {
            ei = n - 1;
        }
        const left = (si / n) * 100;
        const width = ((ei - si + 1) / n) * 100;
        return `left:${left.toFixed(3)}%;width:${width.toFixed(3)}%;`;
    }

    trackStyle() {
        // Weekend shading as hard-stop gradient bands over the track.
        const cols = this.model.columnsArray;
        const n = cols.length;
        if (!n) {
            return "";
        }
        const stops = [];
        let runStart = -1;
        const push = (a, b) => {
            stops.push(
                `var(--ng-weekend) ${(a / n) * 100}%, var(--ng-weekend) ${((b + 1) / n) * 100}%`
            );
        };
        for (let i = 0; i < n; i++) {
            if (!cols[i].isWeekDay) {
                if (runStart < 0) {
                    runStart = i;
                }
            } else if (runStart >= 0) {
                push(runStart, i - 1);
                runStart = -1;
            }
        }
        if (runStart >= 0) {
            push(runStart, n - 1);
        }
        if (!stops.length) {
            return "";
        }
        return `background-image: linear-gradient(to right, ${stops.join(", ")});`;
    }

    todayIndex() {
        const today = this.model.today;
        return this.colIndexOf(today);
    }
    todayStyle() {
        const n = this.model.columnsArray.length;
        const i = this.todayIndex();
        return `left:${((i + 0.5) / n) * 100}%;`;
    }

    isCompact(bar) {
        // Narrow bars hide their inline label (the tooltip still shows
        // the full record name and dates); wide bars keep it.
        const n = this.model.columnsArray.length || 1;
        return bar.days / n < 0.08;
    }

    // ---- Drag to move / resize (pointer events, snap-to-day) ----
    onBarDown(ev, bar) {
        if (ev.button !== 0 && ev.pointerType === "mouse") {
            return;
        }
        if (!this.archInfo.editable) {
            return;
        }
        ev.preventDefault();
        const el = ev.currentTarget;
        const mode = ev.target?.closest?.(".o_handle_l")
            ? "start"
            : ev.target?.closest?.(".o_handle_r")
              ? "stop"
              : "move";
        try {
            el.setPointerCapture(ev.pointerId);
        } catch {}
        const track = el.closest(".o_nova_gantt_track");
        this._drag = {
            bar,
            el,
            track,
            mode,
            startX: ev.clientX,
            origStart: bar.start,
            origStop: bar.stop,
            moved: false,
            pending: null,
        };
        el.onpointermove = (e) => this.onBarMove(e);
        el.onpointerup = (e) => this.onBarUp(e);
        el.onpointercancel = () => this.onBarCancel();
    }

    _dayWidth() {
        const track = this._drag?.track;
        const n = this.model.columnsArray.length || 1;
        return (track?.clientWidth || 1) / n;
    }

    onBarMove(ev) {
        const drag = this._drag;
        if (!drag) {
            return;
        }
        const dx = ev.clientX - drag.startX;
        if (Math.abs(dx) < 3) {
            return;
        }
        drag.moved = true;
        const dDays = Math.round(dx / this._dayWidth());
        let newStart = drag.origStart;
        let newStop = drag.origStop;
        if (drag.mode === "move") {
            newStart = drag.origStart.plus({ days: dDays });
            newStop = drag.origStop.plus({ days: dDays });
        } else if (drag.mode === "start") {
            newStart = drag.origStart.plus({ days: dDays });
            if (newStart > newStop) {
                newStart = newStop;
            }
        } else {
            newStop = drag.origStop.plus({ days: dDays });
            if (newStop < newStart) {
                newStop = newStart;
            }
        }
        drag.pending = { newStart, newStop };
        this._paintPreview(drag);
    }

    _paintPreview(drag) {
        const cols = this.model.columnsArray;
        const n = cols.length;
        const idx = (day) => {
            for (let i = 0; i < n; i++) {
                if (cols[i].start.hasSame(day, "day")) {
                    return i;
                }
            }
            return day < cols[0].start ? -1 : n;
        };
        const { newStart, newStop } = drag.pending;
        const si = Math.max(0, Math.min(n - 1, idx(newStart)));
        const ei = Math.max(0, Math.min(n - 1, idx(newStop)));
        drag.el.style.left = `${(si / n) * 100}%`;
        drag.el.style.width = `${((ei - si + 1) / n) * 100}%`;
    }

    async onBarUp(ev) {
        const drag = this._drag;
        this._drag = null;
        if (!drag) {
            return;
        }
        try {
            drag.el.onpointermove = null;
            drag.el.onpointerup = null;
            drag.el.onpointercancel = null;
        } catch {}
        if (!drag.moved) {
            await this.openRecord(drag.bar);
            return;
        }
        if (!drag.pending) {
            return;
        }
        drag.bar.saving(true);
        try {
            await this.model.moveBar(drag.bar, drag.pending.newStart, drag.pending.newStop);
        } finally {
            drag.bar.saving(false);
        }
    }

    onBarCancel() {
        const drag = this._drag;
        this._drag = null;
        if (!drag) {
            return;
        }
        // Drop the preview; the next render restores computed positions.
        drag.el.style.left = "";
        drag.el.style.width = "";
    }

    onBarKeydown(ev, bar) {
        if (!this.archInfo.editable) {
            if (ev.key === "Enter") {
                ev.preventDefault();
                this.openRecord(bar);
            }
            return;
        }
        const step = ev.key === "ArrowLeft" ? -1 : ev.key === "ArrowRight" ? 1 : 0;
        if (!step) {
            if (ev.key === "Enter") {
                ev.preventDefault();
                this.openRecord(bar);
            }
            return;
        }
        ev.preventDefault();
        let newStart = bar.start;
        let newStop = bar.stop;
        if (ev.shiftKey) {
            newStop = bar.stop.plus({ days: step });
            if (newStop < newStart) {
                newStop = newStart;
            }
        } else {
            newStart = bar.start.plus({ days: step });
            newStop = bar.stop.plus({ days: step });
        }
        bar.saving(true);
        this.model
            .moveBar(bar, newStart, newStop)
            .finally(() => bar.saving(false))
            .then(() => this._refocusBar(bar));
    }

    _refocusBar(bar) {
        requestAnimationFrame(() => {
            this.gridRef.el?.querySelector(`[data-bar="${bar.id}"]`)?.focus?.();
        });
    }

    async openRecord(bar) {
        try {
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: this.model.resModel,
                res_id: bar.id,
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
}
