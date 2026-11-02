/** @odoo-module **/
//
// Nova Launcher — the Community home screen Odoo never shipped.
// Clean-room original: uses only public Community services (menu, action,
// orm) and plain browser storage. No Enterprise code copied or referenced.
//
// Concept: search-first command palette, not a browse grid. Empty query
// browses apps; any query searches apps AND every permitted menu item with
// breadcrumb paths, keyboard-first. Tabs (favorites / most / recent /
// recommended), live insight tiles, and a rule-based shortcut assistant
// round it out. The assistant navigates; it never pretends to be AI.
//

import { Component, onMounted, onWillUnmount, signal, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { rpc } from "@web/core/network/rpc";
import { fuzzyLookup } from "@web/core/utils/search";
import { setBookmarks, bookmarkState } from "@nova_backend_theme/home/nova_bookmark_store";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

const TILE_SELECTOR = ".nova_app, .nova_result, .nova_dock_row";
const MAX_MENU_RESULTS = 30;
const MAX_TILES = 24;
const RECORD_MIN_CHARS = 2;
const RECORD_DEBOUNCE_MS = 250;
const RECORD_LIMIT = 8;

const LENSES = [
    { id: "all", label: "All", match: [] },
    { id: "sales", label: "Sales", match: ["sale", "pos", "crm"] },
    { id: "desk", label: "Desk", match: ["project", "helpdesk", "mail", "discuss"] },
    { id: "stock", label: "Stock", match: ["stock", "purchase", "mrp", "inventory"] },
];

let bookmarkMigrated = false;
let novaQueueSeq = 0;
let novaQueueRun = null;

function storageKey() {
    try {
        const uid = (user && user.userId) || (session && session.uid) || "anon";
        const db = (session && session.db) || "db";
        return `nova_home:${db}:${uid}`;
    } catch {
        return null;
    }
}

function insightPrevKey() {
    try {
        const uid = (user && user.userId) || (session && session.uid) || "anon";
        const db = (session && session.db) || "db";
        return `nova_insights_prev:${db}:${uid}`;
    } catch {
        return null;
    }
}

function readJSON(key, fallback) {
    try {
        const raw = window.localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
    } catch {
        return fallback;
    }
}

function writeJSON(key, value) {
    if (!key) {
        return;
    }
    try {
        window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
        // QuotaExceeded (private mode/full disk): prune usage to newest 50,
        // cap favorites, retry once; else stay ephemeral.
        try {
            const pruned = { ...(value || {}) };
            if (pruned.usage && typeof pruned.usage === "object") {
                const ids = Object.keys(pruned.usage).sort(
                    (a, b) => (pruned.usage[b] && pruned.usage[b].ts || 0) - (pruned.usage[a] && pruned.usage[a].ts || 0)
                );
                const kept = {};
                for (const id of ids.slice(0, 50)) {
                    kept[id] = pruned.usage[id];
                }
                pruned.usage = kept;
            }
            if (Array.isArray(pruned.favorites)) {
                pruned.favorites = pruned.favorites.slice(0, 50);
            }
            window.localStorage.setItem(key, JSON.stringify(pruned));
        } catch {
            // ephemeral it is
        }
    }
}

let messageSeq = 0;

function compactNumber(value) {
    try {
        return new Intl.NumberFormat("en", { notation: "compact" }).format(value);
    } catch {
        return String(value);
    }
}

export class NovaHome extends Component {
    static template = "nova_backend_theme.NovaHome";
    props = useProps({ ...standardActionServiceProps });
    searchRef = signal.ref();
    rootRef = signal.ref();
    assistantInputRef = signal.ref();

    setup() {
        this.menu = useService("menu");
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this._alive = true;
        this.search = signal("");
        this.searchOpen = signal(false);
        this.showAllTiles = signal(false);
        this.tab = signal("recommended");
        this.favorites = signal([]);
        this.usageBump = signal(0);
        this.insights = signal(null);
        this.assistantOpen = signal(false);
        this.messages = signal([]);
        this.selected = signal(null);
        this.records = signal({ q: "", list: [] });
        this.lens = signal("all");
        this.dock = signal(null);
        this.attention = signal(null);
        // The home screen shows the single most urgent activity; the rest
        // wait behind "N more" so quick create and apps stay above the fold.
        this.attExpanded = signal(false);
        this.quickActions = signal([]);
        this.onboard = signal({});
        this._recordSeq = 0;
        this._recordTimer = null;
        this._indexCache = { key: null, entries: [] };
        try {
            const stored = readJSON(storageKey(), null) || {};
            this.favorites.set(
                Array.isArray(stored.favorites) ? stored.favorites.filter((v) => typeof v === "number") : []
            );
            this.usage = stored.usage && typeof stored.usage === "object" ? stored.usage : {};
            if (stored.onboard && typeof stored.onboard === "object") {
                this.onboard.set(stored.onboard);
            }
            if (typeof stored.lens === "string" && LENSES.some((l) => l.id === stored.lens)) {
                this.lens.set(stored.lens);
            }
        } catch {
            this.usage = {};
        }
        onMounted(() => {
            // No autofocus: the search reveals on first keystroke instead.
            // Type-anywhere listener (document-level: root-div keydown never
            // sees body-focus keys). Removed on unmount; gated to this
            // screen's lifetime.
            this._onDocumentKeydown = (ev) => {
                try {
                    this.maybeRevealSearch(ev);
                } catch {
                    // grid nav continues unaffected
                }
            };
            try {
                document.addEventListener("keydown", this._onDocumentKeydown, true);
            } catch {
                // type-to-search stays dormant
            }
            // Click-outside-to-dismiss (Escape semantics): a click anywhere
            // on the home outside the bar fully dismisses it. Scoped to the
            // launcher — navbar, systray and assistant flows never trigger it.
            this._onDocumentClick = (ev) => {
                try {
                    if (!this._alive || !this.showSearch) {
                        return;
                    }
                    const t = ev.target;
                    if (!t || !t.closest) {
                        return;
                    }
                    if (!t.closest(".nova_launcher")) {
                        return;
                    }
                    if (t.closest(".nova_search_wrap")) {
                        return;
                    }
                    if (t.closest(".nova_assistant, .nova_fab")) {
                        return;
                    }
                    this.clearSearch();
                } catch {
                    // click proceeds normally
                }
            };
            try {
                document.addEventListener("click", this._onDocumentClick, true);
            } catch {
                // dismiss stays manual (Esc / clear button)
            }
            // Launcher owns the navbar context: hide the previous app's
            // menus + brand while open (stock home-menu behaviour).
            try {
                document.body.classList.add("o_nova_home_open");
            } catch {
                // navbar keeps its context
            }
            // One-trip bootstrap: insights + bookmarks + dock + attention in a
            // single round-trip; each section applies independently.
            this.loadHome();
        });
        onWillUnmount(() => {
            this._alive = false;
            try {
                document.body.classList.remove("o_nova_home_open");
            } catch {
                // class already gone
            }
            try {
                if (this._onDocumentKeydown) {
                    document.removeEventListener("keydown", this._onDocumentKeydown, true);
                }
            } catch {
                // listener already gone
            }
            this._onDocumentKeydown = null;
            try {
                if (this._onDocumentClick) {
                    document.removeEventListener("click", this._onDocumentClick, true);
                }
            } catch {
                // listener already gone
            }
            this._onDocumentClick = null;
            try {
                clearTimeout(this._recordTimer);
            } catch {
                // no timer to clear
            }
            try {
                window.removeEventListener("nova:bookmarks-changed", this._onBookmarksChanged);
            } catch {
                // listener already gone
            }
        });
        // Palette pin/unpin (nova_bookmark_provider) refreshes the dock even
        // when the launcher isn't the active screen — guarded by _alive.
        this._onBookmarksChanged = () => {
            if (!this._alive) {
                return;
            }
            try {
                this.loadDock();
            } catch {
                // dock refreshes next visit
            }
        };
        try {
            window.addEventListener("nova:bookmarks-changed", this._onBookmarksChanged);
        } catch {
            // dock refreshes next visit
        }
    }

    // ---------- server bookmarks (cross-device favorites) ----------

    applyBookmarkState(state) {
        try {
            const menus = state && Array.isArray(state.menus)
                ? state.menus.map(Number).filter((v) => v)
                : null;
            const records = state && Array.isArray(state.records) ? state.records : null;
            if (menus) {
                this.favorites.set(menus);
            }
            setBookmarks(
                menus || this.favorites(),
                records || []
            );
            this.persist();
        } catch {
            // local state stays put
        }
    }

    async loadHome(sections, fresh) {
        let res = null;
        try {
            const params = {};
            if (Array.isArray(sections) && sections.length) {
                params.sections = sections;
            }
            if (fresh) {
                params.fresh = true;
            }
            res = await rpc("/nova/home", params);
        } catch {
            res = null;
        }
        if (!this._alive) {
            return;
        }
        if (!res || typeof res !== "object") {
            return;
        }
        if ("tiles" in res) {
            this.applyInsightTiles(Array.isArray(res.tiles) ? res.tiles : []);
        }
        if ("bookmarks" in res && res.bookmarks) {
            await this.adoptBookmarkState(res.bookmarks);
        }
        if ("dock" in res) {
            try {
                this.dock.set(Array.isArray(res.dock) ? res.dock : []);
            } catch {
                // dock stays hidden
            }
        }
        if ("attention" in res && res.attention && typeof res.attention === "object") {
            try {
                this._attentionLoaded = true;
            } catch {
                // flag stays put
            }
            try {
                this.attention.set(res.attention);
            } catch {
                // strip stays hidden
            }
        }
        if ("create" in res) {
            try {
                this.quickActions.set(Array.isArray(res.create) ? res.create : []);
            } catch {
                // shortcuts stay hidden
            }
        }
    }

    applyInsightTiles(rows) {
        let tiles = [];
        let prev = {};
        try {
            prev = readJSON(insightPrevKey(), {}) || {};
            if (typeof prev !== "object" || Array.isArray(prev)) {
                prev = {};
            }
        } catch {
            prev = {};
        }
        const next = {};
        try {
            for (const row of (Array.isArray(rows) ? rows : []).slice(0, 4)) {
                try {
                    const app = this.findApp(row.app_hints || []);
                    if (!app || typeof row.value !== "number") {
                        continue;
                    }
                    // Zero-value tiles are noise on a calm home: hide them.
                    // The section hides itself when nothing remains.
                    if (row.value === 0) {
                        continue;
                    }
                    const key = typeof row.key === "string" ? row.key : String(row.key || "");
                    next[key] = row.value;
                    let delta = 0;
                    try {
                        const old = prev[key];
                        delta = typeof old === "number" ? row.value - old : 0;
                    } catch {
                        delta = 0;
                    }
                    tiles.push({
                        id: `insight:${row.key}`,
                        title: row.label,
                        value: compactNumber(row.value),
                        raw: row.value,
                        overdue: typeof row.overdue === "number" ? row.overdue : 0,
                        delta,
                        res_model: typeof row.res_model === "string" ? row.res_model : null,
                        domain: Array.isArray(row.domain) ? row.domain : null,
                        app,
                    });
                } catch {
                    // skip malformed tile
                }
            }
        } catch {
            tiles = [];
        }
        try {
            writeJSON(insightPrevKey(), next);
        } catch {
            // baseline stays put
        }
        try {
            this._insightsAt = Date.now();
        } catch {
            // freshness stays put
        }
        try {
            this.insights.set(tiles);
        } catch {
            // section simply never appears
        }
    }

    get showCaughtUp() {
        try {
            if (this.searchText || !this._attentionLoaded) {
                return false;
            }
            return !this.showAttention;
        } catch {
            return false;
        }
    }

    // "Updated 4m ago" freshness stamp for the Insights header.
    get insightsFresh() {
        try {
            const at = this._insightsAt;
            if (!at) {
                return "";
            }
            const mins = Math.max(0, Math.floor((Date.now() - at) / 60000));
            if (mins < 1) {
                return _t("updated just now");
            }
            if (mins === 1) {
                return _t("updated 1 min ago");
            }
            if (mins < 60) {
                return _t("updated %s min ago", mins);
            }
            const hours = Math.floor(mins / 60);
            return hours === 1
                ? _t("updated 1 hour ago")
                : _t("updated %s hours ago", hours);
        } catch {
            return "";
        }
    }

    // Delta caption for a tile ("▲2 since last visit", "" when flat/new).
    deltaCaption(tile) {
        try {
            const delta = tile && typeof tile.delta === "number" ? tile.delta : 0;
            if (!delta) {
                return "";
            }
            const arrow = delta > 0 ? "▲" : "▼";
            return _t("%s since last visit", arrow + Math.abs(delta));
        } catch {
            return "";
        }
    }

    async adoptBookmarkState(server) {
        if (!server) {
            setBookmarks(this.favorites(), []);
            return;
        }
        try {
            const empty =
                !(Array.isArray(server.menus) && server.menus.length) &&
                !(Array.isArray(server.records) && server.records.length);
            const legacy = this.favorites();
            if (empty && legacy.length && !bookmarkMigrated) {
                bookmarkMigrated = true;
                let valid = [];
                try {
                    const validIds = new Set(this.allApps.map((app) => Number(app.id)));
                    valid = legacy.filter((v) => validIds.has(Number(v))).slice(0, 20);
                } catch {
                    valid = [];
                }
                // Fire all migrations at once instead of stalling 20 serial RPCs.
                try {
                    server = await Promise.allSettled(
                        valid.map((id) => this.orm.call("nova.bookmark", "toggle_menu", [id]))
                    ).then((settled) => {
                        const done = settled.find(
                            (s) => s.status === "fulfilled" && s.value
                        );
                        return done ? done.value : server;
                    });
                } catch {
                    // migration best-effort only
                }
            }
        } catch {
            // migration best-effort only
        }
        this.applyBookmarkState(server);
    }

    // ---------- identity ----------

    get firstName() {
        try {
            const full = (user && user.name) || "";
            return full.trim().split(/\s+/)[0] || "there";
        } catch {
            return "there";
        }
    }

    get todayLabel() {
        try {
            return new Date().toLocaleDateString(undefined, {
                weekday: "long",
                month: "long",
                day: "numeric",
            });
        } catch {
            return "";
        }
    }

    get daypart() {
        const hour = new Date().getHours();
        if (hour < 5) {
            return "Good night";
        } else if (hour < 12) {
            return "Good morning";
        } else if (hour < 18) {
            return "Good afternoon";
        }
        return "Good evening";
    }

    // ---------- persistence ----------

    persist() {
        try {
            const usage = this.usage && typeof this.usage === "object" ? { ...this.usage } : {};
            const ids = Object.keys(usage);
            if (ids.length > 100) {
                ids.sort((a, b) => ((usage[b] && usage[b].ts) || 0) - ((usage[a] && usage[a].ts) || 0));
                for (const id of ids.slice(100)) {
                    delete usage[id];
                }
                this.usage = usage;
            }
            writeJSON(storageKey(), {
                favorites: this.favorites(),
                usage,
                onboard: this.onboardFlags(),
                lens: this.activeLens,
            });
        } catch {
            // persistence best-effort
        }
    }

    get activeLens() {
        try {
            const id = this.lens();
            return LENSES.some((l) => l.id === id) ? id : "all";
        } catch {
            return "all";
        }
    }

    get lensOptions() {
        return LENSES;
    }

    setLens(id) {
        try {
            if (LENSES.some((l) => l.id === id)) {
                this.lens.set(id);
                this.selected.set(null);
                this.persist();
            }
        } catch {
            // lens stays put
        }
    }

    lensOf(app) {
        try {
            const id = this.activeLens;
            if (id === "all") {
                return true;
            }
            const lens = LENSES.find((l) => l.id === id);
            const name = ((app && app.name) || "").toLowerCase();
            return !!lens && lens.match.some((m) => name.includes(m));
        } catch {
            return true;
        }
    }

    onboardFlags() {
        try {
            return this.onboard ? this.onboard() : {};
        } catch {
            return {};
        }
    }

    markOnboard(flag) {
        try {
            const current = this.onboardFlags();
            if (current[flag]) {
                return;
            }
            this.onboard.set({ ...current, [flag]: true });
            this.persist();
        } catch {
            // progress stays ephemeral
        }
    }

    get showOnboarding() {
        try {
            const flags = this.onboardFlags();
            if (flags.dismissed) {
                return false;
            }
            return this.onboardProgress().done < 5;
        } catch {
            return false;
        }
    }

    get onboardProgress() {
        let starred = false;
        let opened = false;
        try {
            starred = this.favorites().length > 0;
        } catch {
            starred = false;
        }
        try {
            opened = Object.values(this.usage || {}).some((u) => u && u.count > 0);
        } catch {
            opened = false;
        }
        let flags = {};
        try {
            flags = this.onboardFlags();
        } catch {
            flags = {};
        }
        const items = [
            { key: "star", label: "Star your first app", done: starred },
            { key: "search", label: "Search apps and menus", done: !!flags.searched },
            { key: "open", label: "Open an app", done: opened },
            { key: "assistant", label: "Ask Nova something", done: !!flags.assistant },
            { key: "tabs", label: "Browse your apps", done: !!flags.tabbed },
        ];
        return { items, done: items.filter((i) => i.done).length };
    }

    dismissOnboarding() {
        try {
            const current = this.onboardFlags();
            this.onboard.set({ ...current, dismissed: true });
            this.persist();
        } catch {
            // card stays
        }
    }

    recordVisit(menuId) {
        try {
            const id = Number(menuId);
            if (!id) {
                return;
            }
            const prev = this.usage[id] || { count: 0, ts: 0 };
            this.usage = {
                ...this.usage,
                [id]: { count: (prev.count || 0) + 1, ts: Date.now() },
            };
            this.usageBump.set(this.usageBump() + 1);
            this.persist();
        } catch {
            // visits stay ephemeral
        }
    }

    usageOf(menuId) {
        try {
            this.usageBump();
            return this.usage[Number(menuId)] || { count: 0, ts: 0 };
        } catch {
            return { count: 0, ts: 0 };
        }
    }

    isFavorite(menuId) {
        try {
            return this.favorites().includes(Number(menuId));
        } catch {
            return false;
        }
    }

    toggleFavorite(app, ev) {
        if (ev) {
            try {
                ev.preventDefault();
                ev.stopPropagation();
            } catch {
                // event stays live
            }
        }
        let id = 0;
        try {
            id = Number(app && app.id) || 0;
        } catch {
            id = 0;
        }
        if (!id) {
            return;
        }
        try {
            const favs = this.favorites().filter((v) => v !== id);
            if (favs.length === this.favorites().length) {
                favs.push(id);
            }
            this.favorites.set(favs);
            let kept = [];
            try {
                kept = bookmarkState.records() || [];
            } catch {
                kept = [];
            }
            setBookmarks(favs, kept);
            this.persist();
        } catch {
            // starring stays visual-only
        }
        const token = (this._favSeq = (this._favSeq || 0) + 1);
        try {
            this.orm.call("nova.bookmark", "toggle_menu", [id]).then(
                (state) => {
                    if (token === this._favSeq) {
                        this.applyBookmarkState(state);
                    }
                },
                () => {}
            );
        } catch {
            // optimistic state stands
        }
    }

    // ---------- apps & search ----------

    get searchText() {
        try {
            return this.search();
        } catch {
            return "";
        }
    }

    get query() {
        try {
            return this.search().trim().toLowerCase();
        } catch {
            return "";
        }
    }

    // Bar visibility: open on first keystroke (or hint click), closed on
    // clear/Escape. Text implies open, so backspacing to empty keeps it.
    get showSearch() {
        try {
            if (this.searchOpen()) {
                return true;
            }
        } catch {
            // fall through to text check
        }
        try {
            return !!(this.search() || "").trim();
        } catch {
            return false;
        }
    }

    revealSearch() {
        try {
            this.searchOpen.set(true);
        } catch {
            // input stays collapsed
        }
        try {
            const el = this.searchRef();
            if (el && typeof el.focus === "function") {
                el.focus({ preventScroll: true });
            }
        } catch {
            // focus stays put
        }
    }

    // Document-level type-anywhere gate. The input stays mounted (visually
    // collapsed), so focusing here is synchronous and the first character
    // is never swallowed.
    maybeRevealSearch(ev) {
        if (!ev || !this._alive) {
            return;
        }
        let key = null;
        try {
            key = ev.key;
        } catch {
            return;
        }
        if (typeof key !== "string" || key.length !== 1) {
            return;
        }
        try {
            if (ev.ctrlKey || ev.metaKey || ev.altKey) {
                return;
            }
        } catch {
            return;
        }
        try {
            if (this.showSearch || this.assistantIsOpen) {
                return;
            }
        } catch {
            return;
        }
        try {
            if (
                ev.target && ev.target.closest &&
                ev.target.closest("input, textarea, select, [contenteditable='true']")
            ) {
                return;
            }
        } catch {
            return;
        }
        // Yield to overlays that own the keyboard: palette, dialogs, menus.
        try {
            if (document.querySelector(".o_command_palette, .o_dialog, .o-dropdown--menu")) {
                return;
            }
        } catch {
            return;
        }
        this.revealSearch();
    }

    get allApps() {
        let apps = [];
        try {
            const result = this.menu ? this.menu.getApps() : null;
            apps = Array.isArray(result) ? result : [];
        } catch {
            apps = [];
        }
        return apps.filter((app) => app && app.actionID);
    }

    get apps() {
        const query = this.query;
        const list = this.allApps;
        if (!query) {
            return list;
        }
        try {
            return fuzzyLookup(query, list, (app) => app.name || "");
        } catch {
            return list.filter((app) => (app.name || "").toLowerCase().includes(query));
        }
    }

    get appCount() {
        try {
            return this.apps.length;
        } catch {
            return 0;
        }
    }

    get activeTab() {
        try {
            return this.tab();
        } catch {
            return "recommended";
        }
    }

    setTab(name) {
        try {
            this.tab.set(name);
            this.markOnboard("tabbed");
        } catch {
            // tab stays put
        }
        try {
            this._tabTouched = true;
        } catch {
            // flag stays put
        }
        try {
            this.selected.set(null);
        } catch {
            // selection stays put
        }
    }

    /** Every app, always: starred first, then alphabetical. Search uses `apps`. */
    get visibleTiles() {
        let list = [];
        try {
            let favs = new Set();
            try {
                favs = new Set((this.favorites() || []).map(Number));
            } catch {
                favs = new Set();
            }
            const starred = [];
            const rest = [];
            for (const app of this.allApps) {
                if (favs.has(Number(app.id))) {
                    starred.push(app);
                } else {
                    rest.push(app);
                }
            }
            const byName = (a, b) => String(a.name || "").localeCompare(String(b.name || ""));
            starred.sort(byName);
            rest.sort(byName);
            list = [...starred, ...rest];
        } catch {
            list = [];
        }
        return list;
    }

    /** Browse grid: favorites first, capped at 12 with a Show-all toggle. */
    get limitedTiles() {
        try {
            if (this.showAllTiles && this.showAllTiles()) {
                return this.visibleTiles;
            }
        } catch {
            // fall through to capped list
        }
        return this.visibleTiles.slice(0, 12);
    }

    get totalTiles() {
        try {
            return this.visibleTiles.length;
        } catch {
            return 0;
        }
    }

    get hasMoreTiles() {
        try {
            return !this.showAllTiles() && this.visibleTiles.length > 12;
        } catch {
            return false;
        }
    }

    toggleShowAll() {
        try {
            this.showAllTiles.set(!this.showAllTiles());
        } catch {
            // grid stays capped
        }
    }

    get emptyTiles() {
        try {
            if (this.searchText || this.visibleTiles.length) {
                return null;
            }
            return {
                title: "No apps available",
                text: "Ask your admin for access — your apps will wait for you here.",
            };
        } catch {
            return null;
        }
    }
    get insightsPending() {
        try {
            return this.insights() === null;
        } catch {
            return false;
        }
    }

    /**
     * Flattened actionable menu entries with breadcrumb paths. Built from the
     * loaded menu tree, so it only ever contains permitted menus. Rebuilt per
     * render; the tree cache inside the menu service keeps it cheap.
     */
    get menuIndex() {
        const entries = [];
        let apps = [];
        try {
            apps = this.allApps;
        } catch {
            apps = [];
        }        // Rebuild only when the app set changes; keystrokes reuse the cache.
        try {
            const key = apps.map((app) => app && app.id).join(",");
            if (this._indexCache.key === key && Array.isArray(this._indexCache.entries)) {
                return this._indexCache.entries;
            }
            this._indexCache.key = key;
        } catch {
            // fall through and rebuild
        }
        try {
            for (const app of apps) {
                let tree = null;
                try {
                    tree = this.menu.getMenuAsTree(app.id);
                } catch {
                    tree = null;
                }
                if (!tree) {
                    continue;
                }
                const walk = (node, trail) => {
                    const kids = (node && node.children) || [];
                    for (const childId of kids) {
                        let child = null;
                        try {
                            child = this.menu.getMenu(childId);
                        } catch {
                            child = null;
                        }
                        if (!child) {
                            continue;
                        }
                        const path = [...trail, child.name || ""];
                        if (child.actionID) {
                            entries.push({ menu: child, app, path: path.join(" › ") });
                        }
                        let sub = null;
                        try {
                            sub = this.menu.getMenuAsTree(childId);
                        } catch {
                            sub = null;
                        }
                        if (sub && sub.children && sub.children.length) {
                            walk(sub, path);
                        }
                    }
                };
                walk(tree, [app.name || ""]);
            }
        } catch {
            // index stays partial; launcher still works
        }
        try {
            this._indexCache.entries = entries;
        } catch {
            // cache stays stale; rebuilt next render
        }
        return entries;
    }

    get matchedMenus() {
        const query = this.query;
        if (!query) {
            return [];
        }
        const normQuery = String(query).trim().toLowerCase();
        let found = [];
        try {
            found = fuzzyLookup(query, this.menuIndex, (entry) => {
                const name = (entry.menu && entry.menu.name) || "";
                const parts = entry.path ? entry.path.split(" › ") : [name];
                return [entry.path || name, [...parts].reverse().join(" › ")];
            }).filter((entry) => this.lensOf(entry.app));
        } catch {
            try {
                found = this.menuIndex.filter(
                    (entry) =>
                        ((entry.menu && entry.menu.name) || "").toLowerCase().includes(normQuery) &&
                        this.lensOf(entry.app)
                );
            } catch {
                found = [];
            }
        }
        // fuzzyLookup already returns best scores first: an exact leaf hit
        // like "Users" outscores fuzzy partials like "Customers". Never
        // re-sort by breadcrumb length — that buried exact hits under short
        // unrelated paths. Only lift exact and prefix leaf-name matches to
        // the top; everything else keeps score order (stable sort).
        found.sort((a, b) => {
            const an = String((a.menu && a.menu.name) || "").trim().toLowerCase();
            const bn = String((b.menu && b.menu.name) || "").trim().toLowerCase();
            const rankOf = (n) =>
                n && n === normQuery ? 0 : n && n.startsWith(normQuery) ? 1 : 2;
            return rankOf(an) - rankOf(bn);
        });
        return found.slice(0, MAX_MENU_RESULTS);
    }

    get menuCount() {
        try {
            return this.matchedMenus.length;
        } catch {
            return 0;
        }
    }

    get hasResults() {
        try {
            return this.appCount + this.menuCount + this.recordCount > 0;
        } catch {
            return false;
        }
    }

    get topHit() {
        try {
            const apps = this.apps;
            if (apps && apps.length) {
                return { kind: "app", app: apps[0] };
            }
            const menus = this.matchedMenus;
            if (menus && menus.length) {
                return { kind: "menu", entry: menus[0] };
            }
        } catch {
            // no top hit
        }
        return null;
    }

    get selectedId() {
        try {
            return this.selected();
        } catch {
            return null;
        }
    }

    get selectedDescId() {
        try {
            const id = this.selected();
            if (id === null || id === undefined || id === "") {
                return null;
            }
            const key = String(id);
            if (/^\d+$/.test(key)) {
                return `nova-tile-${key}`;
            }
            const rec = key.match(/^(?:record|dock):([\w.]+):(\d+)$/);
            if (rec) {
                return `nova-rec-${rec[1].replace(/\./g, "-")}-${rec[2]}`;
            }
            const att = key.match(/^attention:(\d+)$/);
            if (att) {
                return `nova-att-${att[1]}`;
            }
            return null;
        } catch {
            return null;
        }
    }

    isSelected(menuId) {
        try {
            const current = this.selected();
            if (current === null || current === undefined || current === "") {
                return false;
            }
            return String(current) === String(menuId);
        } catch {
            return false;
        }
    }

    // DOM id for record rows. The sanitizing lives here (not in the
    // template) because Owl's expression tokenizer rejects regex literals.
    domId(prefix, model, resId) {
        try {
            const clean = String(model || "rec").replace(/\./g, "-");
            return `${prefix}-${clean}-${resId}`;
        } catch {
            return `${prefix}-row`;
        }
    }

    select(key) {
        const normalized = key === null || key === undefined || key === "" ? null : String(key);
        try {
            this.selected.set(normalized);
        } catch {
            // selection stays put
        }
        if (!normalized) {
            return;
        }
        try {
            const root = this.rootRef();
            if (!root || !root.querySelectorAll) {
                return;
            }
            const tiles = root.querySelectorAll(TILE_SELECTOR);
            for (const tile of tiles) {
                try {
                    if (tile.getAttribute && tile.getAttribute("data-menu-id") === normalized) {
                        tile.focus({ preventScroll: true });
                        tile.scrollIntoView({ block: "nearest" });
                        return;
                    }
                } catch {
                    continue;
                }
            }
        } catch {
            // focus stays put
        }
    }

    get suggestedChips() {
        try {
            const list = this.allApps.slice(0, 3);
            return list.map((app) => ({ label: `Open ${app.name}`, menuId: app.id }));
        } catch {
            return [];
        }
    }

    // ---------- insights (live, fail-silent) ----------

    findApp(hints) {
        try {
            const all = this.allApps;
            for (const hint of hints) {
                const found = all.find((app) =>
                    (app.name || "").toLowerCase().includes(hint)
                );
                if (found) {
                    return found;
                }
            }
        } catch {
            // no match
        }
        return null;
    }

    get dockRows() {
        try {
            const rows = this.dock();
            return Array.isArray(rows) ? rows : [];
        } catch {
            return [];
        }
    }

    get showDock() {
        try {
            return this.dockRows.length > 0;
        } catch {
            return false;
        }
    }

    get attentionData() {
        try {
            const data = this.attention();
            if (data && typeof data === "object") {
                return data;
            }
        } catch {
            // no attention data
        }
        return null;
    }

    get showAttention() {
        try {
            const data = this.attentionData;
            return !!data && ((data.overdue_count || 0) + (data.today_count || 0) > 0);
        } catch {
            return false;
        }
    }

    // One row by default, everything on request. Eight rows pushed the
    // launcher's real work (quick create, apps) off the first screen.
    get attentionItems() {
        try {
            const data = this.attentionData;
            const items = data && Array.isArray(data.items) ? data.items : [];
            return this.attExpanded() ? items : items.slice(0, 1);
        } catch {
            return [];
        }
    }

    get attentionMore() {
        try {
            const data = this.attentionData;
            const items = data && Array.isArray(data.items) ? data.items : [];
            return Math.max(0, items.length - 1);
        } catch {
            return 0;
        }
    }

    get attentionExpanded() {
        try {
            return !!this.attExpanded();
        } catch {
            return false;
        }
    }

    toggleAttention() {
        try {
            this.attExpanded.set(!this.attExpanded());
        } catch {
            // keep the collapsed view
        }
    }

    get digestOverdue() {
        try {
            const data = this.attentionData;
            return data ? Number(data.overdue_count || 0) : 0;
        } catch {
            return 0;
        }
    }

    get digestToday() {
        try {
            const data = this.attentionData;
            return data ? Number(data.today_count || 0) : 0;
        } catch {
            return 0;
        }
    }

    get quickCreate() {
        try {
            const list = this.quickActions();
            return Array.isArray(list) ? list : [];
        } catch {
            return [];
        }
    }

    async openCreate(act) {
        if (!act || !act.res_model) {
            return;
        }
        const label = act.label || act.res_model;
        // Dialog, not full page: dialogs render the action NAME as their
        // title (no "Unnamed" breadcrumb hole), keep the home context
        // behind (no stale navbar), and match "quick" semantics. Proven by
        // the triage queue flow. extra-large keeps order lines usable.
        let context = {};
        try {
            context = { ...(act.context && typeof act.context === "object" ? act.context : {}) };
        } catch {
            context = {};
        }
        try {
            context.dialog_size = "extra-large";
        } catch {
            // default dialog size stands
        }
        try {
            await this.action.doAction(
                {
                    type: "ir.actions.act_window",
                    res_model: act.res_model,
                    name: label,
                    views: [[false, "form"]],
                    target: "new",
                    context,
                },
                {
                    onClose: () => {
                        // Creation may have changed counts: refresh quietly.
                        try {
                            if (this._alive) {
                                this.loadHome();
                            }
                        } catch {
                            // numbers refresh next visit
                        }
                    },
                }
            );
        } catch {
            // stay on launcher
        }
        // NOTE: resolving the owning app menu (orm.read on ir.actions)
        // hung the navigation silently — do NOT add blocking reads here.
        // Navbar context stays as-is (same as stock palette flows).
    }

    openAttention(item) {
        if (!item) {
            return;
        }
        try {
            if (item.res_model && item.res_id) {
                this.action.doAction({
                    type: "ir.actions.act_window",
                    res_model: item.res_model,
                    res_id: item.res_id,
                    views: [[false, "form"]],
                    target: "current",
                });
                return;
            }
            if (item.id) {
                this.action.doAction({
                    type: "ir.actions.act_window",
                    res_model: "mail.activity",
                    res_id: item.id,
                    views: [[false, "form"]],
                    target: "current",
                });
            }
        } catch {
            // stay on launcher
        }
    }

    // Start-day queue: walk overdue activities one dialog at a time. Dialogs
    // (target "new") fire onClose reliably, unlike current-target navigation
    // where breadcrumb pops never call it — so the queue actually advances.
    startDayQueue() {
        let items = [];
        try {
            const data = this.attentionData;
            items = data && Array.isArray(data.items) ? data.items : [];
        } catch {
            items = [];
        }
        const queue = items.filter((i) => i && i.bucket === "overdue" && i.id);
        if (!queue.length) {
            try {
                this.notification.add(_t("All caught up — nothing overdue."), { type: "success" });
            } catch {
                // toast optional
            }
            return;
        }
        novaQueueSeq += 1;
        novaQueueRun = { items: queue, seq: novaQueueSeq };
        this.openQueueAt(0, novaQueueRun.seq);
    }

    openQueueAt(index, seq) {
        const run = novaQueueRun;
        if (!run || run.seq !== seq) {
            return;
        }
        if (!this._alive) {
            novaQueueRun = null;
            return;
        }
        if (index < 0 || index >= run.items.length) {
            novaQueueRun = null;
            try {
                this.notification.add("Triage queue complete.", { type: "success" });
            } catch {
                // toast optional
            }
            try {
                if (this._alive) {
                    this.loadHome(["attention"], true);
                }
            } catch {
                // counts refresh next visit
            }
            return;
        }
        const item = run.items[index];
        const remaining = run.items.length - index;
        try {
            const maybe = this.action.doAction(
                {
                    type: "ir.actions.act_window",
                    res_model: "mail.activity",
                    res_id: item.id,
                    views: [[false, "form"]],
                    target: "new",
                },
                {
                    onClose: () => {
                        if (!this._alive || !novaQueueRun || novaQueueRun.seq !== seq) {
                            return;
                        }
                        try {
                            this.loadHome(["attention"], true);
                        } catch {
                            // queue advances regardless
                        }
                        try {
                            const left = remaining - 1;
                            if (left > 0) {
                                this.notification.add(
                                    left === 1 ? "1 activity left in the queue." : `${left} activities left in the queue.`,
                                    { type: "info" }
                                );
                            }
                        } catch {
                            // progress toast optional
                        }
                        this.openQueueAt(index + 1, seq);
                    },
                }
            );
            if (maybe && typeof maybe.catch === "function") {
                maybe.catch(() => {
                    // dialog failed to open — abort loudly, not silently
                    if (novaQueueRun && novaQueueRun.seq === seq) {
                        novaQueueRun = null;
                    }
                    try {
                        this.notification.add(_t("Couldn't open the activity — queue stopped."), {
                            type: "warning",
                        });
                    } catch {
                        // launcher stands
                    }
                });
            }
        } catch {
            if (novaQueueRun && novaQueueRun.seq === seq) {
                novaQueueRun = null;
            }
        }
    }

    // One-click triage on attention rows: server-side methods respect the
    // user's timezone (context_today) and record rules; denial toasts.
    async snoozeAttention(item, ev) {
        if (ev) {
            try {
                ev.preventDefault();
                ev.stopPropagation();
            } catch {
                // row stays closed
            }
        }
        if (!item || !item.id) {
            return;
        }
        try {
            await this.orm.call("mail.activity", "action_reschedule_tomorrow", [[item.id]]);
        } catch {
            try {
                this.notification.add(_t("Couldn't snooze — access denied."), { type: "warning" });
            } catch {
                // toast optional
            }
            return;
        }
        try {
            await this.loadHome(["attention"], true);
        } catch {
            // row refreshes next visit
        }
    }

    async doneAttention(item, ev) {
        if (ev) {
            try {
                ev.preventDefault();
                ev.stopPropagation();
            } catch {
                // row stays closed
            }
        }
        if (!item || !item.id) {
            return;
        }
        try {
            await this.orm.call("mail.activity", "action_done", [[item.id]]);
        } catch {
            try {
                this.notification.add(_t("Couldn't mark done — access denied."), { type: "warning" });
            } catch {
                // toast optional
            }
            return;
        }
        try {
            await this.loadHome(["attention"], true);
        } catch {
            // row refreshes next visit
        }
    }

    async loadDock() {
        let rows = [];
        try {
            const res = await rpc("/nova/dock", {});
            rows = res && Array.isArray(res.dock) ? res.dock : [];
        } catch {
            rows = [];
        }
        if (!this._alive) {
            return;
        }
        try {
            this.dock.set(rows);
        } catch {
            // dock stays hidden
        }
    }

    isPinned(rec) {
        try {
            if (!rec) {
                return false;
            }
            const model = rec.model || rec.res_model;
            const resId = rec.res_id;
            if (!model || !resId) {
                return false;
            }
            return this.dockRows.some(
                (r) => r.res_model === model && Number(r.res_id) === Number(resId)
            );
        } catch {
            return false;
        }
    }

    async toggleDockPin(row, ev) {
        if (ev) {
            try {
                ev.preventDefault();
                ev.stopPropagation();
            } catch {
                // event stays live
            }
        }
        if (!row || !row.res_model || !row.res_id) {
            return;
        }
        await this.togglePin(
            { model: row.res_model, res_id: row.res_id, display_name: row.display_name },
            null
        );
    }

    async togglePin(rec, ev) {
        if (ev) {
            try {
                ev.preventDefault();
                ev.stopPropagation();
            } catch {
                // event stays live
            }
        }
        if (!rec || !rec.model || !rec.res_id) {
            return;
        }
        try {
            // toggle_record returns the full bookmark state, so no second
            // get_mine round-trip is needed — apply it directly.
            const state = await this.orm.call("nova.bookmark", "toggle_record", [
                rec.model,
                rec.res_id,
                rec.display_name || rec.model,
            ]);
            this.applyBookmarkState(state);
        } catch {
            // pin stays visual-only
        }
        await this.loadDock();
    }

    get insightTiles() {
        try {
            return this.insights() || [];
        } catch {
            return [];
        }
    }

    get showInsights() {
        try {
            const tiles = this.insights();
            return Array.isArray(tiles) && tiles.length > 0;
        } catch {
            return false;
        }
    }

    openInsight(tile) {
        if (!tile) {
            return;
        }
        try {
            if (tile.res_model && Array.isArray(tile.domain)) {
                this.action.doAction({
                    type: "ir.actions.act_window",
                    res_model: tile.res_model,
                    domain: tile.domain,
                    views: [
                        [false, "list"],
                        [false, "form"],
                    ],
                    target: "new",
                    name: tile.title,
                });
                return;
            }
        } catch {
            // fall through to the app
        }
        if (tile.app) {
            this.openApp(tile.app);
        }
    }

    // ---------- icons (navbar fallback chain) ----------

    appIcon(app) {
        try {
            if (app.webIconData) {
                return { kind: "img", src: app.webIconData };
            }
            if (typeof app.webIcon === "string" && app.webIcon) {
                const [icon, color] = app.webIcon.split(",");
                if (icon) {
                    return { kind: "font", icon, color: color || "#606770" };
                }
            } else if (app.webIcon && typeof app.webIcon === "object") {
                return {
                    kind: "font",
                    icon: app.webIcon.icon || "fa-cube",
                    color: app.webIcon.color || "#606770",
                };
            }
        } catch {
            // fall through to initial
        }
        const name = (app && app.name) || "?";
        return { kind: "initial", initial: name.trim().charAt(0).toUpperCase() };
    }

    // ---------- navigation ----------

    openApp(app) {
        if (app && app.actionID) {
            try {
                this.recordVisit(app.id);
                this.menu.selectMenu(app);
            } catch {
                // selection failures stay silent; user stays on launcher
            }
        }
    }

    lookupMenu(menuId) {
        try {
            return this.menu.getMenu(Number(menuId));
        } catch {
            return null;
        }
    }

    openMenu(entry) {
        if (entry && entry.menu) {
            this.openApp(entry.menu);
        }
    }

    openMenuById(menuId) {
        const node = this.lookupMenu(menuId);
        if (node && node.actionID) {
            this.openApp(node);
            return true;
        }
        return false;
    }

    openTopHit() {
        try {
            const id = this.selected();
            if (id) {
                if (this.openMenuById(id)) {
                    return;
                }
                const app = this.allApps.find((a) => Number(a.id) === Number(id));
                if (app) {
                    this.openApp(app);
                    return;
                }
            }
        } catch {
            // fall through to focused element / top hit
        }
        try {
            const active = document.activeElement;
            const row = active && active.closest ? active.closest(".nova_result") : null;
            if (row && row.dataset && row.dataset.model && row.dataset.resId) {
                this.openRecord({
                    model: row.dataset.model,
                    res_id: Number(row.dataset.resId),
                    display_name: row.getAttribute("aria-label") || "record",
                });
                return;
            }
            if (row && row.dataset && row.dataset.menuId) {
                const [kind, todoId] = String(row.dataset.menuId).split(":");
                if (kind === "attention" && todoId) {
                    let items = [];
                    try {
                        const data = this.attentionData;
                        items = (data && data.items) || [];
                    } catch {
                        items = [];
                    }
                    const todo = items.find((t) => String(t.id) === String(todoId));
                    if (todo) {
                        this.openAttention(todo);
                        return;
                    }
                }
            }
        } catch {
            // fall through to top hit
        }
        const hit = this.topHit;
        if (!hit) {
            return;
        }
        if (hit.kind === "app") {
            this.openApp(hit.app);
        } else {
            this.openMenu(hit.entry);
        }
    }

    findBest(query) {
        const q = (query || "").trim().toLowerCase();
        if (!q) {
            return null;
        }
        try {
            const app = this.allApps.find((a) => (a.name || "").toLowerCase().includes(q));
            if (app) {
                return { kind: "app", app };
            }
            const entry = this.menuIndex.find((e) =>
                ((e.menu && e.menu.name) || "").toLowerCase().includes(q)
            );
            if (entry) {
                return { kind: "menu", entry };
            }
        } catch {
            // no match
        }
        return null;
    }

    // ---------- search & keyboard ----------

    onSearchInput(ev) {
        try {
            this.search.set(ev.target.value);
            if ((ev.target.value || "").trim()) {
                this.markOnboard("searched");
            }
        } catch {
            // input stays uncontrolled rather than breaking render
        }
        try {
            this.selected.set(null);
        } catch {
            // selection stays put
        }
        this.scheduleRecordSearch();
    }

    scheduleRecordSearch() {
        const q = this.query;
        try {
            clearTimeout(this._recordTimer);
        } catch {
            // no timer to clear
        }
        if (q.length < RECORD_MIN_CHARS) {
            this._recordSeq = (this._recordSeq || 0) + 1;
            try {
                this.records.set({ q, list: [] });
            } catch {
                // state stays put
            }
            return;
        }
        const token = (this._recordSeq = (this._recordSeq || 0) + 1);
        try {
            this._recordTimer = setTimeout(async () => {
                let list = null;
                try {
                    const res = await rpc("/nova/search", { q, limit: RECORD_LIMIT });
                    if (res && res.throttled) {
                        return;
                    }
                    list = res && Array.isArray(res.results) ? res.results : [];
                } catch {
                    list = [];
                }
                if (token !== this._recordSeq || !this._alive) {
                    return;
                }
                if (list === null) {
                    return;
                }
                try {
                    this.records.set({ q, list });
                } catch {
                    // state stays put
                }
            }, RECORD_DEBOUNCE_MS);
        } catch {
            // search stays menu-only
        }
    }

    get recordResults() {
        try {
            const state = this.records();
            if (state && state.q === this.query && Array.isArray(state.list)) {
                return state.list;
            }
        } catch {
            // no records
        }
        return [];
    }

    get recordCount() {
        try {
            return this.recordResults.length;
        } catch {
            return 0;
        }
    }

    openRecord(rec) {
        if (!rec || !rec.model || !rec.res_id) {
            return;
        }
        try {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: rec.model,
                res_id: rec.res_id,
                views: [[false, "form"]],
                target: "current",
            });
        } catch {
            try {
                this.notification.add(
                    _t("Could not open %s — it may be deleted or restricted.", rec.display_name || "record"),
                    { type: "warning" }
                );
            } catch {
                // stay on launcher on failure
            }
        }
    }

    onSearchKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.openTopHit();
        }
    }

    clearSearch() {
        try {
            this.search.set("");
        } catch {
            // ignore
        }
        try {
            this.searchOpen.set(false);
        } catch {
            // visibility stays put
        }
        // Blur after render settles: blurring synchronously loses to the
        // patch cycle and focus sticks inside the collapsed bar.
        const blurSearch = () => {
            try {
                const el = this.searchRef();
                if (el && typeof el.blur === "function") {
                    el.blur();
                }
            } catch {
                // focus stays put
            }
            try {
                const active = document.activeElement;
                if (active && active.closest && active.closest(".nova_search_wrap")) {
                    active.blur();
                }
            } catch {
                // focus stays put
            }
        };
        try {
            (window.requestAnimationFrame || ((fn) => setTimeout(fn, 0)))(blurSearch);
        } catch {
            blurSearch();
        }
        try {
            this.selected.set(null);
        } catch {
            // selection stays put
        }
        try {
            clearTimeout(this._recordTimer);
            this._recordSeq = (this._recordSeq || 0) + 1;
            this.records.set({ q: "", list: [] });
        } catch {
            // record state stays put
        }
        const el = this.searchRef();
        if (el) {
            el.focus();
        }
    }

    onGridKeydown(ev) {
        try {
            if (ev.target && ev.target.closest && ev.target.closest(".nova_assistant")) {
                return;
            }
        } catch {
            // fall through to grid nav
        }
        if (ev.key === "Escape") {
            this.clearSearch();
            return;
        }
        // Type-anywhere-to-search lives on the document (see setup): a
        // root-div handler never sees keys pressed with body focus, since
        // events bubble up past the launcher, never through it.
        const root = this.rootRef();
        if (!root) {
            return;
        }
        const tiles = [...root.querySelectorAll(TILE_SELECTOR)];
        if (!tiles.length) {
            return;
        }
        if (ev.key === "ArrowDown" && tiles.indexOf(document.activeElement) < 0) {
            ev.preventDefault();
            tiles[0].focus();
            return;
        }
        if (ev.key === "ArrowUp" && tiles.indexOf(document.activeElement) < 0) {
            return;
        }
        const grid = root.querySelector(".nova_apps");
        const cols = Math.max(1, Math.floor((grid ? grid.clientWidth : 800) / 120));
        const idx = tiles.indexOf(document.activeElement);
        const idOf = (el) => {
            try {
                const raw = el && el.getAttribute ? el.getAttribute("data-menu-id") : null;
                return raw === null || raw === undefined || raw === "" ? null : String(raw);
            } catch {
                return null;
            }
        };
        let next = null;
        if (ev.key === "ArrowRight") {
            next = tiles[(idx + 1 + tiles.length) % tiles.length];
        } else if (ev.key === "ArrowLeft") {
            next = tiles[(idx - 1 + tiles.length) % tiles.length];
        } else if (ev.key === "ArrowDown") {
            next = tiles[Math.min(tiles.length - 1, (idx < 0 ? -1 : idx) + cols)];
        } else if (ev.key === "ArrowUp") {
            next = tiles[Math.max(0, (idx < 0 ? cols : idx) - cols)];
        } else {
            return;
        }
        ev.preventDefault();
        if (next) {
            const id = idOf(next);
            if (id) {
                this.select(id);
            } else {
                next.focus();
            }
        }
    }

    // ---------- shortcut assistant (navigates; never pretends to be AI) ----------

    get assistantIsOpen() {
        try {
            return this.assistantOpen();
        } catch {
            return false;
        }
    }

    get assistantMessages() {
        try {
            const list = this.messages();
            return Array.isArray(list) ? list : [];
        } catch {
            return [];
        }
    }

    toggleAssistant() {
        try {
            const next = !this.assistantOpen();
            this.assistantOpen.set(next);
            if (next) {
                this.markOnboard("assistant");
                this.ensureAssistantGreeted();
                setTimeout(() => {
                    if (!this._alive) {
                        return;
                    }
                    try {
                        const box = this.rootRef().querySelector(".nova_assistant_messages");
                        if (box) {
                            box.scrollTop = box.scrollHeight;
                        }
                        const input = this.assistantInputRef();
                        if (input) {
                            input.focus();
                        }
                    } catch {
                        // panel simply appears unfocused
                    }
                }, 60);
            }
        } catch {
            // panel stays shut
        }
    }

    pushMessage(role, text, buttons) {
        try {
            messageSeq += 1;
            const current = this.messages();
            this.messages.set(
                (Array.isArray(current) ? current : []).concat([
                    { id: `m${Date.now()}_${messageSeq}`, role, text, buttons: buttons || [] },
                ])
            );
            setTimeout(() => {
                if (!this._alive) {
                    return;
                }
                try {
                    const box = this.rootRef().querySelector(".nova_assistant_messages");
                    if (box) {
                        box.scrollTop = box.scrollHeight;
                    }
                } catch {
                    // no autoscroll
                }
            }, 60);
        } catch {
            // chat stays quiet
        }
    }

    ensureAssistantGreeted() {
        try {
            if ((this.messages() || []).length) {
                return;
            }
        } catch {
            return;
        }
        const chips = this.suggestedChips;
        this.pushMessage(
            "assistant",
            `Hi ${this.firstName}! I'm Nova, your shortcut guide. Tell me where to go — for example "open invoices" — and I'll take you there.`,
            chips
        );
    }

    sendAssistant() {
        let text = "";
        try {
            const input = this.assistantInputRef();
            text = input ? input.value : "";
            if (input) {
                input.value = "";
            }
        } catch {
            return;
        }
        text = (text || "").trim();
        if (!text) {
            return;
        }
        this.pushMessage("user", text);
        this.answerAssistant(text);
    }

    onAssistantKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendAssistant();
        } else if (ev.key === "Escape") {
            ev.stopPropagation();
            this.toggleAssistant();
            try {
                const el = this.searchRef();
                if (el) {
                    el.focus();
                }
            } catch {
                // focus stays put
            }
        }
    }

    openAssistantButton(button) {
        try {
            if (button && button.create && button.create.model) {
                this.doCreate(button.create.model, button.create.label || "record");
                return;
            }
            if (button && button.menuId && this.openMenuById(button.menuId)) {
                return;
            }
            if (button && button.label) {
                this.answerAssistant(button.label);
            }
        } catch {
            // stay in chat
        }
    }

    answerAssistant(text) {
        try {
            const result = this._answerAssistant(text);
            if (result && typeof result.catch === "function") {
                result.catch(() => {});
            }
        } catch {
            // chat stays quiet
        }
    }

    // Synonyms for creatable records: model + human label for the new record.
    // Deliberately narrow (no orders/invoices: required relations + side effects).
    createSynonyms() {
        return [
            { keys: ["task", "todo"], model: "project.task", label: "Task" },
            { keys: ["lead", "opportunity"], model: "crm.lead", label: "Lead" },
            { keys: ["contact", "customer", "partner"], model: "res.partner", label: "Contact" },
        ];
    }

    matchCreate(text) {
        const lower = (text || "").trim().toLowerCase();
        if (!lower) {
            return null;
        }
        try {
            for (const syn of this.createSynonyms()) {
                if (syn.keys.some((k) => lower.includes(k))) {
                    return syn;
                }
            }
        } catch {
            // no match
        }
        return null;
    }

    async doCreate(model, label) {
        let rec = null;
        try {
            const vals = { name: `New ${label} — Nova` };
            const id = await this.orm.create(model, [vals]);
            const rid = Array.isArray(id) ? id[0] : id;
            if (rid) {
                rec = { model, res_id: rid };
            }
        } catch {
            rec = null;
        }
        if (!rec) {
            return false;
        }
        try {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: rec.model,
                res_id: rec.res_id,
                views: [[false, "form"]],
                target: "current",
            });
        } catch {
            return false;
        }
        try {
            this.notification.add(_t("Created %s — fill it in.", label), { type: "success" });
        } catch {
            // toast optional
        }
        this.pushMessage("assistant", `Created ${label} for you.`, []);
        return true;
    }

    async _answerAssistant(text) {
        const raw = (text || "").trim();
        const lower = raw.toLowerCase();
        if (!lower) {
            return;
        }
        if (/^(hi|hello|hey|yo|good\s(morning|afternoon|evening|night))\b/.test(lower)) {
            this.pushMessage(
                "assistant",
                `Good to see you, ${this.firstName}! Where to?`,
                this.suggestedChips
            );
            return;
        }
        if (/\b(thank|thanks|thx)\b/.test(lower)) {
            this.pushMessage("assistant", "Anytime! Anything else I can open for you?", this.suggestedChips);
            return;
        }
        let m = lower.match(/^(open|launch|go to|show|take me to)\s+(.+)$/);
        if (m) {
            const hit = this.findBest(m[2]);
            if (hit) {
                const label = hit.kind === "app" ? hit.app.name : hit.entry.menu.name;
                this.pushMessage("assistant", `Opening ${label}…`, []);
                if (hit.kind === "app") {
                    this.openApp(hit.app);
                } else {
                    this.openMenu(hit.entry);
                }
            } else {
                this.pushMessage("assistant", `I couldn't find “${m[2]}”. Try one of these:`, this.topChips(3));
            }
            return;
        }
        m = lower.match(/^(create|new|add|make|log)\s+(.+)$/);
        if (m) {
            const syn = this.matchCreate(m[2]);
            if (syn) {
                this.pushMessage("assistant", `Create a new ${syn.label}?`, [
                    { label: `Create ${syn.label}`, create: { model: syn.model, label: syn.label } },
                ]);
                return;
            }
            const hit = this.findBest(m[2]);
            if (hit) {
                const app = hit.kind === "app" ? hit.app : hit.entry.app;
                const label = (app && app.name) || m[2];
                this.pushMessage("assistant", `Opening ${label} — use New there to fill required fields.`, []);
                if (app) {
                    this.openApp(app);
                }
            } else {
                this.pushMessage("assistant", `I couldn't find “${m[2]}”. Try one of these:`, this.topChips(3));
            }
            return;
        }
        const hit = this.findBest(lower);
        if (hit) {
            const label = hit.kind === "app" ? hit.app.name : hit.entry.menu.name;
            const menuId =
                hit.kind === "app" ? hit.app.id : hit.entry.menu && hit.entry.menu.id;
            this.pushMessage("assistant", `Did you mean ${label}?`, [
                { label: `Open ${label}`, menuId },
            ]);
            return;
        }
        this.pushMessage(
            "assistant",
            "I only know the apps and menus you can access. Try “open …” with a name, or pick one:",
            this.topChips(3)
        );
    }

    topChips(count) {
        try {
            return this.allApps.slice(0, count || 3).map((app) => ({
                label: app.name,
                menuId: app.id,
            }));
        } catch {
            return [];
        }
    }
}

registry.category("actions").add("nova_home", NovaHome);
