/** @odoo-module **/
//
// Nova AI drawer - the record-aware chat surface. A systray spark opens
// a 420px right drawer: status strip (agent, model, tier), pinned-record
// chip, transcript with steps, confirm cards for queued writes, and a
// composer with tier-aware suggestions. Everything goes through the
// /nova/ai/* JSON contract; the drawer itself never touches the LLM.
//

import { Component, markup, signal, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { matchWakePhrase } from "@nova_ai/voice/nova_wake_phrase";
import { looksLikeWrite } from "@nova_ai/voice/nova_write_intent";
import { NovaVoiceRecorder } from "@nova_ai/voice/nova_voice_recorder";
import { splitForSpeech, spokenSummaryFromHtml, playWavBase64 } from "@nova_ai/voice/nova_voice_audio";

const SUGGESTIONS = {
    t1: ["Summarize this record", "Suggest next steps", "Draft a reply"],
    t2: ["Summarize this record", "Find open invoices", "What needs attention?"],
    t3: ["Summarize this record", "What needs attention?", "Draft the follow-ups"],
};

function escapeHtml(text) {
    return String(text == null ? "" : text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

// The transcript is stored in Html fields, so Odoo wraps plain text in
// <p> and escapes quotes as entities on save. Strip + decode back to
// plain text BEFORE re-rendering, or the drawer shows literal <p>
// tags and &gt; sequences (seen live on session 13).
function plainFromHtml(html) {
    const stripped = String(html || "").replace(/<[^>]+>/g, "");
    return stripped
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#x27;|&#39;/g, "'")
        .replace(/&amp;/g, "&")
        .trim();
}

// Status buckets mirror the server PDF pills (nova_ai_report.py):
// status-looking cells tint, plain numbers right-align.
const NOVA_ST_GREEN = ["PAID", "POSTED", "DONE", "COMPLETED", "SUCCESS", "VERIFIED", "CONFIRMED", "ACTIVE", "IN STOCK", "AVAILABLE", "OK"];
const NOVA_ST_AMBER = ["PENDING", "DRAFT", "PARTIAL", "WARNING", "SYNCING", "IN PROGRESS", "LOW", "REORDER"];
const NOVA_ST_RED = ["OVERDUE", "UNPAID", "NOT PAID", "FAILED", "ERROR", "OFFLINE", "CANCELLED", "ALERT", "OUT OF STOCK", "ZERO"];
const novaCellClass = (text) => {
    const upper = String(text || "").toUpperCase();
    const has = (words) => words.some((w) => upper.includes(w));
    if (has(NOVA_ST_RED)) {
        return ' class="nova_ai_st_red"';
    }
    if (has(NOVA_ST_GREEN)) {
        return ' class="nova_ai_st_green"';
    }
    if (has(NOVA_ST_AMBER)) {
        return ' class="nova_ai_st_amber"';
    }
    if (/^[$€£₹]?\s*[\d,]+(\.\d+)?\s*%?$/.test(String(text || "").trim())) {
        return ' class="nova_ai_num"';
    }
    return "";
};

// Minimal safe markdown: code fences + inline code, bold/italic,
// headers, - lists, tables, links, paragraphs. Everything is escaped
// first, so model HTML can never execute here. Exported for the
// chatter inline-result dialog (same renderer, no fork).
export function renderMarkdown(text) {
    const source = escapeHtml(text || "");
    const blocks = [];
    let rest = source.replace(/```([\s\S]*?)```/g, (match, code) => {
        blocks.push(`<pre class="nova_ai_code"><code>${code.replace(/^\n+|\n+$/g, "")}</code></pre>`);
        return `\u0000${blocks.length - 1}\u0000`;
    });
    const inline = (line) =>
        line
            .replace(/`([^`]+)`/g, "<code>$1</code>")
            .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
            .replace(/(^|\W)\*([^*\n]+)\*/g, "$1<em>$2</em>")
            .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    const lines = rest.split("\n");
    let html = "";
    let inList = false;
    let inTable = false;
    const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
    const closeTable = () => { if (inTable) { html += "</tbody></table></div>"; inTable = false; } };
    for (const raw of lines) {
        const line = raw.trimEnd();
        const hole = line.match(/^\u0000(\d+)\u0000$/);
        if (hole) { closeList(); closeTable(); html += blocks[Number(hole[1])]; continue; }
        if (/^\s*$/.test(line)) { closeList(); closeTable(); continue; }
        const quote = line.match(/^&gt;\s?(.*)$/) || line.match(/^>\s?(.*)$/);
        if (quote) { closeList(); closeTable(); html += `<blockquote>${inline(quote[1])}</blockquote>`; continue; }
        const head = line.match(/^(#{1,3})\s+(.*)$/);
        if (head) { closeList(); closeTable(); html += `<p class="nova_ai_h${head[1].length}">${inline(head[2])}</p>`; continue; }
        if (/^\s*-\s+/.test(line)) {
            closeTable();
            if (!inList) { html += "<ul>"; inList = true; }
            html += `<li>${inline(line.replace(/^\s*-\s+/, ""))}</li>`;
            continue;
        }
        const cells = line.split("|").map((c) => c.trim());
        // A separator row (|---|---|) only divides: never render it.
        // Tolerant of whitespace/padding around the pipes.
        if (/^\|?[\s:|-]+\|?$/.test(line) && line.replace(/[\s:|>-]/g, "") === "") { continue; }
        // Standalone --- / *** / ___ is a rule, not text.
        if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) { closeList(); closeTable(); html += "<hr/>"; continue; }
        if (line.includes("|")) {
            closeList();
            let body = cells.slice(line.startsWith("|") ? 1 : 0, line.endsWith("|") ? -1 : undefined);
            body = body.filter((c) => c !== "");
            if (!body.length) { continue; }
            // First row opens the table as the header: model tables
            // lead with headers, and a header row reads as columns
            // where body-only rows read as stacked text.
            if (!inTable) {
                html += '<div class="nova_ai_twrap"><table class="nova_ai_table"><thead><tr>' + body.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
                inTable = true;
            } else {
                html += "<tr>" + body.map((c) => `<td${novaCellClass(c)}>${inline(c)}</td>`).join("") + "</tr>";
            }
            continue;
        }
        closeList(); closeTable();
        html += `<p>${inline(line)}</p>`;
    }
    closeList(); closeTable();
    return html || "<p></p>";
}

export class NovaAiDrawer extends Component {
    static template = "nova_ai.NovaAiDrawer";
    static props = {};

    setup() {
        this.action = useService("action");
        this.orm = useService("orm");
        // The drawer renders inside the navbar's systray subtree, whose
        // ancestors cap widths and clip overflow. Portal the panel to
        // <body> on every open so it always gets its full 420px.
        onMounted(() => this.portalDrawer());
        this._open = signal(false);
        this._agents = signal([]);
        this._agentId = signal(0);
        this._sessionId = signal(0);
        this._messages = signal([]);
        this._input = signal("");
        this._sending = signal(false);
        this._error = signal("");
        this._stage = signal("");
        this._stageTimer = null;
        // Live elapsed seconds while a turn runs: the Thinking line
        // ticks, so a long turn reads as working, not stuck.
        this._elapsed = signal(0);
        this._elapsedTimer = null;
        this._aborted = signal(false);
        this._showMore = signal(false);
        this._unreadFrom = signal(-1);
        this._notice = signal("");
        this._histFilter = signal("");
        this._renaming = signal(0);
        this._renameText = signal("");
        this._tone = signal("");
        this._rated = signal({});
        this._mentionOpen = signal(false);
        this._mentionItems = signal([]);
        this._showSnapshots = signal(false);
        this._snapshots = signal([]);
        this._snapName = signal("");
        this._context = signal(null);
        this._version = signal(0);
        this._prompts = signal([]);
        this._sessions = signal([]);
        this._showHistory = signal(false);
        this._copied = signal(-1);
        // Read-aloud for typed turns (browser speech; voice mode uses
        // server TTS and is independent of this toggle).
        this._readAloud = signal(false);
        try {
            this._canSpeak = signal(!!(window.speechSynthesis && window.SpeechSynthesisUtterance));
        } catch {
            this._canSpeak = signal(false);
        }
        // Hands-free voice mode, second generation. Behaviours ported
        // from the zeetavix_ai_local experiment (persistent mic, adaptive
        // gate, write-intent parking, fuzzy wake phrase, streaming TTS);
        // isolation via the NovaML sidecar and confirm cards are Nova's.
        this._voiceMode = signal(false);
        this._vState = signal("idle");
        this._vError = signal("");
        this._wake = signal(false);
        this._wakePhrase = signal("hey nova");
        this._vGen = 0;
        this._vRecorder = null;
        this._voiceCap = null;
        this._level = 0;
        this._levels = [];
        this._audio = null;
        this._lastMs = signal(0);
        this._onExternal = (ev) => this.openWith(ev && ev.detail);
        try {
            window.addEventListener("nova:ai-open", this._onExternal);
        } catch {
            // drawer still opens from the systray button
        }
        try {
            if (window.matchMedia) {
                const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
                this._reduced = signal(!!(mq && mq.matches));
            } else {
                this._reduced = signal(false);
            }
        } catch {
            this._reduced = signal(false);
        }
    }

    // Snapshot for the template (one read per render, same as the bell).
    get state() {
        const read = (sig, fallback) => {
            try {
                const value = sig();
                return value === undefined ? fallback : value;
            } catch {
                return fallback;
            }
        };
        return {
            open: read(this._open, false),
            agents: read(this._agents, []),
            agentId: read(this._agentId, 0),
            messages: read(this._messages, []),
            input: read(this._input, ""),
            history: read(this._sessions, []),
            showHistory: read(this._showHistory, false),
            showMore: read(this._showMore, false),
            unreadFrom: read(this._unreadFrom, -1),
            histFilter: read(this._histFilter, ""),
            renaming: read(this._renaming, 0),
            renameText: read(this._renameText, ""),
            tone: read(this._tone, ""),
            rated: read(this._rated, {}),
            mentionOpen: read(this._mentionOpen, false),
            mentionItems: read(this._mentionItems, []),
            showSnapshots: read(this._showSnapshots, false),
            snapshots: read(this._snapshots, []),
            snapName: read(this._snapName, ""),
            sessionId: read(this._sessionId, 0),
            copied: read(this._copied, -1),
            canRegenerate: this.canRegenerate,
            canSpeak: read(this._canSpeak, false),
            voiceMode: read(this._voiceMode, false),
            vState: read(this._vState, "idle"),
            vError: read(this._vError, ""),
            wake: read(this._wake, false),
            wakePhrase: read(this._wakePhrase, "hey nova"),
            openCardCount: this.openCards().length,
            readAloud: read(this._readAloud, false),
            sending: read(this._sending, false),
            error: read(this._error, ""),
            notice: read(this._notice, ""),
            stage: read(this._stage, ""),
            context: read(this._context, null),
        };
    }

    get agent() {
        try {
            const list = this._agents();
            return list.find((a) => a.id === this._agentId()) || list[0] || null;
        } catch {
            return null;
        }
    }

    get suggestions() {
        let library = [];
        try { library = this._prompts() || []; } catch (e) { library = []; }
        const rows = library.filter((pr) => pr && (pr.place === "drawer" || pr.place === "both") && pr.body);
        if (rows.length) {
            // Short names on the chips; the full prompt body is the
            // payload (and the hover title). Bodies-as-chips was the
            // mess: truncated sentences nobody can scan. Nameless rows
            // fall back to a clipped body, never the whole sentence.
            return rows.slice(0, 6).map((pr) => {
                const body = pr.body || "";
                let name = (pr.name || "").trim();
                if (!name) {
                    name = body.length > 32 ? body.slice(0, 32) + "…" : body;
                }
                return { name: name, body: body };
            });
        }
        const tier = (this.agent && this.agent.tier) || "t1";
        return (SUGGESTIONS[tier] || SUGGESTIONS.t1).map((text) => ({
            name: text,
            body: text,
        }));
    }

    get tierLabel() {
        return { t1: "T1 Basic", t2: "T2 Assistant", t3: "T3 Agent" }[
            (this.agent && this.agent.tier) || "t1"];
    }

    rememberOpener() {
        try {
            const el = document.activeElement;
            this._opener = (el && el instanceof HTMLElement) ? el : null;
        } catch {
            this._opener = null;
        }
    }

    focusComposer() {
        try {
            requestAnimationFrame(() => {
                try {
                    const field = document.querySelector("body > .nova_ai_drawer .nova_ai_input, .nova_ai_drawer .nova_ai_input");
                    if (field) {
                        field.focus();
                    }
                } catch {
                    // focus stays put
                }
            });
        } catch {
            // focus stays put
        }
    }

    async toggle() {
        let open = false;
        try {
            open = !this._open();
            this._open.set(open);
        } catch {
            return;
        }
        if (open) {
            this.rememberOpener();
            this.portalDrawer();
            await this.boot();
            this.portalDrawer();
            this.focusComposer();
        }
    }

    portalDrawer() {
        try {
            requestAnimationFrame(() => {
                try {
                    const node = document.querySelector(".nova_ai .nova_ai_drawer, body > .nova_ai_drawer, .nova_ai_drawer");
                    if (node && node.parentElement !== document.body) {
                        document.body.appendChild(node);
                    }
                } catch {
                    // drawer keeps its place
                }
            });
        } catch {
            // drawer keeps its place
        }
    }

    close() {
        this.rememberSeen();
        try {
            this._open.set(false);
        } catch {
            // stays put
        }
        try {
            if (this._voiceMode && this._voiceMode()) {
                this._voiceMode.set(false);
            }
        } catch {
            // mode stays put
        }
        this.stopVoiceMode();
        // Return focus to whatever opened the drawer.
        try {
            const opener = this._opener;
            if (opener && opener.isConnected) {
                requestAnimationFrame(() => {
                    try {
                        opener.focus();
                    } catch {
                        // focus stays put
                    }
                });
            }
        } catch {
            // focus stays put
        }
    }

    toggleSteps(messageIndex) {
        // Steps collapse to a one-line summary (stepsSummary); the
        // full list opens on tap. The turn reads as one line until
        // the user asks for the detail.
        let list = null;
        try {
            list = this._messages();
        } catch {
            return;
        }
        if (!Array.isArray(list) || !list[messageIndex]) {
            return;
        }
        try {
            const next = list.slice();
            next[messageIndex] = {
                ...next[messageIndex],
                unfolded: !next[messageIndex].unfolded,
            };
            this._messages.set(next);
        } catch {
            // steps stay put
        }
    }

    stepsSummary(messageIndex) {
        // One-line rollup for a finished turn: "3 checks · 1 queued".
        // The full list stays one tap away via toggleSteps. Compact
        // by default so the transcript reads as answers, not logs.
        let steps = [];
        try {
            steps = (((this._messages() || [])[messageIndex] || {}).steps) || [];
        } catch {
            return "";
        }
        if (!steps.length) {
            return "";
        }
        let checks = 0;
        let queued = 0;
        let opened = 0;
        let asked = 0;
        let failed = 0;
        let ms = 0;
        for (const step of steps) {
            if (!step) {
                continue;
            }
            if (step.queued) {
                queued += 1;
            } else if (step.action) {
                opened += 1;
            } else if (step.asked) {
                asked += 1;
            } else if (step.error) {
                failed += 1;
            } else if (!step.note) {
                checks += 1;
            }
            try {
                ms += Number(step.ms) || 0;
            } catch {
                // total stays put
            }
        }
        const parts = [];
        if (checks) {
            parts.push(checks + (checks === 1 ? " check" : " checks"));
        }
        if (asked) {
            parts.push(asked + (asked === 1 ? " question" : " questions"));
        }
        if (queued) {
            parts.push(queued + (queued === 1 ? " queued" : " queued"));
        }
        if (opened) {
            parts.push(opened + (opened === 1 ? " opened" : " opened"));
        }
        if (failed) {
            parts.push(failed + (failed === 1 ? " failed" : " failed"));
        }
        if (!parts.length) {
            parts.push(steps.length + (steps.length === 1 ? " step" : " steps"));
        }
        let clock = "";
        try {
            clock = ms >= 1000 ? (Math.round(ms / 100) / 10) + "s" : ms + "ms";
        } catch {
            clock = "";
        }
        return parts.join(" · ") + (clock ? " · " + clock : "");
    }

    seenKey(sessionId) {
        try {
            return "nova_ai_seen_" + (Number(sessionId) || 0);
        } catch {
            return "";
        }
    }

    rememberSeen() {
        // Messages on screen count as read when the drawer closes or
        // the session changes; anything newer gets the divider back.
        try {
            const id = this._sessionId();
            const key = this.seenKey(id);
            const list = this._messages() || [];
            if (key && id && window.localStorage) {
                window.localStorage.setItem(key, String(list.length));
            }
        } catch {
            // seen stays put
        }
    }

    restoreUnread() {
        // Divider only on return visits with growth: first opens show
        // no divider (nothing is "new" yet), caught-up returns none.
        let from = -1;
        try {
            const id = this._sessionId();
            const key = this.seenKey(id);
            const list = this._messages() || [];
            if (key && id && window.localStorage) {
                const seen = Number(
                    window.localStorage.getItem(key) || 0) || 0;
                if (seen > 0 && seen < list.length) {
                    from = seen;
                }
            }
        } catch {
            from = -1;
        }
        try {
            this._unreadFrom.set(from);
        } catch {
            // divider stays put
        }
    }

    async openWith(detail) {
        try {
            this._open.set(true);
        } catch {
            return;
        }
        this.rememberOpener();
        this.portalDrawer();
        await this.boot();
        this.portalDrawer();
        this.focusComposer();
        const context = detail && detail.context;
        if (context && context.model && Number(context.res_id || 0)) {
            try {
                this._context.set({
                    model: context.model,
                    res_id: Number(context.res_id),
                    name: "",
                });
                this.nameContext(context.model, Number(context.res_id));
            } catch {
                // drawer pins from the current view instead
            }
        }
        const message = detail && detail.message;
        if (message) {
            try {
                this._input.set(String(message));
            } catch {
                // composer keeps its text
            }
        }
        const agentId = detail && Number(detail.agentId || 0);
        if (agentId) {
            try {
                const list = this._agents();
                if (Array.isArray(list) && list.some((a) => a.id === agentId)) {
                    this.pickAgent(agentId);
                }
            } catch {
                // agent stays put
            }
        }
        try {
            const field = document.querySelector(".nova_ai_input");
            if (field) {
                field.focus();
            }
        } catch {
            // focus stays put
        }
    }

    readContext() {
        // Pinned context: a record when the form has one, otherwise the
        // active list filter - "what needs attention here?" should work
        // on the view being looked at, not just a single row.
        try {
            const current = this.action && this.action.currentController;
            const props = (current && current.props) || {};
            const model = props.resModel || "";
            const resId = Number(props.resId || 0);
            if (model && resId) {
                this._context.set({ model: model, res_id: resId, name: "" });
                this.nameContext(model, resId);
                return;
            }
            if (model) {
                let domain = null;
                try {
                    const root = current.model && current.model.root;
                    let raw = null;
                    const candidates = [
                        root && root.config && root.config.domain,
                        current.model && current.model.config && current.model.config.domain,
                        root && root.domain,
                        props.domain,
                    ];
                    for (const cand of candidates) {
                        if (cand) {
                            raw = cand;
                            break;
                        }
                    }
                    if (raw && typeof raw.toList === "function") {
                        domain = raw.toList();
                    } else if (Array.isArray(raw)) {
                        domain = raw;
                    }
                } catch (e) {
                    domain = null;
                }
                if (Array.isArray(domain)) {
                    this._context.set({ model: model, res_id: 0, domain: domain, name: "" });
                    return;
                }
            }
        } catch (e) {
            // fall through
        }
        try {
            this._context.set(null);
        } catch (e) {
            // context stays put
        }
    }
    async nameContext(model, resId) {
        try {
            const rows = await this.orm.read(model, [resId], ['display_name']);
            const nm = rows && rows[0] && rows[0].display_name;
            if (nm) {
                let cur = null;
                try { cur = this._context(); } catch (e) { cur = null; }
                if (cur && cur.res_id === resId) {
                    this._context.set({ model: cur.model, res_id: resId, name: nm });
                }
            }
        } catch (e) {}
    }

    async boot() {
        this.readContext();
        try {
            const res = await rpc("/nova/ai/prompts", {});
            this._prompts.set(((res && res.prompts) || []));
        } catch (e) {
            try { this._prompts.set([]); } catch (ignored) {}
        }
        let agents = [];
        try {
            agents = ((await rpc("/nova/ai/agents", {})) || {}).agents || [];
        } catch {
            agents = [];
        }
        try {
            this._agents.set(agents);
            if (agents.length && !this._agentId()) {
                this._agentId.set(agents[0].id);
            }
        } catch {
            // drawer shows the empty state
        }
        // After the agent is known: its conversation history.
        this.loadSessions();
        // Resume the latest session for this agent, if any.
        let sessionId = 0;
        try {
            const agentId = this._agentId();
            const sessions = await this.ormSearch("nova.ai.session", [
                ["agent_id", "=", agentId],
            ]);
            sessionId = (sessions[0] && sessions[0].id) || 0;
        } catch {
            sessionId = 0;
        }
        if (sessionId) {
            await this.loadSession(sessionId);
        } else {
            try {
                this._messages.set([]);
            } catch {
                // transcript stays put
            }
        }
    }

    async ormSearch(model, domain) {
        try {
            return await this.orm.searchRead(model, domain, ["id"], { limit: 1, order: "id desc" });
        } catch {
            return [];
        }
    }

    async loadSession(sessionId) {
        try {
            const detail = await rpc("/nova/ai/session", { session_id: sessionId });
            const session = (detail && detail.session) || null;
            if (!session) {
                return;
            }
            try {
                this._sessionId.set(session.id);
                this._tone.set(session.tone || "");
                this._rated.set({});
                const pending = Array.isArray(session.pending)
                    ? session.pending.map((c) => ({ ...c, state: "open" }))
                    : [];
                this._messages.set(((detail && detail.messages) || []).map((m, i, all) => ({
                    role: m.role,
                    html: markup(m.role === "user"
                        ? escapeHtml(plainFromHtml(m.content)).replace(/\n/g, "<br/>")
                        : renderMarkdown(plainFromHtml(m.content))),
                    steps: this.foldSteps(m.steps),
                    // Pending confirmations re-attach to the last assistant
                    // turn so a resumed session never loses its cards.
                    cards: (i === all.length - 1) ? pending : [],
                    pending: [],
                })));
                this.restoreUnread();
            } catch {
                // transcript stays put
            }
        } catch {
            // keeps the current transcript
        }
    }

    setInput(ev) {
        try {
            this._input.set(ev.target.value);
        } catch {
            // composer keeps its text
        }
        // Autoresize, capped: one row at rest, 120px of scroll past.
        try {
            const box = ev && ev.target;
            if (box && box.tagName === "TEXTAREA") {
                box.style.height = "auto";
                box.style.height = Math.min(box.scrollHeight, 120) + "px";
            }
        } catch {
            // height stays put
        }
        this.updateMentions();
    }

    mentionToken() {
        // Trailing @token in the composer, if any.
        let text = "";
        try {
            text = this._input() || "";
        } catch {
            return null;
        }
        const match = /@([A-Za-z0-9_ ]{0,30})$/.exec(text);
        return match ? match[1] : null;
    }

    updateMentions() {
        // @-completion (T-284): @agent-name switches the agent,
        // @here pins the record on screen. Popup only, no server
        // change — picking applies the action and clears the token.
        let token = null;
        try {
            token = this.mentionToken();
        } catch {
            token = null;
        }
        if (token === null) {
            try {
                this._mentionOpen.set(false);
                this._mentionItems.set([]);
            } catch {}
            return;
        }
        const needle = token.trim().toLowerCase();
        let agents = [];
        try {
            agents = this._agents() || [];
        } catch {
            agents = [];
        }
        const items = [];
        for (const agent of agents) {
            const name = (agent && agent.name) || "";
            if (!needle || name.toLowerCase().includes(needle)) {
                items.push({ type: "agent", id: agent.id, label: "@" + name });
            }
            if (items.length >= 6) {
                break;
            }
        }
        if (!needle || "here".includes(needle)) {
            items.push({ type: "pin", id: 0, label: "@here — pin current record" });
        }
        try {
            this._mentionItems.set(items);
            this._mentionOpen.set(items.length > 0);
        } catch {
            // popup stays put
        }
    }

    pickMention(item) {
        if (!item) {
            return;
        }
        try {
            const text = this._input() || "";
            this._input.set(text.replace(/@[A-Za-z0-9_ ]{0,30}$/, ""));
        } catch {
            return;
        }
        try {
            this._mentionOpen.set(false);
            this._mentionItems.set([]);
        } catch {}
        if (item.type === "agent") {
            this.pickAgent(item.id);
        } else if (item.type === "pin") {
            this.pinCurrent();
        }
        try {
            const field = document.querySelector(".nova_ai_input");
            if (field) {
                field.focus();
            }
        } catch {
            // focus stays put
        }
    }

    onComposerKey(ev) {
        if (!ev) {
            return;
        }
        try {
            let open = false;
            try {
                open = !!this._mentionOpen();
            } catch {
                open = false;
            }
            if (open && ev.key === "Escape") {
                ev.preventDefault();
                this._mentionOpen.set(false);
                return;
            }
            if (open && ev.key === "Enter" && !ev.shiftKey) {
                const items = this._mentionItems() || [];
                if (items.length) {
                    ev.preventDefault();
                    this.pickMention(items[0]);
                    return;
                }
            }
            if (ev.key === "Enter" && !ev.shiftKey) {
                ev.preventDefault();
                this.send();
            } else if (ev.key === "Escape") {
                this.close();
            }
        } catch {
            // key stays put
        }
    }

    onDrawerKey(ev) {
        // Drawer-scoped keys: Ctrl/Cmd-K focuses the composer (global
        // Ctrl-K stays Odoo's), Tab wraps inside the open panel so
        // keyboard users never fall behind it.
        if (!ev) {
            return;
        }
        try {
            if ((ev.ctrlKey || ev.metaKey) &&
                    (ev.key === "k" || ev.key === "K")) {
                ev.preventDefault();
                const box = document.querySelector(
                    ".nova_ai_drawer .nova_ai_input");
                if (box) {
                    box.focus();
                }
                return;
            }
            if (ev.key !== "Tab") {
                return;
            }
            const root = document.querySelector(".nova_ai_drawer");
            if (!root) {
                return;
            }
            const focusables = Array.from(root.querySelectorAll(
                'button:not([disabled]), textarea, input, [tabindex="0"]'
            )).filter((el) => el.offsetParent !== null);
            if (!focusables.length) {
                return;
            }
            const first = focusables[0];
            const last = focusables[focusables.length - 1];
            if (ev.shiftKey && document.activeElement === first) {
                ev.preventDefault();
                last.focus();
            } else if (!ev.shiftKey && document.activeElement === last) {
                ev.preventDefault();
                first.focus();
            }
        } catch {
            // key stays put
        }
    }

    useSuggestion(text) {
        try {
            this._input.set(text);
            const field = document.querySelector(".nova_ai_input");
            if (field) {
                field.focus();
            }
        } catch {
            // composer keeps its text
        }
    }

    optLabel(opt) {
        // Ask options ride as strings or {label, description} objects.
        try {
            if (opt && typeof opt === "object") {
                return opt.label || "";
            }
            return opt || "";
        } catch {
            return "";
        }
    }

    optTitle(opt) {
        try {
            if (opt && typeof opt === "object") {
                return opt.description || opt.label || "";
            }
            return opt || "";
        } catch {
            return "";
        }
    }

    answerOption(opt) {
        // One tap answers a clarifying question: the choice becomes
        // the next message, and the turn resumes with full history.
        let text = "";
        try {
            text = this.optLabel(opt) || "";
        } catch {
            return;
        }
        try {
            this._input.set(text);
        } catch {
            return;
        }
        this.send();
    }

    setTone(ev) {
        // Reply register for this session: the server appends one
        // tone line to the system prompt. Empty means neutral.
        let tone = "";
        try {
            tone = (ev && ev.target && ev.target.value) || "";
        } catch {
            tone = "";
        }
        if (tone !== "" && tone !== "concise" && tone !== "friendly" && tone !== "formal") {
            tone = "";
        }
        try {
            this._tone.set(tone);
        } catch {
            // tone stays put
        }
    }

    async rateTurn(messageIndex, vote) {
        // Thumbs on one assistant turn: audit-grade feedback, one tap.
        let sessionId = 0;
        try {
            sessionId = this._sessionId() || 0;
        } catch {
            return;
        }
        if (!sessionId) {
            return;
        }
        try {
            const res = await rpc("/nova/ai/feedback", {
                session_id: sessionId,
                message_index: messageIndex,
                vote: vote,
            });
            if (res && res.ok) {
                const rated = { ...(this._rated() || {}) };
                rated[messageIndex] = vote;
                this._rated.set(rated);
            } else if (res && res.error) {
                this._error.set(res.error);
            }
        } catch {
            // rating stays put
        }
    }

    async undoLast() {
        // Undo the last applied set_field write, if any.
        let sessionId = 0;
        try {
            sessionId = this._sessionId() || 0;
        } catch {
            return;
        }
        if (!sessionId) {
            return;
        }
        try {
            this._showMore.set(false);
            const res = await rpc("/nova/ai/undo", { session_id: sessionId });
            if (res && res.ok) {
                this._notice.set("Undone — the field is back to its previous value.");
                await this.loadSession(sessionId);
            } else if (res && res.error) {
                this._error.set(res.error);
            }
        } catch {
            // undo stays put
        }
    }

    async saveAsView() {
        // Flag this session as a saved view: a named pin reopened
        // from history instead of a past conversation.
        let sessionId = 0;
        try {
            sessionId = this._sessionId() || 0;
        } catch {
            return;
        }
        if (!sessionId) {
            return;
        }
        try {
            this._showMore.set(false);
            const res = await rpc("/nova/ai/view", { session_id: sessionId, is_view: true });
            if (res && res.ok) {
                this._notice.set("Saved as a view — reopen it from History.");
                this.loadSessions();
            } else if (res && res.error) {
                this._error.set(res.error);
            }
        } catch {
            // view stays put
        }
    }

    toggleSnapshots() {
        try {
            const open = !this._showSnapshots();
            this._showSnapshots.set(open);
            this._showMore.set(false);
            if (open) {
                this.loadSnapshots();
            }
        } catch {
            // panel stays put
        }
    }

    async loadSnapshots() {
        let sessionId = 0;
        try {
            sessionId = this._sessionId() || 0;
        } catch {
            return;
        }
        if (!sessionId) {
            return;
        }
        try {
            const res = await rpc("/nova/ai/snapshot/list", { session_id: sessionId });
            this._snapshots.set((res && res.snapshots) || []);
        } catch {
            try {
                this._snapshots.set([]);
            } catch (ignored) {}
        }
    }

    setSnapName(ev) {
        try {
            this._snapName.set((ev && ev.target && ev.target.value) || "");
        } catch {
            // name stays put
        }
    }

    async saveSnapshot() {
        let sessionId = 0;
        let name = "";
        try {
            sessionId = this._sessionId() || 0;
            name = (this._snapName() || "").trim();
        } catch {
            return;
        }
        if (!sessionId || !name) {
            return;
        }
        try {
            const res = await rpc("/nova/ai/snapshot/save", { session_id: sessionId, name: name });
            if (res && res.ok) {
                this._snapName.set("");
                this.loadSnapshots();
                this._notice.set("Snapshot saved.");
            } else if (res && res.error) {
                this._error.set(res.error);
            }
        } catch {
            // snapshot stays put
        }
    }

    async restoreSnapshot(snapId) {
        let sessionId = 0;
        try {
            sessionId = this._sessionId() || 0;
        } catch {
            return;
        }
        if (!sessionId || !snapId) {
            return;
        }
        try {
            const res = await rpc("/nova/ai/snapshot/restore", { session_id: sessionId, snapshot_id: snapId });
            if (res && res.ok) {
                this._notice.set("Snapshot restored.");
                await this.loadSession(sessionId);
                this.loadSnapshots();
            } else if (res && res.error) {
                this._error.set(res.error);
            }
        } catch {
            // snapshot stays put
        }
    }

    async deleteSnapshot(snapId) {
        let sessionId = 0;
        try {
            sessionId = this._sessionId() || 0;
        } catch {
            return;
        }
        if (!sessionId || !snapId) {
            return;
        }
        try {
            const res = await rpc("/nova/ai/snapshot/delete", { session_id: sessionId, snapshot_id: snapId });
            if (res && res.ok) {
                this.loadSnapshots();
            } else if (res && res.error) {
                this._error.set(res.error);
            }
        } catch {
            // snapshot stays put
        }
    }

    followups() {
        // Next questions shaped by the last answer: cards invite
        // review, sources invite depth, anything invites brevity or
        // detail. Chips fill the composer; sending stays the user's.
        let last = null;
        try {
            const list = this._messages() || [];
            last = list[list.length - 1] || null;
        } catch {
            last = null;
        }
        if (!last || last.role !== "assistant") {
            return [];
        }
        try {
            if ((last.cards || []).length) {
                return ["Explain these before I approve",
                    "What happens on approve?"];
            }
            if ((last.sources || []).length) {
                return ["Show me the key source lines",
                    "Make it shorter"];
            }
        } catch {
            // fall through
        }
        return ["Tell me more", "Make it shorter"];
    }

    editResend(messageIndex) {
        // Revise a question and ask again as a new turn: history keeps
        // both, so the rerun never rewrites what was asked.
        let list = null;
        try {
            list = this._messages();
        } catch {
            return;
        }
        const message = (list || [])[messageIndex];
        if (!message || message.role !== "user") {
            return;
        }
        try {
            this._input.set(this.messagePlain(message));
            const field = document.querySelector(".nova_ai_input");
            if (field) {
                field.focus();
            }
        } catch {
            // composer keeps its text
        }
    }

    pickAgent(agentId) {
        try {
            this._agentId.set(Number(agentId));
            this._sessionId.set(0);
            this._messages.set([]);
            this._error.set("");
        } catch {
            // agent stays put
        }
        // The open history panel belongs to the previous agent otherwise.
        try {
            if (this._showHistory()) {
                this.loadSessions();
            }
        } catch {
            // history stays put
        }
    }

    pinCurrent() {
        this.readContext();
        this.loadSessions();
    }

    unpin() {
        try {
            this._context.set(null);
        } catch {
            // context stays put
        }
    }

    async send() {
        let text = "";
        try {
            text = (this._input() || "").trim();
        } catch {
            return;
        }
        if (!text) {
            return;
        }
        let agentId = 0;
        let sessionId = 0;
        try {
            agentId = this._agentId();
            sessionId = this._sessionId();
        } catch {
            return;
        }
        if (!agentId) {
            return;
        }
        let context = null;
        try {
            context = this._context();
        } catch {
            context = null;
        }
        try {
            this._sending.set(true);
            this._error.set("");
            this._notice.set("");
            this._input.set("");
            try {
                const box = document.querySelector(
                    ".nova_ai_drawer .nova_ai_input");
                if (box) {
                    box.style.height = "auto";
                }
            } catch {
                // height stays put
            }
            this._stage.set("");
            this._aborted.set(false);
            this._lastSent = text;
            this.pollStage();
        } catch {
            // send proceeds
        }
        // A new question silences any reply still being read out.
        try {
            if (window.speechSynthesis) {
                window.speechSynthesis.cancel();
            }
        } catch {
            // speech stays put
        }
        const userMessage = { role: "user", html: markup(escapeHtml(text).replace(/\n/g, "<br/>")), steps: [], cards: [] };
        this.appendMessage(userMessage);
        let answer = null;
        const t0 = (typeof performance !== "undefined" && performance.now) ? performance.now() : 0;
        // The server stamps the turn with the user's clock: browser zone
        // first (the device they are looking at), else their Preferences
        // tz, else UTC. Never blocks the send.
        let browserTz = "";
        try {
            browserTz = (Intl.DateTimeFormat().resolvedOptions() || {}).timeZone || "";
        } catch {
            browserTz = "";
        }
        try {
            answer = await rpc("/nova/ai/chat", {
                agent_id: agentId,
                session_id: sessionId,
                message: text,
                context: Object.assign({}, context || {},
                    browserTz ? { browser_tz: browserTz } : {}),
                tone: this.state.tone || "",
            });
        } catch (err) {
            try {
                this._error.set(_t("The assistant could not be reached."));
                this._sending.set(false);
                this.stopStage();
            } catch {
                // error stays put
            }
            return;
        }
        try {
            this._sending.set(false);
            this.stopStage();
            if (t0 && typeof performance !== "undefined") {
                this._lastMs.set(Math.round((performance.now() - t0) / 100) / 10);
            }
        } catch {
            // spinner stays put
        }
        let aborted = false;
        try {
            aborted = this._aborted();
        } catch {
            aborted = false;
        }
        if (aborted) {
            // The turn was abandoned: do not append its late answer.
            // Refresh the session instead, so anything the server did
            // finish (queued cards included) still shows up.
            try {
                const lateId = (answer && answer.session_id) || sessionId;
                if (lateId) {
                    this._sessionId.set(lateId);
                    await this.loadSession(lateId);
                }
            } catch {
                // session stays put
            }
            return;
        }
        this.applyAnswer(answer, sessionId);
    }

    async retryLast() {
        // Inline retry on a failed turn: resend the last question.
        let sending = false;
        try {
            sending = this._sending();
        } catch {
            sending = false;
        }
        if (sending) {
            return;
        }
        let text = "";
        try {
            text = this._lastSent || "";
        } catch {
            text = "";
        }
        if (!text) {
            return;
        }
        try {
            this._input.set(text);
        } catch {
            return;
        }
        await this.send();
    }

    async abortTurn() {
        // Stop waiting and tell the server to abandon the turn at its
        // next boundary. Work already queued stays queued (and visible
        // after the refresh); nothing further runs.
        try {
            this._aborted.set(true);
        } catch {
            // abort proceeds
        }
        let sessionId = 0;
        try {
            sessionId = this._sessionId();
        } catch {
            sessionId = 0;
        }
        try {
            await rpc("/nova/ai/cancel", { session_id: sessionId });
        } catch {
            // the flag may still have landed; the loop re-reads it
        }
        try {
            this._sending.set(false);
            this.stopStage();
        } catch {
            // spinner stays put
        }
    }

    pollStage() {
        // While a turn runs, ask the server what stage it is in and
        // show it on the Thinking line. Separate cheap polls: the
        // turn's own response carries the answer, this only the line.
        // An elapsed ticker rides alongside so long turns tick.
        try {
            if (this._stageTimer) {
                clearInterval(this._stageTimer);
                this._stageTimer = null;
            }
            if (this._elapsedTimer) {
                clearInterval(this._elapsedTimer);
                this._elapsedTimer = null;
            }
        } catch {
            // timers stay put
        }
        try {
            this._elapsed.set(0);
        } catch {
            // elapsed stays put
        }
        let t0 = 0;
        try {
            t0 = Date.now();
        } catch {
            t0 = 0;
        }
        try {
            this._elapsedTimer = setInterval(() => {
                let alive = false;
                try {
                    alive = this._sending();
                } catch {
                    alive = false;
                }
                if (!alive || !t0) {
                    return;
                }
                try {
                    this._elapsed.set(Math.floor((Date.now() - t0) / 1000));
                } catch {
                    // elapsed stays put
                }
            }, 500);
        } catch {
            // ticker stays off
        }
        try {
            this._stageTimer = setInterval(async () => {
                let alive = false;
                try {
                    alive = this._sending();
                } catch {
                    alive = false;
                }
                if (!alive) {
                    this.stopStage();
                    return;
                }
                try {
                    const res = await rpc("/nova/ai/progress", {});
                    const stage = (res && res.stage) || "";
                    try {
                        this._stage.set(stage);
                    } catch {
                        // stage stays put
                    }
                } catch {
                    // next tick retries
                }
            }, 2000);
        } catch {
            // polling stays off; the spinner still shows
        }
    }

    stopStage() {
        try {
            if (this._stageTimer) {
                clearInterval(this._stageTimer);
                this._stageTimer = null;
            }
            if (this._elapsedTimer) {
                clearInterval(this._elapsedTimer);
                this._elapsedTimer = null;
            }
        } catch {
            // timers stay put
        }
        try {
            this._stage.set("");
            this._elapsed.set(0);
        } catch {
            // stage stays put
        }
    }

    // ---- Hands-free voice mode --------------------------------------
    // Talk -> server STT -> normal agentic turn -> server TTS -> reply.
    // Writes still need approval: saying "approve" taps the same confirm
    // cards as the buttons (same route, same audit). Barge in any time
    // by talking — playback stops and the mic takes over.

    // ---- Hands-free voice, second generation -------------------------
    // Behaviours ported from the zeetavix_ai_local experiment (persistent
    // mic, adaptive gate, write-intent parking, fuzzy wake phrase,
    // streaming TTS with spoken summaries, generation guards). Nova's own
    // improvements: sidecar isolation (no 1GB loads in the Odoo worker),
    // confirm cards for writes, server-capability gating.

    _vGenBump() {
        try {
            this._vGen = (this._vGen || 0) + 1;
            return this._vGen;
        } catch {
            return 0;
        }
    }

    voiceRecorder() {
        // One recorder for the drawer lifetime; the mic itself opens once
        // per voice-mode session inside it, so no turn loses its first
        // words to setup.
        if (!this._vRecorder) {
            try {
                this._vRecorder = new NovaVoiceRecorder({
                    onLevel: (rms) => {
                        this._level = rms || 0;
                    },
                });
            } catch {
                return null;
            }
        }
        return this._vRecorder;
    }

    setVState(state) {
        try {
            this._vState.set(state);
        } catch {
            // state stays put
        }
    }

    setVError(message) {
        try {
            this._vError.set(message || "");
        } catch {
            // error stays put
        }
    }

    voiceFail(gen, message) {
        try {
            if (gen !== this._vGen) {
                return;
            }
            this.setVError(message);
            this.setVState("idle");
        } catch {
            // state stays put
        }
    }

    toggleVoiceMode() {
        let on = false;
        try {
            on = !this._voiceMode();
            this._voiceMode.set(on);
            this.setVError("");
        } catch {
            return;
        }
        if (on) {
            this.enterVoiceMode();
        } else {
            this.stopVoiceMode();
        }
    }

    async enterVoiceMode() {
        const gen = this._vGenBump();
        // Server capabilities first — shallow only (no model loads).
        // Then the browser's own speech as the zero-config fallback:
        // voice works with nothing installed locally either way.
        let agentId = 0;
        try {
            agentId = this._agentId() || 0;
        } catch {
            // agent stays put
        }
        let cap = null;
        try {
            cap = await rpc("/nova/ai/voice/status", {
                agent_id: agentId,
            });
        } catch {
            cap = null;
        }
        if (gen !== this._vGen) {
            return;
        }
        let serverSTT = false;
        try {
            serverSTT = !!(cap && cap.providerAudio);
        } catch {
            serverSTT = false;
        }
        let hasSR = false;
        try {
            const SR = window.SpeechRecognition ||
                window.webkitSpeechRecognition;
            hasSR = !!SR;
            this._SR = SR || null;
        } catch {
            this._SR = null;
        }
        if (!serverSTT && !hasSR) {
            // Keep the bar open on the error: hiding it would make the
            // failure invisible (the button just "does nothing").
            this.voiceFail(gen,
                (cap && cap.reason) ||
                "Voice needs a microphone-capable browser or an " +
                "OpenAI-compatible audio endpoint.");
            return;
        }
        try {
            this._voiceCap = cap;
            this._serverSTT = serverSTT;
            this._wake.set(!!(cap && cap.wake));
            this._wakePhrase.set((cap && cap.wakePhrase) || "hey nova");
            this._canSpeak.set(!!(cap && cap.canSpeak));
        } catch {
            // caps stay put
        }
        // Open the mic now so the first turn loses nothing to setup.
        // Browser-SR turns need no worklet at all.
        if (serverSTT) {
            const recorder = this.voiceRecorder();
            if (!recorder) {
                this.voiceFail(
                    gen, "Voice is not available in this browser.");
                return;
            }
            try {
                await recorder.open();
            } catch (err) {
                this.voiceFail(
                    gen, NovaVoiceRecorder.describeMicError(err));
                return;
            }
            if (gen !== this._vGen) {
                // Mode died while the mic was opening: release the
                // fresh stream instead of leaving the indicator on
                // with no home.
                try {
                    recorder.release();
                } catch {
                    // mic stays put
                }
                return;
            }
        }
        this.setVState("idle");
        this.startMeter();
    }

    stopVoiceMode() {
        this._vGenBump();
        try {
            // Abort first so a pending record() settles instead of
            // leaking its turn; release() alone leaves it hanging.
            if (this._vRecorder) {
                try {
                    this._vRecorder.abort();
                } catch (ignored) {}
                this._vRecorder.release();
            }
        } catch {
            // mic stays put
        }
        this.interruptSpeech();
        this.setVState("idle");
        this.setVError("");
    }

    interruptSpeech() {
        try {
            if (this._audio && !this._audio.paused) {
                try {
                    this._audio.pause();
                } catch (ignored) {}
            }
        } catch {
            // playback stays put
        }
        this._audio = null;
        try {
            if (window.speechSynthesis) {
                window.speechSynthesis.cancel();
            }
        } catch {
            // speech stays put
        }
    }

    vStateLabel() {
        try {
            const phrase = this._wakePhrase() || "hey nova";
            const map = {
                idle: "Tap and talk.",
                waiting: "Waiting for \u201c" + phrase + "\u201d",
                checking: "Checking",
                listening: "Listening",
                transcribing: "Writing that down",
                thinking: "Working on it",
                speaking: "Speaking",
                confirm: "Check this before it runs",
                error: "Something went wrong",
            };
            return map[this._vState()] || "";
        } catch {
            return "";
        }
    }

    vHint() {
        try {
            const state = this._vState();
            if (state === "confirm") {
                return "Read it above, then press send.";
            }
            if (state === "waiting") {
                return "Say the phrase, then ask.";
            }
            if (state === "checking") {
                return "Checking the wake phrase.";
            }
            if (state === "listening") {
                return "Speak, then pause to send.";
            }
            if (state === "speaking") {
                return "Tap the circle to interrupt.";
            }
            return "";
        } catch {
            return "";
        }
    }

    async toggleTalk() {
        let state = "idle";
        try {
            state = this._vState();
        } catch {
            return;
        }
        if (state === "listening") {
            // Tap stops and discards (the gate, not the tap, decides
            // when a sentence is done).
            try {
                if (this._vRecorder) {
                    this._vRecorder.abort();
                }
            } catch {
                // recording stays put
            }
            return;
        }
        if (state === "thinking" || state === "transcribing" ||
            state === "checking") {
            return;
        }
        if (state === "speaking" || state === "confirm") {
            // Barge in: one generation for the whole takeover (the old
            // audio dies first, then the new turn owns the generation).
            this.interruptSpeech();
            this.voiceTurn(this._vGenBump(), true);
            return;
        }
        this.voiceTurn(this._vGenBump(), true);
    }

    async retryVoice() {
        // Try-again on a voice error: clear it, back to idle, listen
        // fresh. Same path as tapping talk from idle.
        try {
            this.setVError("");
        } catch {
            // error stays put
        }
        try {
            this.setVState("idle");
        } catch {
            // state stays put
        }
        await this.toggleTalk();
    }

    // Wake phrase: hands-free across turns. Honest cost — every
    // utterance is recorded and transcribed (~1s server CPU each) —
    // so this stays off unless switched on, one desk at a time.
    toggleWake() {
        let on = false;
        try {
            on = !this._wake();
            this._wake.set(on);
        } catch {
            return;
        }
        if (on) {
            let idle = true;
            try {
                idle = this._vState() === "idle";
            } catch {
                idle = false;
            }
            if (idle) {
                this.voiceTurn(this._vGenBump(), false);
            }
        } else {
            this._vGenBump();
            try {
                if (this._vRecorder) {
                    this._vRecorder.abort();
                }
            } catch {
                // recording stays put
            }
            this.setVState("idle");
        }
    }

    // One spoken turn: listen, transcribe, send down the exact same path
    // as a typed message (router, budgets, confirm cards all apply), then
    // speak the reply. A voice feature posting straight to the agent would
    // be a second, untested way into the database.
    async voiceTurn(gen, first = false) {
        let wakeOn = false;
        let phrase = "hey nova";
        try {
            wakeOn = !!this._wake();
            phrase = this._wakePhrase() || phrase;
        } catch {
            // wake stays put
        }
        // Clicking the circle is intent enough for the first turn; the
        // phrase gates the turns after that, which is where hands-free
        // matters. (A misheard phrase on turn one reads as broken.)
        if (wakeOn && !first) {
            const rest = await this.waitForWake(gen, phrase);
            if (gen !== this._vGen) {
                return;
            }
            if (rest === null) {
                return;
            }
            if (rest) {
                return this.voiceTurnHeard(gen, rest);
            }
            // Bare phrase: acknowledge without awaiting (words follow
            // while it plays) and listen immediately.
            this.speakNow("Yes?");
        }
        const heard = await this.hearOnce(gen, false);
        if (gen !== this._vGen) {
            return;
        }
        if (heard === null) {
            return;
        }
        return this.voiceTurnHeard(gen, heard);
    }

    // Listen once, by whichever path works: server transcription over
    // the worklet recording, or the browser's own speech recognition
    // when no hosted audio endpoint exists. Returns heard text, "" on
    // silence, or null on failure/mode-off.
    async hearOnce(gen, wakeWait) {
        let serverSTT = false;
        try {
            serverSTT = !!this._serverSTT;
        } catch {
            serverSTT = false;
        }
        if (!serverSTT) {
            return this.dictateViaSR(gen, wakeWait);
        }
        this.setVState(wakeWait ? "waiting" : "listening");
        this.setVError("");
        let wav = null;
        try {
            wav = await this.voiceRecorder().record(
                wakeWait ? { noSpeechMs: 30000 } : {});
        } catch (err) {
            // The mic broke (denied, unplugged, headless): fall back
            // to the browser's own recognition when it exists instead
            // of stranding the turn. No mic anywhere still fails loud.
            if (this.hasSR()) {
                return this.dictateViaSR(gen, wakeWait);
            }
            this.voiceFail(
                gen, NovaVoiceRecorder.describeMicError(err));
            return null;
        }
        if (gen !== this._vGen) {
            return null;
        }
        if (!wav) {
            return "";
        }
        // Under half a second cannot be an instruction; transcribing
        // coughs is pure server cost.
        if (wav.size < 16044) {
            return "";
        }
        this.setVState("transcribing");
        const heard = await this.transcribeWav(gen, wav);
        if (gen !== this._vGen) {
            return null;
        }
        if (heard === null) {
            return null;
        }
        return heard;
    }

    // Browser speech recognition: the zero-config path. No audio leaves
    // for our servers at all (Chrome uses its own service); Firefox
    // simply lacks the API and never reaches here.
    hasSR() {
        try {
            return !!(this._SR || window.SpeechRecognition ||
                window.webkitSpeechRecognition);
        } catch {
            return false;
        }
    }

    async dictateViaSR(gen, wakeWait) {
        let SR = null;
        try {
            SR = this._SR || window.SpeechRecognition ||
                window.webkitSpeechRecognition;
        } catch {
            SR = null;
        }
        if (!SR) {
            this.voiceFail(gen,
                "Voice needs a microphone-capable browser or an " +
                "OpenAI-compatible audio endpoint.");
            return null;
        }
        this.setVState(wakeWait ? "waiting" : "listening");
        this.setVError("");
        let recog = null;
        try {
            recog = new SR();
            recog.lang = (navigator && navigator.language) || "en-US";
            recog.interimResults = false;
            recog.maxAlternatives = 1;
        } catch {
            this.voiceFail(gen, "Voice is not available in this browser.");
            return null;
        }
        const text = await new Promise((resolve) => {
            let done = false;
            const finish = (value) => {
                if (!done) {
                    done = true;
                    resolve(value);
                }
            };
            const timer = setTimeout(() => {
                try {
                    recog.stop();
                } catch (ignored) {}
                finish(null);
            }, wakeWait ? 35000 : 20000);
            recog.onresult = (ev) => {
                try {
                    const results = (ev && ev.results) || [];
                    let final = "";
                    for (const res of results) {
                        if (res && res.isFinal && res[0] &&
                            res[0].transcript) {
                            final += res[0].transcript + " ";
                        }
                    }
                    final = final.trim();
                    if (final) {
                        clearTimeout(timer);
                        // Finish FIRST: stop() fires onend synchronously,
                        // and its finish(null) would otherwise win the
                        // race and discard the transcript.
                        finish(final);
                        try {
                            recog.stop();
                        } catch (ignored) {}
                    }
                } catch {
                    // transcript stays put
                }
            };
            recog.onerror = () => {
                clearTimeout(timer);
                finish(null);
            };
            recog.onend = () => {
                clearTimeout(timer);
                finish(null);
            };
            try {
                recog.start();
            } catch {
                clearTimeout(timer);
                finish(null);
            }
        });
        if (gen !== this._vGen) {
            return null;
        }
        if (text === null) {
            // Recognition failed mid-turn: say why, stay in mode.
            this.voiceFail(gen, wakeWait ? "" :
                "Didn't catch that — try again, closer to the mic.");
            if (wakeWait) {
                this.setVError("");
            }
            return wakeWait ? "" : null;
        }
        return text;
    }

    // The honest, expensive way to do a wake word: record, transcribe,
    // fuzzy-check. No tiny model to train; roughly a second of server CPU
    // per utterance on the server path, nothing at all on the browser
    // path. Fine at one desk — the toggle carries that warning and
    // stays off by default.
    async waitForWake(gen, phrase) {
        while (gen === this._vGen) {
            // hearOnce in waiting mode: long ceiling, silence returns ""
            // instead of ending the loop (waiting IS the point).
            const heard = await this.hearOnce(gen, true);
            if (gen !== this._vGen) {
                return null;
            }
            if (heard === null) {
                return null;
            }
            if (!heard) {
                continue;
            }
            this.setVState("checking");
            let match = null;
            try {
                match = matchWakePhrase(heard, phrase);
            } catch {
                match = null;
            }
            if (!match || !match.matched) {
                continue;
            }
            return match.rest;
        }
        return null;
    }

    // Server voice failed mid-turn: downgrade to browser speech for
    // the rest of the session when it exists, instead of leaving a
    // user with a capable browser stuck because an endpoint is down.
    voiceServerFailed(gen, message) {
        let hasSR = false;
        try {
            hasSR = !!(window.SpeechRecognition ||
                window.webkitSpeechRecognition);
        } catch {
            hasSR = false;
        }
        if (hasSR) {
            try {
                this._serverSTT = false;
            } catch {
                // flag stays put
            }
            this.voiceFail(gen,
                "Server voice failed — using browser speech now. " +
                "Tap to talk again.");
            return;
        }
        this.voiceFail(gen, message);
    }

    async transcribeWav(gen, wav) {
        let b64 = "";
        try {
            b64 = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => resolve(
                    String(reader.result).split(",")[1] || "");
                reader.onerror = reject;
                reader.readAsDataURL(wav);
            });
        } catch {
            this.voiceFail(gen, "That recording could not be read.");
            return null;
        }
        // Plain fetch, not rpc(): this route is type=http and answers
        // plain JSON, which the rpc helper rejects as a bad envelope.
        // Bounded by an AbortController: a wedged server must surface
        // as an error, never as a stuck "transcribing" state.
        let out = null;
        try {
            let agentId = 0;
            try {
                agentId = this._agentId() || 0;
            } catch {
                // agent stays put
            }
            const ctrl = new AbortController();
            const timer = setTimeout(() => {
                try {
                    ctrl.abort();
                } catch (ignored) {}
            }, 90000);
            let res = null;
            try {
                // Session CSRF token: the route is CSRF-checked, Odoo
                // reads it from params (not headers), and plain fetch
                // (unlike rpc()) sends nothing by itself.
                let token = "";
                try {
                    token = (window.odoo && window.odoo.csrf_token) || "";
                } catch {
                    token = "";
                }
                let url = "/nova/ai/voice/transcribe";
                if (token) {
                    url += "?csrf_token=" + encodeURIComponent(token);
                }
                res = await fetch(url, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ audio: b64,
                                           agent_id: agentId }),
                    credentials: "same-origin",
                    signal: ctrl.signal,
                });
            } finally {
                try {
                    clearTimeout(timer);
                } catch (ignored) {}
            }
            out = await res.json();
        } catch (err) {
            const timedOut = err && err.name === "AbortError";
            this.voiceServerFailed(gen, timedOut ?
                "Transcription is taking too long — try a shorter sentence." :
                "The server could not be reached for transcription.");
            return null;
        }
        if (gen !== this._vGen) {
            return null;
        }
        if (!out || out.error) {
            this.voiceServerFailed(gen,
                (out && out.error) || "Transcription failed.");
            return null;
        }
        return ((out && out.text) || "").trim();
    }

    // Room meter: the circle keeps reacting to the room while the mic
    // is open (levels stream from the worklet whether or not a turn is
    // collecting). Stops itself when the panel unmounts or mode ends.
    startMeter() {
        try {
            this._levels = [];
            const step = () => {
                try {
                    const canvas = document.querySelector(".nova_ai_wave");
                    if (!canvas) {
                        return;
                    }
                    let on = false;
                    try {
                        on = !!this._voiceMode();
                    } catch {
                        return;
                    }
                    if (!on) {
                        return;
                    }
                    const ctx2d = canvas.getContext("2d");
                    const w = canvas.width;
                    const h = canvas.height;
                    this._levels.push(this._level || 0);
                    while (this._levels.length > 24) {
                        this._levels.shift();
                    }
                    ctx2d.clearRect(0, 0, w, h);
                    ctx2d.fillStyle = "#0E7C8A";
                    const bars = 24;
                    for (let i = 0; i < bars; i++) {
                        // Squashed towards 1: speech sits low, and a linear
                        // map looks dead while someone is plainly talking.
                        const v = Math.min(
                            Math.sqrt(this._levels[i] || 0) * 1.6, 1);
                        const bh = Math.max(2, v * h);
                        const x = (i * w) / bars;
                        ctx2d.fillRect(
                            x + 1, (h - bh) / 2, w / bars - 2, bh);
                    }
                    requestAnimationFrame(step);
                } catch {
                    // meter stays put
                }
            };
            requestAnimationFrame(step);
        } catch {
            // meter stays put
        }
    }

    async voiceTurnHeard(gen, heard) {
        heard = (heard || "").trim();
        let wakeOn = false;
        try {
            wakeOn = !!this._wake();
        } catch {
            // wake stays put
        }
        if (!heard) {
            this.setVState("idle");
            this.speakNow("I did not catch that.");
            if (wakeOn && gen === this._vGen) {
                return this.voiceTurn(gen);
            }
            return;
        }
        // Spoken approvals first: they never reach the model.
        try {
            if (await this.trySpokenApproval(heard)) {
                this.setVState("idle");
                if (wakeOn && gen === this._vGen) {
                    return this.voiceTurn(gen);
                }
                return;
            }
        } catch {
            // approval stays put
        }
        // A misheard question gives a wrong answer; a misheard
        // instruction writes a wrong record. Anything shaped like an
        // instruction parks in the composer for a human read instead of
        // sending straight away.
        let park = false;
        try {
            park = looksLikeWrite(heard);
        } catch {
            park = false;
        }
        try {
            this._input.set(heard);
        } catch {
            // input stays put
        }
        if (park) {
            // Parked: the read-back speaks, the label returns to
            // confirm (streaming would flip it to speaking), and wake
            // mode keeps listening instead of dead-ending here.
            this.setVState("confirm");
            await this.speakNow("I heard: " + heard +
                ". Check it and press send.");
            if (gen !== this._vGen) {
                return;
            }
            this.setVState("confirm");
            if (wakeOn) {
                return this.voiceTurn(gen);
            }
            return;
        }
        try {
            if (this._sending()) {
                this.setVState("idle");
                return;
            }
        } catch {
            // send proceeds
        }
        this.setVState("thinking");
        await this.send();
        if (gen !== this._vGen) {
            return;
        }
        try {
            const list = this._messages() || [];
            const last = list[list.length - 1];
            if (last && last.role === "assistant") {
                let extra = "";
                const cards = [];
                for (const message of list) {
                    for (const card of message.cards || []) {
                        if (card && card.state === "open") {
                            cards.push(card);
                        }
                    }
                }
                if (cards.length === 1) {
                    extra = " One approval is waiting: " +
                        (cards[0].title || "") +
                        ". Say approve, or tap Approve.";
                } else if (cards.length > 1) {
                    extra = " " + cards.length + " approvals are waiting." +
                        " Say approve all, or tap each Approve.";
                }
                await this.speakStreaming(
                    gen, spokenSummaryFromHtml(last.html) + extra);
            }
        } catch {
            // reply stays silent
        }
        if (gen !== this._vGen) {
            return;
        }
        this.setVState("idle");
        if (wakeOn) {
            return this.voiceTurn(gen);
        }
    }

    async fetchAudioChunk(text) {
        // Bounded like transcription: a stuck reply must fail loudly,
        // not hang the turn in "speaking".
        try {
            let agentId = 0;
            try {
                agentId = this._agentId() || 0;
            } catch {
                // agent stays put
            }
            const ctrl = new AbortController();
            const timer = setTimeout(() => {
                try {
                    ctrl.abort();
                } catch (ignored) {}
            }, 60000);
            let answer = null;
            try {
                answer = await fetch("/nova/ai/voice/speak", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        id: 1,
                        jsonrpc: "2.0",
                        method: "call",
                        params: { text: text, agent_id: agentId },
                    }),
                    credentials: "same-origin",
                    signal: ctrl.signal,
                }).then((res) => res.json());
            } finally {
                try {
                    clearTimeout(timer);
                } catch (ignored) {}
            }
            const audio = answer && (answer.result || {}).audio;
            if (audio) {
                return audio;
            }
        } catch {
            // chunk stays put
        }
        return null;
    }

    // Speak a reply starting with the first sentence: synthesis and
    // playback overlap rather than alternate, so time-to-first-word is
    // one short chunk, not the whole answer.
    async speakStreaming(gen, text) {
        const plain = (text || "").trim();
        if (!plain) {
            return;
        }
        let chunks = [];
        try {
            chunks = splitForSpeech(plain);
        } catch {
            return;
        }
        if (!chunks.length) {
            return;
        }
        let pending = this.fetchAudioChunk(chunks[0]);
        for (let index = 0; index < chunks.length; index++) {
            const audio = await pending;
            if (gen !== this._vGen) {
                return;
            }
            pending = index + 1 < chunks.length ?
                this.fetchAudioChunk(chunks[index + 1]) : null;
            if (!audio) {
                // No server audio: the browser reads aloud instead (its
                // own voices, nothing to install). Speech stays optional;
                // losing it must not end the turn.
                await this.speakBrowser(chunks[index], gen);
                continue;
            }
            this.setVState("speaking");
            await playWavBase64(audio, {
                ref: (el) => {
                    this._audio = el;
                },
            });
            if (gen !== this._vGen) {
                return;
            }
        }
    }

    speakNow(text) {
        const plain = (text || "").trim();
        if (!plain) {
            return Promise.resolve();
        }
        return this.speakStreaming(this._vGen, plain);
    }

    // Browser speech synthesis: the zero-install voice. Used whenever
    // server speech is unavailable; a client with no voices stays
    // silent and the text remains on screen.
    async speakBrowser(text, gen) {
        const plain = (text || "").trim().slice(0, 600);
        if (!plain) {
            return;
        }
        try {
            const synth = window.speechSynthesis;
            const Utter = window.SpeechSynthesisUtterance;
            if (!synth || !Utter) {
                return;
            }
            if (gen !== this._vGen) {
                return;
            }
            this.setVState("speaking");
            await new Promise((resolve) => {
                let done = false;
                const finish = () => {
                    if (!done) {
                        done = true;
                        resolve();
                    }
                };
                const timer = setTimeout(finish, 30000);
                try {
                    const utter = new Utter(plain);
                    utter.lang = (navigator && navigator.language) ||
                        "en-US";
                    utter.onend = () => {
                        clearTimeout(timer);
                        finish();
                    };
                    utter.onerror = () => {
                        clearTimeout(timer);
                        finish();
                    };
                    synth.cancel();
                    synth.speak(utter);
                } catch {
                    clearTimeout(timer);
                    finish();
                }
            });
        } catch {
            // reply stays silent
        }
    }

    openCards() {
        const found = [];
        try {
            const list = this._messages() || [];
            for (let mi = 0; mi < list.length; mi++) {
                const cards = (list[mi] && list[mi].cards) || [];
                for (let ci = 0; ci < cards.length; ci++) {
                    if (cards[ci] && cards[ci].state === "open") {
                        found.push([mi, ci, cards[ci]]);
                    }
                }
            }
        } catch {
            // cards stay put
        }
        return found;
    }

    async approveAllOpen() {
        // Find-first loop, not a snapshot: confirming splices cards,
        // so pre-taken indices go stale after the first one. Bounded:
        // a card that refuses approval must not spin forever.
        for (let guard = this.openCards().length + 1; guard > 0; guard--) {
            const open = this.openCards();
            if (!open.length) {
                return;
            }
            try {
                await this.confirmCard(open[0][0], open[0][1]);
            } catch {
                // card stays put
            }
        }
    }

    async dismissAllOpen() {
        for (let guard = this.openCards().length + 1; guard > 0; guard--) {
            const open = this.openCards();
            if (!open.length) {
                return;
            }
            try {
                await this.dismissCard(open[0][0], open[0][1]);
            } catch {
                // card stays put
            }
        }
    }

    async trySpokenApproval(heard) {
        const said = (heard || "").trim().toLowerCase();
        const open = this.openCards();
        if (!open.length) {
            return false;
        }
        // Full-utterance match with word ends: prefixes like "yes" must
        // not fire on "yesterday", nor "no" on "november"/"note this".
        // Voice approvals demand an explicit verb: a stray "yes" in
        // conversation must never approve a money move.
        const approveRe = /^(approve|approved|confirm|confirmed|do it|approve all)[\s.!?]*$/;
        const dismissRe = /^(dismiss|dismissed|cancel|cancelled|no|reject|rejected|don't|do not|never mind)[\s.!?]*$/;
        if (approveRe.test(said)) {
            if (open.length > 1 && !/all\b/.test(said)) {
                // Several cards and no "all": never pick one by luck.
                await this.speakNow(
                    "There are " + open.length + " approvals waiting. " +
                    "Say approve all, or tap each one.");
                return true;
            }
            await this.approveAllOpen();
            await this.speakNow("Approved.");
            return true;
        }
        if (dismissRe.test(said)) {
            await this.dismissAllOpen();
            await this.speakNow("Dismissed.");
            return true;
        }
        return false;
    }

    toggleReadAloud() {
        let on = false;
        try {
            on = !this._readAloud();
            this._readAloud.set(on);
        } catch {
            return;
        }
        if (!on) {
            try {
                if (window.speechSynthesis) {
                    window.speechSynthesis.cancel();
                }
            } catch {
                // speech stays put
            }
        }
    }

    speak(text) {
        let on = false;
        try {
            on = !!this._readAloud();
        } catch {
            return;
        }
        if (!on) {
            return;
        }
        let plain = "";
        try {
            plain = plainFromHtml(text || "").slice(0, 600);
        } catch {
            return;
        }
        if (!plain) {
            return;
        }
        try {
            const synth = window.speechSynthesis;
            if (!synth || !window.SpeechSynthesisUtterance) {
                return;
            }
            synth.cancel();
            const utterance = new window.SpeechSynthesisUtterance(plain);
            utterance.lang = (navigator && navigator.language) || "en-US";
            synth.speak(utterance);
        } catch {
            // speech stays put
        }
    }

    applyAnswer(answer, sessionId) {
        if (!answer || answer.error) {
            try {
                this._error.set((answer && answer.error) || _t("The assistant could not answer."));
            } catch {
                // error stays put
            }
            return;
        }
        try {
            this._sessionId.set(answer.session_id || sessionId);
        } catch {
            // session stays put
        }
        this.appendMessage({
            role: "assistant",
            html: markup(renderMarkdown(answer.reply || "")),
            markdown: answer.reply || "",
            steps: answer.steps || [],
            cards: (answer.cards || []).map((c) => ({ ...c, state: "open" })),
            sources: answer.sources || [],
            applied: answer.applied || [],
        });
        this.speak(answer.reply || "");
        if (answer.action) {
            this.runAction(answer.action);
        }
    }

    get canRegenerate() {
        try {
            if (this._sending()) {
                return false;
            }
            const list = this._messages() || [];
            return list.length > 0 && list[list.length - 1].role === "assistant";
        } catch {
            return false;
        }
    }

    async regenerate() {
        if (!this.canRegenerate) {
            return;
        }
        let sessionId = 0;
        try {
            sessionId = this._sessionId();
        } catch {
            return;
        }
        if (!sessionId) {
            return;
        }
        // Drop the trailing reply optimistically; a failure reloads it.
        try {
            const list = this._messages() || [];
            this._messages.set(list.slice(0, -1));
            this._error.set("");
            this._sending.set(true);
        } catch {
            // regenerate proceeds
        }
        let answer = null;
        try {
            answer = await rpc("/nova/ai/regenerate", {
                session_id: sessionId,
            });
        } catch (err) {
            answer = null;
        }
        try {
            this._sending.set(false);
        } catch {
            // spinner stays put
        }
        if (!answer || answer.error) {
            // Put the transcript back the way the server has it.
            await this.loadSession(sessionId);
            if (answer && answer.error) {
                try {
                    this._error.set(answer.error);
                } catch {
                    // error stays put
                }
            }
            return;
        }
        let aborted = false;
        try {
            aborted = this._aborted();
        } catch {
            aborted = false;
        }
        if (aborted) {
            await this.loadSession(sessionId);
            return;
        }
        this.applyAnswer(answer, sessionId);
    }

    foldSteps(steps) {
        const list = Array.isArray(steps) ? steps : [];
        const folded = [];
        for (const step of list) {
            const key = JSON.stringify([(step && step.tool) || '', (step && step.note) || '', (step && step.error) || '', !!(step && step.queued), !!(step && step.action)]);
            const last = folded[folded.length - 1];
            if (last && last.key === key) {
                last.n += 1;
                last.step = { ...step, n: last.n };
            } else {
                folded.push({ key: key, n: 1, step: { ...(step || {}), n: 1 } });
            }
        }
        return folded.map((f) => f.step);
    }

    appendMessage(message) {
        try {
            // Collapse identical consecutive steps ("Queued x for approval"
            // twice reads as a bug, not progress).
            const steps = Array.isArray(message.steps) ? message.steps : [];
            const folded = [];
            for (const step of steps) {
                const key = JSON.stringify([step.tool || "", step.note || "", step.error || "", !!step.queued, !!step.action]);
                const last = folded[folded.length - 1];
                if (last && last.key === key) {
                    last.n += 1;
                    last.step = { ...step, n: last.n };
                } else {
                    folded.push({ key, n: 1, step: { ...step, n: 1 } });
                }
            }
            message = { ...message, steps: folded.map((f) => f.step) };
            const list = this._messages();
            this._messages.set([...(Array.isArray(list) ? list : []), message]);
        } catch {
            // transcript stays put
        }
        this.scrollDown();
    }

    scrollDown() {
        try {
            requestAnimationFrame(() => {
                const body = document.querySelector(".nova_ai_body");
                if (body) {
                    body.scrollTop = body.scrollHeight;
                }
            });
        } catch {
            // scroll stays put
        }
    }

    async retryConfirm(messageIndex, cardIndex) {
        // A failed approval retries from the card as shown: reopen it
        // and run the normal confirm path (edits preserved).
        let list = null;
        try {
            list = this._messages();
        } catch {
            return;
        }
        const card = (((list[messageIndex] || {}).cards) || [])[cardIndex];
        if (!card || card.state !== "failed") {
            return;
        }
        try {
            card.state = "open";
            card.error = "";
            this._messages.set([...list]);
        } catch {
            return;
        }
        await this.confirmCard(messageIndex, cardIndex);
    }

    sayNotice(text) {
        try {
            this._notice.set(text || "");
        } catch {
            // notice stays put
        }
    }

    async noteTurn(messageIndex) {
        // Export one assistant turn as a chatter note on the pinned
        // record. Server enforces pin + allow-list + ownership.
        let sessionId = 0;
        try {
            sessionId = this._sessionId();
        } catch {
            return;
        }
        if (!sessionId) {
            return;
        }
        let res = null;
        try {
            res = await rpc("/nova/ai/note", {
                session_id: sessionId, index: messageIndex,
            });
        } catch {
            res = { error: "Could not save the note." };
        }
        if (res && res.error) {
            try {
                this._error.set(res.error);
            } catch {
                // error stays put
            }
            return;
        }
        this.sayNotice("Saved to the record chatter.");
    }

    async branchTurn(messageIndex) {
        // Fork the session at this turn: what-ifs continue in a copy,
        // the original stays untouched.
        let sessionId = 0;
        try {
            sessionId = this._sessionId();
        } catch {
            return;
        }
        if (!sessionId) {
            return;
        }
        let res = null;
        try {
            res = await rpc("/nova/ai/fork", {
                session_id: sessionId, index: messageIndex,
            });
        } catch {
            res = null;
        }
        if (res && res.session_id) {
            await this.loadSession(res.session_id);
            this.sayNotice("Branched into a new conversation.");
        }
    }

    async emailAnswer() {
        this.hideMore();
        let sessionId = 0;
        try {
            sessionId = this._sessionId();
        } catch {
            return;
        }
        if (!sessionId) {
            return;
        }
        let res = null;
        try {
            res = await rpc("/nova/ai/email", { session_id: sessionId });
        } catch {
            res = { error: "Could not send the email." };
        }
        if (res && res.error) {
            try {
                this._error.set(res.error);
            } catch {
                // error stays put
            }
            return;
        }
        this.sayNotice("Emailed you the latest answer.");
    }

    async confirmCard(messageIndex, cardIndex) {
        let cards = [];
        try {
            const list = this._messages();
            cards = (((list[messageIndex] || {}).cards) || []);
        } catch {
            return;
        }
        const card = cards[cardIndex];
        if (!card || card.state !== "open") {
            return;
        }
        try {
            card.state = "working";
            this._messages.set([...this._messages()]);
        } catch {
            // card stays put
        }
        let sessionId = 0;
        try {
            sessionId = this._sessionId();
        } catch {
            return;
        }
        let result = null;
        try {
            // Send the user's edit (if any) with the approval, so the
            // note says what they wrote, not what the model wrote.
            const edited = {};
            if (card.edit !== undefined && card.edit !== null &&
                    String(card.edit).trim() && card.edit !== card.summary) {
                if (card.kind === "log_note") {
                    edited.body = String(card.edit);
                } else if (card.kind === "schedule_activity") {
                    edited.summary = String(card.edit);
                }
            }
            result = await rpc("/nova/ai/confirm", {
                session_id: sessionId,
                token: card.token,
                values: edited,
            });
        } catch {
            result = { error: _t("The confirmation could not be sent.") };
        }
        try {
            const list = this._messages();
            const live = (((list[messageIndex] || {}).cards) || [])[cardIndex];
            if (live) {
                live.state = result && result.ok ? "done" : "failed";
                live.result = result && result.record;
                live.error = result && result.error;
            }
            this._messages.set([...list]);
        } catch {
            // card stays put
        }
        if (result && result.ok && result.record) {
            if (result.record.action) {
                // The method answered with a follow-up action
                // (backorder choice, autoprint...): the write ran,
                // but work still waits in Odoo. Say so plainly.
                try {
                    this.sayNotice("Saved, with a follow-up step waiting "
                        + "in Odoo — open the record to finish it.");
                } catch {
                    // notice stays put
                }
            }
            this.appendMessage({
                role: "assistant",
                html: markup(escapeHtml(`Saved: ${result.record.display_name || ""}`)),
                steps: [],
                cards: [],
                openRecord: result.record,
            });
        }
    }

    editCard(messageIndex, cardIndex, value) {
        // The card's pending text is editable before approval: the user
        // owns the words that land on the record.
        try {
            const list = this._messages();
            const card = (((list[messageIndex] || {}).cards) || [])[cardIndex];
            if (card) {
                card.edit = value;
                this._messages.set([...list]);
            }
        } catch {
            // card text stays put
        }
    }

    async dismissCard(messageIndex, cardIndex) {
        let card = null;
        try {
            const list = this._messages();
            card = (((list[messageIndex] || {}).cards) || [])[cardIndex];
        } catch {
            return;
        }
        if (!card) {
            return;
        }
        let sessionId = 0;
        try {
            sessionId = this._sessionId();
        } catch {
            sessionId = 0;
        }
        try {
            if (sessionId) {
                await rpc("/nova/ai/dismiss", { session_id: sessionId, token: card.token });
            }
        } catch {
            // server keeps it; the UI drops it anyway
        }
        try {
            const list = this._messages();
            const cards = (((list[messageIndex] || {}).cards) || []).filter((_, i) => i !== cardIndex);
            list[messageIndex] = { ...list[messageIndex], cards };
            this._messages.set([...list]);
        } catch {
            // card stays put
        }
    }

    openSource(source) {
        // A citation is a link, not a label: clicking it opens the
        // record the answer came from.
        if (!source) {
            return;
        }
        if (source.model && source.res_id) {
            this.openRecord(source);
            return;
        }
        const uri = source.source || "";
        if (uri.indexOf("nova://knowledge/") === 0) {
            // A concept: open it in the knowledge list, filtered to
            // the one the answer used.
            const code = uri.slice("nova://knowledge/".length);
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "nova.ai.knowledge",
                views: [
                    [false, "kanban"],
                    [false, "list"],
                    [false, "form"],
                ],
                domain: [["code", "=", code]],
                target: "current",
            });
        }
    }

    openRecord(record) {
        if (!record || !record.model || !record.res_id) {
            return;
        }
        try {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: record.model,
                res_id: record.res_id,
                views: [[false, "form"]],
                target: "current",
            });
        } catch {
            // record stays put
        }
    }

    async loadSessions() {
        try {
            const agentId = this._agentId();
            const rows = await this.orm.searchRead(
                "nova.ai.session",
                [["agent_id", "=", agentId]],
                ["id", "title", "last_write", "pending", "is_view"],
                { limit: 5, order: "id desc" });
            this._sessions.set(rows || []);
        } catch (e) {
            try {
                this._sessions.set([]);
            } catch (ignored) {
                // list stays put
            }
        }
    }

    toggleHistory() {
        try {
            const open = !this._showHistory();
            this._showHistory.set(open);
            if (open) {
                this.loadSessions();
            }
        } catch (e) {
            // stays put
        }
    }

    setHistFilter(ev) {
        try {
            this._histFilter.set((ev.target.value || "").toLowerCase());
        } catch {
            // filter stays put
        }
    }

    filteredHistory() {
        let rows = [];
        try {
            rows = this._sessions() || [];
        } catch (e) {
            rows = [];
        }
        // Saved views live in their own section, not the chat list.
        rows = rows.filter((row) => !(row && row.is_view));
        let needle = "";
        try {
            needle = this._histFilter() || "";
        } catch (e) {
            needle = "";
        }
        if (!needle) {
            return rows;
        }
        return rows.filter((row) => {
            try {
                return ((row && row.title) || "").toLowerCase().includes(needle);
            } catch (e) {
                return true;
            }
        });
    }

    filteredViews() {
        let rows = [];
        try {
            rows = this._sessions() || [];
        } catch (e) {
            rows = [];
        }
        return rows.filter((row) => row && row.is_view);
    }

    historyCTA() {
        // First-chat call to action: close history, focus the composer.
        try {
            this._showHistory.set(false);
        } catch {
            // panel stays put
        }
        try {
            const box = document.querySelector(".nova_ai_drawer .nova_ai_input");
            if (box) {
                box.focus();
            }
        } catch {
            // focus stays put
        }
    }

    startRename(sessionId, title) {
        try {
            this._renaming.set(sessionId);
            this._renameText.set(title || "");
        } catch {
            // rename stays put
        }
    }

    cancelRename() {
        try {
            this._renaming.set(0);
            this._renameText.set("");
        } catch {
            // rename stays put
        }
    }

    setRenameText(ev) {
        try {
            this._renameText.set(ev.target.value || "");
        } catch {
            // text stays put
        }
    }

    onRenameKey(ev, sessionId) {
        if (!ev) {
            return;
        }
        try {
            if (ev.key === "Enter") {
                ev.preventDefault();
                this.saveRename(sessionId);
            } else if (ev.key === "Escape") {
                this.cancelRename();
            }
        } catch {
            // key stays put
        }
    }

    async saveRename(sessionId) {
        let text = "";
        try {
            text = (this._renameText() || "").trim().slice(0, 60);
        } catch {
            text = "";
        }
        if (!text) {
            this.cancelRename();
            return;
        }
        let failed = false;
        try {
            const res = await rpc("/nova/ai/rename", {
                session_id: sessionId, title: text,
            });
            failed = !!(res && res.error);
        } catch {
            failed = true;
        }
        if (failed) {
            try {
                this._error.set(_t("Could not rename that conversation."));
            } catch {
                // error stays put
            }
        }
        this.cancelRename();
        await this.loadSessions();
    }

    toggleMore() {
        let open = false;
        try {
            open = !this._showMore();
            this._showMore.set(open);
        } catch (e) {
            open = false;
        }
        return open;
    }

    hideMore() {
        try {
            this._showMore.set(false);
        } catch {
            // menu stays put
        }
    }

    menuRegenerate() {
        this.hideMore();
        try {
            this.regenerate();
        } catch {
            // action stays put
        }
    }

    menuExport() {
        this.hideMore();
        try {
            this.exportChat();
        } catch {
            // action stays put
        }
    }

    menuExportCsv() {
        this.hideMore();
        try {
            this.exportChatCsv();
        } catch {
            // action stays put
        }
    }

    menuReadAloud() {
        this.hideMore();
        try {
            this.toggleReadAloud();
        } catch {
            // action stays put
        }
    }

    async pickSession(sessionId) {
        this.rememberSeen();
        try {
            this._showHistory.set(false);
            this._sessionId.set(Number(sessionId) || 0);
            this._messages.set([]);
            this._error.set("");
        } catch (e) {
            return;
        }
        if (sessionId) {
            await this.loadSession(Number(sessionId));
        }
    }

    sessionLabel(row) {
        try {
            const base = ((row && row.title) || "Conversation").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
            const raw = row && row.last_write ? String(row.last_write) : "";
            const stamp = raw ? raw.slice(0, 16).replace("T", " ") : "";
            return stamp ? base + " · " + stamp : base;
        } catch (e) {
            return "";
        }
    }

    staleNudges() {
        // Sessions whose approvals waited more than a day: reminded
        // on the next visit, not nagged every open. last_write moves
        // on touch, so this is "quiet for 24h with cards open".
        let rows = [];
        try {
            rows = this._sessions() || [];
        } catch (e) {
            rows = [];
        }
        const cutoff = Date.now() - 24 * 3600 * 1000;
        const nudges = [];
        for (const row of rows) {
            let waiting = 0;
            try {
                waiting = (row && row.pending && row.pending.length) || 0;
            } catch (e) {
                waiting = 0;
            }
            if (!waiting) {
                continue;
            }
            let when = 0;
            try {
                when = Date.parse(String(row.last_write || "").replace(" ", "T"));
            } catch (e) {
                when = 0;
            }
            if (when && when < cutoff) {
                nudges.push({ id: row.id, waiting: waiting });
            }
        }
        return nudges;
    }

    messagePlain(message) {
        try {
            return plainFromHtml((message && message.html && message.html.toString()) || "");
        } catch (e) {
            return "";
        }
    }

    async copyMessage(messageIndex) {
        // Copy as Markdown when the turn has it (tables survive),
        // else the plain rendering. One button, best format.
        let text = "";
        try {
            const message = (this._messages() || [])[messageIndex];
            text = (message && message.markdown) ||
                this.messagePlain(message);
        } catch {
            return;
        }
        if (!text) {
            return;
        }
        let done = false;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
                done = true;
            }
        } catch {
            done = false;
        }
        if (!done) {
            // Clipboard API unavailable (permissions, insecure context):
            // fall back to the classic execCommand path.
            try {
                const area = document.createElement("textarea");
                area.value = text;
                area.setAttribute("readonly", "");
                area.style.position = "absolute";
                area.style.left = "-9999px";
                document.body.appendChild(area);
                area.select();
                done = document.execCommand("copy");
                document.body.removeChild(area);
            } catch {
                done = false;
            }
        }
        if (!done) {
            return;
        }
        try {
            this._copied.set(messageIndex);
        } catch {
            // label stays put
        }
        try {
            setTimeout(() => {
                try {
                    if (this._copied() === messageIndex) {
                        this._copied.set(-1);
                    }
                } catch {
                    // label stays put
                }
            }, 2000);
        } catch {
            // label stays put
        }
    }

    async reportMessage(messageIndex, format) {
        // Branded export for one turn: PDF (full answer) or PNG
        // (first table, styled). The server renders from markdown
        // so tables survive as styled tables.
        const want = format === "png" ? "png" : "pdf";
        let text = "";
        try {
            const message = (this._messages() || [])[messageIndex];
            text = (message && message.markdown) ||
                this.messagePlain(message);
        } catch {
            return;
        }
        if (!text || !text.trim()) {
            return;
        }
        let sessionId = 0;
        try {
            sessionId = this._sessionId();
        } catch {
            return;
        }
        let result = null;
        try {
            result = await rpc("/nova/ai/report", {
                session_id: sessionId,
                index: messageIndex,
                markdown: text.slice(0, 50000),
                format: want,
            });
        } catch {
            result = null;
        }
        if (!result || !result.ok || !(result.blob || result.pdf)) {
            try {
                this.sayNotice(result && result.error
                    ? result.error
                    : (want === "png"
                        ? "No table in that reply to render — try PDF."
                        : "The PDF could not be built."));
            } catch {
                // notice stays put
            }
            return;
        }
        try {
            const raw = atob(result.blob || result.pdf);
            const bytes = new Uint8Array(raw.length);
            for (let i = 0; i < raw.length; i++) {
                bytes[i] = raw.charCodeAt(i);
            }
            const blob = new Blob([bytes], {
                type: want === "png" ? "image/png" : "application/pdf",
            });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = result.filename || ("nova-answer." + want);
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            setTimeout(() => {
                try { URL.revokeObjectURL(url); } catch (ignored) {}
            }, 5000);
        } catch {
            // download stays put
        }
    }

    exportChat() {
        let markdown = "";
        try {
            const name = (this.agent && this.agent.name) || "Ask Nova";
            const lines = ["# " + name + " — conversation", ""];
            for (const message of this._messages() || []) {
                const who = message.role === "assistant" ? name : "You";
                lines.push("## " + who, "");
                const plain = this.messagePlain(message);
                lines.push(plain || "(empty reply)", "");
                for (const step of message.steps || []) {
                    lines.push("- ran " + this.stepLabel(step));
                }
                if ((message.steps || []).length) {
                    lines.push("");
                }
                for (const src of message.sources || []) {
                    lines.push("- source: " + (src.name || "untitled"));
                }
                if ((message.sources || []).length) {
                    lines.push("");
                }
            }
            markdown = lines.join("\n");
        } catch {
            return;
        }
        try {
            const blob = new Blob([markdown], { type: "text/markdown" });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = "nova-chat-s" + (this._sessionId() || "new") + "-" + new Date().toISOString().slice(0, 10) + ".md";
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            setTimeout(() => {
                try { URL.revokeObjectURL(url); } catch (ignored) {}
            }, 5000);
        } catch {
            // export stays put
        }
    }

    exportChatCsv() {
        // One row per turn: index, role, text, steps, cards. Opens in
        // any spreadsheet for audits and reviews.
        const cell = (value) => {
            const text = String(value === undefined || value === null ? "" : value);
            return '"' + text.replace(/"/g, '""') + '"';
        };
        let rows = [["n", "role", "text", "steps", "cards"]];
        try {
            const list = this._messages() || [];
            list.forEach((message, n) => {
                const steps = (message.steps || []).map((s) => this.stepLabel(s)).join(" | ");
                const cards = (message.cards || []).map((c) => c.title || c.kind || "").join(" | ");
                rows.push([String(n + 1), message.role || "",
                    this.messagePlain(message), steps, cards]);
            });
        } catch {
            return;
        }
        try {
            const csv = "\uFEFF" + rows.map((r) => r.map(cell).join(",")).join("\r\n");
            const blob = new Blob([csv], { type: "text/csv" });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            try {
                link.download = "nova-chat-s" + (this._sessionId() || "new") + "-" + new Date().toISOString().slice(0, 10) + ".csv";
            } catch {
                // download stays put
            }
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            setTimeout(() => {
                try { URL.revokeObjectURL(url); } catch (ignored) {}
            }, 5000);
        } catch {
            // export stays put
        }
    }
    async newChat() {
        this.rememberSeen();
        try {
            this._sessionId.set(0);
            this._messages.set([]);
            this._error.set("");
            this._unreadFrom.set(-1);
        } catch {
            // transcript stays put
        }
    }

    async runAction(action) {
        if (!action) {
            return;
        }
        try {
            await this.action.doAction(action);
        } catch {
            // action stays put
        }
    }

    stepLabel(step) {
        let label = '';
        if (!step) {
            return '';
        }
        if (step.note) {
            label = step.note;
        } else if (step.asked) {
            label = "Asked the user";
        } else if (step.error) {
            label = 'Checked ' + (step.tool || 'records') + ' failed: ' + step.error;
        } else if (step.queued) {
            label = 'Queued ' + (step.tool || 'an action') + ' for approval';
        } else if (step.action) {
            label = 'Opened ' + (step.tool || 'a view');
        } else {
            label = 'Checked ' + (step.tool || 'records');
        }
        if (step.n > 1) {
            label += ' \u00d7' + step.n;
        }
        return label;
    }

    stepTip(step) {
        // Tooltip: how long the tool call took. Tokens are turn-level
        // (the provider bills whole rounds, not single tool calls),
        // so per-call cost is time, shown where the work happened.
        try {
            if (step && step.ms !== undefined && step.ms !== null) {
                return 'took ' + step.ms + ' ms';
            }
        } catch {
            // no tooltip
        }
        return '';
    }

    previewParts(line) {
        // "Label: value" splits for the two-column preview: amounts
        // right-aligned tabular, labels left. No colon, no split.
        try {
            const text = String(line || '');
            const at = text.lastIndexOf(': ');
            if (at < 0) {
                return [text, ''];
            }
            return [text.slice(0, at), text.slice(at + 2)];
        } catch {
            return [String(line || ''), ''];
        }
    }

    kindLabel(kind) {
        const names = {
            log_note: 'note', schedule_activity: 'task',
            set_field: 'field', create_draft: 'draft',
            copy_record: 'copy', unlink_record: 'delete',
            execute_method: 'run', confirm_sale_order: 'order',
            validate_invoice: 'bill', digitize_bill: 'scan',
        };
        try {
            return names[kind] || String(kind || 'action');
        } catch {
            return 'action';
        }
    }

    get subLine() {
        let model = '';
        let ms = 0;
        try {
            model = (this.agent && this.agent.model) || '';
            ms = this._lastMs() || 0;
        } catch (e) {
            model = '';
        }
        if (model && ms) {
            return model + ' - ' + ms + 's';
        }
        return model;
    }
}

registry.category("systray").add(
    "nova_ai.drawer",
    { Component: NovaAiDrawer },
    { sequence: 40 }
);
