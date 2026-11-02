# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Nova Studio — the agent's control room.

One screen that answers the five questions an operator actually has:

1. **What does the agent know?** Its skills, memories and wiki — with
   who wrote each one and whether a human checked it.
2. **What is waiting on me?** Proposals the agent drafted and the
   write cards the user has not approved yet.
3. **What can it do?** The tools it is allowed to use, and the flows
   (saved plans) it can run in one call.
4. **Why did it answer like that?** The model that ran the turn, the
   skills that were loaded, the tools it called.
5. **What is it allowed to spend?** The routing table: which model
   covers which capability, and what each one costs.

The payload is assembled in one round trip. A control room that
staggers its panels is a control room that flickers.
"""

import json

from odoo import _, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from odoo.addons.nova_ai.models.nova_ai_knowledge_tools import (
    injection_hits, secret_hits)

# The kind icons the UI uses, so the server and the client agree.
_KIND_ORDER = ["skill", "memory", "wiki", "reference", "document"]


def _studio_user():
    """The Studio is for people who work with agents, not for every
    internal user. Users without the agent group get nothing."""
    user = request.env.user
    if not (user.has_group("nova_ai.group_nova_ai_user")
            or user.has_group("base.group_system")):
        raise AccessError(_("Nova Studio is not available for this user."))
    return user


def _knowledge_row(record):
    """One concept, in the shape the Brain grid renders."""
    return {
        "id": record.id,
        "code": record.code or "",
        "name": record.name or "",
        "kind": record.kind or "wiki",
        "description": (record.description or "")[:300],
        "body": record.body or "",
        "trust": record.trust_tier or "unverified",
        "status": record.effective_status or "stable",
        "stale": bool(record.is_stale),
        "proposed": bool(record.is_proposed),
        "use_count": record.use_count or 0,
        "last_used_at": str(record.last_used_at or ""),
        "generated_by": record.generated_by or "",
        "generated_at": str(record.generated_at or ""),
        "verified_by": record.verified_by or "",
        "verified_at": str(record.verified_at or ""),
        "proposed_by": record.proposed_by or "",
        "reviewed_by": record.reviewed_by or "",
        "review_note": record.review_note or "",
        "owner": record.owner_user_id.display_name or "",
        "owner_id": record.owner_user_id.id or 0,
        "tags": record.tags or [],
        "sources": record.sources or [],
        "stale_after": str(record.stale_after or ""),
    }


def _flow_row(record):
    """One flow, with its steps flattened for the timeline UI.

    The timeline reads `label`/`detail`; the editor needs the raw
    `config`, so both ride along. Anything the UI cannot edit (a
    tool the agent may not use, a step type it does not know) is
    flagged, never silently dropped.
    """
    steps = []
    problems = []
    try:
        problems = [text for _id, text in record.validate()]
    except Exception:
        problems = []
    for index, step in enumerate(record.steps or []):
        if not isinstance(step, dict):
            continue
        config = step.get("config") or {}
        steps.append({
            "n": index + 1,
            "type": step.get("type") or "",
            "label": _step_label(step, config),
            "detail": _step_detail(step, config),
            "result": step.get("resultVariable") or "",
            "direct": bool(step.get("directOutput")),
            "config": config if isinstance(config, dict) else {},
        })
    try:
        nodes, edges = record._graph()
    except Exception:
        nodes, edges = [], []
    return {
        "id": record.id,
        "name": record.name or "",
        "description": record.description or "",
        "agent": record.agent_id.name or "",
        "agent_id": record.agent_id.id or 0,
        "active": bool(record.active),
        "use_count": record.use_count or 0,
        "steps": steps,
        "step_count": len(steps),
        "problems": problems,
        "valid": not problems,
        "nodes": nodes if isinstance(nodes, list) else [],
        "edges": edges if isinstance(edges, list) else [],
        "trigger_type": record.trigger_type or "manual",
        "trigger_config": record.trigger_config or {},
        "webhook_url": record.webhook_url or "",
        "version": record.version or 1,
        "run_count": record.run_count or 0,
        "last_status": record.last_status or "",
    }


def _step_label(step, config):
    """One human line for a step. A flow is a plan; it must read like
    one, not like JSON."""
    kind = step.get("type") or ""
    if kind == "start":
        names = [v.get("name") for v in (config.get("variables") or [])
                 if isinstance(v, dict) and v.get("name")]
        return "Ask for %s" % (", ".join(names) if names else "the inputs")
    if kind == "search":
        args = config.get("args") or {}
        return "Search %s" % (args.get("model") or "records")
    if kind == "read":
        args = config.get("args") or {}
        return "Read %s" % (args.get("model") or "a record")
    if kind == "create":
        args = config.get("args") or {}
        return "Queue a new %s" % (args.get("model") or "record")
    if kind == "tool":
        return "Run %s" % (config.get("tool") or "a tool")
    if kind == "agent":
        return "Ask the agent"
    if kind == "note":
        return "Post a note"
    if kind == "output":
        return "Return the result"
    return kind or "step"


def _step_detail(step, config):
    """The second line of a step: what it works on."""
    kind = step.get("type") or ""
    if kind == "search":
        args = config.get("args") or {}
        domain = args.get("domain") or []
        parts = []
        for clause in domain:
            if isinstance(clause, (list, tuple)) and len(clause) >= 3:
                parts.append("%s %s %s" % (
                    clause[0], clause[1],
                    clause[2] if not isinstance(clause[2], (list, tuple))
                    else ", ".join(str(v) for v in clause[2])))
        return " · ".join(parts)[:240]
    if kind in ("read", "create"):
        args = config.get("args") or {}
        values = args.get("values") or {}
        if values:
            return " · ".join(
                "%s: %s" % (k, values[k]) for k in list(values)[:4])[:240]
        return ""
    if kind == "agent":
        return (config.get("instruction") or "")[:240]
    if kind == "note":
        return (config.get("body") or "")[:240]
    if kind == "output":
        return (config.get("value") or "")[:240]
    return ""


def _model_row(record):
    """One model, with the capabilities the router reads."""
    caps = []
    if record.supports_tools:
        caps.append("tools")
    if record.supports_vision:
        caps.append("vision")
    if record.supports_json:
        caps.append("json")
    if record.supports_reasoning:
        caps.append("reasoning")
    if record.supports_embeddings:
        caps.append("embeddings")
    return {
        "id": record.id,
        "name": record.name or "",
        "provider": record.provider_id.name or "",
        "tier": record.tier or "t1",
        "context_length": record.context_length or 0,
        "capabilities": caps,
        "cost_in": record.cost_in_per_mtok or 0.0,
        "cost_out": record.cost_out_per_mtok or 0.0,
        "reliability": record.reliability or 0.0,
        "latency_hint_ms": record.latency_hint_ms or 0,
        "active": bool(record.active),
        "last_probe": str(record.last_probe or ""),
        "note": record.routing_note or "",
    }


def _tool_row(record, env=None, user=None):
    """One tool the agent may use."""
    row = {
        "id": record.id,
        "code": record.code or "",
        "name": record.name or record.code or "",
        "description": (record.description or "")[:200],
        "tier": record.tier_min or "t1",
        "write_kind": record.write_kind or "confirm",
        "destructive": bool(record.destructive),
        "read_only": bool(getattr(record, "read_only", False)),
        "rate_limit": record.rate_limit_per_hour or 0,
    }
    # Hourly usage, from the same audit log the rate limit reads: a
    # hot tool and a throttled one look different, and the operator
    # should see which is which before touching the limit.
    row["used"] = 0
    if env is not None and user is not None and row["rate_limit"]:
        try:
            from odoo.addons.nova_ai.models.nova_ai_tools import _rate_count
            row["used"] = _rate_count(env, row["code"], user.id)
        except Exception:
            row["used"] = 0
    return row


def _log_row(record):
    """One audit line."""
    detail = record.detail
    if not isinstance(detail, dict):
        detail = {}
    tool = detail.get("tool") or ""
    return {
        "id": record.id,
        "at": str(record.created_at or ""),
        "kind": record.kind or "",
        "ok": bool(record.ok),
        "error": (record.error or "")[:300],
        "tool": tool,
        "agent": record.agent_id.name or "",
        "model": record.model_id.name or "",
        "user": record.user_id.display_name or "",
        "latency_ms": record.latency_ms or 0,
        "rounds": record.rounds or 0,
        "tokens_in": record.tokens_in or 0,
        "tokens_out": record.tokens_out or 0,
    }


def studio_payload(env, user):
    """The whole Studio, in one dict.

    A control room that staggers its panels is a control room that
    flickers, so everything comes in one round trip.
    """
    Knowledge = env["nova.ai.knowledge"]
    agents = env["nova.ai.agent"].search(
        [("active", "=", True)], order="sequence, id")
    agent = agents[0] if agents else None
    knowledge = Knowledge.search([], order="kind, code")
    flows = env["nova.ai.flow"].search([], order="name")
    tools = env["nova.ai.tool"].search([], order="code")
    logs = env["nova.ai.log"].search([], order="id desc", limit=60)
    # Pending write cards, across every session of this user: the
    # "waiting on you" count is the number that matters.
    pending = 0
    sessions = env["nova.ai.session"].search(
        [("user_id", "=", user.id)])
    for session in sessions:
        pending += len(session.pending or [])
    # Proposals: concepts the agent drafted that nobody reviewed.
    proposals = [k for k in knowledge if k.is_proposed]
    stale = [k for k in knowledge if k.is_stale]
    by_kind = {kind: 0 for kind in _KIND_ORDER}
    for concept in knowledge:
        by_kind[concept.kind or "wiki"] = \
            by_kind.get(concept.kind or "wiki", 0) + 1
    out = {
        "ok": True,
        "is_admin": user.has_group("nova_ai.group_nova_ai_admin"),
        "agent": {
            "id": agent.id if agent else 0,
            "name": agent.name if agent else "",
            "initials": ((agent.initials or (agent.name or "N")[:2])
                         if agent else "N"),
            "tier": agent.tier if agent else "t1",
            "model": agent.model_id.name if agent else "",
            "model_id": agent.model_id.id if agent else 0,
            "tools": len(agent.tool_ids) if agent else 0,
            "max_rounds": agent.max_rounds if agent else 0,
            "auto_apply": bool(agent.auto_apply) if agent else False,
            "requires": sorted(agent.required_capabilities())
            if agent else [],
        },
        "stats": {
            "skills": by_kind.get("skill", 0),
            "memories": by_kind.get("memory", 0),
            "wiki": by_kind.get("wiki", 0),
            "concepts": len(knowledge),
            "proposals": len(proposals),
            "stale": len(stale),
            "flows": len(flows),
            "tools": len(tools),
            "pending": pending,
            "sessions": len(sessions),
            "agents": len(agents),
        },
            "knowledge": [_knowledge_row(k) for k in knowledge],
            "flows": [_flow_row(f) for f in flows],
            "tools": [_tool_row(t, env, user) for t in tools],
        "models": [_model_row(m) for m in
                   env["nova.ai.model"].search([], order="name")],
        "activity": [_log_row(row) for row in logs],
            "providers": [{
                "id": p.id,
                "name": p.name or "",
                "kind": p.kind or "",
                "base_url": p.base_url or "",
                "active": bool(p.active),
                "models": len(p.model_ids),
                "note": (p.note or "")[:200],
            } for p in env["nova.ai.provider"].search([], order="name")],
                    # The flow editor's agent picker needs ids and names. Names
                    # only: prompts and instructions stay in the agent form.
                    "agents": [{
                        "id": a.id,
                        "name": a.name or "",
                    } for a in agents],
        }
    # Webhook URLs embed the secret token: admins plan rotations,
    # everyone else only needs to know a webhook exists.
    if not user.has_group("nova_ai.group_nova_ai_admin") and not \
            user.has_group("base.group_system"):
        for flow_row in out.get("flows", []):
            flow_row["webhook_url"] = ""
    return out


def knowledge_action(env, user, action="", knowledge_id=0, name="",
                     description="", body="", kind="memory",
                     review_note=""):
    """Review a proposal, verify a concept, or write one.

    A concept written here lands as a draft when it comes from the
    agent, and as a stable concept when a human writes it. The secret
    and injection filters apply to both: the corpus never stores a
    credential, and never stores an instruction aimed at the model.
    """
    if not user.has_group("nova_ai.group_nova_ai_admin"):
        raise AccessError(_(
            "Only a Nova administrator may change the agent's knowledge."))
    Knowledge = env["nova.ai.knowledge"]
    if action == "scan":
        # The Studio's "Scan custom code" button: an administrator's
        # explicit act, so the concepts it builds land stable - the
        # human IS the review (they pressed the button and the Knowledge
        # list shows every row it made). Idempotent: re-running only
        # adds models whose code is new.
        from odoo.addons.nova_ai.models import nova_ai_scanner as scanner
        roots = None
        try:
            import odoo.tools as _tools
            raw = _tools.config.get("addons_path") or []
            if isinstance(raw, str):
                raw = raw.split(",")
            roots = [str(p).strip() for p in raw
                     if "custom" in str(p).lower()]
        except Exception:
            roots = None
        found = scanner.scan_paths(roots or [], limit_models=200)
        created = 0
        skipped = 0
        for model in found.get("models", []):
            concept = scanner.concept_for_model(model)
            if Knowledge.search([("code", "=", concept["code"])], limit=1):
                skipped += 1
                continue
            Knowledge.sudo().create({
                "name": concept["name"],
                "code": concept["code"],
                "kind": "reference",
                "description": concept["description"],
                "body": concept["body"],
                "status": "stable",
                "generated_by": "process:code-scan",
                "sources": concept["sources"],
            })
            created += 1
        return {
            "ok": True,
            "action": "scan",
            "files": found.get("files", 0),
            "models": len(found.get("models", [])),
            "created": created,
            "skipped": skipped,
            "truncated": bool(found.get("truncated", False)),
        }
    if action in ("verify", "accept", "reject"):
        record = Knowledge.browse(int(knowledge_id or 0)).exists()
        if not record:
            return {"ok": False, "error": "That concept no longer exists."}
        if action == "verify":
            record.action_verify()
        elif action == "accept":
            record.action_accept()
        else:
            record.action_reject()
            if review_note:
                record.write({"review_note": review_note})
        return {"ok": True, "action": action,
                "concept": _knowledge_row(record)}
    if action == "write":
        name = (name or "").strip()[:120]
        body = (body or "").strip()[:8000]
        if not name or not body:
            return {"ok": False,
                    "error": "A concept needs a title and a body."}
        if secret_hits(body) or secret_hits(name):
            return {"ok": False,
                    "error": "Refused: the text looks like it carries a "
                             "secret. Knowledge never stores credentials."}
        if injection_hits(body):
            return {"ok": False,
                    "error": "Refused: the text reads like an instruction "
                             "to the model, not knowledge for a person."}
        if kind not in ("skill", "memory", "wiki", "reference", "document"):
            kind = "memory"
        record = Knowledge.sudo().create({
            "name": name,
            "kind": kind,
            "description": (description or "").strip()[:150],
            "body": body,
            "status": "stable",
            "generated_by": "human:%s" % user.id,
            "verified_by": "human:%s" % user.id,
        })
        return {"ok": True, "action": "write",
                "concept": _knowledge_row(record)}
    return {"ok": False, "error": "Unknown action."}


def flow_action(env, user, action="run", flow_id=0, arguments=None):
    """Run, validate, save, or create a flow.

    "Save" writes the Studio editor's working copy after the same
    validation the interpreter runs: a red flow can be saved while it
    is being fixed, and the editor keeps it open until it validates
    clean. "Create" makes a blank flow with a start block so the
    editor never starts from nothing.
    """
    flows = env["nova.ai.flow"]
    if action in ("save", "create"):
        # Changing what the agent may run is an administrator's job, the
        # same as changing what it knows. Running a flow and reading
        # one are open to every Studio user.
        if not env.user.has_group("nova_ai.group_nova_ai_admin"):
            raise AccessError(_(
                "Only a Nova administrator may change a flow."))
    if action == "create":
        name = ((arguments or {}).get("name") or "Untitled flow").strip()
        record = flows.sudo().create({
            "name": name[:120],
            "description": (arguments or {}).get("description") or "",
            "active": True,
            "steps": [{"type": "start",
                       "config": {"variables": []}}],
        })
        return {"ok": True, "flow": _flow_row(record)}
    if action == "validate":
        record = flows.browse(int(flow_id or 0)).exists()
        if not record:
            return {"ok": False, "error": "That flow no longer exists."}
        return {"ok": True, "flow": _flow_row(record)}
    if action == "run":
        record = flows.browse(int(flow_id or 0)).exists()
        if not record:
            return {"ok": False, "error": "That flow no longer exists."}
        raw_trigger = ((arguments or {}).pop("_trigger", None)
                   if isinstance(arguments, dict) else None) or "manual"
        # Spoof guard: Studio callers never run as webhook/record/subflow.
        trigger = raw_trigger if raw_trigger in ("manual", "chat", "test") else "manual"
        outcome = record.run_graph(arguments or {},
                                   trigger_kind=trigger)
        return {
            "ok": bool(outcome.get("ok")),
            "error": outcome.get("error") or "",
            "message": outcome.get("message") or "",
            "needs_approval": bool(outcome.get("needs_approval")),
            "results": outcome.get("results") or [],
            "run_id": outcome.get("run_id", 0),
            "flow": _flow_row(record),
        }
    if action == "runs":
        record = flows.browse(int(flow_id or 0)).exists()
        if not record:
            return {"ok": False, "error": "That flow no longer exists."}
        runs = env["nova.ai.flow.run"].search(
            [("flow_id", "=", record.id)], order="id desc", limit=20)
        return {"ok": True, "runs": [{
            "id": run.id, "status": run.status or "",
            "trigger": run.trigger or "",
            "at": str(run.started_at or ""),
            "duration_ms": run.duration_ms or 0,
            "error": (run.error or "")[:200],
        } for run in runs]}
    if action == "run_detail":
        try:
            run = env["nova.ai.flow.run"].browse(
                int((arguments or {}).get("run_id") or 0)).exists()
        except (AccessError, MissingError):
            run = None
        # Cross-check the flow: run ids are not enumerable across
        # flows, and the record rule already scopes to the user —
        # every path ends in the same "no longer exists".
        if not run or (
                flow_id and run.flow_id.id != int(flow_id or 0)):
            return {"ok": False, "error": "That run no longer exists."}
        return {"ok": True, "run": {
            "id": run.id, "status": run.status or "",
            "trigger": run.trigger or "",
            "at": str(run.started_at or ""),
            "error": run.error or "",
            "inputs": run.inputs or {},
            "outputs": run.outputs or {},
            "node_log": run.node_log or [],
            "node_inputs": run.node_inputs or {},
            "node_outputs": run.node_outputs or {},
        }}
    if action in ("export", "export_n8n"):
        record = flows.browse(int(flow_id or 0)).exists()
        if not record:
            return {"ok": False, "error": "That flow no longer exists."}
        try:
            payload = record.export_n8n() if action == "export_n8n" \
                else record.export_json()
        except Exception as err:
            return {"ok": False, "error": str(err)[:300]}
        return {"ok": True, "definition": payload,
                "flow": _flow_row(record)}
    if action in ("import", "import_n8n"):
        if not env.user.has_group("nova_ai.group_nova_ai_admin"):
            raise AccessError(_(
                "Only a Nova administrator may change a flow."))
        record = flows.browse(int(flow_id or 0)).exists()
        if not record:
            return {"ok": False, "error": "That flow no longer exists."}
        import json as _json
        raw = (arguments or {}).get("definition") or ""
        try:
            payload = _json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return {"ok": False, "error": "That is not valid JSON."}
        # Validate BEFORE writing: a broken import must never
        # persist, not even as a draft.
        from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
            validate_graph)
        try:
            if action == "import_n8n" or (
                    isinstance(payload, dict) and "connections" in payload
                    and "nodes" in payload and not payload.get("edges")):
                from odoo.addons.nova_ai.models import (
                    nova_ai_flow_n8n as _n8n)
                nodes, edges, trig, unsupported = _n8n.from_n8n(payload)
            else:
                nodes = payload.get("nodes") or []
                edges = payload.get("edges") or []
                trig = payload.get("trigger") or {"type": "manual"}
                unsupported = []
            if not isinstance(nodes, list) or not isinstance(edges, list):
                return {"ok": False,
                        "error": "Nodes and edges must be lists."}
            problems = validate_graph(
                nodes, edges,
                {"type": (trig or {}).get("type") or "manual",
                 "config": (trig or {}).get("config") or {}})
            if problems:
                return {"ok": False,
                        "error": "That import does not validate: %s"
                        % "; ".join(problems)[:400]}
            record.write({
                "nodes": nodes, "edges": edges,
                "trigger_type": (trig or {}).get("type") or "manual",
                "trigger_config": (trig or {}).get("config") or {},
                "spec_version": 2,
                "version": (record.version or 1) + 1})
        except Exception as err:
            return {"ok": False, "error": str(err)[:300]}
        problems = [t for _i, t in record.validate()]
        return {"ok": True, "flow": _flow_row(record),
                "problems": problems, "valid": not problems,
                "unsupported": unsupported}
    if action in ("describe", "preview"):
        # No-code canvas helpers: one call gives the inspector
        # everything it needs — no developer, no JSON hand-writing.
        # describe: models (+fields +methods for the picked model).
        # preview: validate_write dry-run for create/write values.
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            execute_tool)
        # Canvas helpers run inside the flow's agent scope (not the
        # global allow-list): the inspector only offers models the
        # agent may actually touch.
        record = flows.browse(int(flow_id or 0)).exists()
        if not record:
            return {"ok": False, "error": "That flow no longer exists."}
        ctx = {"agent": record.agent_id} if record.agent_id else {}
        if action == "describe":
            model = ((arguments or {}).get("model") or "").strip()
            query = ((arguments or {}).get("query") or "").strip()
            models_res = execute_tool(
                env, "list_models", {"query": query}, ctx)
            payload = models_res[1] if models_res else {}
            out = {"ok": True, "models": (payload or {}).get(
                "models") or []}
            if model:
                try:
                    _, fields_payload, _card = execute_tool(
                        env, "fields_get", {"model": model}, ctx)
                    out["fields"] = (fields_payload or {}).get(
                        "fields") or []
                except Exception as err:
                    out["fields_error"] = str(err)[:200]
                try:
                    _, methods_payload, _card = execute_tool(
                        env, "inspect_method", {"model": model}, ctx)
                    out["methods"] = methods_payload or {}
                except Exception as err:
                    out["methods_error"] = str(err)[:200]
                out["model"] = model
            return out
        # preview: dry-run create/write values, guided errors back.
        try:
            _, payload, _card = execute_tool(
                env, "validate_write", dict(arguments or {}), ctx)
        except Exception as err:
            return {"ok": False, "error": str(err)[:300]}
        return {"ok": True, "preview": payload}
    if action == "copilot":
        # Nova copilot INSIDE the canvas: the operator chats, the
        # agent rebuilds the graph, the canvas applies it live.
        # Same screen, no context switch.
        from odoo.addons.nova_ai.controllers.nova_ai import (
            run_chat_turn)
        record = flows.browse(int(flow_id or 0)).exists()
        if not record:
            return {"ok": False, "error": "That flow no longer exists."}
        message = ((arguments or {}).get("message") or "").strip()[:2000]
        if not message:
            return {"ok": False, "error": "Say what to build or change."}
        agents = env["nova.ai.agent"].search(
            [("active", "=", True)], order="sequence, id")
        # The flow's own agent knows its tools and scope — fall back
        # to the first active agent only when the flow has none.
        agent = record.agent_id if record.agent_id else (
            agents[0] if agents else None)
        if not agent or not agent.model_id:
            return {"ok": False,
                    "error": "No agent with a model is available."}
        nodes, edges = ([], [])
        try:
            nodes, edges = record._graph()
        except Exception:
            pass
        import json as _json
        brief = (
            "You are editing the Odoo flow %r (trigger %s). "
            "Current graph JSON: nodes=%s edges=%s. "
            "User request: %s. "
            "If they want a change, call draft_flow (new) or "
            "update_flow(flow=%r, nodes=[...], edges=[...], "
            "trigger={...}) with the FULL corrected graph — node "
            "types: trigger, odoo.search/read/create/write/note, if "
            "(branches true/false), switch, set, code, http (GET), "
            "ai.agent, loop.foreach, merge, human.approval, subflow, "
            "output. Reply in one short paragraph saying what changed."
            % (record.name, record.trigger_type or "manual",
               _json.dumps(nodes or [])[:6000],
               _json.dumps(edges or [])[:2000], message, record.name))
        try:
            session = env["nova.ai.session"].create({
                "agent_id": agent.id})
            result = run_chat_turn(env, agent, session, brief, {})
            reply = (result or {}).get("reply") or ""
            cards = (result or {}).get("cards") or []
        except Exception as err:
            return {"ok": False, "error": str(err)[:300]}
        # A graph-changing turn queues an update_flow card: surface it
        # so Apply is one click, and refresh the flow (approve applies
        # it server-side via run_confirm).
        try:
            record.invalidate_recordset(["nodes", "edges"])
            fresh = _flow_row(record)
        except Exception:
            fresh = _flow_row(record)
        return {"ok": True, "reply": reply, "cards": cards,
                "flow": fresh}
    if action == "save":
        # The Studio flow editor saves the working copy. The value is
        # validated and the problems come back with the write: a flow
        # under construction may be saved while it is still red, and
        # the editor keeps it open until it validates clean.
        record = flows.browse(int(flow_id or 0)).exists()
        if not record:
            return {"ok": False, "error": "That flow no longer exists."}
        values = arguments or {}
        write = {}
        if "name" in values and values["name"]:
            write["name"] = str(values["name"])[:120]
        if "description" in values:
            write["description"] = str(values["description"] or "")[:500]
        if "steps" in values:
            steps = values["steps"]
            if not isinstance(steps, list):
                return {"ok": False,
                        "error": "Steps must be a list of blocks."}
            for index, step in enumerate(steps):
                if not isinstance(step, dict) or not step.get("type"):
                    return {"ok": False,
                            "error": "Step %d needs a type." % (index + 1)}
            write["steps"] = steps
        if "nodes" in values:
            if not isinstance(values["nodes"], list):
                return {"ok": False, "error": "Nodes must be a list."}
            write["nodes"] = values["nodes"]
            write["spec_version"] = 2
        if "edges" in values:
            if not isinstance(values["edges"], list):
                return {"ok": False, "error": "Edges must be a list."}
            write["edges"] = values["edges"]
        if "trigger_type" in values:
            write["trigger_type"] = values["trigger_type"] or "manual"
        if "trigger_config" in values:
            write["trigger_config"] = values["trigger_config"] or {}
        if "nodes" in write or "edges" in write:
            write["version"] = (record.version or 1) + 1
        if "active" in values:
            write["active"] = bool(values["active"])
        if not write:
            return {"ok": False, "error": "Nothing to save."}
        record.write(write)
        outcome = record.validate()
        return {
            "ok": True,
            "flow": _flow_row(record),
            "problems": [text for _id, text in outcome],
            "valid": not outcome,
        }
    return {"ok": False, "error": "Unknown action."}


def skill_action(env, user, action="explain", query="", code=""):
    """Explain the router: which skill would fire for a request.

    The simulator is the point: an operator can type a question and see
    exactly which skill the router picks — before a real user ever
    asks it. ``code`` names one skill to load (used by the canvas
    copilot side-panel); it is honored instead of dropped.
    """
    from odoo.addons.nova_ai.models.nova_ai_knowledge_tools import (
        _skills_of, select_skill, _skill_score)
    query = (query or "").strip()
    if action == "explain":
        skills = _skills_of(env, user)
        rows = []
        for skill in skills:
            score = _skill_score(query, skill) if query else 0.0
            rows.append({
                "id": skill.id,
                "code": skill.code,
                "name": skill.name,
                "description": skill.description or "",
                "score": round(score, 3),
                "use_count": skill.use_count or 0,
            })
        rows.sort(key=lambda r: (-r["score"], -r["use_count"], r["code"]))
        selected = select_skill(env, query, user) if query else None
        return {
            "ok": True,
            "query": query,
            "skills": rows,
            "selected": selected.code if selected else "",
        }
    if action == "select":
        selected = select_skill(env, query, user)
        return {
            "ok": True,
            "query": query,
            "selected": selected.code if selected else "",
            "body": (selected.body or "") if selected else "",
        }
    if action == "load":
        # Load one skill by code (the route's `code` param finally
        # lands somewhere instead of being dropped).
        wanted = (code or query or "").strip()
        if not wanted:
            return {"ok": False, "error": "A skill code is required."}
        for skill in _skills_of(env, user):
            if skill.code == wanted:
                try:
                    skill.action_touch()
                except Exception:
                    pass
                return {"ok": True, "code": skill.code,
                        "name": skill.name,
                        "body": skill.body or ""}
        return {"ok": False,
                "error": "No skill named %r." % wanted}
    return {"ok": False, "error": "Unknown action."}


def routing_action(env, user, requires=(), tier=""):
    """The routing table: which model covers which capability."""
    router = env["nova.ai.router"]
    rows = router.explain(env, requires=requires or (),
                          tier=tier or None)
    chosen = router.pick(env, requires=requires or (),
                          tier=tier or None)
    return {
        "ok": True,
        "requires": sorted(requires or []),
        "models": rows,
        "chosen": chosen.id if chosen else 0,
        "chosen_name": chosen.name if chosen else "",
    }


class NovaAiStudioController(http.Controller):
    """The Studio's JSON contract. One payload, three actions.

    Every action is a thin wrapper around a pure function of ``env``:
    the logic lives in ``studio_payload`` and friends, so it can be
    exercised without an HTTP request.
    """

    @http.route("/nova/ai/studio", type="jsonrpc", auth="user",
                methods=["POST"])
    def studio(self):
        """Everything the Studio shows, in one round trip."""
        env = request.env
        user = _studio_user()
        return studio_payload(env, user)

    @http.route("/nova/ai/studio/knowledge", type="jsonrpc", auth="user",
                methods=["POST"])
    def studio_knowledge(self, action="", knowledge_id=0, name="",
                         description="", body="", kind="memory",
                         review_note=""):
        """Review a proposal, verify a concept, or write one."""
        _studio_user()
        return knowledge_action(
            request.env, request.env.user, action, knowledge_id, name,
            description, body, kind, review_note)

    @http.route("/nova/ai/studio/flow", type="jsonrpc", auth="user",
                methods=["POST"])
    def studio_flow(self, action="run", flow_id=0, arguments=None):
        """Run a flow, or report its validation state."""
        _studio_user()
        return flow_action(
            request.env, request.env.user, action, flow_id, arguments)

    @http.route("/nova/ai/studio/skill", type="jsonrpc", auth="user",
                methods=["POST"])
    def studio_skill(self, action="explain", code="", query=""):
        """Explain the router: which skill would fire for a request, and
        which model would run it."""
        _studio_user()
        return skill_action(
            request.env, request.env.user, action, query, code)

    @http.route("/nova/ai/studio/routing", type="jsonrpc", auth="user",
                methods=["POST"])
    def studio_routing(self, requires=None, tier=""):
        """The routing table: which model covers which capability."""
        _studio_user()
        return routing_action(
            request.env, request.env.user, requires or (), tier or "")
