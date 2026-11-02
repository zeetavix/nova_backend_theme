# Part of Nova. See LICENSE file for full copyright and licensing details.
"""n8n-class flow spec — nodes, edges, triggers.

A flow is a DAG, not a list:

* ``nodes``: [{id, type, name, config, position}] — what runs.
* ``edges``: [{id, source, target, source_handle, label, condition}] —
  how data moves. ``source_handle`` selects the output branch
  (``true``/``false`` for IF, indexed ports for SWITCH, ``done``/
  ``error`` for error routing).
* ``trigger``: {type, config} — manual | chat | webhook | schedule |
  record. The old ``steps`` list keeps working: it is compiled to a
  linear graph on the fly (see ``steps_to_graph``).

Node types (Community, clean-room — no Enterprise code):

* trigger/start      — entry point, declares input variables
* odoo.search/read/create/write/unlink — record ops (writes queue cards)
* odoo.activity/note/email — collaboration ops
* odoo.method        — whitelisted business method behind a card
* if / switch / filter — branching on expressions
* set / transform    — build values with ${var} expressions
* code               — sandboxed Python expression (safe_eval, no imports)
* http               — outbound webhook with SSRF guard
* ai.agent / ai.prompt — ask an agent / run a model prompt
* loop.foreach / merge — iterate a list, join branches
* wait / delay       — pause a run (cron-resumed, capped)
* human.approval     — pause until a card is approved
* subflow            — run another flow, namespaced outputs
* output/respond     — final value
* on.error           — error-route target marker

Expressions keep the legacy ``${path}`` syntax and add ``{{$json…}}``
aliases used by n8n authors: both resolve against the same run scope.
"""

import re
import uuid

# Every node type the canvas may create and the engine may run.
# The engine rejects anything else — a flow cannot smuggle a new
# capability past this list.
NODE_TYPES = [
    ("trigger", "Trigger"),
    ("start", "Start"),
    ("odoo.search", "Search records"),
    ("odoo.read", "Read a record"),
    ("odoo.count", "Count records"),
    ("odoo.group", "Group & aggregate"),
    ("odoo.create", "Create record"),
    ("odoo.write", "Update record"),
    ("odoo.unlink", "Delete record"),
    ("odoo.activity", "Schedule activity"),
    ("odoo.note", "Post a note"),
    ("odoo.email", "Send email"),
    ("odoo.method", "Run business method"),
    ("if", "IF condition"),
    ("switch", "Switch"),
    ("filter", "Filter"),
    ("set", "Set values"),
    ("transform", "Transform"),
    ("code", "Code"),
    ("http", "HTTP request"),
    ("ai.agent", "Ask an agent"),
    ("ai.prompt", "AI prompt"),
    ("loop.foreach", "Loop over items"),
    ("loop.end", "End loop"),
    ("merge", "Merge"),
    ("wait", "Wait"),
    ("human.approval", "Approval"),
    ("subflow", "Run subflow"),
    ("output", "Output"),
    ("respond", "Respond"),
    ("note", "Sticky note"),
    # Legacy linear aliases — accepted by validation, compiled away.
    ("search", "Search records"),
    ("read", "Read a record"),
    ("create", "Queue a new record"),
    ("tool", "Run a tool"),
    ("agent", "Ask another agent"),
]

TRIGGER_TYPES = [
    ("manual", "Manual"),
    ("chat", "Chat"),
    ("webhook", "Webhook"),
    ("schedule", "Schedule"),
    ("record", "Record event"),
]

# Tools a flow may never call unattended.
_FORBIDDEN_TOOLS = frozenset({
    "unlink_record", "execute_method", "read_attachment",
})

# P011 — per-node runtime options (n8n parity). Validated, never trusted.
_NODE_OPTIONS = {
    "retry": {"types": (int,), "min": 0, "max": 5},
    "timeoutMs": {"types": (int,), "min": 1000, "max": 120000},
    "onError": {"types": (str,), "choices": ("stop", "continue")},
    "executeOnce": {"types": (bool,)},
    "alwaysOutput": {"types": (bool,)},
    "notes": {"types": (str,), "maxlen": 2000},
    "disabled": {"types": (bool,)},
}

# P012 — credentials are references, never inline secrets.
_CRED_SECRET_KEYS = ("secret", "token", "password", "api_key", "apikey",
                     "private_key", "client_secret")

# P014 — workflow settings block.
_SETTINGS_SCHEMA = {
    "executionOrder": ("v0", "v1"),
    "timezone": str,
    "errorWorkflow": str,
    "retentionDays": int,
}


def validate_node_options(node):
    """P011/P012: validate runtime options + credential refs."""
    problems = []
    nid = (node or {}).get("id") or "node"
    _nm = (node or {}).get("name") or nid
    for key, rule in _NODE_OPTIONS.items():
        if key not in (node or {}):
            continue
        val = node[key]
        if not isinstance(val, rule["types"]):
            # ints arrive as floats from JSON — accept integral ones
            # (reported, never mutated: validation must not rewrite).
            if not (rule["types"] == (int,)
                    and isinstance(val, float)
                    and float(val).is_integer()):
                problems.append("node %r (%s): %s expects %s — fix: set %s to %s"
                                % (_nm, nid, key, "/".join([t.__name__ for t in rule["types"]]), key, repr(rule.get("choices") or rule.get("max") or "")))
                continue
        if "min" in rule and isinstance(val, (int, float)):
            if val < rule["min"] or val > rule["max"]:
                problems.append("node %r (%s): %s=%r out of range %s-%s — fix: set %s between %s and %s" % (_nm, nid, key, val, rule["min"], rule["max"], key, rule["min"], rule["max"]))
        if "choices" in rule and val not in rule["choices"]:
            problems.append("node %r (%s): %s=%r must be one of %s"
                            % (_nm, nid, key, val, "/".join(rule["choices"])))
        if "maxlen" in rule and len(val) > rule["maxlen"]:
            problems.append("node %r (%s): %s too long (%d > %d) — fix: shorten notes" % (_nm, nid, key, len(val), rule["maxlen"]))
    creds = (node or {}).get("credentials")
    if creds is not None:
        if not isinstance(creds, dict):
            problems.append("node %r: credentials must be an object "
                            "of {name: vault-id}" % nid)
        else:
            for name, ref in creds.items():
                if not isinstance(ref, (str, int)) or not str(ref).strip():
                    problems.append("node %r: credential %r needs a "
                                    "vault reference" % (nid, name))
    # Inline-secret guard: a credential value that looks like a raw
    # secret is refused — secrets live in the vault, never in JSON.
    for key, val in ((node.get("config") or {}).items()
                     if isinstance(node, dict) else []):
        if isinstance(key, str) and isinstance(val, str) \
                and key.lower() in _CRED_SECRET_KEYS and len(val) > 12:
            problems.append("node %r: %r looks like an inline secret — "
                            "use credentials instead" % (nid, key))
    pins = (node or {}).get("pins")
    if pins is not None and not isinstance(pins, (list, dict)):
        problems.append("node %r: pins must be a list or object" % nid)
    return problems


def validate_trigger(trigger):
    """P013: trigger schema per type."""
    problems = []
    trig = trigger or {}
    ttype = trig.get("type") or "manual"
    cfg = trig.get("config") or {}
    if not isinstance(cfg, dict):
        return ["trigger config must be an object"]
    if ttype == "webhook":
        path = str(cfg.get("path") or "")
        if path and not path.startswith("/"):
            problems.append("webhook path must start with /")
        if cfg.get("hmac_secret_ref") is not None and not str(
                cfg.get("hmac_secret_ref")).strip():
            problems.append("webhook hmac_secret_ref is empty")
    elif ttype == "schedule":
        if not (cfg.get("cron") or cfg.get("interval_minutes")):
            problems.append("schedule trigger needs cron or "
                            "interval_minutes")
        if cfg.get("interval_minutes") is not None:
            try:
                mins = int(cfg["interval_minutes"])
                if mins < 1 or mins > 525600:
                    problems.append("interval_minutes out of range")
            except (TypeError, ValueError):
                problems.append("interval_minutes must be a number")
    elif ttype == "record":
        if not cfg.get("model"):
            problems.append("record trigger needs a model")
        if cfg.get("event") and cfg["event"] not in (
                "create", "write", "unlink", "all"):
            problems.append("record event must be create/write/unlink/all")
    return problems


def validate_settings(settings):
    """P014: workflow settings block."""
    problems = []
    if settings is None:
        return problems
    if not isinstance(settings, dict):
        return ["settings must be an object"]
    if "executionOrder" in settings and settings["executionOrder"] \
            not in _SETTINGS_SCHEMA["executionOrder"]:
        problems.append("settings.executionOrder must be v0 or v1")
    # timezone/errorWorkflow are stored for future use — but a wrong
    # type today is a typo, not a plan. (Documented as inert.)
    if "timezone" in settings and not isinstance(
            settings["timezone"], str):
        problems.append("settings.timezone must be text")
    if "errorWorkflow" in settings and not isinstance(
            settings["errorWorkflow"], str):
        problems.append("settings.errorWorkflow must be text")
    if "retentionDays" in settings:
        try:
            days = int(settings["retentionDays"])
            if days < 1 or days > 3650:
                problems.append("settings.retentionDays out of range")
        except (TypeError, ValueError):
            problems.append("settings.retentionDays must be a number")
    return problems

# Legacy linear type -> graph node type.
_LEGACY_MAP = {
    "start": "start",
    "search": "odoo.search",
    "read": "odoo.read",
    "create": "odoo.create",
    "note": "odoo.note",
    "output": "output",
    "tool": "tool",
    "agent": "ai.agent",
}

# n8n node-type aliases, shared with the engine: the exact names n8n
# authors paste. The validator canonicalises through this map before
# checking types, so a smuggled n8n_type can never slip past.
_N8N_TYPE_ALIAS = {
    "n8n-nodes-base.start": "start",
    "n8n-nodes-base.manualTrigger": "trigger",
    "n8n-nodes-base.webhook": "trigger",
    "n8n-nodes-base.scheduleTrigger": "trigger",
    "n8n-nodes-base.executeWorkflowTrigger": "trigger",
    "n8n-nodes-base.set": "set",
    "n8n-nodes-base.if": "if",
    "n8n-nodes-base.switch": "switch",
    "n8n-nodes-base.filter": "filter",
    "n8n-nodes-base.merge": "merge",
    "n8n-nodes-base.splitInBatches": "loop.foreach",
    "n8n-nodes-base.loopOverItems": "loop.foreach",
    "n8n-nodes-base.code": "code",
    "n8n-nodes-base.function": "code",
    "n8n-nodes-base.functionItem": "code",
    "n8n-nodes-base.httpRequest": "http",
    "n8n-nodes-base.noOp": "note",
    "n8n-nodes-base.stickyNote": "note",
    "n8n-nodes-base.stopAndError": "output",
    "n8n-nodes-base.respondToWebhook": "respond",
    "n8n-nodes-base.executeWorkflow": "subflow",
    "n8n-nodes-base.wait": "wait",
    "nova-odoo.search": "odoo.search",
    "nova-odoo.read": "odoo.read",
    "nova-odoo.count": "odoo.count",
    "nova-odoo.group": "odoo.group",
    "nova-odoo.create": "odoo.create",
    "nova-odoo.update": "odoo.write",
    "nova-odoo.note": "odoo.note",
    "nova-odoo.email": "odoo.email",
    "nova-odoo.aiAgent": "ai.agent",
    "transform": "set",
}


def canon_ntype(node):
    """Canonical DAG type for a node (alias-aware)."""
    raw = (node or {}).get("type") or "set"
    if raw in _N8N_TYPE_ALIAS:
        return _N8N_TYPE_ALIAS[raw]
    cfg = (node or {}).get("config") or {}
    n8n_type = cfg.get("n8n_type") or ""
    if n8n_type in _N8N_TYPE_ALIAS:
        return _N8N_TYPE_ALIAS[n8n_type]
    return raw


_VAR_RE = re.compile(r"\$\{([^}]+)\}")
_NODE_BRACKET_RE = re.compile(
    r"\{\{\s*\$node\[([\"'])(.+?)\1\]\s*\.json\s*\.([^}]+?)\s*\}\}")
_NODE_DOT_RE = re.compile(
    r"\{\{\s*\$node\.([\w]+)\s*\.json\s*\.([^}]+?)\s*\}\}")
_JSON_BRACKET_RE = re.compile(r"\{\{\s*\$json\[([\"'])(.+?)\1\]\s*\}\}")
_N8N_RE = re.compile(r"\{\{\s*\$json(?:\.([^}]+))?\s*\}\}")
_N8N_DOLLAR_RE = re.compile(r"\{\{\s*\$([a-zA-Z_][\w.]*)\s*\}\}")


def new_id(prefix="n"):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:8])


def _resolve_path(data, path):
    """Resolve ``a.b[0].c`` against a dict/list structure."""
    cur = data
    for part in re.split(r"\.(?![^\[]*\])", path or ""):
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


def _slug_ref(name, fallback="node"):
    base = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip()).strip("_")
    return base[:60] or fallback


def _norm_expr_vars(text, scope):
    """Rewrite n8n spellings into ${...}.

    Covers ``={{...}}`` prefixes, ``{{$json.path}}``,
    ``{{$json["field"]}}``, ``{{$node["Name"].json.path}}`` and
    ``{{$node.Name.json.path}}`` — node names become scope keys
    through the same slug the converter uses.
    """
    if not isinstance(text, str):
        return text
    out = text.strip()
    if out.startswith("="):
        out = out[1:]

    def _node_sub(match):
        return "${%s.%s}" % (
            _slug_ref(match.group(2), "node"),
            match.group(3).strip())

    out = _NODE_BRACKET_RE.sub(_node_sub, out)
    out = _NODE_DOT_RE.sub(_node_sub, out)

    # E1/E2: token-level pass inside {{...}} so binary JS
    # ({{$json.a + $json.b}}) and $input/$('Name') spellings normalise
    # even when the block-level regex stops at the first }.
    try:
        # $('Name').json.path / $("Name").json.path
        out = re.sub(
            r"\$\(\s*[\"'](.+?)[\"']\s*\)\s*\.json\s*\.([A-Za-z0-9_\.\[\]]+)",
            lambda m: "${%s.%s}" % (_slug_ref(m.group(1), "node"), m.group(2).strip()),
            out)
        # $input.first().json.path / last / all()[i] / item
        out = re.sub(r"\$input\s*\.\s*first\s*\(\s*\)\s*\.json\s*\.([A-Za-z0-9_\.\[\]]+)",
                     r"${\1}", out)
        out = re.sub(r"\$input\s*\.\s*last\s*\(\s*\)\s*\.json\s*\.([A-Za-z0-9_\.\[\]]+)",
                     r"${\1}", out)
        out = re.sub(r"\$input\s*\.\s*item\s*\.json\s*\.([A-Za-z0-9_\.\[\]]+)",
                     r"${\1}", out)
        out = re.sub(r"\$input\s*\.\s*all\s*\(\s*\)", "${items}", out)
        # Bare $json.path tokens (covers binary expressions).
        out = re.sub(r"\$json\s*\.\s*([A-Za-z0-9_\.\[\]]+)",
                     r"${\1}", out)
    except Exception:
        pass

    def _jbracket_sub(match):
        return "${%s}" % match.group(2).strip()

    out = _JSON_BRACKET_RE.sub(_jbracket_sub, out)

    def _json_sub(match):
        path = (match.group(1) or "").strip()
        return "${%s}" % path if path else "${json}"

    out = _N8N_RE.sub(_json_sub, out)

    def _dollar_sub(match):
        return "${%s}" % match.group(1)

    return _N8N_DOLLAR_RE.sub(_dollar_sub, out)


def interp(value, variables):
    """Replace ``${path}`` placeholders (and n8n aliases).

    Unknown paths stay as written — an empty string silently turns
    ``ilike`` into match-all and hides the typo, while a leftover
    placeholder fails loudly in domain validation.
    """
    if isinstance(value, str):
        value = _norm_expr_vars(value, variables)

        def sub(match):
            found = _resolve_path(variables, match.group(1))
            if found is None:
                return match.group(0)
            if isinstance(found, (dict, list)):
                import json as _json
                return _json.dumps(found, default=str)
            return str(found)

        return _VAR_RE.sub(sub, value)
    if isinstance(value, list):
        return [interp(v, variables) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = interp(k, variables) if isinstance(k, str) else k
            out[key] = interp(v, variables)
        return out
    return value


def config_strings(value):
    """Every string inside a node config, however deeply nested."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from config_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from config_strings(item)


def steps_to_graph(steps):
    """Compile a legacy linear ``steps`` list into nodes+edges."""
    nodes, edges = [], []
    prev = None
    for index, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        kind = step.get("type") or "set"
        node_type = _LEGACY_MAP.get(kind, kind)
        config = dict(step.get("config") or {})
        # Legacy tool indirection: search/read/create carried args.model
        # style configs; keep them as-is so the engine can reuse them.
        nid = "n%d_%s" % (index + 1, (kind or "node"))
        node = {
            "id": nid,
            "type": node_type,
            "name": "%s %d" % ((kind or "step").title(), index + 1),
            "config": config,
            "position": {"x": 120 + index * 220, "y": 160},
        }
        if step.get("resultVariable"):
            node["config"] = dict(config)
            node["config"]["resultVariable"] = step["resultVariable"]
        nodes.append(node)
        if prev:
            edges.append({
                "id": "e_%s_%s" % (prev, nid),
                "source": prev, "target": nid,
            })
        prev = nid
    if nodes and nodes[0]["type"] not in ("start", "trigger"):
        nodes.insert(0, {
            "id": "n0_start", "type": "start", "name": "Start",
            "config": {"variables": []},
            "position": {"x": 40, "y": 160},
        })
        edges.insert(0, {
            "id": "e_n0_%s" % nodes[1]["id"],
            "source": "n0_start", "target": nodes[1]["id"],
        })
    return nodes, edges


def graph_to_steps(nodes, edges):
    """Best-effort linearisation for the legacy form view."""
    order = topo_order(nodes, edges)
    by_id = {n.get("id"): n for n in (nodes or []) if isinstance(n, dict)}
    steps = []
    for nid in order:
        node = by_id.get(nid)
        if not node:
            continue
        ntype = node.get("type") or "set"
        rev = {
            "odoo.search": "search", "odoo.read": "read",
            "odoo.create": "create", "odoo.note": "note",
            "output": "output", "ai.agent": "agent", "tool": "tool",
            "start": "start", "trigger": "start",
        }.get(ntype, ntype)
        config = dict(node.get("config") or {})
        step = {"type": rev, "config": config}
        if config.get("resultVariable"):
            step["resultVariable"] = config.pop("resultVariable")
        steps.append(step)
    return steps


def topo_order(nodes, edges):
    """Topological order of node ids (Kahn, stable by position.x).

    Only default-port edges count toward indegree: branch ports
    (true/false/error/indexed) and done/loop ports express control
    flow, not data dependency, and must never fake a cycle or an
    order.
    """
    ids = [n.get("id") for n in (nodes or [])
           if isinstance(n, dict) and n.get("id")]
    indeg = {i: 0 for i in ids}
    children = {i: [] for i in ids}
    for edge in (edges or []):
        if not isinstance(edge, dict):
            continue
        src, tgt = edge.get("source"), edge.get("target")
        handle = edge.get("source_handle") or "default"
        if src in indeg and tgt in indeg:
            if handle == "default":
                indeg[tgt] += 1
            children[src].append((tgt, handle))
    pos = {}
    for n in (nodes or []):
        try:
            pos[n.get("id")] = (n.get("position") or {}).get("x", 0)
        except Exception:
            pos[n.get("id")] = 0
    import heapq as _hq
    heap = [(pos.get(i, 0), i) for i, d in indeg.items() if d == 0]
    _hq.heapify(heap)
    order = []
    queued = {i for i, d in indeg.items() if d == 0}
    while heap:
        _, nid = _hq.heappop(heap)
        if nid in order:
            continue
        order.append(nid)
        for child, handle in children.get(nid, []):
            # Mirrors the counting above: only default edges carry
            # order weight, so branch edges can never drive indegree
            # negative or duplicate entries.
            if handle != "default":
                if child not in queued:
                    queued.add(child)
                    _hq.heappush(heap, (pos.get(child, 0), child))
                continue
            indeg[child] -= 1
            if indeg[child] <= 0 and child not in queued:
                queued.add(child)
                _hq.heappush(heap, (pos.get(child, 0), child))
    # Cycles: append leftovers in position order, validation flags them.
    for nid in sorted(set(ids) - set(order),
                      key=lambda i: pos.get(i, 0)):
        order.append(nid)
    return order


def validate_graph(nodes, edges, trigger=None, settings=None):
    """Return a list of problem strings. Empty means runnable."""
    problems = []
    nodes = nodes or []
    edges = edges or []
    problems.extend(validate_trigger(trigger or {"type": "manual"}))
    problems.extend(validate_settings(settings))
    # Duplicate node names slug to the same $node scope key and one
    # silently wins — refuse the ambiguity at design time.
    seen_slugs = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        slug = _slug_ref(node.get("name") or node.get("id") or "", "node")
        if slug in seen_slugs:
            problems.append(
                "node names %r and %r collide as $node.%s — rename one"
                % (seen_slugs[slug], node.get("name") or node.get("id"),
                   slug))
        else:
            seen_slugs[slug] = node.get("name") or node.get("id")
    if not isinstance(nodes, list) or not nodes:
        return ["a flow needs at least one node"]
    allowed = {t for t, _label in NODE_TYPES}
    ids = set()
    seen_outputs = {}
    for index, node in enumerate(nodes):
        _nid = node.get("id") if isinstance(node, dict) else "?"
        _nm = node.get("name") if isinstance(node, dict) else "?"
        _tp = canon_ntype(node) if isinstance(node, dict) else "?"
        label = "node %d %r (%s, %s)" % (index + 1, _nm, _nid, _tp)
        if not isinstance(node, dict) or not node.get("id"):
            problems.append("%s needs an id" % label)
            continue
        problems.extend(validate_node_options(node))
        nid = node.get("id")
        if nid in ids:
            problems.append("node id %r is used twice" % nid)
        ids.add(nid)
        # Canonicalise first: what the engine will run is what
        # gets validated — a smuggled n8n_type can never slip past.
        # A declared DAG type that disagrees with its own n8n_type
        # (e.g. type=set running as subflow) is a lie, not a quirk.
        raw_declared = node.get("type")
        ntype = canon_ntype(node)
        smuggled = (node.get("config") or {}).get("n8n_type")
        if smuggled and raw_declared != ntype:
            problems.append(
                "%s declares type %r but its n8n node runs as %r — "
                "fix the type instead" % (label, raw_declared, ntype))
            continue
        if node.get("type") not in allowed \
                and ntype not in allowed \
                and (node.get("config") or {}).get("n8n_type") not in (
                    _N8N_TYPE_ALIAS):
            # Truly unknown: neither the declared type, the alias,
            # nor an n8n native type the converter understands.
            if (node.get("config") or {}).get("n8n_type"):
                problems.append(
                    "%s uses n8n node %r, which has no Odoo mapping "
                    "yet — replace it or map it first"
                    % (label, (node.get("config") or {})["n8n_type"]))
            else:
                problems.append("%s has unknown type %r"
                                % (label, node.get("type")))
            continue
        config = node.get("config")
        if config is not None and not isinstance(config, dict):
            problems.append("%s has a malformed config" % label)
            continue
        config = config or {}
        tool_code = (config.get("tool") or "")
        if ntype == "tool" and tool_code in _FORBIDDEN_TOOLS:
            problems.append(
                "%s uses %s, which a flow may not run" % (label, tool_code))
        names = [k for k in ("resultVariable", "as")
                 if isinstance((config.get(k) or ""), str)
                 and (config.get(k) or "").strip()]
        if isinstance(config.get("output"), str) \
                and config["output"].strip():
            names.append(config["output"].strip())
        if len(set(names)) > 1:
            problems.append(
                "%s names its output %s — pick one of "
                "resultVariable/as/output" % (label, names))
        if node.get("pins") is not None and ntype in (
                "start", "trigger", "note", "merge", "if", "switch",
                "filter", "loop.foreach", "loop.end", "output",
                "respond", "subflow", "wait", "human.approval"):
            problems.append(
                "%s pins are ignored on %s nodes — pins only feed "
                "compute nodes (fix: remove pins or move to a compute node)" % (label, ntype))
        if ntype == "http":
            url = str(config.get("url") or "")
            if url and ("${" in url or "{{" in url):
                pass  # templated — checked at run time
            elif url and not url.startswith("https://"):
                problems.append(
                    "%s url must use https:// — plain http is refused "
                    "by policy" % label)
        if ntype == "code":
            code = str(config.get("code") or "")
            if not code.strip():
                problems.append("%s needs code to run" % label)
            elif len(code) > 8000:
                problems.append("%s code is too long (max 8000 chars)" % label)
        if ntype in ("output", "respond"):
            if not str(config.get("value") or "").strip() \
                    and not str(config.get("resultVariable") or "").strip() \
                    and not str(config.get("as") or "").strip() \
                    and not str(config.get("output") or "").strip():
                problems.append(
                    "%s returns nothing — give it a value" % label)
        if ntype == "wait":
            try:
                secs = int(config.get("seconds") or 0)
            except (TypeError, ValueError):
                problems.append(
                    "%s seconds must be a number" % label)
                secs = 0
            if secs < 0:
                problems.append("%s seconds cannot be negative" % label)
        if ntype == "subflow" and not str(
                config.get("flow") or "").strip():
            problems.append(
                "%s names no flow to run" % label)
        if ntype == "tool" and not str(
                config.get("tool") or "").strip() and (
                    config.get("n8n_type") or "") not in _N8N_TYPE_ALIAS:
            problems.append(
                "%s names no tool — an unmapped n8n node needs a "
                "tool mapping first" % label)
        if ntype in ("if",):
            if not str(config.get("condition") or "").strip():
                problems.append("%s needs a condition" % label)
        if ntype in ("loop.foreach",):
            if not str(config.get("items") or "").strip():
                problems.append("%s needs an items expression" % label)
        if ntype in ("odoo.search", "odoo.count", "odoo.group",
                     "odoo.read", "odoo.create", "odoo.write"):
            model = ((config.get("args") or {}).get("model")
                     or config.get("model") or "")
            if not str(model).strip():
                problems.append("%s needs a model" % label)
        if ntype == "odoo.group":
            groupby = ((config.get("args") or {}).get("groupby")
                       or config.get("groupby") or [])
            if not groupby:
                problems.append("%s needs groupby fields" % label)
        if ntype == "odoo.email":
            if not str(config.get("to") or "").strip() and not str(
                    ((config.get("args") or {}).get("to")) or "").strip():
                problems.append("%s needs a recipient" % label)
            if not str(config.get("subject") or "").strip() and not str(
                    ((config.get("args") or {}).get("subject"))
                    or "").strip():
                problems.append("%s needs a subject" % label)
    by_id = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    for edge in edges:
        if not isinstance(edge, dict):
            problems.append("an edge is not an object")
            continue
        if edge.get("source") not in ids or edge.get("target") not in ids:
            problems.append(
                "edge %s points at a missing node"
                % (edge.get("id") or "unnamed"))
            continue
        if edge.get("source") == edge.get("target"):
            problems.append("a node may not link to itself")
        handle = edge.get("source_handle") or "default"
        src_type = canon_ntype(by_id.get(edge["source"]) or {})
        # Switch nodes own dynamic outputs (rules name their ports);
        # every other node speaks the fixed port vocabulary.
        if src_type == "switch":
            if not str(handle).strip():
                problems.append(
                    "edge %s from a Switch needs a port name"
                    % (edge.get("id") or "unnamed"))
        elif handle not in ("default", "true", "false", "error",
                            "done", "loop") and not handle.isdigit():
            problems.append(
                "edge %s uses unknown port %r (default/true/false/"
                "error/done/loop or an index)" % (
                    edge.get("id") or "unnamed", handle))
    # Reachability: every node except triggers should be reachable.
    # Canonical types: an n8n-native trigger counts as a trigger.
    entry = [n.get("id") for n in nodes
             if isinstance(n, dict)
             and canon_ntype(n) in ("trigger", "start")]
    if not entry:
        problems.append("a flow needs a Trigger or Start node")
    # Forward-reference check across the DAG order. Seeded with
    # every name the runtime provides (defaults, caller args land on
    # the same keys, loop/item context, error and input aliases).
    order = topo_order(nodes, edges)
    written = {"today", "today_start", "today_end", "json", "$json",
               "$now", "$run", "$trigger", "$item", "$index", "$items",
               "item", "index", "items", "$error", "$input", "input",
               "data", "$flow_depth"}
    seen_vars = set()
    for nid in order:
        node = by_id.get(nid) or {}
        if canon_ntype(node) in ("start", "trigger"):
            for var in ((node.get("config") or {}).get("variables") or []):
                if not isinstance(var, dict) or not var.get("name"):
                    continue
                vname = var["name"]
                if vname in seen_vars:
                    problems.append(
                        "input %r is declared twice — the later "
                        "declaration wins silently" % vname)
                elif vname in ids:
                    problems.append(
                        "input %r shadows node id %r — rename one"
                        % (vname, vname))
                seen_vars.add(vname)
                written.add(vname)
        # Name-slugs are scope keys too ($node["My Node"] -> slug).
        try:
            written.add(_slug_ref(node.get("name") or nid, "node"))
        except Exception:
            pass
        _bad = 0
        for text in config_strings(node.get("config") or {}):
            for path in _VAR_RE.findall(_norm_expr_vars(text, {})):
                root = re.split(r"[.\[]", path.strip())[0].strip()
                if root and root not in written:
                    problems.append(
                        "node %r (%s) reads %r before anything writes it — fix: write it upstream or rename to %s_all" 
                        % (node.get("name") or nid, nid, root, root))
                    _bad += 1
                    if _bad >= 3:
                        break
            if _bad >= 3:
                break
        result = (node.get("config") or {}).get("resultVariable") \
            or (node.get("config") or {}).get("as") \
            or node.get("id")
        if result:
            _rk = str(result)
            if _rk in seen_outputs:
                problems.append(
                    "output %r written twice (%s and %s) — rename one"
                    % (_rk, seen_outputs[_rk], node.get("name") or nid))
            else:
                seen_outputs[_rk] = node.get("name") or nid
            written.add(_rk)
            if _rk in ids and _rk != nid:
                problems.append(
                    "output %r shadows node id %r — rename one" % (_rk, _rk))
        out = (node.get("config") or {}).get("output")
        if isinstance(out, str) and out.strip():
            written.add(out.strip())
    # Cycle detection: real cycles never finish. Unconditional:
    # topo_order appends leftovers, so order length alone never
    # proves acyclicity.
    seen, stack = set(), set()
    children = {}
    for edge in edges:
        if isinstance(edge, dict) and edge.get("source") in ids:
            children.setdefault(edge["source"], []).append(
                edge.get("target"))
    cycle = []

    def _visit(nid):
        if nid in stack:
            cycle.append(nid)
            return True
        if nid in seen:
            return False
        seen.add(nid)
        stack.add(nid)
        for child in children.get(nid, []):
            if _visit(child):
                return True
        stack.discard(nid)
        return False

    for nid in list(ids):
        if _visit(nid):
            problems.append(
                "the graph has a cycle at node %r — loops must use "
                "a Loop node, not a backward edge" % cycle[0])
            break
    trig = (trigger or {})
    ttype = trig.get("type") or "manual"
    if ttype not in {t for t, _l in TRIGGER_TYPES}:
        problems.append("unknown trigger type %r" % ttype)
    if ttype == "webhook" and not trig.get("path"):
        pass  # path auto-derived from token when empty
    if ttype == "record":
        cfg = trig.get("config") or {}
        if not cfg.get("model"):
            problems.append("record trigger needs a model")
    if ttype == "schedule":
        cfg = trig.get("config") or {}
        if not (cfg.get("cron") or cfg.get("interval_minutes")):
            problems.append("schedule trigger needs a cron or interval")
    return problems
