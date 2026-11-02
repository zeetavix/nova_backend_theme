# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Agent flows — a saved, multi-step plan the agent can run in one call.

Ported from AnythingLLM's flow system (MIT) and re-shaped for Odoo:
a flow is an ordered list of steps, each step is a block with a type and
a config, and blocks pass values to each other through ``${variable}``
placeholders. What we keep from the reference and what we change:

* **Kept** — the block list shape, ``${var}`` interpolation, a
  ``resultVariable`` per step, a ``start`` block declaring the typed
  inputs, and a ``directOutput`` short-circuit.
* **Changed** — a step that creates or changes a record queues the
  normal Nova confirmation card instead of executing on the spot. A
  flow never writes behind the user's back; the whole point of the
  card system is that the user presses Approve.
* **Changed** — the interpreter is server-side and typed. AnythingLLM
  lets a flow call an arbitrary URL; here a step can only be a tool
  this agent is allowed to use, so a flow cannot become a privilege
  escalation.
"""

import re

from odoo import _, api, fields, models

from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
    TRIGGER_TYPES, graph_to_steps, steps_to_graph, validate_graph,
)

# Block types. The interpreter rejects anything else.
STEP_TYPES = [
    ("start", "Start"),
    ("search", "Search records"),
    ("read", "Read a record"),
    ("tool", "Run a tool"),
    ("agent", "Ask another agent"),
    ("create", "Queue a new record"),
    ("note", "Post a note"),
    ("output", "Return a value"),
]

_VAR_RE = re.compile(r"\$\{([^}]+)\}")

# Tools a flow may never call, whatever the agent is allowed to do:
# a flow runs unattended, so it must not be able to delete or to run
# arbitrary business methods.
_FORBIDDEN_IN_FLOW = ("unlink_record", "execute_method", "read_attachment")


def _resolve_path(data, path):
    """Resolve ``a.b[0].c`` against a dict/list structure.

    Brackets are consumed left to right; a segment fully consumed by
    brackets (``rows[0]``) must NOT fall through to the dict lookup
    below, or every indexed path resolves to nothing.
    """
    cur = data
    for part in re.split(r"\.(?![^\[]*\])", path):
        if not part:
            continue
        while "[" in part:
            name, _, rest = part.partition("[")
            if name:
                if not isinstance(cur, dict) or name not in cur:
                    return None
                cur = cur[name]
            idx, _, part = rest.partition("]")
            try:
                cur = cur[int(idx)]
            except (ValueError, TypeError, IndexError, KeyError):
                return None
        if not part:
            # Brackets consumed the whole segment: the value is
            # already in hand, there is nothing left to look up.
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit():
            try:
                cur = cur[int(part)]
            except (IndexError, ValueError):
                return None
        else:
            return None
    return cur


def _interp(value, variables):
    """Replace ``${path}`` placeholders.

    Unknown paths stay as written (``${path}``) instead of becoming
    empty: an empty string silently turns ``ilike`` into match-all and
    hides the typo, while a leftover placeholder fails loudly in
    domain validation or shows honestly in an output step.
    """
    if isinstance(value, str):
        def sub(match):
            found = _resolve_path(variables, match.group(1))
            if found is None:
                return match.group(0)
            if isinstance(found, (dict, list)):
                import json
                return json.dumps(found, default=str)
            return str(found)
        return _VAR_RE.sub(sub, value)
    if isinstance(value, list):
        return [_interp(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: _interp(v, variables) for k, v in value.items()}
    return value


def _config_strings(value):
    """Every string inside a step config, however deeply nested.

    Forward references hide in domain lists and arg dicts, not just in
    top-level strings — validation must look through the structure.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _config_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _config_strings(item)


class NovaAiFlow(models.Model):
    _name = "nova.ai.flow"
    _description = "Nova AI Flow"
    _order = "name"
    # The form carries a chatter: mail.thread gives the Discuss thread
    # API the webclient demands (_get_thread_with_access). Without it,
    # opening a flow 500s in mail's store (seen live on /web).
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True)
    description = fields.Text(
        help="What this flow does. Shown to the agent when it lists "
             "flows, so keep it one line.")
    agent_id = fields.Many2one(
        "nova.ai.agent", ondelete="cascade",
        help="Agent that owns the steps. A flow may only use tools that "
             "agent is allowed to use.")
    steps = fields.Json(
        help="Ordered list of steps: [{type, config, resultVariable, "
             "directOutput}]. A start step declares the input variables. "
             "An empty flow is allowed to be saved — validate() is what "
             "reports it as unfinished, not the database.")
    # n8n-class DAG. ``steps`` stays the source of truth for migrated
    # flows; ``nodes``/``edges`` take over as soon as the canvas saves.
    nodes = fields.Json(
        help="Graph nodes: [{id, type, name, config, position}].")
    edges = fields.Json(
        help="Graph edges: [{id, source, target, source_handle}].")
    spec_version = fields.Integer(
        default=2, help="1 = legacy linear steps, 2 = node graph.")
    version = fields.Integer(
        default=1, help="Bumped on every canvas save; runs pin it.")
    trigger_type = fields.Selection(
        TRIGGER_TYPES, default="manual",
        help="How this flow starts: by hand, from chat, webhook, "
             "schedule or a record event.")
    trigger_config = fields.Json(
        help="Trigger options: webhook path, cron, record model/event/"
             "domain, chat phrases.")
    settings = fields.Json(
        help="Workflow settings: {executionOrder, timezone, "
             "errorWorkflow, retentionDays}. Validated like n8n's "
             "workflow settings block.")
    webhook_token = fields.Char(
        copy=False, index=True,
        groups="nova_ai.group_nova_ai_admin",
        help="Secret path segment for the webhook trigger. "
             "Auto-generated on first use.")
    webhook_url = fields.Char(
        compute="_compute_webhook_url",
        help="Public URL for the webhook trigger (token included).")
    run_count = fields.Integer(compute="_compute_run_stats")
    last_run_at = fields.Datetime(compute="_compute_run_stats")
    last_status = fields.Char(compute="_compute_run_stats")
    active = fields.Boolean(default=True)
    use_count = fields.Integer(default=0, readonly=True)

    _name_uniq = models.Constraint(
        "UNIQUE(name)", "One flow per name.")

    def _default_variables(self):
        """Variables every flow starts with: the user's today.

        Calendar-style steps need real datetimes, and asking the user
        for "today" on every run is noise — so today and its day
        bounds are pre-seeded. A start step declaring the same name
        still wins (explicit input beats the default).
        """
        from odoo import fields as _fields
        from datetime import datetime as _dt, time as _time
        try:
            today = _fields.Date.context_today(self)
        except Exception:
            today = _fields.Date.today()
        try:
            start = _dt.combine(today, _time.min).strftime(
                "%Y-%m-%d %H:%M:%S")
            end = _dt.combine(today, _time.max).strftime(
                "%Y-%m-%d %H:%M:%S")
        except Exception:
            start = end = _fields.Date.to_string(today)
        return {
            "today": _fields.Date.to_string(today),
            "today_start": start,
            "today_end": end,
        }

    # ---------------------------------------------------------------
    # graph helpers (n8n-class DAG, backward compatible)
    # ---------------------------------------------------------------
    def _graph(self):
        """Nodes+edges, compiling legacy steps when the canvas never saved."""
        self.ensure_one()
        nodes = self.nodes or []
        edges = self.edges or []
        if (not nodes) and self.steps:
            try:
                nodes, edges = steps_to_graph(self.steps or [])
            except Exception:
                nodes, edges = [], []
        return nodes, edges

    def _trigger_dict(self):
        self.ensure_one()
        trig = {"type": self.trigger_type or "manual",
                "config": self.trigger_config or {}}
        if trig["type"] == "manual" and (self.nodes or self.edges):
            return trig
        return trig

    @api.model_create_multi
    def create(self, vals_list):
        import secrets as _secrets
        for vals in vals_list:
            try:
                if (vals.get("trigger_type") == "webhook" and not vals.get("webhook_token")):
                    vals["webhook_token"] = _secrets.token_urlsafe(24)
            except Exception:
                pass
        recs = super().create(vals_list)
        return recs

    def write(self, vals):
        import secrets as _secrets
        try:
            if vals.get("trigger_type") == "webhook":
                for rec in self:
                    if not rec.webhook_token and not vals.get("webhook_token"):
                        rec.sudo().write({"webhook_token": _secrets.token_urlsafe(24)})
        except Exception:
            pass
        return super().write(vals)

    def _ensure_webhook_token(self):
        import secrets as _secrets
        for flow in self:
            if not flow.webhook_token:
                flow.sudo().write({
                    "webhook_token": _secrets.token_urlsafe(24)})

    @api.depends("webhook_token", "trigger_type")
    def _compute_webhook_url(self):
        base = ""
        try:
            base = self.env["ir.config_parameter"].sudo().get_str(
                "web.base.url") or ""
        except Exception:
            base = ""
        for flow in self:
            if flow.trigger_type == "webhook" and flow.webhook_token:
                flow.webhook_url = "%s/nova/ai/flow/hook/%s" % (
                    (base or "").rstrip("/"), flow.webhook_token)
            else:
                flow.webhook_url = ""

    def _compute_run_stats(self):
        try:
            _ids = [f.id for f in self if f.id]
            _counts = {}
            if _ids:
                try:
                    _groups = self.env["nova.ai.flow.run"].read_group(
                        [("flow_id", "in", _ids)], ["flow_id"], ["flow_id"])
                    for _g in _groups:
                        _fid = _g["flow_id"][0] if isinstance(_g["flow_id"], (list, tuple)) else _g["flow_id"]
                        _counts[_fid] = _g["flow_id_count"]
                except Exception:
                    pass
            for flow in self:
                try:
                    flow.run_count = _counts.get(flow.id, 0) if flow.id in _counts else self.env["nova.ai.flow.run"].search_count([("flow_id", "=", flow.id)])
                    _last = self.env["nova.ai.flow.run"].search([("flow_id", "=", flow.id)], order="id desc", limit=1)
                    flow.last_run_at = _last.started_at if _last else False
                    flow.last_status = _last.status if _last else ""
                except Exception:
                    flow.run_count = 0
                    flow.last_run_at = False
                    flow.last_status = ""
        except Exception:
            pass

    def ensure_graph_from_steps(self):
        """One-time migration: materialise nodes/edges from steps."""
        for flow in self:
            try:
                flow.check_access("write")
            except Exception:
                continue
            if (not flow.nodes) and flow.steps:
                try:
                    nodes, edges = steps_to_graph(flow.steps or [])
                except Exception:
                    continue
                flow.sudo().write({
                    "nodes": nodes, "edges": edges, "spec_version": 2,
                    "version": (flow.version or 1) + 1})

    def action_graph_from_steps(self):
        self.ensure_graph_from_steps()
        return True

    def export_json(self):
        """Portable flow definition for copy/paste between databases."""
        self.ensure_one()
        nodes, edges = self._graph()
        import json as _json
        return _json.dumps({
            "name": self.name, "description": self.description or "",
            "trigger": self._trigger_dict(),
            "nodes": nodes, "edges": edges,
            "spec_version": 2, "version": self.version or 1,
        }, default=str, indent=2)

    # ---------------------------------------------------------------
    # n8n import / export — the exact n8n workflow pattern
    # ---------------------------------------------------------------
    def export_n8n(self):
        """This flow as n8n workflow JSON (paste straight into n8n)."""
        self.ensure_one()
        from odoo.addons.nova_ai.models import nova_ai_flow_n8n as n8n
        nodes, edges = self._graph()
        import json as _json
        return _json.dumps(
            n8n.to_n8n(self.name, nodes, edges), default=str, indent=2)

    def import_n8n(self, payload):
        """Replace this flow's graph from n8n workflow JSON.

        Unknown n8n node types import as flagged passthrough nodes —
        nothing is dropped silently, and validate() names them.
        """
        self.ensure_one()
        try:
            self.check_access("write")
        except Exception as _e:
            from odoo.exceptions import AccessError as _AE2
            raise _AE2("You may not edit this flow.")
        from odoo.addons.nova_ai.models import nova_ai_flow_n8n as n8n
        from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
            graph_to_steps)
        nodes, edges, trigger, unsupported = n8n.from_n8n(payload)
        try:
            steps = graph_to_steps(nodes, edges)
        except Exception:
            steps = self.steps or []
        self.sudo().write({
            "nodes": nodes, "edges": edges,
            "steps": steps, "spec_version": 2,
            "trigger_type": (trigger or {}).get("type") or "manual",
            "trigger_config": (trigger or {}).get("config") or {},
            "version": (self.version or 1) + 1,
        })
        return unsupported

    def action_rotate_webhook(self):
        import secrets as _secrets
        if not self.env.user.has_group("nova_ai.group_nova_ai_admin"):
            from odoo.exceptions import AccessError
            raise AccessError("Only flow admins may rotate webhook tokens.")
        for flow in self:
            flow.sudo().write({
                "webhook_token": _secrets.token_urlsafe(24)})
        return True

    def action_open_canvas(self):
        """Open the visual DAG canvas for this flow."""
        self.ensure_one()
        self._ensure_webhook_token()
        return {
            "type": "ir.actions.client",
            "tag": "nova_flow_canvas",
            "name": "Flow canvas: %s" % self.name,
            "params": {"flow_id": self.id},
        }

    def action_open_runs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Runs: %s" % self.name,
            "res_model": "nova.ai.flow.run",
            "view_mode": "list,form",
            "domain": [("flow_id", "=", self.id)],
            "context": {"default_flow_id": self.id},
        }

    # ---------------------------------------------------------------
    # crons (P073/P079/P080): schedules, wait resume, retention
    # ---------------------------------------------------------------
    @api.model
    def cron_run_schedules(self):
        """Run due schedule-trigger flows (claim-first, tz-aware)."""
        import datetime as _dt
        now_utc = fields.Datetime.now()
        for flow in self.search([
                ("active", "=", True),
                ("trigger_type", "=", "schedule")], limit=200):
            cfg = flow.trigger_config or {}
            settings = flow.settings or {}
            # TZ: settings.timezone or user TZ, else UTC.
            try:
                _tz = settings.get("timezone") or self.env.user.tz or "UTC"
                import pytz as _pytz
                _tzinfo = _pytz.timezone(_tz)
                now = now_utc.replace(tzinfo=_pytz.UTC).astimezone(_tzinfo).replace(tzinfo=None)
            except Exception:
                now = now_utc
            last = cfg.get("_last_run")
            due = False
            if cfg.get("interval_minutes") and cfg.get("cron"):
                # Both set: validator should refuse; run interval, log once.
                pass
            if cfg.get("interval_minutes"):
                try:
                    mins = max(1, int(cfg["interval_minutes"]))
                except (TypeError, ValueError):
                    continue
                if not last:
                    due = True
                else:
                    try:
                        prev = fields.Datetime.from_string(last)
                        due = (now_utc - prev).total_seconds() >= mins * 60
                    except Exception:
                        due = True
            elif cfg.get("cron"):
                # Cron expressions are matched once per minute: compare
                # the current minute stamp against the last run stamp.
                stamp = now.strftime("%Y-%m-%d %H:%M")
                due = last != stamp and self._cron_match(
                    cfg["cron"], now)
            if not due:
                continue
            # Claim first (overlap-safe): two workers can't both fire.
            try:
                _cfg = dict(flow.trigger_config or {})
                _cfg["_last_run"] = fields.Datetime.to_string(now_utc)
                flow.sudo().write({"trigger_config": _cfg})
            except Exception:
                continue
            try:
                _runner = flow.create_uid
                try:
                    if not _runner or not _runner.active:
                        _runner = self.env.user
                except Exception:
                    _runner = self.env.user
                flow.with_user(_runner).run_graph(
                    {"scheduled_at": fields.Datetime.to_string(now_utc)},
                    trigger_kind="schedule")
            except Exception as _e:
                try:
                    self.env["nova.ai.log"].sudo().create({
                        "message": "schedule failed for %s: %s" % (flow.name, str(_e)[:200]),
                    })
                except Exception:
                    pass
                continue
        return True

    @api.model
    def _cron_match(self, expr, now):
        """Minute/hour/dom/month/dow match (subset of cron)."""
        try:
            parts = str(expr).strip().split()
            if len(parts) != 5:
                return False
            cur = [now.minute, now.hour, now.day, now.month,
                   (now.weekday() + 1) % 7]
            bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
            for (part, val, (lo_b, hi_b)) in [(p, v, b) for p, v, b in zip(parts, cur, bounds)]:
                if part in ("*", "?"):
                    continue
                nums = set()
                for chunk in part.split(","):
                    if "/" in chunk:
                        base, step = chunk.split("/", 1)
                        step = max(1, int(step))
                        if base in ("*", ""):
                            lo, hi = lo_b, hi_b
                        else:
                            try:
                                lo = int(base)
                            except Exception:
                                return False
                            hi = hi_b
                        nums.update(range(lo, hi + 1, step))
                    elif "-" in chunk:
                        lo, hi = chunk.split("-", 1)
                        nums.update(range(int(lo), int(hi) + 1))
                    else:
                        nums.add(int(chunk))
                if val not in nums:
                    return False
            return True
        except Exception:
            return False

    @api.model
    def cron_trim_runs(self):
        """Retention (P080): per-flow retentionDays + batched deletes."""
        keep = 500
        try:
            keep = max(50, int(self.env["ir.config_parameter"].sudo(
            ).get_str("nova_ai.flow_run_keep") or 500))
        except Exception:
            keep = 500
        try:
            import datetime as _dt
            for flow in self.search([], limit=500):
                try:
                    days = 90
                    cfg = flow.settings or {}
                    if cfg.get("retentionDays"):
                        days = max(1, min(3650, int(cfg["retentionDays"])))
                    cutoff = fields.Datetime.to_string(
                        fields.Datetime.now() - _dt.timedelta(days=days))
                    # Age pass, batched.
                    for _ in range(5):
                        old = self.env["nova.ai.flow.run"].sudo().search([
                            ("flow_id", "=", flow.id),
                            ("started_at", "<", cutoff)], limit=500)
                        if not old:
                            break
                        for run in old:
                            try:
                                run.sudo().write({"inputs": {}, "outputs": {},
                                                  "node_inputs": {}, "node_outputs": {},
                                                  "node_log": []})
                            except Exception:
                                pass
                    # Count pass, batched deletes.
                    for _ in range(10):
                        excess = self.env["nova.ai.flow.run"].sudo().search(
                            [("flow_id", "=", flow.id)], order="id desc",
                            limit=500, offset=keep)
                        if not excess:
                            break
                        excess.unlink()
                except Exception:
                    continue
        except Exception:
            pass
        return True

    # ---------------------------------------------------------------
    # validation
    # ---------------------------------------------------------------
    def validate(self):
        """Return a list of problems. Empty means the flow may run."""
        problems = []
        for flow in self:
            steps = flow.steps or []
            if not isinstance(steps, list) or not steps:
                problems.append((flow.id, "a flow needs at least one step"))
                continue
            allowed = {t for t, _label in STEP_TYPES}
            seen_result = set()
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    problems.append((
                        flow.id, "step %d is not an object" % (index + 1)))
                    continue
                kind = step.get("type")
                if kind not in allowed:
                    problems.append((
                        flow.id,
                        "step %d has unknown type %r" % (index + 1, kind)))
                    continue
                config = step.get("config") or {}
                if not isinstance(config, dict):
                    problems.append((
                        flow.id,
                        "step %d has a malformed config" % (index + 1)))
                    continue
                if kind == "tool":
                    code = config.get("tool")
                    if not code:
                        problems.append((
                            flow.id,
                            "step %d names no tool" % (index + 1)))
                    elif code in _FORBIDDEN_IN_FLOW:
                        problems.append((
                            flow.id,
                            "step %d uses %s, which a flow may not run"
                            % (index + 1, code)))
                if kind == "agent":
                    if not config.get("agent") and not flow.agent_id:
                        problems.append((
                            flow.id,
                            "step %d names no agent, and the flow has no "
                            "agent of its own" % (index + 1)))
                result = step.get("resultVariable")
                if result:
                    if result in seen_result:
                        problems.append((
                            flow.id,
                            "resultVariable %r is set twice" % result))
                    seen_result.add(result)
        # A variable may only be read after the step that writes it.
        # The engine's own defaults (today, its bounds) are always
        # written before step one, and so are the start step's
        # declared inputs — the runner seeds them from the caller.
        written = {"today", "today_start", "today_end"}
        for step in steps:
            if not isinstance(step, dict) or \
                    step.get("type") != "start":
                continue
            for var in ((step.get("config") or {}).get("variables")
                        or []):
                if isinstance(var, dict) and var.get("name"):
                    written.add(var["name"])
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            config = step.get("config") or {}
            if not isinstance(config, dict):
                continue
            for text in _config_strings(config):
                for path in _VAR_RE.findall(text):
                    root = path.split(".")[0].split("[")[0]
                    if root and root not in written:
                        problems.append((
                            flow.id,
                            "step %d reads %r before anything "
                            "writes it" % (index + 1, root)))
            if step.get("resultVariable"):
                written.add(step["resultVariable"])
        return problems

    # ---------------------------------------------------------------
    # execution
    # ---------------------------------------------------------------
    def run_graph(self, arguments=None, trigger_kind="manual"):
        """Execute the node/edge DAG. Returns results + run id."""
        self.ensure_one()
        # Idempotency (P078): same key + flow within the window reuses
        # the earlier run instead of double-creating records.
        idem_key = (arguments or {}).get("$idempotency_key") or \
            (arguments or {}).get("idempotency_key")
        if idem_key:
            try:
                _ikey = str(idem_key)[:120]
                prior = self.env["nova.ai.flow.run"].sudo().search([
                    ("flow_id", "=", self.id),
                    ("idempotency_key", "=", _ikey),
                ], order="id desc", limit=1)
                if not prior:
                    # Back-compat: legacy runs stored the key only inside inputs.
                    _esc = _ikey.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    prior = self.env["nova.ai.flow.run"].sudo().search([
                        ("flow_id", "=", self.id),
                        ("inputs", "ilike", _esc),
                    ], order="id desc", limit=1)
                if prior and prior.status in ("ok", "waiting_approval"):
                    return {"ok": prior.status == "ok",
                            "results": prior.node_log or [],
                            "variables": prior.outputs or {},
                            "run_id": prior.id,
                            "idempotent": True,
                            "needs_approval":
                            prior.status == "waiting_approval"}
            except Exception:
                pass
        from odoo.addons.nova_ai.models.nova_ai_flow_engine import (
            FlowEngine)
        engine = FlowEngine(self.env)
        problems = engine.validate(self)
        if problems:
            self._log_run(arguments, trigger_kind, "error", {},
                          [], "; ".join(problems[:5]))
            return {"ok": False,
                    "error": "; ".join(problems[:5])}
        outcome = engine.run(self, arguments or {},
                             trigger_kind=trigger_kind)
        status = "waiting_approval" if outcome.get(
            "needs_approval") else ("ok" if outcome.get("ok") else "error")
        try:
            _lat = int(outcome.get("latency_ms") or 0)
        except Exception:
            _lat = 0
        run_id = self._log_run(
            arguments, trigger_kind, status,
            outcome.get("variables") or {},
            outcome.get("results") or [], outcome.get("error") or "",
            outcome.get("node_inputs"), outcome.get("node_outputs"),
            duration_ms=_lat,
            idempotency_key=str(idem_key)[:120] if idem_key else False,
            resume_scope=outcome.get("resume_scope"),
            resume_pending=outcome.get("resume_pending"),
            resume_visited=outcome.get("resume_visited"),
            paused_node=outcome.get("paused_nid") or False)
        outcome["run_id"] = run_id
        if outcome.get("ok") and not outcome.get("needs_approval"):
            self.sudo().write({"use_count": (self.use_count or 0) + 1})
        return outcome

    def resume_run(self, run_id):
        """Resume a waiting_approval run past its paused node."""
        self.ensure_one()
        run = self.env["nova.ai.flow.run"].sudo().browse(int(run_id)).exists()
        if not run or run.flow_id.id != self.id:
            return {"ok": False, "error": "run not found for this flow"}
        if run.status != "waiting_approval":
            return {"ok": False, "error": "run is not paused (status=%s)" % run.status}
        # Only the owner or an admin resumes: a paused scope holds PII.
        try:
            if run.user_id.id != self.env.user.id and not self.env.user.has_group("nova_ai.group_nova_ai_admin"):
                from odoo.exceptions import AccessError
                raise AccessError("Only the starter or a flow admin may resume.")
        except Exception as _e:
            from odoo.exceptions import AccessError as _AE
            if isinstance(_e, _AE):
                raise
        from odoo.addons.nova_ai.models.nova_ai_flow_engine import FlowEngine
        engine = FlowEngine(self.env)
        resume_state = {
            "scope": dict(run.resume_scope or run.outputs or {}),
            "pending": list(run.resume_pending or []),
            "visited": list(run.resume_visited or []),
            "paused_nid": run.paused_node,
            "results": list(run.node_log or []),
        }
        outcome = engine.run(self, dict(run.inputs or {}),
                             trigger_kind=run.trigger or "manual",
                             resume_state=resume_state)
        status = "waiting_approval" if outcome.get("needs_approval") else ("ok" if outcome.get("ok") else "error")
        try:
            _lat2 = int(outcome.get("latency_ms") or 0)
        except Exception:
            _lat2 = 0
        new_id = self._log_run(
            dict(run.inputs or {}), run.trigger or "manual", status,
            outcome.get("variables") or {}, outcome.get("results") or [],
            outcome.get("error") or "", outcome.get("node_inputs"),
            outcome.get("node_outputs"), duration_ms=_lat2,
            resume_scope=outcome.get("resume_scope"),
            resume_pending=outcome.get("resume_pending"),
            resume_visited=outcome.get("resume_visited"),
            paused_node=outcome.get("paused_nid") or False)
        try:
            run.sudo().write({"status": "ok" if outcome.get("ok") else run.status})
        except Exception:
            pass
        outcome["run_id"] = new_id
        return outcome

    def _log_run(self, arguments, trigger_kind, status, outputs,
                 node_log, error, node_inputs=None, node_outputs=None,
                 duration_ms=0, idempotency_key=False, resume_scope=None,
                 resume_pending=None, resume_visited=None, paused_node=False):
        try:
            capped = (node_log or [])[-60:]
            # Run-level byte budget: keep head (cause) + tail (effect).
            try:
                import json as _js
                _total = len(_js.dumps(capped, default=str)) + len(_js.dumps(node_outputs or {}, default=str))
                if _total > 500_000 and len(capped) > 20:
                    capped = (capped[:10] + [{"truncated": True, "note": "middle dropped for size"}] + capped[-10:])
            except Exception:
                pass
            vals = {
                "flow_id": self.id,
                "user_id": self.env.user.id,
                "trigger": trigger_kind or "manual",
                "status": status,
                "inputs": dict(arguments or {}),
                "outputs": outputs if isinstance(outputs, dict)
                else {"value": str(outputs)[:4000]},
                "node_log": capped,
                "error": (error or "")[:2000],
                "duration_ms": int(duration_ms or 0),
            }
            try:
                if idempotency_key:
                    vals["idempotency_key"] = idempotency_key
            except Exception:
                pass
            try:
                if resume_scope is not None:
                    vals["resume_scope"] = dict(resume_scope or {})
                if resume_pending is not None:
                    vals["resume_pending"] = list(resume_pending or [])
                if resume_visited is not None:
                    vals["resume_visited"] = list(resume_visited or [])
                if paused_node:
                    vals["paused_node"] = str(paused_node)[:120]
            except Exception:
                pass
            try:
                vals["node_inputs"] = dict(node_inputs or {})
                vals["node_outputs"] = dict(node_outputs or {})
            except Exception:
                pass
            rec = self.env["nova.ai.flow.run"].sudo().create(vals)
            return rec.id
        except Exception:
            return 0

    def _fire_record_trigger(self, records, event):
        """Called by the record-event hook: run matching record flows."""
        flows = self.search([
            ("active", "=", True), ("trigger_type", "=", "record")])
        for flow in flows:
            try:
                cfg = flow.trigger_config or {}
            except Exception:
                continue
            if cfg.get("model") and records \
                    and cfg["model"] != records._name:
                continue
            if cfg.get("event") and cfg["event"] not in (event, "all"):
                continue
            for record in records[:25]:
                try:
                    flow.with_user(
                        self.env.user).run_graph(
                            {"record_id": record.id,
                             "record_ids": [record.id],
                             "model": records._name,
                             "event": event},
                            trigger_kind="record")
                except Exception:
                    continue

    def run(self, arguments=None):
        """Execute the flow. Returns a dict with the steps and results.

        A step that would write queues a confirmation card instead of
        writing: the flow returns the card for the user to approve, and
        the flow stops there unless the user approves and runs it again.
        """
        self.ensure_one()
        # Graph flows (canvas saves) run on the DAG engine; legacy
        # linear flows keep the original interpreter untouched.
        if self.nodes:
            return self.run_graph(arguments or {}, trigger_kind="manual")
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool, CONFIRM_KINDS)
        depth = 0
        try:
            depth = int(self.env.context.get("_flow_depth") or 0)
        except (TypeError, ValueError):
            depth = 0
        if depth >= 3:
            # A flow that calls a flow that calls a flow: stop before
            # the stack (or the model bill) explodes. Flatten instead.
            return {
                "ok": False,
                "error": "Flow nested too deep (max 3): flatten the "
                         "plan instead of chaining flows.",
            }
        child_ctx = {"_flow_depth": depth + 1}
        problems = self.validate()
        if problems:
            return {
                "ok": False,
                "error": "; ".join(text for _id, text in problems),
            }
        variables = self._default_variables()
        steps = self.steps or []
        # A start step declares the inputs.
        for step in steps:
            if isinstance(step, dict) and step.get("type") == "start":
                config = step.get("config") or {}
                for var in (config.get("variables") or []):
                    if not isinstance(var, dict):
                        continue
                    name = var.get("name")
                    if not name:
                        continue
                    variables[name] = (arguments or {}).get(name)
        results = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            kind = step.get("type")
            config = step.get("config") or {}
            if not isinstance(config, dict):
                continue
            if kind == "start":
                continue
            if kind == "output":
                value = _interp(config.get("value"), variables)
                if step.get("resultVariable"):
                    variables[step["resultVariable"]] = value
                results.append({"step": index + 1, "type": kind,
                                "value": value})
                continue
            if kind == "agent":
                value = self._run_agent_step(config, variables)
                if step.get("resultVariable"):
                    variables[step["resultVariable"]] = value
                results.append({"step": index + 1, "type": kind,
                                "value": value})
                continue
            if kind == "note":
                value = _interp(config.get("body"), variables)
                results.append({"step": index + 1, "type": kind,
                                "value": value})
                continue
            # search / read / tool / create all go through the tool
            # layer, so a flow gets the same ACL, the same confirmation
            # cards and the same audit trail as the agent.
            code = config.get("tool") or {
                "search": "search_records",
                "read": "read_record",
                "create": "create_draft",
            }.get(kind)
            if not code:
                results.append({"step": index + 1, "type": kind,
                                "error": "no tool for this step"})
                continue
            if code in _FORBIDDEN_IN_FLOW:
                results.append({"step": index + 1, "type": kind,
                                "error": "%s may not run in a flow" % code})
                continue
            args = _interp(config.get("args") or {}, variables)
            try:
                tool_env, tool_args, kind_out, payload, card = \
                    self._agent_env(), args, None, None, None
                outcome = execute_tool(
                    tool_env, code, args, dict(child_ctx))
                out_kind = outcome[0] if outcome else "result"
                out_payload = outcome[1] if len(outcome) > 1 else {}
                out_card = outcome[2] if len(outcome) > 2 else None
                if step.get("resultVariable"):
                    variables[step["resultVariable"]] = out_payload
                results.append({"step": index + 1, "type": kind,
                                "tool": code, "result": out_payload})
                if out_kind == "confirm" and out_card:
                    # A write step queues its card and the flow stops:
                    # the user approves it, and the flow can be re-run.
                    results[-1]["card"] = out_card
                    return {
                        "ok": True,
                        "needs_approval": True,
                        "results": results,
                        "message": "The flow reached a write step. Approve "
                                   "the card, then run the flow again to "
                                   "continue.",
                    }
            except Exception as err:
                results.append({"step": index + 1, "type": kind,
                                "tool": code,
                                "error": str(err)[:200]})
                return {"ok": False, "results": results}
        self.sudo().write({"use_count": (self.use_count or 0) + 1})
        return {"ok": True, "results": results, "variables": variables}

    def action_run(self):
        """Run this flow from the form and show what happened."""
        self.ensure_one()
        outcome = self.run({})
        steps = outcome.get("results") or []
        if not outcome.get("ok"):
            message = outcome.get("error") or "The flow failed."
            self.env.user.notify(
                message=message, title="Flow failed", type="danger")
            return True
        if outcome.get("needs_approval"):
            self.env.user.notify(
                message=outcome.get("message") or "Approve the card.",
                title="Flow needs approval", type="warning")
            return True
        lines = []
        for step in steps:
            if step.get("error"):
                lines.append("Step %s: %s" % (step.get("step"), step["error"]))
            else:
                lines.append("Step %s (%s) ok" % (
                    step.get("step"), step.get("type")))
        self.env.user.notify(
            message="\n".join(lines) or "The flow finished.",
            title="Flow finished", type="success")
        return True

    def _agent_env(self):
        """The environment a flow step runs in: the invoking user's.

        A flow runs because a user pressed Run or asked the agent to
        run it, so it inherits their rights - never anyone else's.
        There is no agent user to borrow (agents have no login).
        """
        return self.env

    def _run_agent_step(self, config, variables):
        """Ask another agent and return its reply."""
        instruction = _interp(config.get("instruction") or "", variables)
        agent = None
        agent_id = config.get("agent")
        if agent_id:
            try:
                agent = self.env["nova.ai.agent"].browse(int(agent_id)).exists()
            except (TypeError, ValueError):
                agent = None
        if not agent:
            # No agent named: the flow's own agent does the work.
            agent = self.agent_id
        if not agent:
            return "Error: no agent to run this step."
        try:
            from odoo.addons.nova_ai.controllers.nova_ai import (
                run_chat_turn)
            session = self.env["nova.ai.session"].create({
                "agent_id": agent.id,
            })
            result = run_chat_turn(
                self.env, agent, session, instruction, {})
            return (result or {}).get("reply") or ""
        except Exception as err:
            return "Error: %s" % str(err)[:200]
