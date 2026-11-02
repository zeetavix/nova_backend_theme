/** @odoo-module **/
//
// Nova toast history — a systray bell with the last 20 notifications.
// The NotificationPlugin.add() patch is pass-through only: it mirrors a
// serializable snapshot {message, type, title, sticky, ts} into a
// db+uid-namespaced localStorage ring buffer, then forwards options
// untouched so sticky/autoclose/dedup keep working. With per-user DND on,
// non-sticky toasts are logged but not shown.
//

import { Component, signal } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { session } from "@web/session";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";

const HISTORY_MAX = 20;

function historyKey() {
    try {
        const db = session.db || "db";
        const uid = session.uid || "anon";
        return `nova_toast_history:${db}:${uid}`;
    } catch {
        return "nova_toast_history";
    }
}

function isDnd() {
    try {
        if (session.nova_dnd_sticky_only === true) {
            return true;
        }
    } catch {
        return false;
    }
    return inQuietHours();
}

function bypassSet() {
    try {
        const list = session.nova_dnd_bypass;
        return new Set(Array.isArray(list) ? list : []);
    } catch {
        return new Set();
    }
}

function isBypass(options) {
    try {
        const type = String((options && options.type) || "warning").toLowerCase();
        return bypassSet().has(type);
    } catch {
        return false;
    }
}

function quietWindow() {
    // Whole hours only: the clock reads integer hours, so 22.5 would leave
    // 22:00-22:29 wrongly loud. Floor defensively; start===end disables.
    try {
        const q = session.nova_quiet;
        const start = Math.floor(Number(q && q.start));
        const end = Math.floor(Number(q && q.end));
        if (!Number.isFinite(start) || !Number.isFinite(end)) {
            return null;
        }
        if (start < 0 || start > 23 || end < 0 || end > 23 || start === end) {
            return null;
        }
        return { start, end };
    } catch {
        return null;
    }
}

function hourNow(when) {
    // User's Odoo timezone first (correct when traveling); browser zone
    // only as fallback.
    const date = when instanceof Date ? when : new Date();
    let tz = null;
    try {
        tz = (session.user_context && session.user_context.tz) || null;
    } catch {
        tz = null;
    }
    try {
        if (tz) {
            const parts = new Intl.DateTimeFormat("en-US", {
                hour: "numeric",
                hour12: false,
                timeZone: tz,
            }).formatToParts(date);
            const hour = parts.find((p) => p.type === "hour");
            const h = hour ? Number(hour.value) % 24 : NaN;
            if (Number.isFinite(h)) {
                return h;
            }
        }
        return date.getHours();
    } catch {
        return date.getHours();
    }
}

function inQuietHours(nowHour) {
    const window_ = quietWindow();
    if (!window_) {
        return false;
    }
    let h = nowHour;
    if (typeof h !== "number" || !Number.isFinite(h)) {
        try {
            h = hourNow();
        } catch {
            return false;
        }
    }
    const { start, end } = window_;
    if (start > end) {
        return h >= start || h < end;
    }
    return h >= start && h < end;
}

function digestKey() {
    // Single evergreen key (value = local day): no per-day key litter.
    try {
        const db = session.db || "db";
        const uid = session.uid || "anon";
        return `nova_quiet_digest:${db}:${uid}`;
    } catch {
        return "nova_quiet_digest";
    }
}

// Morning digest: one sticky summary of what arrived overnight. Fires once
// per day, only when the user loads Odoo after quiet hours ended.
export function maybeMorningDigest(notification) {
    try {
        const window_ = quietWindow();
        if (!window_) {
            return;
        }
        if (inQuietHours()) {
            return;
        }
        let done = null;
        try {
            // Value is the local day the digest last fired; a new morning
            // re-arms it. Single key, no per-day litter.
            done = window.localStorage.getItem(digestKey());
        } catch {
            done = "1";
        }
        let today = "";
        try {
            today = new Date().toLocaleDateString("en-CA");
        } catch {
            today = "";
        }
        if (done && (!today || done === today)) {
            return;
        }
        const items = readToastHistory().filter((t) => {
            try {
                if (!t || !t.ts) {
                    return false;
                }
                const d = new Date(Number(t.ts));
                if (Number.isNaN(d.getTime())) {
                    return false;
                }
                return inQuietHours(hourNow(d));
            } catch {
                return false;
            }
        });
        if (!items.length || !notification) {
            // No marker consumed: a quiet toast arriving later this morning
            // still earns its summary.
            return;
        }
        try {
            window.localStorage.setItem(digestKey(), today || "1");
        } catch {
            // digest retries next load
            return;
        }
        novaReplaying = true;
        try {
            notification.add(
                items.length === 1
                    ? _t("1 notification arrived overnight.")
                    : _t("%s notifications arrived overnight — see the Nova bell.", items.length),
                { type: "info", sticky: true }
            );
        } catch {
            // digest stays in history
        } finally {
            novaReplaying = false;
        }
    } catch {
        // digest optional
    }
}

function safeText(value) {
    try {
        if (value === null || value === undefined || value === false) {
            return "";
        }
        return String(value).slice(0, 280);
    } catch {
        return "";
    }
}

export function readToastHistory() {
    try {
        const raw = window.localStorage.getItem(historyKey());
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

export function logToast(message, options) {
    const entry = {
        message: safeText(typeof message === "string" ? message : message?.toString?.()),
        type: options && typeof options.type === "string" ? options.type : "warning",
        title: safeText(options && options.title),
        sticky: !!(options && options.sticky),
        ts: Date.now(),
    };
    if (!entry.message) {
        return entry;
    }
    try {
        const items = [entry, ...readToastHistory()].slice(0, HISTORY_MAX);
        window.localStorage.setItem(historyKey(), JSON.stringify(items));
    } catch {
        // quota or private mode: drop oldest half and retry once
        try {
            const items = [entry, ...readToastHistory()].slice(0, 10);
            window.localStorage.setItem(historyKey(), JSON.stringify(items));
        } catch {
            // history stays memory-only
        }
    }
    try {
        window.dispatchEvent(new CustomEvent("nova:toasts"));
    } catch {
        // bell polls on open instead
    }
    return entry;
}

let novaToastPatched = false;
// Set around history replay so the replayed toast isn't logged again.
let novaReplaying = false;
if (!novaToastPatched) {
    novaToastPatched = true;
    try {
        patch(NotificationPlugin.prototype, {
            add(message, options = {}) {
                if (!novaReplaying) {
                    logToast(message, options);
                }
                // Priority bypass: danger (default) and opted-in warning pop
                // even under DND/quiet hours; everything else waits in the bell.
                if (isDnd() && !(options && options.sticky) && !isBypass(options)) {
                    return () => {};
                }
                return super.add(message, options);
            },
        });
    } catch (e) {
        // eslint-disable-next-line no-console
        console.error("[nova] toast history patch failed:", e);
    }
}

export class NovaToastBell extends Component {
    static template = "nova_backend_theme.NovaToastBell";
    static props = {};

    setup() {
        this.notification = useService("notification");
        // Owl 3 has no useState: three signals + a snapshot getter the
        // template reads (same pattern as NovaHome).
        this._open = signal(false);
        this._unread = signal(0);
        this._items = signal([]);
        this.onStorage = () => this.refresh(false);
        try {
            window.addEventListener("nova:toasts", this.onStorage);
        } catch {
            // bell refreshes on open instead
        }
        try {
            maybeMorningDigest(this.notification);
        } catch {
            // digest optional
        }
    }

    get state() {
        let open = false;
        let unread = 0;
        let items = [];
        try {
            open = this._open();
        } catch {
            open = false;
        }
        try {
            unread = this._unread();
        } catch {
            unread = 0;
        }
        try {
            const list = this._items();
            items = Array.isArray(list) ? list : [];
        } catch {
            items = [];
        }
        return { open, unread, items };
    }

    refresh(resetUnread = true) {
        let items = [];
        try {
            items = readToastHistory();
        } catch {
            items = [];
        }
        try {
            this._items.set(items);
        } catch {
            // panel stays put
        }
        if (resetUnread) {
            try {
                this._unread.set(0);
            } catch {
                // counter stays put
            }
        } else {
            try {
                if (!this._open()) {
                    this._unread.set(this._unread() + 1);
                }
            } catch {
                // counter stays put
            }
        }
    }

    toggle() {
        let open = false;
        try {
            open = !this._open();
            this._open.set(open);
        } catch {
            open = false;
        }
        if (open) {
            this.refresh(true);
        }
    }

    close() {
        try {
            this._open.set(false);
        } catch {
            // panel stays put
        }
    }

    replay(item, ev) {
        if (ev) {
            try {
                ev.preventDefault();
                ev.stopPropagation();
            } catch {
                // replay proceeds
            }
        }
        if (!item || !item.message) {
            return;
        }
        // Replay bypasses the history log (module flag) so re-showing never
        // duplicates the entry or bumps the unread counter.
        novaReplaying = true;
        try {
            this.notification.add(item.message, {
                type: item.type || "warning",
                title: item.title || undefined,
                sticky: true,
            });
        } catch {
            // toast stays in history
        } finally {
            novaReplaying = false;
        }
    }

    timeLabel(ts) {
        try {
            const date = new Date(Number(ts));
            if (Number.isNaN(date.getTime())) {
                return "";
            }
            return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        } catch {
            return "";
        }
    }
}

registry.category("systray").add(
    "nova_backend_theme.toast_bell",
    { Component: NovaToastBell },
    { sequence: 50 }
);
