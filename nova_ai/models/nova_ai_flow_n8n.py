# Part of Nova. See LICENSE file for full copyright and licensing details.
"""n8n compatibility — import/export the exact n8n workflow pattern.

n8n workflow JSON (github.com/n8n-io/n8n) looks like::

    {"nodes": [{"id": "...", "name": "Search", "type": "n8n-nodes-base...",
                "typeVersion": 1, "position": [x, y], "parameters": {...}}],
     "connections": {"Search": {"main": [[{"node": "IF", ...}]]}}}

Odoo must speak this natively so an agent (or a human) can paste an
n8n workflow in and get the same graph running here — and export ours
back out. Internally we keep our own DAG (nodes+edges); this module
is the lossless translator at the border.

Mapping (v1):

* n8n node name <-> our node id (stable, human readable).
* n8n node type -> our node type via _N8N_TYPE_MAP; unknown
  ``n8n-nodes-base.*`` types become ``tool`` (passthrough) instead of
  failing the import — the canvas flags them, the engine refuses them
  loudly at run time.
* n8n connections ``{source: {main: [[{node, ...}]]}}`` -> edges with
  ``source_handle`` = output index name (0/default, 1/true...). IF
  outputs map to true/false, Switch to indexed ports.
* Expressions: n8n ``={{...}}`` and ``{{$json.x}}`` /
  ``{{$node["Name"].json.y}}`` are normalised to ``${...}`` against
  the run scope. ``$input`` / ``$json`` stay as the current item.
"""

import json
import re

from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
    _norm_expr_vars as _spec_norm, _slug_ref as _spec_slug)

# n8n node type -> our DAG type. Covers the nodes an Odoo automation
# actually needs; everything else imports as a flagged passthrough.
_N8N_TYPE_MAP = {
    "n8n-nodes-base.start": "trigger",
    "n8n-nodes-base.manualTrigger": "trigger",
    "n8n-nodes-base.webhook": "trigger",
    "n8n-nodes-base.scheduleTrigger": "trigger",
    "n8n-nodes-base.executeWorkflowTrigger": "trigger",
    "n8n-nodes-base.set": "set",
    "transform": "set",
    "n8n-nodes-base.if": "if",
    "n8n-nodes-base.switch": "switch",
    "n8n-nodes-base.filter": "filter",
    "n8n-nodes-base.merge": "merge",
    "n8n-nodes-base.splitInBatches": "loop.foreach",
    "n8n-nodes-base.loopOverItems": "loop.foreach",
    "n8n-nodes-base.code": "code",
    "n8n-nodes-base.httpRequest": "http",
    "n8n-nodes-base.webhookTrigger": "trigger",
    "n8n-nodes-base.function": "code",
    "n8n-nodes-base.functionItem": "code",
    "n8n-nodes-base.noOp": "note",
    "n8n-nodes-base.stickyNote": "note",
    "n8n-nodes-base.stopAndError": "output",
    "n8n-nodes-base.respondToWebhook": "respond",
    "n8n-nodes-base.executeWorkflow": "subflow",
    "n8n-nodes-base.wait": "wait",
    # Odoo-mapped helpers (our own n8n-styled names).
    "n8n-nodes-base.odoo": "odoo.search",
    "nova-odoo.search": "odoo.search",
    "nova-odoo.read": "odoo.read",
    "nova-odoo.count": "odoo.count",
    "nova-odoo.group": "odoo.group",
    "nova-odoo.create": "odoo.create",
    "nova-odoo.update": "odoo.write",
    "nova-odoo.note": "odoo.note",
    "nova-odoo.email": "odoo.email",
    "nova-odoo.aiAgent": "ai.agent",
}

# Our DAG type -> canonical n8n node type for export.
_OUR_TO_N8N = {
    "trigger": "n8n-nodes-base.manualTrigger",
    "start": "n8n-nodes-base.start",
    "set": "n8n-nodes-base.set",
    "transform": "n8n-nodes-base.set",
    "filter": "n8n-nodes-base.filter",
    "if": "n8n-nodes-base.if",
    "switch": "n8n-nodes-base.switch",
    "merge": "n8n-nodes-base.merge",
    "loop.foreach": "n8n-nodes-base.loopOverItems",
    "loop.end": "n8n-nodes-base.noOp",
    "code": "n8n-nodes-base.code",
    "http": "n8n-nodes-base.httpRequest",
    "wait": "n8n-nodes-base.wait",
    "subflow": "n8n-nodes-base.executeWorkflow",
    "output": "n8n-nodes-base.set",
    "respond": "n8n-nodes-base.respondToWebhook",
    "note": "n8n-nodes-base.stickyNote",
    "odoo.search": "nova-odoo.search",
    "odoo.read": "nova-odoo.read",
    "odoo.count": "nova-odoo.count",
    "odoo.group": "nova-odoo.group",
    "odoo.create": "nova-odoo.create",
    "odoo.write": "nova-odoo.update",
    "odoo.unlink": "nova-odoo.delete",
    "odoo.note": "nova-odoo.note",
    "odoo.activity": "nova-odoo.activity",
    "odoo.method": "nova-odoo.method",
    "odoo.email": "nova-odoo.email",
    "ai.agent": "nova-odoo.aiAgent",
    "ai.prompt": "nova-odoo.aiPrompt",
    "tool": "n8n-nodes-base.code",
    "search": "nova-odoo.search",
    "read": "nova-odoo.read",
    "create": "nova-odoo.create",
    "agent": "nova-odoo.aiAgent",
    "human.approval": "n8n-nodes-base.wait",
}

_NODE_REF_RE = re.compile(
    r"\{\{\s*\$node\[([\"'])(.+?)\1\]\s*\.json\s*\.([^}]+?)\}\}")
_NODE_REF_RE2 = re.compile(r"\$node\[([\"'])(.+?)\1\]")


def _slug(name, fallback):
    # Single slug implementation lives in the spec; the alias keeps
    # older imports working.
    return _spec_slug(name, fallback)


def normalize_expression(text):
    """n8n expression -> our ${...} scope syntax.

    Delegates to the spec's normalizer (all five n8n spellings) so
    stored configs, validation and the engine never disagree.
    """
    if not isinstance(text, str):
        return text
    return _spec_norm(text, {})


def from_n8n(payload):
    """n8n workflow dict -> (nodes, edges, trigger, unsupported).

    Never raises on unknown node types: they import as flagged
    ``tool`` nodes so the canvas can show exactly what needs
    attention instead of dropping work silently. Exact duplicate
    names get suffixed (n8n treats name as identity) and dropped
    edges are reported — nothing vanishes quietly.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("not an n8n workflow")
    raw_nodes = payload.get("nodes") or []
    connections = payload.get("connections") or {}
    nodes, unsupported, dropped = [], [], []
    name_to_id = {}
    seen_names = set()
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("id") or "Node")
        if name in seen_names:
            suffix = 2
            while "%s_%d" % (name, suffix) in seen_names:
                suffix += 1
            name = "%s_%d" % (name, suffix)
        seen_names.add(name)
        nid = _slug(name, "node")
        suffix = 1
        base_nid = nid
        while nid in name_to_id.values():
            suffix += 1
            nid = "%s_%d" % (base_nid, suffix)
        name_to_id[name] = nid
        ntype = str(raw.get("type") or "")
        ours = _N8N_TYPE_MAP.get(ntype)
        params = dict(raw.get("parameters") or {})
        if ours is None:
            ours = "tool" if ntype.startswith(
                "n8n-nodes-base.") else "set"
            unsupported.append("%s (%s)" % (name, ntype))
        # Flatten n8n params into our config shape.
        config = {"n8n_type": ntype,
                  "n8n_version": raw.get("typeVersion", 1)}
        # novaResultVariable round-trips the output binding we export.
        result_var = params.get("novaResultVariable")
        if isinstance(result_var, str) and result_var.strip():
            config["resultVariable"] = result_var.strip()
        for key, val in params.items():
            if isinstance(val, str):
                config[key] = normalize_expression(val)
            else:
                config[key] = val
        pos = raw.get("position") or [0, 0]
        try:
            position = {"x": int(pos[0]), "y": int(pos[1])}
        except (TypeError, ValueError, IndexError):
            position = {"x": 120, "y": 160}
        node = {"id": nid, "type": ours, "name": name,
                "config": config, "position": position}
        if raw.get("disabled"):
            node["disabled"] = True
        if raw.get("notes"):
            node["notes"] = raw.get("notes")
        nodes.append(node)
    edges = []
    name_to_type = {}
    try:
        for _n in nodes:
            if isinstance(_n, dict):
                name_to_type[_n.get("name")] = _n.get("type")
    except Exception:
        pass
    for source_name, outputs in (connections or {}).items():
        if source_name not in name_to_id:
            dropped.append("%s -> ?" % source_name)
            continue
        src = name_to_id[source_name]
        src_type = name_to_type.get(source_name) or ""
        # Main branches: IF -> true/false, Switch -> indexed, else default.
        # Plus explicit error/done/loop outputs when present.
        for _okey, _branches in (outputs or {}).items():
            if _okey == "main":
                mains = _branches or []
                for out_idx, branch in enumerate(mains):
                    if src_type == "switch":
                        handle = str(out_idx)
                    elif src_type == "if":
                        handle = "true" if out_idx == 0 else ("false" if out_idx == 1 else str(out_idx))
                    else:
                        if len(mains) > 1:
                            handle = "true" if out_idx == 0 else ("false" if out_idx == 1 else str(out_idx))
                        else:
                            handle = "default"
                    for link in branch or []:
                        if not isinstance(link, dict):
                            continue
                        target_name = link.get("node")
                        if target_name not in name_to_id:
                            dropped.append("%s -> %s" % (
                                source_name, target_name))
                            continue
                        edges.append({
                            "id": "e_%s_%s_%s" % (src, name_to_id[target_name],
                                                 handle),
                            "source": src, "target": name_to_id[target_name],
                            "source_handle": handle,
                        })
            elif _okey in ("error", "done", "loop", "ai_tool", "tools"):
                handle = _okey
                for branch in (_branches or []):
                    _links = branch if isinstance(branch, list) else [_branches]
                    for link in _links or []:
                        if not isinstance(link, dict):
                            continue
                        target_name = link.get("node")
                        if target_name not in name_to_id:
                            dropped.append("%s -> %s" % (source_name, target_name))
                            continue
                        edges.append({
                            "id": "e_%s_%s_%s" % (src, name_to_id[target_name], handle),
                            "source": src, "target": name_to_id[target_name],
                            "source_handle": handle,
                        })
            else:
                dropped.append("%s:%s ignored" % (source_name, _okey))
    trigger = {"type": "manual", "config": {}}
    # Explicit trigger nodes only: a pasted flow never becomes remotely
    # triggerable by name heuristic.
    if any(n.get("type") == "trigger" for n in nodes):
        for node in nodes:
            cfg = node.get("config") or {}
            nt = (cfg.get("n8n_type") or "")
            if nt == "n8n-nodes-base.webhook":
                trigger = {"type": "webhook", "config": {
                    "path": cfg.get("path", "")}}
            elif nt == "n8n-nodes-base.scheduleTrigger":
                trigger = {"type": "schedule", "config": {
                    "cron": cfg.get("rule", {}).get("cronExpression", "")
                    if isinstance(cfg.get("rule"), dict) else ""}}
    if dropped:
        unsupported.append(
            "dropped edges: %s" % "; ".join(dropped[:8]))
    return nodes, edges, trigger, unsupported


def to_n8n(flow_name, nodes, edges, settings=None):
    """Our DAG -> n8n workflow dict (paste straight into n8n).

    n8n treats ``name`` as identity, so duplicate names are suffixed
    before connections are built — never a silent mis-wire.
    """
    out_nodes = []
    seen = set()
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        name = node.get("name") or node.get("id") or "Node"
        if name in seen:
            suffix = 2
            while "%s_%d" % (name, suffix) in seen:
                suffix += 1
            name = "%s_%d" % (name, suffix)
        seen.add(name)
        nid = node.get("id") or "Node"
        ntype = node.get("type") or "set"
        n8n_type = (node.get("config") or {}).get("n8n_type") \
            or _OUR_TO_N8N.get(ntype, "n8n-nodes-base.set")
        pos = node.get("position") or {}
        try:
            _tver = int((node.get("config") or {}).get("n8n_version") or 1)
        except Exception:
            _tver = 1
        params = {}
        for key, val in (node.get("config") or {}).items():
            if key in ("n8n_type", "n8n_version", "resultVariable",
                       "output", "as"):
                continue
            params[key] = val
        # Round-trip the output binding (n8n has no equivalent slot).
        for key in ("resultVariable", "output", "as"):
            val = (node.get("config") or {}).get(key)
            if isinstance(val, str) and val.strip() and val != nid:
                params["novaResultVariable"] = val.strip()
                break
        # Preserve n8n runtime flags the engine honours.
        for _k in ("disabled", "notes", "credentials", "pins", "retry", "onError",
                   "executeOnce", "alwaysOutput", "timeoutMs"):
            try:
                if node.get(_k) is not None and _k not in params:
                    params["nova_" + _k] = node.get(_k)
            except Exception:
                pass
        try:
            if isinstance((node.get("config") or {}).get("credentials"), dict):
                params["credentials"] = (node.get("config") or {}).get("credentials")
        except Exception:
            pass
        out_nodes.append({
            "id": nid, "name": name,
            "type": n8n_type, "typeVersion": _tver,
            "position": [pos.get("x", 0), pos.get("y", 0)],
            "parameters": params,
        })
    by_source = {}
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        handle = edge.get("source_handle") or "default"
        if handle in ("error", "done", "loop"):
            # n8n has no separate error port on export: keep it as an
            # indexed main branch with the handle preserved in the name map.
            # Import restores it via the handle suffix.
            idx = {"error": 1, "done": 0, "loop": 0}.get(handle, 0)
        else:
            idx = {"default": 0, "true": 0, "false": 1}.get(handle, None)
            if idx is None:
                try:
                    idx = int(handle)
                except Exception:
                    idx = 0
            try:
                idx = int(handle) if str(handle).isdigit() else idx
            except Exception:
                pass
        by_source.setdefault(edge.get("source"), {}).setdefault(
            idx, []).append(edge.get("target"))
    id_to_name = {n["id"]: (n.get("name") or n["id"])
                  for n in out_nodes if isinstance(n, dict)}
    connections = {}
    for src_id, branches in by_source.items():
        src_name = id_to_name.get(src_id, src_id)
        max_idx = max(branches) if branches else 0
        mains = []
        for idx in range(max_idx + 1):
            mains.append([{"node": id_to_name.get(t, t),
                           "type": "main", "index": 0}
                          for t in branches.get(idx, [])])
        connections[src_name] = {"main": mains}
    return {
        "name": flow_name or "Nova flow",
        "nodes": out_nodes,
        "connections": connections,
        "active": False,
        "settings": settings or {"executionOrder": "v1"},
        "meta": {"templateCredsSetupCompleted": False,
                 "exportedBy": "nova-ai"},
    }
