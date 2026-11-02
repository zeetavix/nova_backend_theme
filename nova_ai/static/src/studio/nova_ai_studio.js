/** @odoo-module **/
//
// Nova Studio — the agent's control room.
//
// One screen for the five questions an operator actually has: what does
// the agent know, what is waiting on me, what can it do, why did it
// answer like that, and what may it spend.
//
// The server hands over one payload (see controllers/nova_ai_studio.py)
// and this component renders it without a second round trip. Actions
// (review a proposal, run a flow, simulate the router) patch the
// payload in place so the screen never reflows under the cursor.
//
//

import { Component, markup, onMounted, signal, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { renderMarkdown } from "@nova_ai/chat/nova_ai_drawer";

// The rail, in reading order. The count badge is filled from the
// payload; a section with nothing to say shows no badge at all.
const SECTIONS = [
    { id: "overview", label: "Overview", icon: "◈" },
    { id: "brain", label: "Brain", icon: "❋" },
    { id: "proposals", label: "Proposals", icon: "✎" },
    { id: "flows", label: "Flows", icon: "⇉" },
    { id: "tools", label: "Tools", icon: "⚙" },
    { id: "routing", label: "Routing", icon: "⇄" },
    { id: "activity", label: "Activity", icon: "◷" },
];

const KIND_LABELS = {
    skill: "Skill",
    memory: "Memory",
    wiki: "Wiki",
    reference: "Reference",
    document: "Document",
};

const TRUST_LABELS = {
    "human-reviewed": "Human reviewed",
    "machine-confirmed": "Machine confirmed",
    unverified: "Unverified",
};

const CAPABILITIES = [
    { id: "tools", label: "Tool calling" },
    { id: "vision", label: "Reads images" },
    { id: "json", label: "Strict JSON" },
    { id: "reasoning", label: "Long reasoning" },
    { id: "embeddings", label: "Embeddings" },
];

const STEP_ICONS = {
    start: "◆",
    search: "⌕",
    read: "▤",
    create: "＋",
    tool: "⚙",
    agent: "❋",
    note: "✎",
    output: "→",
};

function fmtTime(value) {
    // Server timestamps are UTC strings; show a relative age, which is
    // what an operator scanning an audit log actually wants.
    if (!value) {
        return "";
    }
    const then = new Date(value).getTime();
    if (!then || Number.isNaN(then)) {
        return String(value).slice(0, 16).replace("T", " ");
    }
    const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (seconds < 60) {
        return "just now";
    }
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) {
        return `${minutes}m ago`;
    }
    const hours = Math.round(minutes / 60);
    if (hours < 24) {
        return `${hours}h ago`;
    }
    const days = Math.round(hours / 24);
    return days < 30 ? `${days}d ago` : `${Math.round(days / 30)}mo ago`;
}

function fmtNumber(value, digits = 0) {
    const num = Number(value) || 0;
    return num.toLocaleString(undefined, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
    });
}

export class NovaAiStudio extends Component {
    static template = "nova_ai.NovaAiStudio";

    props = useProps(standardActionServiceProps);

    setup() {
        this.action = useService("action");
        this.orm = useService("orm");
        this._data = signal(null);
        this._section = signal("overview");
        this._loading = signal(true);
        this._error = signal("");
        this._notice = signal("");
        this._busy = signal("");
        this._query = signal("");
        this._filter = signal("all");
        this._selected = signal(null);
        this._composer = signal(false);
        this._draft = signal({ name: "", description: "", body: "", kind: "memory" });
        this._skillQuery = signal("");
        this._skillRows = signal([]);
        this._requires = signal([]);
        this._routing = signal(null);
        this._routingBusy = signal(false);
        // The flow editor's working copy. Null when no flow is being
        // edited; a plain object otherwise, so every input binds to
        // the working copy and the saved flow only changes on Save.
        this._editing = signal(null);
        this._editProblems = signal([]);
        this._editError = signal("");
        // The run-arguments sheet: which inputs a flow run needs.
        this._running = signal(null);
        this._runError = signal("");
        onMounted(() => this.load());
    }

    // ---------------------------------------------------------------- data
    async load() {
        try {
            this._loading.set(true);
            this._error.set("");
            const payload = await rpc("/nova/ai/studio", {});
            this._data.set(payload || {});
            if (!this._routing()) {
                await this.refreshRouting();
            }
        } catch (err) {
            this._error.set(
                err && err.data && err.data.message
                    ? err.data.message
                    : _t("The Studio could not be loaded."));
        } finally {
            this._loading.set(false);
        }
    }

    async refresh() {
        await this.load();
        this._notice.set(_t("Refreshed."));
    }

    // ------------------------------------------------------------- getters
    get payload() {
        try {
            return this._data() || {};
        } catch {
            return {};
        }
    }

    get agent() {
        return this.payload.agent || {};
    }

    get stats() {
        return this.payload.stats || {};
    }

    get sections() {
        const stats = this.stats;
        const counts = {
            overview: 0,
            brain: stats.concepts || 0,
            proposals: stats.proposals || 0,
            flows: stats.flows || 0,
            tools: stats.tools || 0,
            routing: (this.payload.models || []).length,
            activity: (this.payload.activity || []).length,
        };
        return SECTIONS.map((section) => ({
            ...section,
            count: counts[section.id] || 0,
            // A zero badge on every section is noise: only the ones with
            // something to say get a number.
            show: counts[section.id] > 0,
            alert: section.id === "proposals" && (stats.proposals || 0) > 0,
        }));
    }

    get knowledge() {
        return this.payload.knowledge || [];
    }

    get flows() {
        return this.payload.flows || [];
    }

    get tools() {
        return this.payload.tools || [];
    }

    get models() {
        return this.payload.models || [];
    }

    get activity() {
        return this.payload.activity || [];
    }

    get providers() {
        return this.payload.providers || [];
    }

    get proposals() {
        return this.knowledge.filter((k) => k.proposed);
    }

    get staleConcepts() {
        return this.knowledge.filter((k) => k.stale);
    }

    get filteredKnowledge() {
        const query = (this._query() || "").trim().toLowerCase();
        const filter = this._filter();
        return this.knowledge.filter((item) => {
            if (filter === "skills" && item.kind !== "skill") {
                return false;
            }
            if (filter === "memory" && item.kind !== "memory") {
                return false;
            }
            if (filter === "wiki" && item.kind !== "wiki") {
                return false;
            }
            if (filter === "reference" && item.kind !== "reference") {
                return false;
            }
            if (filter === "proposed" && !item.proposed) {
                return false;
            }
            if (filter === "stale" && !item.stale) {
                return false;
            }
            if (filter === "trusted" && item.trust !== "human-reviewed") {
                return false;
            }
            if (!query) {
                return true;
            }
            return (
                (item.name || "").toLowerCase().includes(query) ||
                (item.code || "").toLowerCase().includes(query) ||
                (item.description || "").toLowerCase().includes(query)
            );
        });
    }

    get selected() {
        try {
            return this._selected();
        } catch {
            return null;
        }
    }

    get isAdmin() {
        // The server enforces this too; the UI just stops offering the
        // actions that would be refused.
        return !!(this.payload.is_admin);
    }

    get routingRows() {
        const routing = this._routing();
        let rows;
        if (routing && Array.isArray(routing.models)) {
            rows = routing.models;
        } else {
            rows = this.models.map((m) => ({
                id: m.id,
                name: m.name,
                tier: m.tier,
                capabilities: m.capabilities || [],
                missing: [],
                eligible: true,
                cost_in: m.cost_in,
                cost_out: m.cost_out,
                reliability: m.reliability,
            }));
        }
        // The table is the routing table, so it must lead with what can
        // do the job: eligible models first, then the rest by name.
        return [...rows].sort((a, b) => {
            if (a.eligible !== b.eligible) {
                return a.eligible ? -1 : 1;
            }
            return String(a.name || "").localeCompare(String(b.name || ""));
        });
    }

    get chosenName() {
        const routing = this._routing();
        return (routing && routing.chosen_name) || "";
    }

    get capabilities() {
        return CAPABILITIES;
    }

    // ------------------------------------------------------------- actions
    setSection(id) {
        this._section.set(id);
        if (id === "brain" || id === "proposals") {
            this._selected.set(null);
        }
    }

    setQuery(ev) {
        this._query.set((ev && ev.target && ev.target.value) || "");
    }

    setFilter(filter) {
        this._filter.set(filter);
    }

    openConcept(concept) {
        this._selected.set(concept);
    }

    closeConcept() {
        this._selected.set(null);
    }

    async reviewConcept(concept, action) {
        if (!concept) {
            return;
        }
        this._busy.set(`knowledge:${concept.id}`);
        try {
            const res = await rpc("/nova/ai/studio/knowledge", {
                action: action,
                knowledge_id: concept.id,
            });
            if (res && res.ok) {
                this._notice.set(
                    action === "reject"
                        ? _t("Proposal rejected.")
                        : action === "accept"
                            ? _t("Proposal accepted — the agent may use it now.")
                            : _t("Marked as reviewed."));
                await this.load();
            } else {
                this._error.set((res && res.error) || _t("That did not work."));
            }
        } catch (err) {
            this._error.set(
                err && err.data && err.data.message
                    ? err.data.message
                    : _t("That did not work."));
        } finally {
            this._busy.set("");
        }
    }

    // ---- the concept composer -----------------------------------------
    openComposer() {
        this._draft.set({ name: "", description: "", body: "", kind: "memory" });
        this._composer.set(true);
    }

    closeComposer() {
        this._composer.set(false);
    }

    setDraftField(field, ev) {
        const current = this._draft();
        this._draft.set({
            ...current,
            [field]: (ev && ev.target && ev.target.value) || "",
        });
    }

    setDraftKind(ev) {
        const current = this._draft();
        this._draft.set({
            ...current,
            kind: (ev && ev.target && ev.target.value) || "memory",
        });
    }

    async saveConcept() {        const draft = this._draft();
        if (!draft.name.trim() || !draft.body.trim()) {
            this._error.set(_t("A concept needs a title and a body."));
            return;
        }
        this._busy.set("compose");
        try {
            const res = await rpc("/nova/ai/studio/knowledge", {
                action: "write",
                name: draft.name,
                description: draft.description,
                body: draft.body,
                kind: draft.kind,
            });
            if (res && res.ok) {
                this._composer.set(false);
                this._notice.set(_t("Concept saved."));
                await this.load();
            } else {
                this._error.set((res && res.error) || _t("That did not work."));
            }
        } catch (err) {
            this._error.set(
                err && err.data && err.data.message
                    ? err.data.message
                    : _t("That did not work."));
        } finally {
            this._busy.set("");
        }
    }

    // ---- codebase scan ------------------------------------------------
    // One click fills the brain from the deployment's own code: one
    // concept per model, with the file as its source. Admin only -
    // the server enforces it too.
    async scanCodebase() {
        this._busy.set("scan");
        try {
            this._error.set("");
            const res = await rpc("/nova/ai/studio/knowledge", {
                action: "scan",
            });
            if (res && res.ok) {
                this._notice.set(
                    `${res.files} file(s) scanned: ${res.created} new concept(s), ${res.skipped} already known.`
                    + (res.truncated ? " (capped at 200 models)" : ""));
                await this.load();
            } else {
                this._error.set((res && res.error) || _t("That did not work."));
            }
        } catch (err) {
            this._error.set(
                err && err.data && err.data.message
                    ? err.data.message
                    : _t("That did not work."));
        } finally {
            this._busy.set("");
        }
    }

    // ---- flows ----------------------------------------------------------
    // Running a flow that asks for inputs opens the arguments sheet
    // first: a variable left empty becomes an empty string in the
    // domain, which silently returns the wrong rows. Better to ask.
    flowStartVars(flow) {
        try {
            const first = (flow && flow.steps && flow.steps[0]) || null;
            if (!first || first.type !== "start") {
                return [];
            }
            const config = first.config || {};
            return (config.variables || []).filter((v) => v && v.name);
        } catch (e) {
            return [];
        }
    }

    async runFlow(flow) {
        if (!flow) {
            return;
        }
        const needed = this.flowStartVars(flow);
        if (needed.length) {
            this._running.set({
                id: flow.id,
                name: flow.name || "",
                vars: needed.map((v) => ({
                    name: v.name,
                    description: v.description || "",
                    required: (v.type || "") !== "optional",
                    value: (v.type || "") === "optional" && v.value != null
                        ? String(v.value) : "",
                })),
            });
            this._runError.set("");
            return;
        }
        await this.executeFlow(flow.id, {});
    }

    setRunArg(index, ev) {
        const run = this._running();
        if (!run || !run.vars[index]) {
            return;
        }
        const next = run.vars.slice();
        next[index] = {
            ...next[index],
            value: (ev && ev.target && ev.target.value) || "",
        };
        this._running.set({ ...run, vars: next });
    }

    closeRun() {
        try {
            this._running.set(null);
            this._runError.set("");
        } catch (e) {
            // sheet stays put
        }
    }

    async confirmRunFlow() {
        const run = this._running();
        if (!run) {
            return;
        }
        for (const v of run.vars) {
            if (v.required && !(v.value || "").trim()) {
                this._runError.set(_t("“%s” is required.", v.name));
                return;
            }
        }
        const args = {};
        for (const v of run.vars) {
            args[v.name] = (v.value || "").trim();
        }
        this.closeRun();
        await this.executeFlow(run.id, args);
    }

    async executeFlow(flowId, args) {
        this._busy.set(`flow:${flowId}`);
        try {
            const res = await rpc("/nova/ai/studio/flow", {
                action: "run",
                flow_id: flowId,
                arguments: args || {},
            });
            if (res && res.ok) {
                this._notice.set(
                    res.needs_approval
                        ? res.message || _t("The flow reached a write step.")
                        : _t("Flow finished."));
            } else {
                this._error.set((res && res.error) || _t("The flow failed."));
            }
            await this.load();
        } catch (err) {
            this._error.set(
                err && err.data && err.data.message
                    ? err.data.message
                    : _t("The flow failed."));
        } finally {
            this._busy.set("");
        }
    }

    // ---- the flow editor -----------------------------------------------
    //
    // A flow is edited as a working copy: every input binds to
    // `_editing`, and the saved flow only changes on Save. JSON
    // blobs (a domain, create values, tool args) are edited as
    // text and parsed on save, with the step number in the error.
    // Anything the form cannot express stays out; the Odoo form
    // keeps the raw steps for the rest.
    flowStepTypes() {
        return [
            { id: "start", label: "Ask for inputs" },
            { id: "search", label: "Search records" },
            { id: "read", label: "Read a record" },
            { id: "create", label: "Queue a new record" },
            { id: "tool", label: "Run a tool" },
            { id: "agent", label: "Ask another agent" },
            { id: "note", label: "Post a note" },
            { id: "output", label: "Return the result" },
        ];
    }

    workingStepBlank(type) {
        // A new step, with the fields its type edits. Defaults are
        // the common case, not an empty shell the model must guess.
        const step = { type: type, result: "", direct: false };
        if (type === "start") {
            step.variables = [];
        } else if (type === "search") {
            step.model = "res.partner";
            step.domainText = "[]";
            step.fieldsText = "";
            step.limitText = "10";
            step.orderText = "";
            step.offsetText = "";
        } else if (type === "read") {
            step.model = "res.partner";
            step.resIdText = "";
        } else if (type === "create") {
            step.model = "res.partner";
            step.valuesText = "{}";
        } else if (type === "tool") {
            step.tool = "search_records";
            step.argsText = "{}";
        } else if (type === "agent") {
            step.agentId = 0;
            step.instruction = "";
        } else if (type === "note") {
            step.body = "";
        } else if (type === "output") {
            step.valueText = "";
        }
        return step;
    }

    stepToWorking(serverStep) {
        const config = (serverStep && serverStep.config) || {};
        const args = config.args || {};
        const w = this.workingStepBlank(serverStep.type || "");
        w.result = serverStep.result || "";
        w.direct = !!serverStep.direct;
        try {
            if (w.type === "start") {
                w.variables = (config.variables || []).map((v) => ({
                    name: (v && v.name) || "",
                    vartype: (v && v.type) || "required",
                    description: (v && v.description) || "",
                }));
            } else if (w.type === "search") {
                w.model = args.model || "";
                w.domainText = JSON.stringify(args.domain || []);
                w.fieldsText = (args.fields || []).join(", ");
                w.limitText = String(args.limit != null ? args.limit : 10);
                w.orderText = args.order || "";
                w.offsetText = args.offset != null ? String(args.offset) : "";
            } else if (w.type === "read") {
                w.model = args.model || "";
                w.resIdText = args.res_id != null ? String(args.res_id) : "";
            } else if (w.type === "create") {
                w.model = args.model || "";
                w.valuesText = JSON.stringify(args.values || {}, null, 1);
            } else if (w.type === "tool") {
                w.tool = config.tool || "";
                w.argsText = JSON.stringify(args || {}, null, 1);
            } else if (w.type === "agent") {
                w.agentId = Number(config.agent || 0);
                w.instruction = config.instruction || "";
            } else if (w.type === "note") {
                w.body = config.body || "";
            } else if (w.type === "output") {
                w.valueText = config.value != null ? String(config.value) : "";
            }
        } catch (e) {
            // A broken saved step still opens: the working copy
            // survives it, and Save reports what is wrong.
        }
        return w;
    }

    openEditor(flow) {
        if (!flow) {
            return;
        }
        this._editing.set({
            id: flow.id,
            name: flow.name || "",
            description: flow.description || "",
            active: flow.active !== false,
            steps: (flow.steps || []).map((s) => this.stepToWorking(s)),
        });
        this._editProblems.set(flow.problems || []);
        this._editError.set("");
    }

    closeEditor() {
        try {
            this._editing.set(null);
            this._editProblems.set([]);
            this._editError.set("");
        } catch {
            // the editor stays put
        }
    }

    async newFlow() {
        this._busy.set("flow:new");
        try {
            const res = await rpc("/nova/ai/studio/flow", {
                action: "create",
                arguments: { name: _t("Untitled flow") },
            });
            if (res && res.ok && res.flow) {
                await this.load();
                const fresh = (this.flows || []).find(
                    (f) => f.id === res.flow.id) || res.flow;
                this.openEditor(fresh);
            } else {
                this._error.set((res && res.error) || _t("That did not work."));
            }
        } catch (err) {
            this._error.set(
                err && err.data && err.data.message
                    ? err.data.message
                    : _t("That did not work."));
        } finally {
            this._busy.set("");
        }
    }

    addStep(type) {
        const ed = this._editing();
        if (!ed) {
            return;
        }
        const next = ed.steps.concat([this.workingStepBlank(type)]);
        this._editing.set({ ...ed, steps: next });
    }

    removeStep(index) {
        const ed = this._editing();
        if (!ed || index < 0 || index >= ed.steps.length) {
            return;
        }
        const next = ed.steps.slice(0, index).concat(ed.steps.slice(index + 1));
        this._editing.set({ ...ed, steps: next });
    }

    moveStep(index, dir) {
        const ed = this._editing();
        if (!ed) {
            return;
        }
        const other = index + dir;
        if (index < 0 || index >= ed.steps.length ||
                other < 0 || other >= ed.steps.length) {
            return;
        }
        const next = ed.steps.slice();
        const tmp = next[index];
        next[index] = next[other];
        next[other] = tmp;
        this._editing.set({ ...ed, steps: next });
    }

    setEditor(field, ev) {
        const ed = this._editing();
        if (!ed) {
            return;
        }
        this._editing.set({
            ...ed,
            [field]: (ev && ev.target && ev.target.value) || "",
        });
    }

    setStepField(index, field, ev) {
        const ed = this._editing();
        if (!ed || !ed.steps[index]) {
            return;
        }
        let value = (ev && ev.target && ev.target.value);
        if (value === undefined) {
            return;
        }
        const next = ed.steps.slice();
        next[index] = { ...next[index], [field]: value };
        this._editing.set({ ...ed, steps: next });
    }

    setStepCheck(index, field, ev) {
        const ed = this._editing();
        if (!ed || !ed.steps[index]) {
            return;
        }
        const next = ed.steps.slice();
        next[index] = {
            ...next[index],
            [field]: !!(ev && ev.target && ev.target.checked),
        };
        this._editing.set({ ...ed, steps: next });
    }

    setStepTool(index, ev) {
        const ed = this._editing();
        if (!ed || !ed.steps[index]) {
            return;
        }
        const next = ed.steps.slice();
        next[index] = {
            ...next[index],
            tool: (ev && ev.target && ev.target.value) || "",
        };
        this._editing.set({ ...ed, steps: next });
    }

    setEditorCheck(field, ev) {
        const ed = this._editing();
        if (!ed) {
            return;
        }
        this._editing.set({
            ...ed,
            [field]: !!(ev && ev.target && ev.target.checked),
        });
    }

    setStepType(index, ev) {
        const ed = this._editing();
        if (!ed || !ed.steps[index]) {
            return;
        }
        const type = (ev && ev.target && ev.target.value) || "";
        const next = ed.steps.slice();
        const blank = this.workingStepBlank(type);
        // The result wiring survives a type change; the config is new.
        blank.result = (next[index].result || "");
        blank.direct = !!next[index].direct;
        next[index] = blank;
        this._editing.set({ ...ed, steps: next });
    }

    setStepAgent(index, ev) {
        const ed = this._editing();
        if (!ed || !ed.steps[index]) {
            return;
        }
        let value = 0;
        try {
            value = Number((ev && ev.target && ev.target.value) || 0);
        } catch {
            value = 0;
        }
        const next = ed.steps.slice();
        next[index] = { ...next[index], agentId: value };
        this._editing.set({ ...ed, steps: next });
    }

    addFlowVar(index) {
        const ed = this._editing();
        if (!ed || !ed.steps[index]) {
            return;
        }
        const next = ed.steps.slice();
        const rows = (next[index].variables || []).concat([{
            name: "", vartype: "required", description: "",
        }]);
        next[index] = { ...next[index], variables: rows };
        this._editing.set({ ...ed, steps: next });
    }

    removeFlowVar(index, row) {
        const ed = this._editing();
        if (!ed || !ed.steps[index]) {
            return;
        }
        const next = ed.steps.slice();
        const rows = (next[index].variables || []).slice();
        rows.splice(row, 1);
        next[index] = { ...next[index], variables: rows };
        this._editing.set({ ...ed, steps: next });
    }

    setFlowVar(index, row, field, ev) {
        const ed = this._editing();
        if (!ed || !ed.steps[index]) {
            return;
        }
        const next = ed.steps.slice();
        const rows = (next[index].variables || []).slice();
        if (!rows[row]) {
            return;
        }
        rows[row] = {
            ...rows[row],
            [field]: (ev && ev.target && ev.target.value) || "",
        };
        next[index] = { ...next[index], variables: rows };
        this._editing.set({ ...ed, steps: next });
    }

    stepWorkingToServer(w, position) {
        // A working step back into the shape validate() and run()
        // understand. JSON text is parsed here so a broken bracket
        // names its step instead of its traceback.
        const n = position + 1;
        const config = {};
        if (w.type === "start") {
            config.variables = (w.variables || [])
                .filter((v) => (v.name || "").trim())
                .map((v) => ({
                    name: v.name.trim(),
                    type: v.vartype || "required",
                    description: v.description || "",
                }));
        } else if (w.type === "search") {
            let domain = [];
            try {
                domain = JSON.parse(w.domainText || "[]");
            } catch (e) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": the domain is not valid JSON.") };
            }
            if (!Array.isArray(domain)) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": the domain must be a list.") };
            }
            const fields = String(w.fieldsText || "").split(",")
                .map((s) => s.trim()).filter(Boolean);
            const limit = parseInt(w.limitText, 10);
            config.args = {
                model: (w.model || "").trim(),
                domain: domain,
                fields: fields,
                limit: Number.isNaN(limit) ? 10 : limit,
            };
            if ((w.orderText || "").trim()) {
                config.args.order = w.orderText.trim();
            }
            const offset = parseInt(w.offsetText, 10);
            if (!Number.isNaN(offset) && offset > 0) {
                config.args.offset = offset;
            }
            if (!config.args.model) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": pick a model.") };
            }
        } else if (w.type === "read") {
            const resId = parseInt(w.resIdText, 10);
            config.args = { model: (w.model || "").trim(), res_id: 0 };
            if (!config.args.model) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": pick a model.") };
            }
            if (!Number.isNaN(resId) && resId > 0) {
                config.args.res_id = resId;
            }
        } else if (w.type === "create") {
            let values = {};
            try {
                values = JSON.parse(w.valuesText || "{}");
            } catch (e) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": the values are not valid JSON.") };
            }
            if (values === null || typeof values !== "object" ||
                    Array.isArray(values)) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": the values must be an object.") };
            }
            config.args = { model: (w.model || "").trim(), values: values };
            if (!config.args.model) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": pick a model.") };
            }
        } else if (w.type === "tool") {
            let toolArgs = {};
            try {
                toolArgs = JSON.parse(w.argsText || "{}");
            } catch (e) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": the arguments are not valid JSON.") };
            }
            if (toolArgs === null || typeof toolArgs !== "object" ||
                    Array.isArray(toolArgs)) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": the arguments must be an object.") };
            }
            config.tool = (w.tool || "").trim();
            config.args = toolArgs;
            if (!config.tool) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": pick a tool.") };
            }
        } else if (w.type === "agent") {
            config.instruction = w.instruction || "";
            if (w.agentId) {
                config.agent = w.agentId;
            }
            if (!config.instruction.trim()) {
                return { ok: false, error: _t("Step ") + n +
                    _t(": the instruction is empty.") };
            }
        } else if (w.type === "note") {
            config.body = w.body || "";
        } else if (w.type === "output") {
            config.value = w.valueText != null ? String(w.valueText) : "";
        } else {
            return { ok: false, error: _t("Step ") + n +
                _t(": pick a step type first.") };
        }
        const step = { type: w.type, config: config };
        if ((w.result || "").trim()) {
            step.resultVariable = w.result.trim();
        }
        if (w.direct) {
            step.directOutput = true;
        }
        return { ok: true, step: step };
    }

    async saveFlow() {
        const ed = this._editing();
        if (!ed) {
            return;
        }
        if (!ed.name.trim()) {
            this._editError.set(_t("The flow needs a name."));
            return;
        }

        const steps = [];
        for (let i = 0; i < ed.steps.length; i++) {
            const built = this.stepWorkingToServer(ed.steps[i], i);
            if (!built.ok) {
                this._editError.set(built.error);
                return;
            }
            steps.push(built.step);
        }
        this._busy.set(`flow:${ed.id}`);
        try {
            this._editError.set("");
            const res = await rpc("/nova/ai/studio/flow", {
                action: "save",
                flow_id: ed.id,
                arguments: {
                    name: ed.name,
                    description: ed.description,
                    steps: steps,
                    active: ed.active !== false,
                },
            });
            if (res && res.ok) {
                this._editProblems.set(res.problems || []);
                await this.load();
                if (res.valid) {
                    this._notice.set(_t("Flow saved."));
                    this.closeEditor();
                } else {
                    this._notice.set(
                        _t("Saved, but the flow is red - fix the steps."));
                }
            } else {
                this._editError.set((res && res.error) ||
                    _t("That did not work."));
            }
        } catch (err) {
            this._editError.set(
                err && err.data && err.data.message
                    ? err.data.message
                    : _t("That did not work."));
        } finally {
            this._busy.set("");
        }
    }

    workingLabel(w, index) {
        // The timeline read for a working step: what it does, now.
        const n = index + 1;
        const type = (w && w.type) || "";
        if (type === "start") {
            const names = ((w.variables || []).map((v) => v.name)
                .filter(Boolean)).join(", ");
            return names ? `Ask for ${names}` : "Ask for the inputs";
        }
        if (type === "search") {
            return `Search ${(w.model || "records")}`;
        }
        if (type === "read") {
            return `Read ${w.model || "a record"}`;
        }
        if (type === "create") {
            return `Queue a new ${w.model || "record"}`;
        }
        if (type === "tool") {
            return `Run ${(w.tool || "a tool")}`;
        }
        if (type === "agent") {
            return "Ask the agent";
        }
        if (type === "note") {
            return "Post a note";
        }
        if (type === "output") {
            return "Return the result";
        }
        return `Step ${n} - pick a type`;
    }

    get editing() {
        try {
            return this._editing();
        } catch {
            return null;
        }
    }

    get agentsList() {
        return this.payload.agents || [];
    }

    // ---- the router simulator -------------------------------------------
    setSkillQuery(ev) {
        this._skillQuery.set((ev && ev.target && ev.target.value) || "");
    }

    onSkillKey(ev) {
        // Enter runs the simulation: the operator types a request and
        // sees which skill the router would fire.
        if (!ev || ev.key !== "Enter") {
            return;
        }
        ev.preventDefault();
        this.explainSkills();
    }

    async explainSkills() {
        const query = (this._skillQuery() || "").trim();
        if (!query) {
            this._skillRows.set([]);
            return;
        }
        this._routingBusy.set(true);
        try {
            const res = await rpc("/nova/ai/studio/skill", {
                action: "explain",
                query: query,
            });
            this._skillRows.set((res && res.skills) || []);
        } catch (err) {
            this._error.set(
                err && err.data && err.data.message
                    ? err.data.message
                    : _t("The router could not be explained."));
        } finally {
            this._routingBusy.set(false);
        }
    }

    toggleRequirement(capability) {
        const current = this._requires();
        const next = current.includes(capability)
            ? current.filter((c) => c !== capability)
            : [...current, capability];
        this._requires.set(next);
        this.refreshRouting();
    }

    async refreshRouting() {
        this._routingBusy.set(true);
        try {
            const res = await rpc("/nova/ai/studio/routing", {
                requires: this._requires(),
            });
            this._routing.set(res || null);
        } catch (err) {
            this._error.set(
                err && err.data && err.data.message
                    ? err.data.message
                    : _t("The routing table could not be read."));
        } finally {
            this._routingBusy.set(false);
        }
    }

    // ---- cross-links -----------------------------------------------------
    openRecord(model, resId) {
        if (!model || !resId) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: resId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openKnowledge() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "nova.ai.knowledge",
            views: [
                [false, "kanban"],
                [false, "list"],
                [false, "form"],
            ],
            target: "current",
        });
    }

    openFlows() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "nova.ai.flow",
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    openTools() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "nova.ai.tool",
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    openModels() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "nova.ai.model",
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    openLogs() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "nova.ai.log",
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    // Ask the drawer to open with this question: the Studio is the
    // control room, the drawer is the conversation.
    askNova(text) {
        const question = (text || "").trim();
        try {
            window.dispatchEvent(
                new CustomEvent("nova:ai-open", {
                    detail: { message: question, agentId: this.agent.id },
                })
            );
        } catch {
            // the drawer listens on window; if that failed, nothing
            // breaks — the operator can open it by hand.
        }
    }

    // -------------------------------------------------------------- helpers
    kindLabel(kind) {
        return KIND_LABELS[kind] || kind || "";
    }

    trustLabel(trust) {
        return TRUST_LABELS[trust] || trust || "";
    }

    tierLabel(tier) {
        return { t1: "T1 Basic", t2: "T2 Assistant", t3: "T3 Agent" }[tier] || "";
    }

    tierClass(tier) {
        return { t1: "nova_tier-t1", t2: "nova_tier-t2", t3: "nova_tier-t3" }[
            tier
        ] || "nova_tier-t1";
    }

    stepIcon(type) {
        return STEP_ICONS[type] || "·";
    }

    markdown(text) {
        return markup(renderMarkdown(text || ""));
    }

    age(value) {
        return fmtTime(value);
    }

    num(value, digits) {
        return fmtNumber(value, digits);
    }

    percent(value) {
        return `${Math.round((Number(value) || 0) * 100)}%`;
    }

    latency(value) {
        const ms = Number(value) || 0;
        if (!ms) {
            return "—";
        }
        return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
    }

    reliabilityClass(value) {
        const score = Number(value) || 0;
        if (score >= 0.8) {
            return "nova_studio_rel nova_studio_rel_high";
        }
        if (score >= 0.5) {
            return "nova_studio_rel nova_studio_rel-mid";
        }
        return "nova_studio_rel nova_studio_rel-low";
    }

    costPair(model) {
        // A model with no price on file is not "0.00": an em dash says
        // "not priced" honestly, and a local model is genuinely free.
        const inCost = Number(model.cost_in) || 0;
        const outCost = Number(model.cost_out) || 0;
        if (!inCost && !outCost) {
            return "—";
        }
        return `${fmtNumber(inCost, 2)} / ${fmtNumber(outCost, 2)}`;
    }

    get busyConcept() {
        try {
            return this._busy();
        } catch {
            return "";
        }
    }
}

registry.category("actions").add("nova_ai_studio", NovaAiStudio);
