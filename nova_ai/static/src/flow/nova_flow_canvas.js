/** @odoo-module **/
//
// Nova Flow Canvas v2 — n8n parity inside Odoo Community.
//
// Layout mirrors n8n so muscle memory transfers:
// * top bar: workflow name, Active toggle, Save, Test workflow, Import,
//   Export (Nova + n8n JSON), webhook URL
// * left: searchable nodes panel (Triggers, Odoo, Logic, Transform,
//   AI, Flow) — click or drag to add
// * canvas: SVG bezier wires, true/false/error ports, pan + zoom
// * right inspector: Parameters / Code-JSON / Input / Output tabs —
//   Input/Output show the last execution per node, exactly like n8n
// * bottom: executions bar (run list + per-run node IO viewer)
// * copilot drawer: Nova AI in the same screen — ask, it rebuilds the
//   graph via draft/update tools, Apply lands it on the canvas
//

import { Component, onMounted, onWillUnmount, signal, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

const PALETTE = [
    { group: "Triggers", items: [
        { type: "trigger", label: "Manual Trigger", hint: "Start manually / chat" },
        { type: "trigger", label: "Webhook", hint: "POST /hook/<token>", preset: { trigger_kind: "webhook" } },
        { type: "trigger", label: "Schedule", hint: "cron / interval", preset: { trigger_kind: "schedule" } },
        { type: "trigger", label: "Record Event", hint: "on create/write", preset: { trigger_kind: "record" } },
    ]},
    { group: "Odoo", items: [
        { type: "odoo.search", label: "Odoo Search", hint: "model + domain" },
        { type: "odoo.read", label: "Odoo Read", hint: "model + res_id" },
        { type: "odoo.count", label: "Odoo Count", hint: "model + domain" },
        { type: "odoo.group", label: "Odoo Group", hint: "groupby + sums" },
        { type: "odoo.create", label: "Odoo Create", hint: "queues card" },
        { type: "odoo.write", label: "Odoo Update", hint: "queues card" },
        { type: "odoo.email", label: "Odoo Email", hint: "queues send" },
        { type: "odoo.note", label: "Odoo Note", hint: "chatter note" },
        { type: "odoo.activity", label: "Odoo Activity", hint: "schedule" },
        { type: "odoo.method", label: "Odoo Method", hint: "business button" },
    ]},
    { group: "Logic", items: [
        { type: "if", label: "IF", hint: "true / false" },
        { type: "switch", label: "Switch", hint: "0 / 1 / …" },
        { type: "filter", label: "Filter", hint: "pass items" },
        { type: "merge", label: "Merge", hint: "join branches" },
    ]},
    { group: "Transform", items: [
        { type: "set", label: "Set", hint: "values = ${…} / {{$json…}}" },
        { type: "code", label: "Code", hint: "python · result =" },
        { type: "http", label: "HTTP Request", hint: "GET · https" },
    ]},
    { group: "AI", items: [
        { type: "ai.agent", label: "AI Agent", hint: "instruction ${vars}" },
        { type: "ai.prompt", label: "AI Prompt", hint: "model prompt" },
    ]},
    { group: "Flow", items: [
        { type: "loop.foreach", label: "Loop Over Items", hint: "items=${rows}" },
        { type: "wait", label: "Wait", hint: "pause step" },
        { type: "human.approval", label: "Human Approval", hint: "card gate" },
        { type: "subflow", label: "Execute Flow", hint: "run subflow" },
        { type: "output", label: "Output", hint: "final value" },
        { type: "respond", label: "Respond", hint: "webhook reply" },
        { type: "note", label: "Sticky Note", hint: "annotate" },
    ]},
];

const TYPE_COLOR = {
    trigger: "#ea4b71", start: "#ea4b71",
    "odoo.search": "#0ea5e9", "odoo.read": "#0ea5e9",
    "odoo.count": "#0ea5e9", "odoo.group": "#6366f1",
    "odoo.email": "#f59e0b",
    "odoo.create": "#10b981", "odoo.write": "#10b981",
    "odoo.note": "#f59e0b", "odoo.activity": "#f59e0b", "odoo.method": "#8b5cf6",
    if: "#c026d3", switch: "#c026d3", filter: "#c026d3", merge: "#6366f1",
    set: "#14b8a6", code: "#22c55e", http: "#06b6d4",
    "loop.foreach": "#a855f7", wait: "#64748b", "human.approval": "#f43f5e",
    subflow: "#0ea5e9", "ai.agent": "#7c5cff", "ai.prompt": "#7c5cff",
    output: "#10b981", respond: "#10b981", note: "#eab308",
};

let _seq = 1;

export class NovaFlowCanvas extends Component {
    static template = "nova_ai.NovaFlowCanvas";
    props = useProps(standardActionServiceProps);

    setup() {
        this.action = useService("action");
        this.orm = useService("orm");
        this.flowId = (this.props.action?.params?.flow_id) || 0;
        this._flow = signal(null);
        this._nodes = signal([]);
        this._edges = signal([]);
        this._sel = signal(null);
        this._tab = signal("params");
        this._linkFrom = signal(null);
        this._problems = signal([]);
        this._runs = signal([]);
        this._runDetail = signal(null);
        this._notice = signal("");
        this._error = signal("");
        this._busy = signal("");
        this._query = signal("");
        this._copilot = signal(false);
        this._chat = signal([]);
        this._importOpen = signal(false);
        this._importText = signal("");
        this._importN8n = signal(false);
        this._hasLegacy = signal(false);
        this._draft = signal("");
        this._pan = signal({ x: 0, y: 0, z: 1 });
        this._drag = null;
        this._suppressClick = false;
        this._describeToken = 0;
        this._modelsTimer = null;
        this._proposal = signal(null);
        // No-code helpers: model/field/method catalogs for guided forms.
        this._models = signal([]);
        this._fields = signal([]);
        this._methods = signal([]);
        this._modelsQuery = signal("");
        this._describeBusy = signal(false);
        onMounted(() => this.load());
        onWillUnmount(() => this._detachDrag());
    }

    // ------------------------------------------------------------ data
    async load() {
        this._busy.set("load");
        try {
            const flows = await this.orm.searchRead("nova.ai.flow",
                this.flowId ? [["id", "=", this.flowId]] : [], [
                "id", "name", "description", "nodes", "edges", "steps",
                "trigger_type", "trigger_config", "webhook_url",
                "version", "active"], { limit: 1 });
            const flow = flows[0] || null;
            this._flow.set(flow);
            this._nodes.set((flow?.nodes || []).map((n) => ({
                ...n, position: n.position || { x: 120, y: 160 } })));
            this._edges.set(flow?.edges || []);
            this._hasLegacy.set(
                !(flow?.nodes || []).length && !!((flow?.steps || []).length));
            if (flow) { await this.refreshRuns(flow.id); }
        } catch (e) {
            this._error.set(e?.message || _t("Could not load the flow."));
        } finally {
            this._busy.set("");
        }
    }

    async refreshRuns(flowId) {
        try {
            const res = await rpc("/nova/ai/studio/flow", {
                action: "runs", flow_id: flowId, arguments: {} });
            this._runs.set((res && res.runs) || []);
        } catch { /* history is optional */ }
    }

    async openRun(runId) {
        if (!this.flow?.id || !runId) { return; }
        try {
            const res = await rpc("/nova/ai/studio/flow", {
                action: "run_detail", flow_id: this.flow.id,
                arguments: { run_id: runId } });
            this._runDetail.set((res && res.run) || null);
            if ((res?.run?.node_outputs) && this._sel()) {
                this._tab.set("output");
            }
        } catch (e) {
            this._error.set(_t("Could not open that run."));
        }
    }

    get flow() { try { return this._flow() || null; } catch { return null; } }
    // Bare globals do not resolve inside this Owl build's template
    // scope (same root cause as the bare-identifier crash): expose
    // JSON explicitly so templates use this.JSON.
    get JSON() { return JSON; }
    get nodes() { try { return this._nodes() || []; } catch { return []; } }
    get edges() { try { return this._edges() || []; } catch { return []; } }
    get selected() {
        try {
            const id = this._sel();
            return this.nodes.find((n) => n.id === id) || null;
        } catch { return null; }
    }
    get filteredPalette() {
        const q = (this._query() || "").toLowerCase().trim();
        if (!q) { return PALETTE; }
        return PALETTE.map((g) => ({
            ...g, items: g.items.filter((i) =>
                (i.label + " " + i.type + " " + i.hint).toLowerCase().includes(q)),
        })).filter((g) => g.items.length);
    }
    nodeColor(t) { return TYPE_COLOR[t] || "#64748b"; }

    // Copy n8n gives nodes a display name; ours keys scope by id.
    // The inspector shows both so expressions stay readable.
    nodeIO(nodeId, which) {
        try {
            const run = this._runDetail();
            if (!run) { return null; }
            const bag = which === "input" ? run.node_inputs : run.node_outputs;
            const val = bag && bag[nodeId];
            return val === undefined ? null : val;
        } catch { return null; }
    }

    // ------------------------------------------------------------ edit
    addNode(type, preset = {}) {
        const id = `${type.replace(/[^a-z]/gi, "").slice(0, 4) || "node"}_${Date.now().toString(36)}${_seq++}`;
        const count = this.nodes.length;
        const node = {
            id, type, name: (preset && preset.name) || `${type} ${_seq}`,
            config: (preset && preset.config) || (
                (type === "trigger" || type === "start")
                    ? { variables: [] } : {}),
            position: (preset && preset.position) || {
                x: 120 + (count % 6) * 230,
                y: 140 + Math.floor(count / 6) * 190 },
        };
        if (preset?.trigger_kind && this.flow) {
            this._flow.set({ ...this.flow,
                trigger_type: preset.trigger_kind });
        }
        this._nodes.set([...this.nodes, node]);
        this._sel.set(id);
        this._tab.set("params");
    }

    removeNode(id) {
        this._nodes.set(this.nodes.filter((n) => n.id !== id));
        this._edges.set(this.edges.filter((e) => e.source !== id && e.target !== id));
        if (this._sel() === id) { this._sel.set(null); }
    }

    armWire(id, handle = "default") {
        if (this._linkFrom() === id) { this._linkFrom.set(null); return; }
        this._linkFrom.set({ from: id, handle });
        const label = handle === "default" ? _t("Armed — click the target node (click source again to cancel).")
            : _t("Armed (%s) — click the target node.", handle);
        this._notice.set(label);
    }

    clickNode(id, ev) {
        if (this._suppressClick) {
            this._suppressClick = false;
            return;
        }
        const armed = this._linkFrom();
        const armedFrom = typeof armed === "string" ? armed : armed?.from;
        const armedHandle = typeof armed === "string" ? "default" : (armed?.handle || "default");
        if (ev?.shiftKey || armedFrom) {
            const from = armedFrom || null;
            if (!from) { this._linkFrom.set(id);
                this._notice.set(_t("Source armed — shift-click the target.")); return; }
            if (from === id) { this._linkFrom.set(null); return; }
            this.addEdge(from, id, ev?.shiftKey ? "default" : armedHandle);
            this._linkFrom.set(null);
            return;
        }
        this._sel.set(id);
        // Preload catalogs so guided forms never show empty dropdowns.
        try {
            const node = this.nodes.find((n) => n.id === id);
            const t = node?.type || "";
            if (t.startsWith("odoo.")) {
                const cfg = node.config || {};
                this.describe(cfg.args?.model || cfg.model || "");
            }
        } catch { /* optional */ }
    }

    addEdge(source, target, handle = "default") {
        if (!source || !target || source === target) { return; }
        if (this.edges.some((e) => e.source === source && e.target === target
                && (e.source_handle || "default") === handle)) { return; }
        this._edges.set([...this.edges, {
            id: `e_${source}_${target}_${handle}`, source, target,
            source_handle: handle }]);
    }

    removeEdge(id) {
        this._edges.set(this.edges.filter((e) => e.id !== id));
    }

    setNodeField(field, ev) {
        const node = this.selected;
        if (!node) { return; }
        const value = ev?.target?.value ?? "";
        this._patchNode(node.id, { [field]: value });
    }

    setNodeConfig(text) {
        const node = this.selected;
        if (!node) { return; }
        try {
            const config = JSON.parse(text || "{}");
            this._patchNode(node.id, {}, config);
            this._error.set("");
        } catch (e) {
            this._error.set(_t("Config is not valid JSON — not applied. ") + (e?.message || ""));
        }
    }

    setNodeCode(text) {
        const node = this.selected;
        if (!node) { return; }
        const code = text ?? "";
        if (code.length > 8000) {
            this._error.set(_t("Code is too long (max 8000 chars) — not applied."));
            return;
        }
        this._patchNode(node.id, {}, { ...(node.config || {}), code });
        this._error.set("");
    }

    _patchNode(id, patch = {}, config = null) {
        this._nodes.set(this.nodes.map((n) => {
            if (n.id !== id) { return n; }
            const next = { ...n, ...patch };
            if (config !== null) { next.config = config; }
            return next;
        }));
    }

    // ------------------------------------------------------------ view
    edgePath(edge) {
        const a = this.nodes.find((n) => n.id === edge.source);
        const b = this.nodes.find((n) => n.id === edge.target);
        if (!a || !b) { return ""; }
        const x1 = (a.position.x || 0) + 180, y1 = (a.position.y || 0) + 36;
        const x2 = (b.position.x || 0), y2 = (b.position.y || 0) + 36;
        const dx = Math.max(40, Math.abs(x2 - x1) / 2);
        return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
    }

    edgeLabel(edge) {
        return edge.source_handle && edge.source_handle !== "default"
            ? String(edge.source_handle) : "";
    }

    _detachDrag() {
        try {
            if (this._dragMove) {
                window.removeEventListener("pointermove", this._dragMove);
            }
            if (this._dragUp) {
                window.removeEventListener("pointerup", this._dragUp);
            }
        } catch { /* already gone */ }
        this._drag = null;
        this._dragMove = null;
        this._dragUp = null;
    }

    startDrag(node, ev) {
        if (ev.shiftKey) { return; }
        ev.preventDefault();
        this._detachDrag();
        this._drag = { id: node.id, x: ev.clientX, y: ev.clientY,
            px: node.position.x, py: node.position.y, moved: false };
        this._dragMove = (e2) => {
            if (!this._drag) { return; }
            const dx = e2.clientX - this._drag.x;
            const dy = e2.clientY - this._drag.y;
            if (Math.abs(dx) + Math.abs(dy) > 4) {
                this._drag.moved = true;
            }
            if (!this._drag.moved) { return; }
            const z = this._pan().z || 1;
            const nx = this._drag.px + dx / z;
            const ny = this._drag.py + dy / z;
            this._nodes.set(this.nodes.map((n) => n.id === this._drag.id
                ? { ...n, position: { x: Math.round(nx), y: Math.round(ny) } } : n));
        };
        this._dragUp = () => {
            // A real drag ends with a click — swallow it so the node
            // is not reselected and catalogs are not re-fetched.
            if (this._drag && this._drag.moved) {
                this._suppressClick = true;
            }
            this._detachDrag();
        };
        window.addEventListener("pointermove", this._dragMove);
        window.addEventListener("pointerup", this._dragUp);
    }

    zoom(by) {
        const pan = this._pan();
        const z = Math.min(1.6, Math.max(0.4, (pan.z || 1) + by));
        this._pan.set({ ...pan, z });
    }

    get zoomBox() {
        // The zoom layer sizes itself to the content so the stage
        // scrolls exactly to the farthest node — no 3000px dead zone.
        let w = 1200, h = 800;
        try {
            for (const n of this.nodes) {
                const x = (n.position?.x || 0) + 260;
                const y = (n.position?.y || 0) + 220;
                if (x > w) { w = x; }
                if (y > h) { h = y; }
            }
        } catch { /* empty canvas */ }
        const pan = (() => { try { return this._pan(); } catch { return {}; } })();
        return ("width:" + w + "px;height:" + h + "px;"
            + "transform: translate(" + (pan.x || 0) + "px,"
            + (pan.y || 0) + "px) scale(" + (pan.z || 1)
            + ");transform-origin: 0 0;");
    }

    // ------------------------------------------------------------ server
    async toggleActive() {
        const flow = this.flow;
        if (!flow) { return; }
        try {
            await this.orm.write("nova.ai.flow", [flow.id],
                { active: !flow.active });
            await this.load();
        } catch (e) {
            this._error.set(_t("Could not toggle the workflow."));
        }
    }

    async save() {
        const flow = this.flow;
        if (!flow || this._busy()) { return; }
        this._busy.set("save");
        try {
            this._error.set("");
            this._notice.set("");
            const res = await rpc("/nova/ai/studio/flow", {
                action: "save", flow_id: flow.id,
                arguments: { name: flow.name, description: flow.description || "",
                    steps: [], nodes: this.nodes, edges: this.edges,
                    trigger_type: flow.trigger_type || "manual",
                    trigger_config: flow.trigger_config || {},
                    active: flow.active !== false },
            });
            this._problems.set((res && res.problems) || []);
            if (res?.ok) {
                this._notice.set(res.valid
                    ? _t("Saved — valid and ready.")
                    : _t("Saved, but the graph is red — fix the steps."));
                await this.load();
            } else {
                this._error.set((res?.error) || _t("Save failed."));
            }
        } catch (e) {
            this._error.set(e?.message || _t("Save failed."));
        } finally {
            this._busy.set("");
        }
    }

    async run() {
        const flow = this.flow;
        if (!flow || this._busy()) { return; }
        this._busy.set("run");
        try {
            this._error.set("");
            this._notice.set("");
            const res = await rpc("/nova/ai/studio/flow", {
                action: "run", flow_id: flow.id, arguments: {} });
            if (res?.ok) {
                this._notice.set(res.needs_approval
                    ? _t("Waiting on approval — approve the card to continue.")
                    : _t("Workflow executed."));
                if (res.run_id) { await this.openRun(res.run_id); }
            } else {
                this._error.set(res?.error || _t("The workflow failed."));
                if (res?.run_id) { await this.openRun(res.run_id); }
            }
            await this.refreshRuns(flow.id);
        } catch (e) {
            this._error.set(e?.message || _t("The workflow failed."));
        } finally {
            this._busy.set("");
        }
    }

    async doExport(n8n = false) {
        const flow = this.flow;
        if (!flow) { return; }
        try {
            const res = await rpc("/nova/ai/studio/flow", {
                action: n8n ? "export_n8n" : "export", flow_id: flow.id,
                arguments: {} });
            if (res?.ok) {
                await navigator.clipboard.writeText(res.definition || "");
                this._notice.set(n8n
                    ? _t("n8n JSON copied — paste it straight into n8n.")
                    : _t("Nova JSON copied to the clipboard."));
            } else {
                this._error.set(res?.error || _t("Export failed."));
            }
        } catch (e) {
            this._error.set(_t("Export failed."));
        }
    }

    async doImport(n8n = false) {
        const flow = this.flow;
        if (!flow || this._busy()) { return; }
        this._importN8n.set(!!n8n);
        this._importText.set("");
        this._importOpen.set(true);
    }

    cancelImport() {
        this._importOpen.set(false);
        this._importText.set("");
    }

    async convertToGraph() {
        const flow = this.flow;
        if (!flow || !flow.id || this._busy()) { return; }
        this._busy.set("convert");
        this._error.set("");
        try {
            await this.orm.call(
                "nova.ai.flow", "action_graph_from_steps", [flow.id]);
            this._hasLegacy.set(false);
            await this.load();
            this._notice.set(_t("Converted to graph nodes."));
        } catch (e) {
            this._error.set(
                e?.message || _t("Could not convert the flow."));
        } finally {
            this._busy.set("");
        }
    }

    async confirmImport() {
        const flow = this.flow;
        const text = (this._importText() || "").trim();
        const n8n = !!this._importN8n();
        if (!flow || !text || this._busy()) { return; }
        // Keep the dialog open on failure so the pasted JSON is not lost.
        this._busy.set("import");
        this._error.set("");
        this._notice.set("");
        try {
            const res = await rpc("/nova/ai/studio/flow", {
                action: n8n ? "import_n8n" : "import", flow_id: flow.id,
                arguments: { definition: text } });
            if (res?.ok) {
                this._problems.set(res.problems || []);
                if ((res.unsupported || []).length) {
                    this._notice.set(_t("Imported with notes: ") +
                        res.unsupported.join("; ").slice(0, 300));
                } else {
                    this._notice.set(_t("Imported."));
                }
                this._importOpen.set(false);
                this._importText.set("");
                await this.load();
            } else {
                this._error.set(res?.error || _t("Import failed — your text is still above."));
            }
        } catch (e) {
            this._error.set((e?.message || _t("Import failed — your text is still above.")));
        } finally {
            this._busy.set("");
        }
    }

    // ------------------------------------------------------------ copilot
    toggleCopilot() { this._copilot.set(!this._copilot()); }

    onCopilotKey(ev) {
        if ((ev && ev.key) === "Enter") {
            this.sendCopilot();
        }
    }

    async sendCopilot() {
        const flow = this.flow;
        const text = (this._draft() || "").trim();
        if (!flow || !text || this._busy()) { return; }
        this._chat.set([...this._chat(), { role: "user", text }]);
        this._draft.set("");
        this._busy.set("copilot");
        try {
            const res = await rpc("/nova/ai/studio/flow", {
                action: "copilot", flow_id: flow.id,
                arguments: { message: text } });
            if (res?.ok) {
                this._chat.set([...this._chat(),
                    { role: "nova", text: res.reply || _t("Done.") }]);
                const cards = res.cards || [];
                if (cards.length) {
                    this._chat.set([...this._chat(),
                        { role: "nova", text:
                            _t("Approval queued — approve the card, then I apply it here. Card: ")
                            + (cards[0].title || cards[0].kind || "") }]);
                }
                // A refreshed graph is a PROPOSAL until the operator
                // presses Apply: never overwrite live edits silently.
                if (res.flow?.nodes) {
                    this._proposal.set({
                        nodes: res.flow.nodes,
                        edges: res.flow.edges || [],
                        version: res.flow.version || 0,
                    });
                    this._chat.set([...this._chat(),
                        { role: "nova", text:
                            _t("I staged a graph update — press Apply to put it on the canvas, Discard to drop it.") }]);
                }
                this._notice.set(_t("Nova replied in the copilot."));
            } else {
                this._chat.set([...this._chat(),
                    { role: "nova", text: res?.error || _t("That did not work.") }]);
            }
        } catch (e) {
            this._chat.set([...this._chat(),
                { role: "nova", text: _t("The copilot is unavailable.") }]);
        } finally {
            this._busy.set("");
        }
    }

    applyProposal() {
        const proposal = this._proposal();
        if (!proposal) { return; }
        const norm = (proposal.nodes || []).map((n) => ({
            ...n, position: n.position || { x: 120, y: 160 } }));
        const curN = this.nodes.length, curE = this.edges.length;
        const newN = norm.length, newE = (proposal.edges || []).length;
        // Guard: never silently wipe live edits. Require confirm when
        // the canvas already has work and the proposal drops nodes.
        try {
            if (curN > 0 && newN < curN) {
                const ok = window.confirm(
                    `Apply ${newN} nodes / ${newE} edges over current ${curN} nodes / ${curE} edges? Your canvas is replaced.`);
                if (!ok) { return; }
            }
            if (proposal.version && this.flow && proposal.version < (this.flow.version || 0)) {
                this._error.set(_t("Proposal is stale (flow changed since staging) — review before applying."));
            }
        } catch { /* confirm unavailable in tests */ }
        this._nodes.set(norm);
        this._edges.set(proposal.edges || []);
        this._proposal.set(null);
        this._notice.set(_t(`Proposal applied (${newN} nodes / ${newE} edges) — review, then Save.`));
    }

    discardProposal() {
        this._proposal.set(null);
        this._notice.set(_t("Proposal discarded."));
    }

    // ------------------------------------------------------------ no-code
    // Guided builders: the inspector edits forms, the canvas owns JSON.
    // A user who never learns ${} syntax can still build any flow.
    guided() {
        const t = this.selected?.type || "";
        return ["trigger", "start",         "odoo.search", "odoo.read", "odoo.count", "odoo.group",
            "odoo.create", "odoo.write", "odoo.email", "odoo.note",
            "odoo.activity", "odoo.method", "if", "switch", "set",
            "loop.foreach", "http", "ai.agent", "ai.prompt", "output",
            "respond", "subflow", "wait", "human.approval"].includes(t);
    }

    async describe(model = "", query = "") {
        const flow = this.flow;
        if (!flow) { return; }
        const token = ++this._describeToken;
        this._describeBusy.set(true);
        try {
            const selId = this._sel();
            const res = await rpc("/nova/ai/studio/flow", {
                action: "describe", flow_id: flow.id,
                arguments: { model, query } });
            // Race guard: a newer selection wins; stale catalogs are
            // dropped instead of attached to the wrong node.
            if (token !== this._describeToken || this._sel() !== selId) {
                return;
            }
            if (res?.ok) {
                if (res.models) { this._models.set(res.models); }
                if (model) {
                    this._fields.set(res.fields || []);
                    const m = res.methods || {};
                    this._methods.set(m.methods || m.buttons || []);
                }
            }
        } catch { /* catalogs are optional */ }
        finally {
            if (token === this._describeToken) {
                this._describeBusy.set(false);
            }
        }
    }

    // All odoo.* guided edits land under config.args (single shape the
    // engine merges with top-level). Migrates legacy top-level keys.
    _argsCfg(node) {
        const cfg = { ...(node.config || {}) };
        if ((node.type || "").startsWith("odoo.") && !cfg.args) {
            cfg.args = {};
            for (const k of ["model", "res_id", "res_ids", "domain",
                    "fields", "limit", "order", "offset", "values"]) {
                if (cfg[k] !== undefined) {
                    cfg.args[k] = cfg[k];
                    delete cfg[k];
                }
            }
        }
        return cfg;
    }

    cfgGet(key, fallback = "") {
        try {
            const v = (this.selected?.config || {})[key];
            return v === undefined || v === null ? fallback : v;
        } catch { return fallback; }
    }

    cfgSet(key, ev) {
        const node = this.selected;
        if (!node) { return; }
        const value = ev?.target?.value ?? "";
        this._patchNode(node.id, {}, { ...(node.config || {}), [key]: value });
    }

    cfgSetObj(patch) {
        const node = this.selected;
        if (!node) { return; }
        this._patchNode(node.id, {}, { ...(node.config || {}), ...patch });
    }

    domainRows() {
        try {
            const cfg = this.selected?.config || {};
            const args = cfg.args || {};
            const domain = args.domain || cfg.domain || [];
            return Array.isArray(domain) ? domain.filter(
                (d) => Array.isArray(d)) : [];
        } catch { return []; }
    }

    domainSet(idx, part, ev) {
        const node = this.selected;
        if (!node) { return; }
        const rows = this.domainRows().map((r) => [...r]);
        while (rows.length <= idx) { rows.push(["", "=", ""]); }
        rows[idx][part] = ev?.target?.value ?? "";
        const cfg = this._argsCfg(node);
        if (cfg.args) { cfg.args = { ...cfg.args, domain: rows }; }
        else { cfg.domain = rows; }
        this._patchNode(node.id, {}, cfg);
    }

    domainAdd() {
        const node = this.selected;
        if (!node) { return; }
        const rows = [...this.domainRows(), ["", "=", ""]];
        const cfg = this._argsCfg(node);
        if (cfg.args) { cfg.args = { ...cfg.args, domain: rows }; }
        else { cfg.domain = rows; }
        this._patchNode(node.id, {}, cfg);
    }

    domainDel(idx) {
        const node = this.selected;
        if (!node) { return; }
        const rows = this.domainRows().filter((_, i) => i !== idx);
        const cfg = this._argsCfg(node);
        if (cfg.args) { cfg.args = { ...cfg.args, domain: rows }; }
        else { cfg.domain = rows; }
        this._patchNode(node.id, {}, cfg);
    }

    valueRows() {
        try {
            const cfg = this.selected?.config || {};
            const vals = (cfg.args?.values) || cfg.values || {};
            return Object.entries(vals);
        } catch { return []; }
    }

    valueSetRow(i, which, ev) {
        const node = this.selected;
        if (!node) { return; }
        const rows = this.valueRows();
        const newKey = which === 0 ? (ev?.target?.value ?? "") : null;
        if (newKey !== null) {
            const clash = rows.some(([k], j) => j !== i && k === newKey);
            if (clash) {
                this._error.set(
                    _t("That field is already in the list — edit its row instead."));
                return;
            }
        }
        const next = {};
        rows.forEach(([k, v], j) => {
            next[j === i && which === 0 ? (ev?.target?.value ?? k) : k] =
                j === i && which === 1 ? (ev?.target?.value ?? v) : v;
        });
        const cfg = this._argsCfg(node);
        if (cfg.args) { cfg.args = { ...cfg.args, values: next }; }
        else { cfg.values = next; }
        this._patchNode(node.id, {}, cfg);
    }

    valueAddRow() {
        const node = this.selected;
        if (!node) { return; }
        const next = Object.fromEntries(this.valueRows());
        // Unique placeholder keys: repeated "" keys used to collapse
        // into a single row, silently eating fields.
        let n = 1;
        while (`field_${n}` in next) { n++; }
        next[`field_${n}`] = "";
        const cfg = this._argsCfg(node);
        if (cfg.args) { cfg.args = { ...cfg.args, values: next }; }
        else { cfg.values = next; }
        this._patchNode(node.id, {}, cfg);
        this._notice.set(_t("Rename “%s” to the real field name.", `field_${n}`));
    }

    valueDelRow(i) {
        const node = this.selected;
        if (!node) { return; }
        const next = Object.fromEntries(
            this.valueRows().filter((_, j) => j !== i));
        const cfg = this._argsCfg(node);
        if (cfg.args) { cfg.args = { ...cfg.args, values: next }; }
        else { cfg.values = next; }
        this._patchNode(node.id, {}, cfg);
    }

    onModelChange(ev) {
        const model = ev?.target?.value || "";
        const node = this.selected;
        if (!node) { return; }
        const cfg = this._argsCfg(node);
        if (cfg.args) { cfg.args = { ...cfg.args, model }; }
        else { cfg.model = model; }
        this._patchNode(node.id, {}, cfg);
        if (model) { this.describe(model); }
    }

    searchModels(ev) {
        this._modelsQuery.set(ev?.target?.value || "");
        clearTimeout(this._modelsTimer);
        this._modelsTimer = setTimeout(() => {
            this.describe("", this._modelsQuery());
        }, 300);
    }

    backToStudio() {
        this.action.doAction("nova_ai.action_nova_ai_studio");
    }

    get palette() { return this.filteredPalette; }
}

registry.category("actions").add("nova_flow_canvas", NovaFlowCanvas);
