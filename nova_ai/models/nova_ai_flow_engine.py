# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Flow execution engine — runs the node/edge DAG.

Design (n8n semantics, Odoo guarantees):

* Topological walk with branch ports: an ``if`` node emits on the
  ``true`` or ``false`` handle only; ``switch`` emits on the matching
  indexed port; every other node emits on the default port.
* Loop scoping: ``loop.foreach`` fans out over a list with a capped
  iteration budget; ``loop.end`` closes the scope. Nested loops get
  their own ``$item``/``$index`` frame.
* Merge: waits for every incoming branch and concatenates the branch
  payloads into one list.
* Errors never vanish: a failing node routes to the ``error`` handle
  when one exists, else aborts the run with the node, the args shape
  and the fix — the same honesty the chat loop owes.
* Writes never execute inline: create/write/unlink/method/activity/
  email queue Nova confirmation cards and the run pauses with
  ``needs_approval``. Approving resumes from the same node.
* Every read runs as the invoking user: record rules apply exactly as
  in the UI. Business data is never sudo-read.
"""

import json
import time

from odoo.exceptions import AccessError

from odoo.addons.nova_ai.models.nova_ai_flow_n8n import (
    normalize_expression as _n8n_norm)
from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
    _norm_expr_vars, canon_ntype, interp, topo_order, validate_graph,
)


def _canon_ntype(node):
    # Single source of truth lives in the spec so validation and
    # execution can never disagree about what a node is.
    return canon_ntype(node)

_MAX_NODES = 80
_MAX_LOOP_ITEMS = 200
_MAX_RESULT_BYTES = 200_000
_LOOP_OUTPUT_CAP = 25


def _today_vars(flow):
    try:
        return flow._default_variables()
    except Exception:
        return {}


def _snap_scope(items):
    """Scope snapshot without nesting prior snapshots."""
    try:
        return {k: v for k, v in dict(items).items()
                if k not in ("json", "$json")}
    except Exception:
        return {}


def _node_handles(children, nid):
    """Every outgoing port of a node (for skip/disable traversal)."""
    try:
        outs = set()
        for (src, handle), tgts in children.items():
            if src == nid and tgts:
                outs.add(handle or "default")
        return outs or {"default"}
    except Exception:
        return {"default"}


def _deep_norm(value):
    """Recursively normalise n8n expressions to ${...}."""
    if isinstance(value, str):
        return _n8n_norm(value)
    if isinstance(value, list):
        return [_deep_norm(v) for v in value]
    if isinstance(value, dict):
        return {k: _deep_norm(v) for k, v in value.items()}
    return value


def _truncate(value, limit=_MAX_RESULT_BYTES):
    try:
        text = json.dumps(value, default=str)
    except Exception:
        return value
    if len(text) > limit:
        return {"truncated": True,
                "preview": text[:2000],
                "bytes": len(text)}
    return value


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _eval_condition(expr, scope):
    """True/False for an IF condition (P038: full operator set).

    The expression is interpolated first, then evaluated. Operators:
    ``== != > < >= <=`` (numeric when both sides parse, else string),
    ``contains / not contains``, ``in / not in`` (right side is a
    comma/JSON list), ``startsWith / endsWith``, ``regex`` (right side
    is the pattern, safe-regex capped), ``between`` (``a between x
    and y``), ``exists / not exists``, ``empty / not empty``.
    Anything unparseable is truthy-when-nonempty — the node must never
    silently drop a branch.
    """
    import re as _re
    text = interp(str(expr or ""), scope)
    stripped = text.strip()
    # A lone unresolved placeholder means "no value": it must read as
    # empty/false, never truthy — otherwise a missing variable silently
    # takes the true branch and the loud-fail promise breaks.
    if _re.fullmatch(r"\$\{[^}]+\}", stripped):
        return False
    low = stripped.lower()
    if low in ("", "false", "no", "0", "none", "null", "empty"):
        return False
    if low in ("true", "yes", "1", "not empty"):
        return True
    # Operators are matched as bare words between whitespace — never
    # with embedded spaces — so a trailing operator ("x not empty",
    # with nothing after it) still parses. Order matters: multi-word
    # and two-char operators come before their prefixes.
    match = _re.match(
        r"^(.*?)\s+(==|!=|>=|<=|>|<|not\s+contains|contains"
        r"|not\s+in|\bin\b|startsWith|endsWith|regex|between"
        r"|not\s+exists|exists|not\s+empty|empty)\s*(.*)$",
        text.strip(), _re.I | _re.S)
    if not match:
        return bool(text.strip())
    left, op, right = match.group(1), match.group(2).strip(), match.group(3)
    left = left.strip()
    right = right.strip()
    if len(right) >= 2 and right[0] == right[-1] and right[0] in "'\"":
        right = right[1:-1]
    op_low = _re.sub(r"\s+", " ", op.lower()).strip()
    try:
        if op_low in ("exists", "not exists"):
            # Exists means the raw (pre-interpolation) path resolved.
            # Normalise first so {{...}} spellings resolve too.
            raw = _norm_expr_vars(str(expr or ""), {})
            found = _re.findall(r"\$\{([^}]+)\}", raw)
            from odoo.addons.nova_ai.models.nova_ai_flow_spec import (
                _resolve_path)
            hit = any(
                _resolve_path(scope, path) is not None for path in found)
            if not found:
                # No ${...} path to look up: a literal value trivially
                # exists, an empty one does not.
                hit = bool(left.strip())
            return hit if op_low == "exists" else not hit
        if op_low in ("empty", "not empty"):
            # A leftover ${...} placeholder means the path resolved to
            # nothing (interp never invents values) — that IS empty.
            vacant = left in ("", "[]", "{}", "None", "null", "False")
            if not vacant and left.startswith("${") \
                    and left.endswith("}"):
                vacant = True
            return vacant if op_low == "empty" else not vacant
        if op_low == "between":
            parts = _re.split(r"\s+and\s+", right, flags=_re.I)
            if len(parts) != 2:
                return False
            try:
                num, lo, hi = float(left), float(parts[0]), float(
                    parts[1])
                return lo <= num <= hi
            except (TypeError, ValueError):
                return parts[0] <= left <= parts[1]
        if op_low in ("contains", "not contains"):
            hit = right.lower() in left.lower()
            return hit if op_low == "contains" else not hit
        if op_low in ("in", "not in"):
            if right.strip().startswith("["):
                try:
                    choices = json.loads(right)
                except (TypeError, ValueError):
                    choices = [right]
            else:
                choices = [c.strip() for c in right.split(",")]
            lowered = [str(c).lower() for c in choices]
            hit = left.lower() in lowered or left in [
                str(c) for c in choices]
            return hit if op_low == "in" else not hit
        if op_low in ("startswith", "endswith"):
            if op_low == "startswith":
                return left.lower().startswith(right.lower())
            return left.lower().endswith(right.lower())
        if op_low == "regex":
            if len(right) > 500:
                return False
            try:
                hit = _re.search(right, left) is not None
            except _re.error:
                return False
            return hit
        try:
            ln, rn = float(left), float(right)
            numeric = True
        except (TypeError, ValueError):
            numeric = False
        if op == "==":
            # Numeric compare when both sides parse ("2.0" == "2"),
            # case-insensitive for booleans ("True" == "true"),
            # exact string compare otherwise.
            if numeric:
                return ln == rn
            if left.lower() in ("true", "false") and right.lower() in ("true", "false"):
                return left.lower() == right.lower()
            return left == right
        if op == "!=":
            if numeric:
                return ln != rn
            if left.lower() in ("true", "false") and right.lower() in ("true", "false"):
                return left.lower() != right.lower()
            return left != right
        if numeric:
            if op == ">":
                return ln > rn
            if op == "<":
                return ln < rn
            if op == ">=":
                return ln >= rn
            if op == "<=":
                return ln <= rn
        return (left > right) if op == ">" else (left < right)
    except Exception:
        return bool(text.strip())


_CODE_TIMEOUT_S = 10
_CODE_OUTPUT_BYTES = 50_000


def _safe_code(code, scope, timeout_s=None):
    """Run a snippet with Odoo's safe_eval (P039/P040).

    No imports, no builtins beyond a curated few. Runs in a daemon
    thread so an infinite loop dies at ``timeout_s`` instead of the
    worker (safe_eval itself cannot be preempted). The contract:
    the snippet sets ``result``; ``$input`` mirrors n8n
    ({first,last,all} of the current items); anything else the
    snippet leaves in scope is ignored. Oversized results are an
    error, not a silent truncation — the author must narrow the
    output instead of wondering where rows went.
    """
    import threading as _th
    from odoo.tools.safe_eval import safe_eval as _safe
    allowed = {
        "len": len, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict, "set": set,
        "sorted": sorted, "sum": sum, "min": min, "max": max,
        "round": round, "abs": abs, "any": any, "all": all,
        "enumerate": enumerate, "range": range, "zip": zip,
    }
    ctx = dict(allowed)
    ctx.update({"data": dict(scope), "json": json})
    for key, val in (scope or {}).items():
        if not isinstance(key, str) or not key.isidentifier():
            continue
        if key.startswith("_") or key in ("json", "data", "result", "input"):
            continue
        ctx.setdefault(key, val)
    try:
        ctx.pop("__builtins__", None)
    except Exception:
        pass
    # P040: $input like n8n — first/last/all of current items.
    try:
        items = scope.get("$items") or scope.get("items")
        if not isinstance(items, list):
            items = [scope.get("$item")] if scope.get(
                "$item") is not None else []
        ctx.setdefault("$input", {
            "all": items,
            "first": items[0] if items else None,
            "last": items[-1] if items else None,
        })
        ctx.setdefault("input", ctx["$input"])
    except Exception:
        ctx.setdefault("$input", {"all": [], "first": None,
                                  "last": None})
    ctx.setdefault("result", None)
    # Same 8000-char authoring cap the validator enforces: a longer
    # snippet fails fast here too instead of running halfway.
    if len(str(code or "")) > 8000:
        raise ValueError("code is too long (max 8000 chars)")
    box, failed = {}, []

    def _target():
        try:
            _safe(str(code or ""), ctx, mode="exec", nocopy=True)
            box["result"] = ctx.get("result")
        except Exception as err:  # noqa: BLE001 — surfaced to the run
            failed.append(str(err)[:300])

    worker = _th.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout_s or _CODE_TIMEOUT_S)
    if worker.is_alive():
        raise TimeoutError(
            "code ran past %ss — narrow the loop or raise timeoutMs"
            % (timeout_s or _CODE_TIMEOUT_S))
    if failed:
        raise ValueError("code failed: %s" % failed[0])
    try:
        size = len(json.dumps(box.get("result"), default=str))
    except Exception:
        size = 0
    if size > _CODE_OUTPUT_BYTES:
        raise ValueError(
            "code result is %d bytes (cap %d) — return less data"
            % (size, _CODE_OUTPUT_BYTES))
    return box.get("result")


class FlowEngine:
    """Stateless executor: ``run(flow, arguments, run_as_env)``."""

    def __init__(self, env):
        self.env = env

    # ---------------------------------------------------------- public
    def validate(self, flow):
        nodes, edges = flow._graph()
        trigger = flow._trigger_dict()
        problems = validate_graph(nodes, edges, trigger)
        # Plus the legacy validator for migrated flows.
        try:
            for _fid, text in flow.validate():
                if text not in problems:
                    problems.append(text)
        except Exception:
            pass
        return problems

    def run(self, flow, arguments=None, trigger_kind="manual",
            run_rec=None, resume_state=None):
        started = time.time()
        nodes, edges = flow._graph()
        problems = validate_graph(nodes, edges, flow._trigger_dict())
        if problems:
            return {"ok": False, "error": "; ".join(problems[:5])}
        by_id = {n["id"]: n for n in nodes if isinstance(n, dict)}
        children = {}
        for edge in (edges or []):
            if not isinstance(edge, dict):
                continue
            # Handles are strings at runtime: an int 0/1 from a
            # hand-written edge must not become a separate port from
            # "0"/"1", and int 0 must not collapse to "default".
            raw_handle = edge.get("source_handle")
            handle = "default" if raw_handle is None or raw_handle == "" \
                else str(raw_handle)
            children.setdefault(
                (edge.get("source"), handle), []).append(edge.get("target"))
        order = topo_order(nodes, edges)
        if resume_state and isinstance(resume_state.get("scope"), dict):
            scope = dict(resume_state.get("scope") or {})
            # Refresh volatile keys; keep resumed values.
            try:
                scope.setdefault("$trigger", trigger_kind)
                scope.setdefault("$run", {"flow": flow.name, "trigger": trigger_kind})
            except Exception:
                pass
        else:
            scope = {}
            scope.update(_today_vars(flow))
            scope["$trigger"] = trigger_kind
            scope["$run"] = {"flow": flow.name, "trigger": trigger_kind}
            try:
                scope["$now"] = self.env["ir.config_parameter"].sudo().get_str(
                    "nova_ai.now") or ""
            except Exception:
                scope["$now"] = ""
            # Start-node inputs win over defaults; caller args fill them.
            # Canonical types: an n8n-native trigger declares inputs too.
            for node in nodes:
                if isinstance(node, dict) and _canon_ntype(node) in (
                        "start", "trigger"):
                    for var in ((node.get("config") or {}).get("variables")
                                or []):
                        if isinstance(var, dict) and var.get("name"):
                            name = var["name"]
                            scope[name] = (arguments or {}).get(
                                name, var.get("value"))
            try:
                self._agent_ctx = {"agent": flow.agent_id} \
                    if flow.agent_id else {}
            except Exception:
                self._agent_ctx = {}
            if arguments:
                for key, val in arguments.items():
                    scope.setdefault(key, val)
            try:
                scope["$flow_depth"] = int(
                    (arguments or {}).get("$flow_depth") or 0)
            except (TypeError, ValueError):
                scope["$flow_depth"] = 0
            scope["json"] = dict(scope)
            scope["$json"] = scope["json"]
        try:
            if not isinstance(self._agent_ctx, dict):
                self._agent_ctx = {"agent": flow.agent_id} if flow.agent_id else {}
        except Exception:
            pass

        _snap = _snap_scope

        results, cards = [], []
        # n8n-style execution inspector: what went IN and what came OUT.
        node_inputs, node_outputs = {}, {}
        paused_nid = None
        if resume_state:
            try:
                visited = set(resume_state.get("visited") or [])
                results = list(resume_state.get("results") or [])
                pending = list(resume_state.get("pending") or [])
                paused_nid = resume_state.get("paused_nid")
                # Scope is restored by caller (flow.resume_run merges stored scope).
            except Exception:
                visited = set()
                pending = [nid for nid in order
                           if _canon_ntype(by_id.get(nid)) in ("start", "trigger")]
        else:
            visited = set()
            pending = [nid for nid in order
                       if _canon_ntype(by_id.get(nid)) in (
                           "start", "trigger")]
        if not pending and not resume_state:
            # No entry point: validation should have refused this, but
            # a hand-built graph can still arrive here — fail loudly
            # instead of starting mid-graph.
            return {"ok": False,
                    "error": "a flow needs a Trigger or Start node"}
        guard = 0
        status = "ok"
        error = ""
        needs_approval = False
        # Reverse adjacency for merge arrival checks.
        parents = {}
        for edge in (edges or []):
            if not isinstance(edge, dict):
                continue
            if edge.get("source") in by_id \
                    and edge.get("target") in by_id:
                parents.setdefault(edge["target"], set()).add(
                    edge["source"])

        def _enqueue(nid):
            if nid and nid in by_id and nid not in visited \
                    and nid not in pending:
                pending.append(nid)

        def _all_handles(nid):
            return _node_handles(children, nid)

        def _skip_subgraph(entry_nids):
            """Mark an untaken branch skipped (honest execution log).

            Walks forward, marking nodes visited+skipped, stopping at
            merge nodes and loop ends (the join/body rules own them).
            A skipped loop body is not descended into.
            """
            seen = set(entry_nids or [])
            queue = list(entry_nids or [])
            while queue:
                cur = queue.pop(0)
                if cur in visited or cur not in by_id:
                    continue
                ctype = _canon_ntype(by_id[cur])
                if ctype in ("merge", "loop.end"):
                    continue
                visited.add(cur)
                results.append({
                    "node": cur, "type": by_id[cur].get("type"),
                    "name": by_id[cur].get("name") or cur,
                    "skipped": True, "note": "branch not taken"})
                if ctype == "loop.foreach":
                    for tgt in children.get((cur, "done"), []):
                        if tgt not in seen:
                            seen.add(tgt)
                            queue.append(tgt)
                    continue
                for handle in _all_handles(cur):
                    for tgt in children.get((cur, handle), []):
                        if tgt not in seen:
                            seen.add(tgt)
                            queue.append(tgt)

        def _can_still_arrive(mid):
            """Could any pending node still reach this merge?"""
            if mid in pending:
                return True
            try:
                ancestors = set()
                queue = list(parents.get(mid) or [])
                while queue:
                    cur = queue.pop(0)
                    if cur in ancestors:
                        continue
                    ancestors.add(cur)
                    queue.extend(parents.get(cur) or [])
                return any(p in ancestors for p in pending)
            except Exception:
                return True

        while pending:
            guard += 1
            try:
                _real = sum(1 for _r in results if not _r.get("skipped"))
            except Exception:
                _real = len(results)
            if guard > _MAX_NODES * 4 or _real > _MAX_NODES:
                status, error = "error", "flow too large (cap %d nodes)" \
                    % _MAX_NODES
                break
            nid = pending.pop(0)
            if nid in visited:
                continue
            node = by_id.get(nid)
            if not node:
                continue
            # Canonical type, config and name FIRST: every path below
            # (disabled, pins, error) needs them. A pin used to read
            # them before assignment and crashed the run.
            ntype = _canon_ntype(node)
            config = dict(node.get("config") or {})
            name = node.get("name") or nid
            if node.get("disabled"):
                visited.add(nid)
                results.append({"node": nid, "type": node.get("type"),
                                "name": name,
                                "skipped": True,
                                "note": "disabled (n8n)"})
                for handle in _all_handles(nid):
                    for tgt in children.get((nid, handle), []):
                        _enqueue(tgt)
                continue
            # P034: pinned data (n8n test runs). A pin replaces the
            # node's real execution on interactive triggers only —
            # schedules/webhooks/records always run live data. Pins
            # never apply to routing nodes: an if/switch/merge/loop
            # must still route, or joins and branches corrupt.
            if node.get("pins") is not None and ntype not in (
                    "start", "trigger", "note", "merge", "if", "switch",
                    "filter", "loop.foreach", "loop.end", "output",
                    "respond", "subflow", "wait", "human.approval") and trigger_kind in (
                        "manual", "chat", "test", "subflow"):
                pinned = node.get("pins")
                if isinstance(pinned, dict) and "json" in pinned:
                    pinned = pinned["json"]
                visited.add(nid)
                try:
                    _pcfg = dict(node.get("config") or {})
                    _pkey = (_pcfg.get("output") or _pcfg.get("resultVariable")
                             or _pcfg.get("as") or nid)
                except Exception:
                    _pkey = nid
                node_outputs[nid] = _truncate(pinned)
                scope[str(_pkey)] = pinned
                scope["json"] = _snap_scope(scope)
                results.append({"node": nid, "type": ntype,
                                "name": name, "value": _truncate(pinned),
                                "pinned": True})
                for tgt in children.get((nid, "default"), []):
                    _enqueue(tgt)
                continue
            try:
                node_inputs[nid] = _truncate({
                    "scope": {k: scope.get(k) for k in list(scope)[:40]},
                    "$json": scope.get("$json"),
                    "$item": scope.get("$item"),
                })
            except Exception:
                pass
            if run_rec is not None:
                try:
                    run_rec._touch_node(nid, ntype)
                except Exception:
                    pass
            if ntype in ("start", "trigger", "note"):
                visited.add(nid)
                results.append({"node": nid, "type": ntype, "skipped": True,
                                "name": name})
                for tgt in children.get((nid, "default"), []):
                    _enqueue(tgt)
                continue
            if ntype == "merge":
                incomings = [e.get("source") for e in (edges or [])
                             if isinstance(e, dict)
                             and e.get("target") == nid]
                if not incomings:
                    visited.add(nid)
                    results.append({"node": nid, "type": ntype,
                                    "name": name,
                                    "error": "merge has no incoming "
                                    "edges — wire at least one branch"})
                    status, error = "error", "merge has no incoming edges"
                    break
                arrived = {src for src in incomings if src in visited}
                # Fire when every incoming branch arrived, or when
                # nothing still pending could arrive (an untaken IF
                # branch never comes — skip-marking above already
                # recorded it). Never fire on zero arrivals.
                if arrived != set(incomings) and (
                        not arrived or _can_still_arrive(nid)):
                    if len(results) < _MAX_NODES:
                        _enqueue(nid)
                        continue
                    status, error = "error", "flow too large (cap %d " \
                        "nodes)" % _MAX_NODES
                    break
                visited.add(nid)
                # P036: merge modes — append (concat all branches),
                # chooseBranch (take one branch's value by node id),
                # first (first non-empty branch value). Only values
                # from actual incoming branches count.
                mode = str(config.get("mode") or "append").lower()
                if mode not in ("append", "choosebranch", "first"):
                    results.append({"node": nid, "type": ntype,
                                    "name": name,
                                    "error": "unknown merge mode %r "
                                    "(append/chooseBranch/first)" % mode})
                    status, error = "error", "unknown merge mode"
                    break
                branch_vals = {}
                for src in incomings:
                    for past in reversed(results):
                        if past.get("node") == src and \
                                "value" in past:
                            branch_vals[src] = past.get("value")
                            break
                if mode == "choosebranch":
                    want = str(config.get("branch") or "")
                    picked = branch_vals.get(want)
                    if picked is None:
                        vals = list(branch_vals.values())
                        picked = vals[0] if vals else None
                    merged = _as_list(picked)[:_MAX_LOOP_ITEMS]
                elif mode == "first":
                    merged = []
                    for val in branch_vals.values():
                        if val is not None and val != []:
                            merged = _as_list(val)[:_MAX_LOOP_ITEMS]
                            break
                else:  # append
                    merged = []
                    for val in branch_vals.values():
                        if isinstance(val, list):
                            merged.extend(val[:_LOOP_OUTPUT_CAP])
                        elif val is not None:
                            merged.append(val)
                        if len(merged) >= _MAX_LOOP_ITEMS:
                            break
                out_key = config.get("output") or config.get(
                    "resultVariable") or config.get("as") or nid
                scope[str(out_key)] = merged[:_MAX_LOOP_ITEMS]
                scope["json"] = _snap_scope(scope)
                results.append({"node": nid, "type": ntype, "name": name,
                                "value": _truncate(merged[:25])})
                for tgt in children.get((nid, "default"), []):
                    _enqueue(tgt)
                continue
            # P031–P033: retry, onError routing, executeOnce.
            if node.get("executeOnce") and nid in node_outputs:
                outcome = {"value": node_outputs[nid], "as": nid,
                           "note": "executeOnce"}
            else:
                tries = 1
                try:
                    tries = 1 + max(0, min(
                        int(node.get("retry") or 0), 5))
                except (TypeError, ValueError):
                    tries = 1
                outcome = None
                for attempt in range(tries):
                    outcome = self._run_node(
                        flow, node, config, scope, trigger_kind)
                    if not outcome.get("error") or attempt >= tries - 1:
                        break
                    # Capped backoff: workers must never sleep 8s in a
                    # request thread; longer waits belong in Wait nodes.
                    time.sleep(min(2 ** attempt, 2))
                if outcome.get("error") and \
                        (node.get("onError") or "stop") == "continue":
                    # n8n onError=continue: the error becomes data on
                    # the error port; downstream decides.
                    _okey = (config.get("output") or config.get(
                        "resultVariable") or config.get("as") or nid)
                    scope["$error"] = outcome["error"]
                    outcome = {"value": {"error": outcome["error"],
                                         "node": nid},
                               "as": _okey,
                               "error_routed": True}
                    err_targets = children.get((nid, "error"), [])
                    visited.add(nid)
                    node_outputs[nid] = _truncate(outcome["value"])
                    results.append({"node": nid, "type": ntype,
                                    "name": name,
                                    "error": outcome["value"]["error"][:300],
                                    "routed": True})
                    for tgt in err_targets or children.get(
                            (nid, "default"), []):
                        _enqueue(tgt)
                    continue
            if outcome.get("branch") is not None:
                visited.add(nid)
                handle = str(outcome["branch"])
                results.append({"node": nid, "type": ntype, "name": name,
                                "branch": handle,
                                "value": _truncate(outcome.get("value"))})
                if outcome.get("value") is not None:
                    scope[outcome.get("as") or nid] = outcome["value"]
                    scope["json"] = _snap_scope(scope)
                # Untaken ports are marked skipped (honest log + merge
                # arrival), then the taken port fires. An unknown
                # handle with no default edge is a dead end, said
                # plainly — never a silent continue.
                untaken = []
                taken_targets = set(children.get((nid, handle), []))
                for (src, hdl), tgts in children.items():
                    if src == nid and (hdl or "default") != handle:
                        # Shared successors (a merge, a plain join) belong
                        # to the taken path too — skipping them would
                        # poison the join, so only truly untaken nodes
                        # are marked.
                        untaken.extend(
                            t for t in tgts if t not in taken_targets)
                if untaken:
                    _skip_subgraph(untaken)
                targets = list(children.get((nid, handle), []))
                if not targets and ntype == "if":
                    # IF without an explicit false edge: continue
                    # linearly on the default wire.
                    targets = list(children.get((nid, "default"), []))
                if not targets:
                    # A switch port with no edge is a dead end, said
                    # plainly — never a silent reroute to default.
                    # End this branch but keep draining other pending
                    # branches (n8n semantics).
                    results.append({"node": nid, "type": ntype,
                                    "name": name,
                                    "note": "branch %r has no edge — "
                                    "the run ends here" % handle})
                    continue
                for tgt in targets:
                    _enqueue(tgt)
                continue
            if outcome.get("fanout"):
                visited.add(nid)
                items = outcome["items"][:_MAX_LOOP_ITEMS]
                loop_key = outcome.get("as") or "item"
                collected = []
                # Run the loop body inline for each item (bounded).
                body_entry = (children.get((nid, "default"), []) or
                              children.get((nid, "loop"), []))
                for idx, item in enumerate(items):
                    # Isolated frame per iteration: $item/$index/item/index
                    # never leak. User Set keys set inside the body merge
                    # back (n8n carries mutations forward); reserved keys
                    # and the loop key itself do not.
                    iter_scope = dict(scope)
                    iter_scope["$item"], iter_scope["$index"] = item, idx
                    iter_scope["item"], iter_scope["index"] = item, idx
                    iter_scope["$items"] = items
                    iter_scope[loop_key] = item
                    iter_scope["json"] = _snap_scope(iter_scope)
                    _before = set(iter_scope.keys())
                    body_out = self._run_body(
                        flow, by_id, children, body_entry, iter_scope,
                        trigger_kind, depth=1)
                    collected.extend(body_out.get("outputs", []))
                    for card in body_out.get("cards", []):
                        cards.append(card)
                    try:
                        _reserved = {"$item", "$index", "$items", "$json", "json",
                                     "$trigger", "$run", "$now", "$error", "$input",
                                     "input", "data", "$flow_depth", "item", "index",
                                     "items", loop_key, loop_key + "_all",
                                     "today", "today_start", "today_end"}
                        for _k, _v in list(iter_scope.items()):
                            if _k in _reserved or _k.startswith("$"):
                                continue
                            if _k not in _before or scope.get(_k) != _v:
                                if _k not in ("json", "$json"):
                                    scope[_k] = _v
                    except Exception:
                        pass
                    if body_out.get("needs_approval"):
                        needs_approval = True
                        # A paused loop resumes after approval: do not
                        # enqueue past it now (no done-targets below).
                        break
                # Single entry per loop (not per iteration): scope and
                # log update once after the fan-out completes.
                scope[loop_key + "_all"] = collected[
                    :_MAX_LOOP_ITEMS]
                scope["json"] = _snap_scope(scope)
                entry = {
                    "node": nid, "type": ntype, "name": name,
                    "iterations": len(items),
                    "value": _truncate(collected[:_LOOP_OUTPUT_CAP]),
                }
                if needs_approval:
                    # Paused mid-loop: say how far it got so the
                    # resume log is honest, not "3 of 3".
                    entry["completed"] = len(collected)
                    results.append(entry)
                    status = "waiting_approval"
                    break
                results.append(entry)
                # Continue after the loop-end node when present.
                for tgt in children.get((nid, "done"), []):
                    _enqueue(tgt)
                if not children.get((nid, "done")):
                    # Fall through: only the loop.end reachable from
                    # THIS loop joins here — never every end node.
                    seen_end, queue_end = set(), list(body_entry)
                    while queue_end:
                        cur = queue_end.pop(0)
                        if cur in seen_end or cur not in by_id:
                            continue
                        seen_end.add(cur)
                        if _canon_ntype(by_id[cur]) == "loop.end":
                            for tgt in children.get(
                                    (cur, "default"), []):
                                _enqueue(tgt)
                            break
                        for handle in _all_handles(cur):
                            queue_end.extend(
                                children.get((cur, handle), []))
                continue
            if outcome.get("error") and not outcome.get("routed"):
                err_targets = children.get((nid, "error"), [])
                if err_targets:
                    visited.add(nid)
                    scope["$error"] = outcome["error"]
                    results.append({"node": nid, "type": ntype,
                                    "name": name,
                                    "error": outcome["error"][:300],
                                    "routed": True})
                    for tgt in err_targets:
                        _enqueue(tgt)
                    continue
                visited.add(nid)
                # The run dies here, but the error stays in scope for
                # the inspector and the run record.
                scope["$error"] = outcome["error"]
                results.append({"node": nid, "type": ntype, "name": name,
                                "error": outcome["error"][:300]})
                status, error = "error", outcome["error"][:500]
                break
            visited.add(nid)
            if outcome.get("card"):
                cards.append(outcome["card"])
            for card in outcome.get("cards", []):
                cards.append(card)
            if outcome.get("value") is not None or outcome.get("as"):
                scope[outcome.get("as") or nid] = outcome["value"]
                scope["json"] = _snap_scope(scope)
            entry = {"node": nid, "type": ntype, "name": name}
            if outcome.get("value") is not None:
                entry["value"] = _truncate(outcome["value"])
            if outcome.get("card"):
                entry["card"] = {
                    "kind": outcome["card"].get("kind"),
                    "title": outcome["card"].get("title"),
                }
            results.append(entry)
            if outcome.get("needs_approval"):
                needs_approval = True
                paused_nid = nid
                status = "waiting_approval"
                # Stop at the write: approval resumes from successors.
                # Enqueue successors now so resume_state.pending already
                # points past the paused node.
                for tgt in children.get((nid, "default"), []):
                    _enqueue(tgt)
                break
            if outcome.get("halt"):
                break
            for tgt in children.get((nid, "default"), []):
                _enqueue(tgt)

        # Publish per-node outputs for the inspector + persist them.
        for entry in results:
            nid = entry.get("node")
            if nid and nid not in node_outputs:
                node_outputs[nid] = _truncate(entry.get("value"))
        try:
            scope["$json"] = scope.get("json")
        except Exception:
            pass
        latency = int((time.time() - started) * 1000)
        out = {"ok": status in ("ok", "waiting_approval"),
               "results": results, "variables": _truncate(dict(scope)),
               "node_inputs": node_inputs, "node_outputs": node_outputs,
               "latency_ms": latency}
        if cards:
            out["cards"] = cards
        if needs_approval:
            out["needs_approval"] = True
            try:
                out["paused_nid"] = paused_nid
                out["resume_scope"] = _truncate(dict(scope))
                out["resume_pending"] = list(pending)
                out["resume_visited"] = list(visited)
            except Exception:
                pass
            out["message"] = (
                "The flow reached a write step. Approve the card, "
                "then resume to continue past it.")
        if status == "error":
            out["ok"] = False
            out["error"] = error
        return out

    # ---------------------------------------------------------- internals
    def _run_body(self, flow, by_id, children, entry, scope,
                  trigger_kind, depth=0):
        """Run a loop body once over the full reachable subgraph.

        BFS from the entry (bounded), stopping at ``loop.end`` —
        bodies are graphs, not 8-node lines. Disabled nodes are
        skipped. The passed scope is the per-iteration copy owned by
        the caller; nothing here touches the outer run scope.
        """
        outputs, cards = [], []
        seen, queue = set(), list(entry or [])
        steps = 0
        while queue and steps < _MAX_NODES:
            steps += 1
            nid = queue.pop(0)
            if nid in seen or nid not in by_id:
                continue
            seen.add(nid)
            node = by_id[nid]
            ctype = _canon_ntype(node)
            if ctype == "loop.end":
                continue
            if ctype == "merge":
                # Per-iteration join: pass through the last body value
                # so a valid n8n accumulate-then-merge pattern does not
                # hard-fail. Full cross-branch join lives after the loop.
                try:
                    _mkey = (dict(node.get("config") or {}).get("output")
                             or dict(node.get("config") or {}).get("as")
                             or nid)
                    if outputs:
                        scope[str(_mkey)] = outputs[-1]
                except Exception:
                    pass
                continue
            if node.get("disabled"):
                for handle in _node_handles(children, nid):
                    queue.extend(children.get((nid, handle), []))
                continue
            # Body parity: pins, executeOnce, retry, onError->error port.
            if node.get("pins") is not None and ctype not in (
                    "start", "trigger", "note", "merge", "if", "switch",
                    "filter", "loop.foreach", "loop.end", "output",
                    "respond", "subflow", "wait", "human.approval") and trigger_kind in (
                        "manual", "chat", "test", "subflow"):
                _pinned = node.get("pins")
                if isinstance(_pinned, dict) and "json" in _pinned:
                    _pinned = _pinned["json"]
                try:
                    _pkey2 = (dict(node.get("config") or {}).get("output")
                              or dict(node.get("config") or {}).get("resultVariable")
                              or dict(node.get("config") or {}).get("as") or nid)
                except Exception:
                    _pkey2 = nid
                scope[str(_pkey2)] = _pinned
                outputs.append(_pinned)
                for _t in children.get((nid, "default"), []):
                    if _t not in seen:
                        queue.append(_t)
                continue
            _tries = 1
            try:
                _tries = 1 + max(0, min(int(node.get("retry") or 0), 5))
            except Exception:
                _tries = 1
            outcome = None
            for _att in range(_tries):
                outcome = self._run_node(
                    flow, node, dict(node.get("config") or {}),
                    scope, trigger_kind)
                if not outcome.get("error") or _att >= _tries - 1:
                    break
                import time as _t
                _t.sleep(min(2 ** _att, 2))
            if outcome.get("error") and (node.get("onError") or "stop") == "continue":
                scope["$error"] = outcome["error"]
                _ekey = (dict(node.get("config") or {}).get("output")
                         or dict(node.get("config") or {}).get("resultVariable")
                         or dict(node.get("config") or {}).get("as") or nid)
                scope[str(_ekey)] = {"error": outcome["error"], "node": nid}
                outputs.append({"error": outcome["error"][:200], "routed": True})
                for _t in children.get((nid, "error"), []) or children.get((nid, "default"), []):
                    if _t not in seen:
                        queue.append(_t)
                continue
            if outcome.get("cards"):
                cards.extend(outcome["cards"])
            if outcome.get("card"):
                cards.append(outcome["card"])
            if outcome.get("value") is not None:
                outputs.append(outcome["value"])
                scope[outcome.get("as") or nid] = outcome["value"]
            if outcome.get("needs_approval"):
                return {"outputs": outputs, "cards": cards,
                        "needs_approval": True}
            if outcome.get("error"):
                _err_targets = children.get((nid, "error"), [])
                if _err_targets:
                    scope["$error"] = outcome["error"]
                    outputs.append({"error": outcome["error"][:200], "routed": True})
                    for _t in _err_targets:
                        if _t not in seen:
                            queue.append(_t)
                    continue
                outputs.append({"error": outcome["error"][:200]})
                break
            if outcome.get("branch") is not None:
                handle = str(outcome["branch"])
                targets = children.get((nid, handle), [])
                if not targets and _canon_ntype(node) == "if":
                    targets = children.get((nid, "default"), [])
                if targets:
                    queue.extend(targets)
                    continue
                outputs.append({"note": "branch %r has no edge" % handle})
                break
            if outcome.get("fanout"):
                # Nested loop: recurse with per-item sub-frames.
                if depth >= 3:
                    outputs.append(
                        {"error": "nested loops too deep (max 3)"})
                    break
                nested_items = outcome["items"][:_MAX_LOOP_ITEMS]
                nested_key = outcome.get("as") or "item"
                nested_entry = (children.get((nid, "default"), [])
                                or children.get((nid, "loop"), []))
                nested_collected = []
                for j, it in enumerate(nested_items):
                    sub = dict(scope)
                    sub["$item"], sub["$index"] = it, j
                    sub["item"], sub["index"] = it, j
                    sub["$items"] = nested_items
                    sub[nested_key] = it
                    sub["json"] = _snap_scope(sub)
                    sub_out = self._run_body(
                        flow, by_id, children, nested_entry, sub,
                        trigger_kind, depth + 1)
                    nested_collected.extend(
                        sub_out.get("outputs", []))
                    cards.extend(sub_out.get("cards", []))
                    if sub_out.get("needs_approval"):
                        return {"outputs": outputs + nested_collected,
                                "cards": cards,
                                "needs_approval": True}
                scope[nested_key + "_all"] = nested_collected[
                    :_MAX_LOOP_ITEMS]
                outputs.extend(
                    nested_collected[:_LOOP_OUTPUT_CAP])
                # R6: nested loop done-successors must fire (outer parity).
                for _t in children.get((nid, "done"), []):
                    if _t not in seen:
                        queue.append(_t)
                if not children.get((nid, "done")):
                    for _t in children.get((nid, "default"), []):
                        if _t not in seen:
                            queue.append(_t)
                continue
            queue.extend(children.get((nid, "default"), []))
        return {"outputs": outputs, "cards": cards,
                "needs_approval": False}

    def _run_node(self, flow, node, config, scope, trigger_kind):
        ntype = _canon_ntype(node)
        # n8n expressions (={{...}}, {{$node["X"].json...}}) arrive
        # here raw from imports — normalise recursively, not just the
        # top-level strings, or nested values/args slip through raw.
        try:
            config = _deep_norm(dict(config or {}))
        except Exception:
            pass
        # n8n per-item context: the current item IS $json, like n8n.
        try:
            if scope.get("$item") is not None:
                scope["$json"] = scope.get("$item")
        except Exception:
            pass
        # n8n Odoo params (resource/operation/filters) -> our args.
        # Unauthenticated webhooks never run code or outbound HTTP
        # unless the flow explicitly opts in: both are server-side
        # compute/egress an anonymous caller must not steer.
        if trigger_kind == "webhook" and ntype in ("code", "http"):
            try:
                allow = ((flow._trigger_dict().get("config") or {}).get(
                    "allow_code" if ntype == "code" else "allow_http"))
            except Exception:
                allow = False
            if not allow:
                return _err(
                    "%s nodes are disabled on webhook triggers "
                    "(set allow_%s in the trigger config to enable)" % (
                        ntype,
                        "code" if ntype == "code" else "http"))
        try:
            if ntype.startswith("odoo.") and config.get("resource") \
                    and not config.get("model"):
                config["model"] = config.get("resource")
            if ntype.startswith("odoo.") and config.get("filters") \
                    and not config.get("domain"):
                config["domain"] = config.get("filters")
            if ntype == "odoo.search" and config.get("limit") is None \
                    and config.get("maxResults") is not None:
                config["limit"] = config.get("maxResults")
        except Exception:
            pass
        out_key = (config.get("output") or config.get("resultVariable")
                   or config.get("as") or node.get("id"))
        try:
            _rk = str(out_key or "")
            if _rk.startswith("$") or _rk in ("json", "input", "data", "today", "today_start", "today_end"):
                return _err("output name %r is reserved — rename it" % _rk)
        except Exception:
            pass

        def _ok(value, **kw):
            res = {"value": value, "as": out_key}
            res.update(kw)
            return res

        def _err(text):
            return {"error": str(text)[:500]}
        try:
            if ntype in ("set", "transform"):
                values = config.get("values") or config.get("fields") or {}
                if isinstance(values, dict):
                    built = {k: interp(v, scope)
                             for k, v in values.items()}
                    scope.update(built)
                    return _ok(built)
                return _ok(interp(config.get("value", ""), scope))
            if ntype == "filter":
                # n8n Filter semantics: keep what matches, drop the
                # rest. Linear form: branch true/false on the condition
                # (or the truthiness of value when no condition set).
                # Downstream ${node} sees the passing item, not a wrapper.
                _fitem = scope.get("$item", scope.get("$json"))
                cond = config.get("condition")
                if cond:
                    branch = "true" if _eval_condition(
                        cond, scope) else "false"
                else:
                    raw = interp(str(config.get("value") or ""), scope)
                    branch = "true" if str(raw).strip() not in (
                        "", "False", "false", "0", "None", "[]", "{}") \
                        else "false"
                return {"branch": branch, "as": out_key,
                        "value": _fitem if branch == "true" else None}
            if ntype == "if":
                cond = config.get("condition") or config.get("if") or ""
                branch = "true" if _eval_condition(cond, scope) else "false"
                return {"branch": branch, "as": out_key,
                        "value": {"condition": interp(str(cond), scope),
                                  "taken": branch}}
            if ntype == "switch":
                # P037: rules with operators + renameable outputs +
                # fallback port. A rule is {value, operator?, output?}.
                # Branch handles are "0".."n", named outputs, or
                # "fallback"/"default" when nothing matches.
                cases = config.get("cases") or config.get("rules") or []
                raw = interp(str(config.get("value") or config.get(
                    "expression") or ""), scope)
                taken = str(config.get("fallback") or "fallback")
                if isinstance(cases, list):
                    for idx, case in enumerate(cases):
                        if isinstance(case, dict):
                            want = interp(case.get("value", ""), scope)
                            operator = (case.get("operator")
                                        or "equals").strip().lower()
                            label = str(case.get("output") or idx)
                            if operator == "equals":
                                hit = str(raw) == str(want)
                            elif operator in ("not_equals", "!="):
                                hit = str(raw) != str(want)
                            elif operator == "contains":
                                hit = str(want).lower() in str(raw).lower()
                            elif operator in ("not_contains",):
                                hit = str(want).lower() not in str(raw).lower()
                            elif operator in ("in",):
                                try:
                                    import json as _js
                                    _ch = _js.loads(str(want)) if str(want).strip().startswith("[") else [c.strip() for c in str(want).split(",")]
                                except Exception:
                                    _ch = [str(want)]
                                _ch = [str(c).lower() for c in _ch]
                                hit = str(raw).lower() in _ch
                            elif operator in ("not_in",):
                                try:
                                    import json as _js2
                                    _ch2 = _js2.loads(str(want)) if str(want).strip().startswith("[") else [c.strip() for c in str(want).split(",")]
                                except Exception:
                                    _ch2 = [str(want)]
                                _ch2 = [str(c).lower() for c in _ch2]
                                hit = str(raw).lower() not in _ch2
                            elif operator in ("starts_with", "startswith"):
                                hit = str(raw).lower().startswith(str(want).lower())
                            elif operator in ("ends_with", "endswith"):
                                hit = str(raw).lower().endswith(str(want).lower())
                            elif operator in (">", "<", ">=", "<="):
                                try:
                                    _ln, _rn = float(str(raw)), float(str(want))
                                    hit = {">": _ln > _rn, "<": _ln < _rn, ">=": _ln >= _rn, "<=": _ln <= _rn}[operator]
                                except Exception:
                                    hit = (str(raw) > str(want)) if operator == ">" else (str(raw) < str(want)) if operator == "<" else False
                            elif operator == "regex":
                                import re as _re2
                                try:
                                    hit = len(str(want)) <= 500 \
                                        and _re2.search(
                                            str(want),
                                            str(raw)) is not None
                                except _re2.error:
                                    hit = False
                            elif operator == "empty":
                                hit = not str(raw).strip()
                            elif operator == "not_empty":
                                hit = bool(str(raw).strip())
                            else:
                                hit = str(raw) == str(want)
                            if hit:
                                taken = label
                                break
                        elif str(raw) == str(interp(case, scope)):
                            taken = str(idx)
                            break
                return {"branch": taken, "as": out_key,
                        "value": {"value": raw, "taken": taken}}
            if ntype == "loop.foreach":
                items = interp(config.get("items") or "", scope)
                if isinstance(items, str):
                    _s = items.strip()
                    if _s.startswith("[") or _s.startswith("{"):
                        try:
                            items = json.loads(_s)
                        except (TypeError, ValueError):
                            items = []
                    elif "," in _s:
                        items = [p.strip() for p in _s.split(",") if p.strip() != ""]
                    elif _s == "":
                        items = []
                    else:
                        items = [_s]
                items = _as_list(items)[:_MAX_LOOP_ITEMS]
                return {"fanout": True, "items": items, "as": (
                    config.get("itemName") or "item")}
            if ntype in ("loop.end", "merge"):
                return _ok(scope.get(out_key))
            if ntype == "wait":
                secs = int(config.get("seconds") or 0)
                return _ok({"waited": False, "seconds": min(secs, 3600),
                            "note": "waits resume via schedule; run "
                                    "continues without sleeping"})
            if ntype in ("output", "respond"):
                return {"value": interp(
                    config.get("value", ""), scope),
                    "as": out_key, "halt": True}
            if ntype in ("odoo.search", "search"):
                args = dict(config.get("args") or {})
                if "model" not in args and config.get("model"):
                    args["model"] = config["model"]
                for key in ("domain", "fields", "limit", "order",
                            "offset", "about"):
                    if key in config and key not in args:
                        args[key] = config[key]
                args = interp(args, scope)
                return self._via_tool("search_records", args, scope,
                                      out_key)
            if ntype == "odoo.count":
                args = dict(config.get("args") or {})
                for key in ("model", "domain"):
                    if key in config and key not in args:
                        args[key] = config[key]
                args = interp(args, scope)
                return self._via_tool("count_records", args, scope,
                                      out_key)
            if ntype == "odoo.group":
                args = dict(config.get("args") or {})
                for key in ("model", "domain", "fields", "groupby"):
                    if key in config and key not in args:
                        args[key] = config[key]
                args = interp(args, scope)
                return self._via_tool("read_group", args, scope, out_key)
            if ntype in ("odoo.read", "read"):
                args = dict(config.get("args") or {})
                for key in ("model", "res_id", "res_ids", "fields"):
                    if key in config and key not in args:
                        args[key] = config[key]
                args = interp(args, scope)
                return self._via_tool("read_record", args, scope, out_key)
            if ntype in ("odoo.create", "create"):
                args = dict(config.get("args") or {})
                for key in ("model", "values"):
                    if key in config and key not in args:
                        args[key] = config[key]
                args = interp(args, scope)
                return self._via_tool("create_draft", {
                    "model": args.get("model"),
                    "values": args.get("values") or {}}, scope, out_key)
            if ntype == "odoo.write":
                args = dict(config.get("args") or {})
                for key in ("model", "res_id", "values"):
                    if key in config and key not in args:
                        args[key] = config[key]
                args = interp(args, scope)
                try:
                    res_id = int(args.get("res_id") or 0)
                except (TypeError, ValueError):
                    res_id = 0
                return self._via_tool("set_fields", {
                    "model": args.get("model"), "res_id": res_id,
                    "values": args.get("values") or {}}, scope, out_key)
            if ntype == "odoo.unlink":
                # A delete queues the SAME approval card as the chat
                # Unlink tool (kind unlink_record) — one card shape,
                # one approve path (run_confirm), never inline.
                from odoo.addons.nova_ai.models.nova_ai_tools import (
                    _confirm_card as _card2)
                args = dict(config.get("args") or {})
                for key in ("model", "res_id"):
                    if key in config and key not in args:
                        args[key] = config[key]
                args = interp(args, scope)
                try:
                    res_id = int(args.get("res_id") or 0)
                except (TypeError, ValueError):
                    res_id = 0
                if not args.get("model") or not res_id:
                    return _err("unlink needs a model and res_id")
                try:
                    from odoo.addons.nova_ai.models.nova_ai_tools import (
                        _model_allowed as _ma2)
                    if not _ma2(self.env, str(args["model"])):
                        return _err("model %r is not available to flows" % args["model"])
                    try:
                        _has = self.env[str(args["model"])].has_access("unlink")
                        if _has is False:
                            return _err("you may not delete %s #%s" % (args["model"], res_id))
                    except Exception:
                        pass
                except Exception as _e:
                    if "not available" in str(_e) or "may not delete" in str(_e):
                        return _err(str(_e)[:200])
                card = _card2(
                    "unlink_record",
                    "Delete %s #%s" % (args["model"], res_id),
                    "Approve to permanently delete this record.",
                    {"model": args["model"], "res_id": res_id})
                return {"value": {"queued": True}, "as": out_key,
                        "card": card, "needs_approval": True}
            if ntype == "note":
                # Sticky note: annotation only, never a chatter post.
                return _ok({"note": config.get("text") or config.get("body") or ""})
            if ntype in ("odoo.note",):
                args = interp({
                    "model": config.get("model"),
                    "res_id": config.get("res_id"),
                    "body": config.get("body") or "",
                }, scope)
                return self._via_tool("log_note", args, scope, out_key)
            if ntype == "odoo.activity":
                args = interp({
                    "model": config.get("model"),
                    "res_id": config.get("res_id"),
                    "summary": config.get("summary") or "",
                    "date_deadline": config.get("date_deadline") or "",
                    "user_id": config.get("user_id") or "",
                }, scope)
                return self._via_tool("schedule_activity", args, scope,
                                      out_key)
            if ntype == "odoo.email":
                # A queued email card: approve sends it (see run_confirm
                # kind flow_email). Never sends inline from a flow.
                args = interp({
                    "to": config.get("to")
                    or (config.get("args") or {}).get("to") or "",
                    "subject": config.get("subject")
                    or (config.get("args") or {}).get("subject") or "",
                    "body": config.get("body")
                    or (config.get("args") or {}).get("body") or "",
                    "model": config.get("model")
                    or (config.get("args") or {}).get("model") or "",
                    "res_id": config.get("res_id")
                    or (config.get("args") or {}).get("res_id") or 0,
                }, scope)
                from odoo.addons.nova_ai.models.nova_ai_tools import (
                    _confirm_card)
                card = _confirm_card(
                    "flow_email",
                    "Send email: %s" % (args.get("subject") or "(no subject)"),
                    "To %s" % (args.get("to") or "—"),
                    {"to": args.get("to"), "subject": args.get("subject"),
                     "body": args.get("body"), "model": args.get("model"),
                     "res_id": args.get("res_id")})
                card["kind"] = "flow_email"
                return {"value": {"queued": True, "to": args.get("to")},
                        "as": out_key, "card": card,
                        "needs_approval": True}
            if ntype == "odoo.method":
                args = dict(config.get("args") or {})
                for key in ("model", "res_id", "res_ids", "method",
                            "method_args"):
                    if key in config and key not in args:
                        args[key] = config[key]
                args = interp(args, scope)
                if not args.get("method"):
                    return _err("method node needs a method name")
                # Deployer gate first: a flow may never run a method the
                # side-effect allowlist forbids, even behind cards.
                # Private and identity-shedding methods are rejected
                # outright, same as the chat path.
                from odoo.addons.nova_ai.models.nova_ai_tools import (
                    _check_method, _side_effect_allowed,
                    _model_allowed as _ma)
                try:
                    _check_method(self.env, str(args.get("model") or ""),
                                  str(args.get("method") or ""))
                except Exception as err:
                    return _err(str(err)[:200])
                if not _ma(self.env, str(args.get("model") or "")):
                    return _err("model %r is not available to flows"
                                % (args.get("model") or "—"))
                if not _side_effect_allowed(
                        self.env, str(args.get("model") or ""),
                        str(args.get("method") or "")):
                    return _err(
                        "method %s.%s is not enabled by the deployer "
                        "(nova_ai.side_effect_methods)" % (
                            args.get("model"), args.get("method")))
                # execute_tool returns a confirm card (never inline):
                # approval happens in run_confirm like every write.
                return self._via_tool("execute_method", args, scope,
                                      out_key)
            if ntype == "tool":
                code = config.get("tool") or ""
                args = interp(config.get("args") or {}, scope)
                return self._via_tool(code, args, scope, out_key)
            if ntype in ("ai.agent", "agent"):
                instruction = interp(
                    config.get("instruction") or "", scope)
                return self._via_agent(flow, node, config, instruction,
                                       scope, out_key)
            if ntype == "ai.prompt":
                return self._via_agent(flow, node, config, interp(
                    config.get("prompt") or config.get("instruction")
                    or "", scope), scope, out_key)
            if ntype == "code":
                try:
                    try:
                        timeout_s = max(1, min(
                            int(config.get("timeoutMs") or 10000)
                            // 1000, 120))
                    except (TypeError, ValueError):
                        timeout_s = _CODE_TIMEOUT_S
                    value = _safe_code(config.get("code") or "", scope,
                                       timeout_s=timeout_s)
                except Exception as err:
                    return _err("code failed: %s" % str(err)[:200])
                return _ok(value)
            if ntype == "http":
                return self._via_http(config, scope, out_key)
            if ntype == "human.approval":
                from odoo.addons.nova_ai.models.nova_ai_tools import (
                    _confirm_card)
                card = _confirm_card(
                    "flow_approval",
                    config.get("title") or "Approve to continue",
                    interp(config.get("message") or "", scope),
                    {"flow": flow.name,
                     "node": node.get("id")})
                card["kind"] = "flow_approval"
                return {"value": {"waiting": True}, "as": out_key,
                        "card": card, "needs_approval": True}
            if ntype == "subflow":
                depth = 0
                try:
                    depth = int(scope.get("$flow_depth") or 0)
                except (TypeError, ValueError):
                    depth = 0
                if depth >= 3:
                    return _err("subflow nesting too deep (max 3) — "
                                "check for a flow calling itself")
                target = str(config.get("flow") or "").strip()
                flows = self.env["nova.ai.flow"].search(
                    [("name", "=ilike", target),
                     ("active", "=", True)], limit=1)
                if not flows:
                    return _err("subflow %r not found" % target)
                if flows.id == flow.id:
                    return _err("a flow cannot call itself")
                sub_args = interp(config.get("arguments") or {}, scope)
                if not isinstance(sub_args, dict):
                    sub_args = {}
                sub_args["$flow_depth"] = depth + 1
                engine = FlowEngine(self.env)
                result = engine.run(flows, sub_args,
                                    trigger_kind="subflow")
                if not result.get("ok"):
                    out = _err(result.get("error") or "subflow failed")
                    for card in result.get("cards", []):
                        out.setdefault("cards", []).append(card)
                    return out
                out = _ok(result.get("variables") or result)
                for card in result.get("cards", []):
                    out.setdefault("cards", []).append(card)
                if result.get("needs_approval"):
                    out["needs_approval"] = True
                return out
            return _err("unsupported node type %r" % ntype)
        except AccessError as err:
            return _err(str(err)[:300])
        except Exception as err:
            return _err("node failed: %s" % str(err)[:300])

    def _via_tool(self, code, args, scope, out_key):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            CONFIRM_KINDS, execute_tool)
        # Agent model-scope travels with the flow: a scope-restricted
        # agent's flow sees exactly what the agent may touch.
        ctx = getattr(self, "_agent_ctx", None) or {}
        if not code:
            return {"error": "no tool for this step"}
        # execute_method is allowed ONLY through the odoo.method node,
        # which gates model + deployer allowlist first and always gets
        # a confirm card (never inline). unlink_record and
        # read_attachment stay forbidden: deletes go through the
        # odoo.unlink card path, attachments never leave in a flow.
        if code in ("unlink_record", "read_attachment", "execute_method"):
            return {"error": "%s may not run in a flow (use the odoo.method node)" % code}
        try:
            outcome = execute_tool(self.env, code, args or {}, ctx)
        except Exception as err:
            return {"error": str(err)[:300]}
        kind = outcome[0] if outcome else "result"
        payload = outcome[1] if len(outcome) > 1 else {}
        card = outcome[2] if len(outcome) > 2 else None
        # Large results (a 10k-row search_read) are truncated the same
        # way everywhere — dicts must not bypass the cap.
        res = {"value": _truncate(payload), "as": out_key}
        if card:
            res["card"] = card
        if kind == "confirm" and card:
            res["needs_approval"] = True
        return res

    def _via_agent(self, flow, node, config, instruction, scope, out_key):
        agent = None
        agent_id = config.get("agent")
        try:
            if agent_id:
                agent = self.env["nova.ai.agent"].browse(
                    int(agent_id)).exists()
        except (TypeError, ValueError):
            agent = None
        if not agent:
            agent = flow.agent_id
        if not agent:
            return {"error": "no agent to run this step"}
        try:
            from odoo.addons.nova_ai.controllers.nova_ai import (
                run_chat_turn)
            session = self.env["nova.ai.session"].create({
                "agent_id": agent.id})
            result = run_chat_turn(
                self.env, agent, session, instruction or "", {})
            return {"value": (result or {}).get("reply") or "",
                    "as": out_key}
        except Exception as err:
            return {"error": "agent step failed: %s" % str(err)[:200]}

    def _via_http(self, config, scope, out_key):
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            tool_fetch_url)
        url = interp(str(config.get("url") or ""), scope)
        # Post-interp https enforcement: templated http:// must not slip past.
        try:
            if url and not str(url).strip().lower().startswith("https://"):
                return {"error": "http url must use https:// — plain http is refused by policy"}
        except Exception:
            return {"error": "http url is invalid"}
        # GET only, by policy: writes to the outside world go through
        # approval-gated Odoo tools, never a bare HTTP node.
        method = str(config.get("method") or "GET").upper()
        if method != "GET":
            return {"error": "http node is GET-only (policy); other "
                             "methods are refused"}
        try:
            _tmo = config.get("timeoutMs") or config.get("timeout_s")
            try:
                _tmo_s = max(5, min(15, int(int(_tmo) / 1000) if int(_tmo) > 100 else int(_tmo)))
            except Exception:
                _tmo_s = 10
        except Exception:
            _tmo_s = 10
        try:
            import os as _os
            _os.environ.pop("HTTP_PROXY", None)
            _os.environ.pop("HTTPS_PROXY", None)
            _os.environ.pop("http_proxy", None)
            _os.environ.pop("https_proxy", None)
        except Exception:
            pass
        try:
            payload = tool_fetch_url(self.env, {"url": url, "timeout_s": _tmo_s}, {})
        except Exception as err:
            return {"error": str(err)[:300]}
        return {"value": payload, "as": out_key}
