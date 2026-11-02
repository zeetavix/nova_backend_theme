# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Write-preparation tools: do the right thing BEFORE the card exists.

Ported from the strongest idea in the mcp-odoo reference server
(erpipe-org/mcp-odoo, MIT): a model that must hand-write an Odoo domain
and guess a payload will get both wrong, and a model that cannot check
its work will queue writes that fail at Approve.

Four tools, in Nova's idiom \u2014 nothing here writes:

* ``build_domain`` \u2014 structured conditions in, a validated domain out.
  The model stops hand-writing domain triplets; the server builds and
  validates them.
* ``validate_write`` \u2014 a dry run of a create or write against the
  LIVE field metadata: unknown fields, missing required fields, wrong
  types, and what the write would change. No card, no side effect.
* ``list_models`` \u2014 the models this agent may actually touch, with
  their labels. The agent is no longer stuck guessing model names.
* ``set_fields`` \u2014 write SEVERAL fields on one record in ONE card.
  One approval, one card, one undo. Chaining five ``set_field`` calls
  meant five cards, five approvals, and a half-applied record in
  between.
"""

from odoo import _
from odoo.exceptions import AccessError



def _helpers():
    """The shared helpers, pulled in lazily.

    nova_ai_tools imports THIS module to build its REGISTRY, so a
    module-level import back would be a cycle. The helpers are plain
    functions: fetching them per call costs a dict lookup.
    """
    from odoo.addons.nova_ai.models.nova_ai_tools import (
        _allowed_models, _check_settable, _chain_target,
        _coerce_field_value, _confirm_card, _denied, _model_allowed,
        _selection_options)
    return {
        "allowed_models": _allowed_models,
        "check_settable": _check_settable,
        "chain_target": _chain_target,
        "coerce": _coerce_field_value,
        "confirm_card": _confirm_card,
        "denied": _denied,
        "model_allowed": _model_allowed,
        "selection_options": _selection_options,
    }

# Domains are a list of conditions; each condition is a field, an
# operator and a value. The operator vocabulary is the only part a
# model gets wrong, so the server owns it.
_OPERATORS = {
    "=": "=",
    "==": "=",
    "!=": "!=",
    "<>": "!=",
    ">": ">",
    ">=": ">=",
    "<": "<",
    "<=": "<=",
    "like": "like",
    "ilike": "ilike",
    "not like": "not like",
    "not ilike": "not ilike",
    "in": "in",
    "not in": "not in",
    "child_of": "child_of",
    "parent_of": "parent_of",
}

# Fields a search may not filter on: they are stored, computed or
# relational in a way that makes a domain on them either wrong or
# expensive.
_UNFILTERABLE = ("id", "create_uid", "create_date", "write_uid",
                 "write_date", "__last_update")


def _norm_operator(raw):
    """The operator, spelled the way Odoo spells it. Unknown operators
    are refused by name instead of silently becoming "="."""
    key = str(raw or "=").strip().lower()
    if key not in _OPERATORS:
        raise AccessError(_(
            "Unknown domain operator %(op)s. Use one of: %(list)s.") % {
                "op": raw,
                "list": ", ".join(sorted(set(_OPERATORS.values())))})
    return _OPERATORS[key]


def tool_build_domain(env, args, ctx):
    """Build a validated Odoo domain from structured conditions.

    The model states WHAT it wants ("customers that are overdue, on the
    company Acme") and the server decides HOW that is spelled. This
    removes the single most common source of failed calls: a
    hand-written domain with a wrong operator or a nested shape.
    """
    helpers = _helpers()
    model_name = args.get("model", "")
    if not helpers["model_allowed"](env, model_name):
        raise helpers["denied"](model_name, env)
    conditions = args.get("conditions")
    if conditions is None:
        conditions = args.get("domain")
    if not isinstance(conditions, list):
        raise AccessError(_(
            "build_domain needs a list of conditions, each one "
            "{field, operator, value}."))
    built = []
    for raw in conditions:
        if not isinstance(raw, dict):
            raise AccessError(_(
                "Each condition must be an object with field, operator "
                "and value."))
        field_name = str(raw.get("field") or "").strip()
        if not field_name:
            raise AccessError(_("A condition needs a field."))
        if field_name in _UNFILTERABLE:
            raise AccessError(_(
                "%(field)s cannot be used in a domain.") % {
                    "field": field_name})
        try:
            field = env[model_name]._fields[field_name]
        except KeyError:
            raise AccessError(_(
                "No field %(field)s on %(model)s. Use fields_get to see "
                "the real names.") % {"field": field_name,
                                      "model": model_name})
        operator = _norm_operator(raw.get("operator"))
        value = raw.get("value")
        # A relational field filters by id, and a name is not an id.
        if field.type == "many2one" and isinstance(value, str):
            raise AccessError(_(
                "%(field)s is a link: filter it by id, or search for the "
                "record first and use the id you found.") % {
                    "field": field_name})
        built.append([field_name, operator, value])
    domain = built
    match = (args.get("match") or "all").strip().lower()
    if match == "any" and len(built) > 1:
        # OR is prefix notation: N-1 "|" operators, then the clauses.
        domain = ["|"] * (len(built) - 1)
        for clause in built:
            domain += clause
    elif match not in ("all", "any"):
        raise AccessError(_(
            "match must be 'all' (every condition) or 'any' "
            "(at least one)."))
    return {
        "model": model_name,
        "domain": domain,
        "conditions": built,
        "note": "Pass this domain to search_records or count_records.",
    }


def tool_list_models(env, args, ctx):
    """The models this agent may touch, with their labels.

    An agent that cannot ask what exists will guess model names, and a
    guess is a refusal. This is the discovery call that makes the
    allow-list usable.
    """
    helpers = _helpers()
    query = str(args.get("query") or "").strip().lower()
    names = helpers["allowed_models"](env)
    rows = []
    for name in names:
        try:
            model = env[name]
        except KeyError:
            continue
        label = (model._meta.label or name) if model else name
        if query and query not in name.lower() and query not in label.lower():
            continue
        rows.append({
            "model": name,
            "label": label,
            "fields": len(model._fields) if model else 0,
        })
        if len(rows) >= 60:
            break
    rows.sort(key=lambda row: row["model"])
    return {
        "models": rows,
        "count": len(rows),
        "note": "Use fields_get(model=...) for the field names, and "
                "search_records to read records.",
    }


def _payload_fields(env, model_name, values):
    """Check a create/write payload against live field metadata.

    Returns (clean_values, problems). Problems are advisory here: the
    card is the real gate, and this is the model checking its work
    BEFORE the user is asked to approve something broken.
    """
    problems = []
    clean = {}
    try:
        fields = env[model_name]._fields
    except KeyError:
        raise AccessError(_("No model %(model)s.") % {"model": model_name})
    for name, value in (values or {}).items():
        if name not in fields:
            problems.append({
                "field": name,
                "problem": "unknown field on %s" % model_name,
            })
            continue
        field = fields[name]
        if field.type in ("one2many", "many2many", "binary", "reference"):
            problems.append({
                "field": name,
                "problem": "%s fields are out of scope for this tool"
                           % field.type,
            })
            continue
        if field.readonly:
            problems.append({
                "field": name,
                "problem": "field is readonly",
            })
            continue
        clean[name] = value
    return clean, problems


def tool_validate_write(env, args, ctx):
    """Dry-run a create or write. Nothing is written, nothing is queued.

    The model gets told what would happen before the user is asked to
    approve it: unknown fields, readonly fields, missing required
    fields, and the current values of the fields it wants to change.
    """
    helpers = _helpers()
    model_name = args.get("model", "")
    if not helpers["model_allowed"](env, model_name):
        raise helpers["denied"](model_name, env)
    operation = (args.get("operation") or "write").strip().lower()
    if operation not in ("write", "create"):
        raise AccessError(_("operation must be write or create."))
    values = args.get("values")
    if not isinstance(values, dict) or not values:
        raise AccessError(_("validate_write needs a values object."))
    clean, problems = _payload_fields(env, model_name, values)
    changes = []
    res_id = int(args.get("res_id") or 0)
    record = None
    if operation == "write":
        if not res_id:
            raise AccessError(_("A write needs res_id."))
        record = env[model_name].browse(res_id).exists()
        if not record:
            raise AccessError(_("Record not found."))
        for name, value in clean.items():
            field = env[model_name]._fields[name]
            try:
                old = record[name]
            except Exception:
                old = ""
            if field.type == "many2one":
                old_display = old.display_name if old else ""
                new_display = value
            elif field.type == "selection":
                options = dict(helpers["selection_options"](field, record))
                old_display = options.get(old, old) or ""
                new_display = options.get(value, value) or ""
            else:
                old_display = str(old if old is not False else "")[:80]
                new_display = str(value)[:80]
            changes.append({
                "field": name,
                "label": field.string or name,
                "old": old_display,
                "new": new_display,
            })
    else:
        for name, value in clean.items():
            field = env[model_name]._fields[name]
            changes.append({
                "field": name,
                "label": field.string or name,
                "old": "",
                "new": str(value)[:80],
            })
    # Required fields the payload does not carry: a create without
    # them will fail at Approve, and the user deserves to know now.
    missing = []
    if operation == "create":
        try:
            required = env[model_name]._fields
        except KeyError:
            required = {}
        for name, field in required.items():
            if not getattr(field, "required", False):
                continue
            if name in clean:
                continue
            if field.compute or field.related or field.default:
                continue
            missing.append({
                "field": name,
                "label": field.string or name,
                "note": "Odoo will default it if it has a default",
            })
    return {
        "model": model_name,
        "operation": operation,
        "ok": not problems and not missing,
        "problems": problems,
        "missing_required": missing,
        "changes": changes,
        "note": "Nothing was written. Queue the same values with "
                "set_fields to make it happen.",
    }


def tool_set_fields(env, args, ctx):
    """Write SEVERAL fields on one record in ONE card.

    One card, one approval, one undo. Chaining five ``set_field``
    calls meant five cards and a record that was half-updated if the
    user approved only some of them.
    """
    helpers = _helpers()
    model_name = args.get("model", "")
    if not helpers["model_allowed"](env, model_name):
        raise helpers["denied"](model_name, env)
    values = args.get("values")
    if not isinstance(values, dict) or not values:
        raise AccessError(_("set_fields needs a values object."))
    record = helpers["chain_target"](
        env, model_name, args.get("res_id"), args)
    if record is None:
        raise AccessError(_(
            "set_fields needs the id of an existing record."))
    clean, problems = _payload_fields(env, model_name, values)
    if problems:
        names = ", ".join(p["field"] for p in problems)
        raise AccessError(_(
            "These fields cannot be written: %(fields)s. Fix them or use "
            "fields_get.") % {"fields": names})
    changes = []
    for name, value in clean.items():
        field = env[model_name]._fields[name]
        try:
            coerced, display = helpers["coerce"](
                env, field, value, record)
        except AccessError:
            raise
        try:
            old = record[name]
        except Exception:
            old = ""
        if field.type == "many2one":
            old_display = old.display_name if old else ""
        elif field.type == "selection":
            options = dict(helpers["selection_options"](field, record))
            old_display = options.get(old, old) or ""
        else:
            old_display = str(old if old is not False else "")[:80]
        changes.append({
            "field": name,
            "label": field.string or name,
            "old": old_display,
            "new": display,
            "value": coerced,
        })
    if not changes:
        raise AccessError(_("Nothing to write: no writable field given."))
    summary = ", ".join(
        "%s: %s -> %s" % (c["label"], c["old"] or "-", c["new"] or "-")
        for c in changes[:3])
    if len(changes) > 3:
        summary += " (+%d more)" % (len(changes) - 3)
    card = helpers["confirm_card"](
        "set_fields",
        env._("Update %(count)s fields on %(record)s") % {
            "count": len(changes), "record": record.display_name},
        summary,
        {"model": model_name, "res_id": record.id,
         "values": {c["field"]: c["value"] for c in changes}},
    )
    card["preview"] = [
        "%s: %s -> %s" % (c["label"], c["old"] or "-", c["new"] or "-")
        for c in changes]
    return card


def _flow_graph_from_args(args):
    """Nodes/edges from chat args, or compiled from legacy steps."""
    from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
        steps_to_graph, validate_graph)
    nodes = args.get("nodes") or []
    edges = args.get("edges") or []
    trigger = args.get("trigger") or {"type": "chat", "config": {}}
    if not isinstance(trigger, dict):
        trigger = {"type": "chat", "config": {}}
    if not nodes and args.get("steps"):
        try:
            nodes, edges = steps_to_graph(args.get("steps") or [])
        except Exception:
            nodes, edges = [], []
    return nodes, edges, trigger


def tool_draft_flow(env, args, ctx):
    """Save the current plan as a reusable flow — so next time it is
    one call instead of a whole conversation.

    Accepts EITHER the legacy ``steps`` list OR the n8n-class
    ``nodes`` + ``edges`` + ``trigger`` graph. The graph is validated
    with the flow's own rules BEFORE anything is queued: a broken
    flow never becomes a card. A valid one queues a normal create
    card, and approving it registers the flow under the agent that
    drafted it.
    """
    from odoo.addons.nova_ai.models.nova_ai_tools import tool_create_draft
    name = (args.get("name") or "").strip()[:120]
    description = (args.get("description") or "").strip()[:500]
    steps = args.get("steps")
    nodes, edges, trigger = _flow_graph_from_args(args)
    if not name:
        raise AccessError(env._("draft_flow needs a name for the flow."))
    has_graph = bool(nodes) or args.get("nodes") is not None
    if has_graph:
        from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
            validate_graph as _validate_graph)
        problems = _validate_graph(nodes, edges, trigger)
        # Legacy mirror: keep steps in sync so old clients still render.
        try:
            from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
                graph_to_steps as _g2s)
            steps = _g2s(nodes, edges)
        except Exception:
            steps = steps or []
    else:
        if not isinstance(steps, list) or not steps:
            raise AccessError(env._(
                "draft_flow needs steps ([{type, config}]) or "
                "nodes+edges ([{id, type, config}] + [{source, target}]). "
                "Node types: trigger, odoo.search, odoo.read, odoo.create, "
                "odoo.write, odoo.note, if, switch, set, code, http, "
                "ai.agent, loop.foreach, merge, human.approval, subflow, "
                "output. Edges link node ids; IF uses source_handle "
                "true/false."))
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not step.get("type"):
                raise AccessError(env._(
                    "Step %d needs a type.") % (index + 1))
        from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
            validate_graph as _validate_graph,
            steps_to_graph as _s2g)
        try:
            nodes, edges = _s2g(steps)
        except Exception:
            nodes, edges = [], []
        trigger = {"type": "chat", "config": {}}
        problems = None  # computed on the probe below (legacy rules)
    # Validate without writing: the model's own rules are the gate.
    # The probe carries the drafting agent, exactly as the queued card
    # will - otherwise an agent step with no explicit agent looks
    # orphaned here while being perfectly fine on approve.
    agent = (ctx or {}).get("agent")
    probe_vals = {
        "name": name,
        "description": description,
        "steps": steps or [],
        "nodes": nodes,
        "edges": edges,
        "trigger_type": (trigger or {}).get("type") or "chat",
        "trigger_config": (trigger or {}).get("config") or {},
        "spec_version": 2 if nodes else 1,
    }
    if agent:
        probe_vals["agent_id"] = agent.id
    probe = env["nova.ai.flow"].new(probe_vals)
    if problems is None:
        problems = [text for _id, text in probe.validate()]
        try:
            from odoo.addons.nova_ai.models.nova_ai_flow_engine import (
                FlowEngine as _Engine)
            problems = _validate_graph(
                nodes, edges,
                {"type": probe_vals.get("trigger_type"),
                 "config": probe_vals.get("trigger_config") or {}}) \
                or problems
        except Exception:
            pass
    if problems:
        # Refuse like create_draft refuses a bad value: the turn loop
        # hands it straight back to the model, which fixes the steps
        # and calls again in the same turn. A result-shaped dict would
        # look like a queued card downstream - this must never read as
        # an approval.
        raise AccessError(env._(
            "That flow does not validate: %(problems)s Fix the flow "
            "and call draft_flow again - nothing was queued.") % {
                "problems": "; ".join(problems)[:600]})
    values = {
        "name": name,
        "description": description,
        "steps": steps or [],
        "nodes": nodes,
        "edges": edges,
        "trigger_type": (trigger or {}).get("type") or "chat",
        "trigger_config": (trigger or {}).get("config") or {},
        "spec_version": 2 if nodes else 1,
        "active": True,
    }
    if agent:
        values["agent_id"] = agent.id
    card = tool_create_draft(
        env, {"model": "nova.ai.flow", "values": values}, ctx)
    # Its own kind, so the audit log, the tool gating and the drawer
    # show a flow draft - not a generic record create.
    if isinstance(card, dict):
        card["kind"] = "draft_flow"
        card["title"] = "Draft flow: %s" % name
    return card


def tool_update_flow(env, args, ctx):
    """Revise a flow from chat: add/replace nodes, rewire, retrigger.

    The model names the flow, passes the full corrected ``nodes`` +
    ``edges`` (+ optional ``trigger``), and the server validates
    BEFORE queueing. Nothing writes inline — the change queues a
    card the user approves, exactly like draft_flow.
    """
    from odoo.addons.nova_ai.models.nova_ai_tools import tool_create_draft
    from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
        graph_to_steps, validate_graph)
    name = (args.get("flow") or args.get("name") or "").strip()
    if not name:
        raise AccessError(env._("update_flow needs a flow name."))
    flows = env["nova.ai.flow"].search(
        [("name", "=ilike", name), ("active", "=", True)], limit=1)
    if not flows:
        raise AccessError(env._("No flow named %r.") % name)
    flow = flows[0]
    nodes = args.get("nodes") or flow.nodes or []
    edges = args.get("edges") or flow.edges or []
    trigger = args.get("trigger") or {
        "type": flow.trigger_type or "manual",
        "config": flow.trigger_config or {}}
    if not isinstance(nodes, list) or not nodes:
        raise AccessError(env._("update_flow needs the full nodes list."))
    problems = validate_graph(nodes, edges, trigger)
    if problems:
        raise AccessError(env._(
            "That update does not validate: %(problems)s Fix it and "
            "call update_flow again - nothing was queued.") % {
                "problems": "; ".join(problems)[:600]})
    try:
        steps = graph_to_steps(nodes, edges)
    except Exception:
        steps = flow.steps or []
    agent = (ctx or {}).get("agent")
    values = {
        "name": flow.name,
        "description": (args.get("description")
                        if args.get("description") is not None
                        else flow.description) or "",
        "steps": steps,
        "nodes": nodes,
        "edges": edges,
        "trigger_type": (trigger or {}).get("type") or "manual",
        "trigger_config": (trigger or {}).get("config") or {},
        "spec_version": 2,
        "version": (flow.version or 1) + 1,
        "active": True,
    }
    if agent and not flow.agent_id:
        values["agent_id"] = agent.id
    # An update is a revision card: it carries the target flow id so
    # approve-time applies it onto the flow instead of creating a twin.
    card = tool_create_draft(
        env, {"model": "nova.ai.flow", "values": {
            "name": "Update %s (v%d)" % (
                flow.name, (flow.version or 1) + 1),
            "description": "Revision of flow #%d" % flow.id,
            "steps": steps, "active": True}}, ctx)
    if isinstance(card, dict):
        card["kind"] = "update_flow"
        card["title"] = "Update flow: %s" % flow.name
        payload = card.get("payload") or {}
        payload["update_flow_id"] = flow.id
        payload["update_values"] = values
        card["payload"] = payload
    return card


def tool_export_flow(env, args, ctx):
    """Return a flow's portable JSON so chat can show or copy it."""
    name = (args.get("flow") or args.get("name") or "").strip()
    flows = env["nova.ai.flow"].search(
        [("name", "=ilike", name)], limit=1) if name else env[
            "nova.ai.flow"]
    if not flows:
        return {"error": "No flow named %r." % name}
    flavor = (args.get("format") or "nova").strip().lower()
    try:
        definition = flows[0].export_n8n() if flavor == "n8n" \
            else flows[0].export_json()
    except Exception as err:
        return {"error": str(err)[:300]}
    return {"flow": flows[0].name, "format": flavor,
            "definition": definition[:15000]}


def tool_import_flow(env, args, ctx):
    """Build a flow from pasted JSON — Nova-native OR n8n workflow JSON.

    Paste an n8n workflow (nodes+connections) and get the same graph
    running in Odoo. Unknown n8n node types import as flagged nodes;
    validation names them before anything is queued.
    """
    from odoo.addons.nova_ai.models.nova_ai_tools import tool_create_draft
    from odoo.addons.nova_ai.models.nova_ai_flow_n8n import from_n8n
    from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
        graph_to_steps, validate_graph)
    import json as _json
    name = (args.get("name") or "").strip()[:120]
    raw = args.get("definition") or args.get("workflow") or ""
    if not name:
        raise AccessError(env._("import_flow needs a name."))
    if not raw:
        raise AccessError(env._("import_flow needs a definition."))
    try:
        payload = _json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        raise AccessError(env._("That is not valid JSON."))
    if not isinstance(payload, dict):
        raise AccessError(env._("That is not a workflow."))
    if "connections" in payload and "nodes" in payload \
            and not payload.get("edges"):
        nodes, edges, trigger, unsupported = from_n8n(payload)
    else:
        nodes = payload.get("nodes") or []
        edges = payload.get("edges") or []
        trigger = payload.get("trigger") or {"type": "chat",
                                             "config": {}}
        unsupported = []
    problems = validate_graph(nodes, edges, trigger)
    if problems:
        raise AccessError(env._(
            "That import does not validate: %(problems)s") % {
                "problems": "; ".join(problems)[:600]})
    try:
        steps = graph_to_steps(nodes, edges)
    except Exception:
        steps = []
    agent = (ctx or {}).get("agent")
    values = {
        "name": name,
        "description": (args.get("description") or "")[:500],
        "steps": steps, "nodes": nodes, "edges": edges,
        "trigger_type": (trigger or {}).get("type") or "chat",
        "trigger_config": (trigger or {}).get("config") or {},
        "spec_version": 2, "active": True,
    }
    if agent:
        values["agent_id"] = agent.id
    card = tool_create_draft(
        env, {"model": "nova.ai.flow", "values": values}, ctx)
    if isinstance(card, dict):
        card["kind"] = "draft_flow"
        card["title"] = "Import flow: %s" % name
        if unsupported:
            card["summary"] = (card.get("summary") or "") + (
                " Note: %s did not map 1:1 — review flagged nodes."
                % ", ".join(unsupported)[:200])
    return card
