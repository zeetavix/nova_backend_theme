# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Tool registry — what an agent may actually do to your data.

Every handler runs with the *user's own* environment, so record rules
and access rights apply exactly as in the UI: there is no sudo read of
business data anywhere in this file. Models are allow-listed per
deployment (config parameter, same denial rules as the theme search),
and write tools never execute inline — they return confirm cards that
only /nova/ai/confirm turns into writes.
"""

import base64
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse
import requests

from odoo import _, fields
from odoo.exceptions import AccessError
_MODEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")

_DENIED_PREFIXES = ("ir.", "res.config")
_DENIED_EXACT = {"res.users", "res.groups"}

# Everyday models worth suggesting when a request is refused: the
# gate itself is wider than this (anything the user can read), but
# the menu stays short so weak models can act on it.
_COMMON_MODELS = (
    "res.partner,sale.order,sale.order.line,account.move,account.move.line,"
    "account.account,account.tax,account.journal,"
    "account.bank.statement,account.bank.statement.line,"
    "project.project,project.task,crm.lead,"
    "product.product,product.template,product.category,"
    "uom.uom,uom.category,"
    "purchase.order,purchase.order.line,"
    "stock.picking,stock.move,stock.move.line,"
    "stock.quant,stock.location,stock.lot,stock.scrap,"
    "mrp.bom,mrp.bom.line,mrp.production,"
    "account.payment,account.payment.register,account.move.reversal,"
    "helpdesk.ticket,calendar.event,"
    "hr.expense,hr.expense.sheet,account.analytic.line,"
    "hr.employee,mail.activity"
)
_FETCH_MAX_BYTES = 400_000
_FETCH_TIMEOUT = 15


def _common_models(env):
    """Everyday models the requesting user can actually read: the
    suggestion menu for refusals and the frontend's button gating.
    The turn gate itself (_model_allowed) is wider than this."""
    out = []
    for name in _COMMON_MODELS.split(","):
        name = name.strip()
        if not name or not _MODEL_RE.match(name):
            continue
        if name in _DENIED_EXACT or name.startswith(_DENIED_PREFIXES):
            continue
        try:
            model = env[name]
        except KeyError:
            continue
        try:
            if not model.has_access("read"):
                continue
        except Exception:
            continue
        out.append(name)
    return out


def _denied_reason(env, name):
    """Why is this model off-limits? Checked cheapest-first so the
    message names the real blocker, not a guess."""
    if not name or not _MODEL_RE.match(name or ""):
        return "not_a_model"
    if name in _DENIED_EXACT or name.startswith(_DENIED_PREFIXES):
        return "system"
    try:
        env[name]
    except KeyError:
        return "missing"
    scope = None
    try:
        scope = (env.context or {}).get("nova_model_scope")
    except Exception:
        scope = None
    if scope is not None and name not in scope:
        return "scoped"
    try:
        raw = env["ir.config_parameter"].get_str(
            "nova_ai.tool_models", "")
    except Exception:
        raw = ""
    if raw and (raw or "").strip():
        subset = {part.strip() for part in raw.split(",") if part.strip()}
        if name not in subset:
            return "param"
    return "access"


def _allowed_models(env):
    """Suggestion menu, not the gate: everyday models worth trying,
    for refusal texts and the drawer's button gating. See
    _model_allowed for the actual rule."""
    return _common_models(env)


def _model_allowed(env, name):
    """May the agent touch this model? The mirror rule: anything the
    requesting user can read, except system models. Writes still
    queue confirm cards, record rules still apply per row, and the
    agent subset + deployer subset still narrow further."""
    if not name or not _MODEL_RE.match(name):
        return False
    if name in _DENIED_EXACT or name.startswith(_DENIED_PREFIXES):
        return False
    try:
        record = env[name]
    except KeyError:
        return False
    scope = None
    try:
        scope = (env.context or {}).get("nova_model_scope")
    except Exception:
        scope = None
    if scope is not None and name not in scope:
        return False
    try:
        raw = env["ir.config_parameter"].get_str(
            "nova_ai.tool_models", "")
    except Exception:
        raw = ""
    if raw and (raw or "").strip():
        subset = {part.strip() for part in raw.split(",") if part.strip()}
        if name not in subset:
            return False
    try:
        # Odoo 20: has_access mirrors check_access as a boolean.
        return bool(record.has_access("read"))
    except Exception as err:  # noqa: BLE001 - temporary debug
        import logging as _logging
        _logging.getLogger("nova.ai.debug").exception(
            "TEMPDEBUG _model_allowed failed for %r uid=%r su=%r: %r",
            name, getattr(env, "uid", "?"),
            getattr(env, "su", "?"), err)
        return False


def _scoped_env(env, agent):
    """The agent's model subset, carried in context so all 29
    _model_allowed checks honor it without signature changes.
    Empty scope means the global allow-list."""
    try:
        raw = (agent.model_scope if agent else "") or ""
    except Exception:
        raw = ""
    models = tuple(
        name.strip() for name in raw.split(",") if name.strip())
    if not models:
        return env
    return env(context=dict(env.context or {}, nova_model_scope=models))


def _denied(name, env):
    """Actionable refusal: name the real blocker and teach discovery.
    Weak models self-correct when told the menu AND the tools to
    explore it with (fields_get for fields, inspect_method for
    business methods) instead of guessing."""
    allowed = _common_models(env)[:12]
    try:
        scope = (env.context or {}).get("nova_model_scope")
    except Exception:
        scope = None
    if scope is not None:
        allowed = [candidate for candidate in allowed
                   if candidate in scope][:12]
    reason = _denied_reason(env, name)
    if reason == "system":
        why = "It is a system model, never agent-readable."
    elif reason == "missing":
        why = "It is not installed here."
    elif reason == "scoped":
        why = "This agent is scoped away from it."
    elif reason == "param":
        why = "The deployer's model list excludes it."
    else:
        why = "You have no read access to it."
    return AccessError(env._(
        "Model %(name)s is not available to agents. %(why)s "
        "Everyday models you can try: %(list)s. "
        "To see what any model offers, use fields_get for its "
        "fields or inspect_method for its business methods.") % {
            "name": name, "why": why,
            "list": ", ".join(allowed) or "—"})


def _safe_fields(env, model_name, fields):
    """Keep only fields that exist and are safe to expose to a model."""
    model = env[model_name]
    extra = _field_acl_denied(env, model_name)
    out = []
    for name in fields or []:
        try:
            field = model._fields[name]
        except KeyError:
            continue
        if field.type == "binary" or field.groups:
            continue
        lowered = name.lower()
        if lowered in _SENSITIVE_FIELDS or name in extra:
            continue
        out.append(name)
    return out


def _side_effect_allowed(env, model_name, method):
    """Exact method allowlist, opt-in hardening (mcp-odoo pattern).

    nova_ai.side_effect_methods = "sale.order.action_confirm,...".
    Unset (default) keeps today's behavior: any public method may be
    queued behind a card. Set it, and anything unlisted is refused
    before a card exists.
    """
    try:
        raw = env["ir.config_parameter"].get_str(
            "nova_ai.side_effect_methods")
    except Exception:
        return True
    if not (raw or "").strip():
        return True
    allowed = {entry.strip() for entry in raw.split(",") if entry.strip()}
    return "%s.%s" % (model_name, method) in allowed


_DOMAIN_OPS = frozenset([
    "=", "!=", ">", ">=", "<", "<=", "=?", "=like", "=ilike", "like",
    "not like", "ilike", "not ilike", "in", "not in", "child_of",
    "parent_of",
])

_SENSITIVE_FIELDS = frozenset([
    "salary", "wage", "password", "pin", "ssn", "passport_no",
    "passport", "bank_account", "iban", "credit_card", "private_key",
    "secret", "token", "api_key",
])


def _field_acl_denied(env, model_name):
    """Deployer-owned per-model deny list on top of the hardcoded
    sensitive blocklist. nova_ai.field_acl = {"res.partner":
    {"deny": ["credit_limit"]}}. Malformed config fails closed
    (denies nothing extra but logs nothing either — reads stay
    on the hardcoded fence)."""
    try:
        import json as _json
        raw = env["ir.config_parameter"].get_str("nova_ai.field_acl")
        if not raw:
            return frozenset()
        data = _json.loads(raw)
        denied = ((data.get(model_name) or {}).get("deny") or [])
        return frozenset(str(name) for name in denied if name)
    except Exception:
        return frozenset()


def _norm_domain_value(value, operator="="):
    """Turn a domain leaf value into something Odoo accepts.

    Models hand us values that are almost-but-not-quite right: a
    comma string for "in", a JSON string for a list. Coerce the
    obvious ones instead of failing the whole turn. The list parsing
    only fires for in/not in, so a char field that legitimately
    holds "[1, 2]" keeps its text.
    """
    if isinstance(value, str) and operator in ("in", "not in"):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return parsed
            except (ValueError, TypeError):
                pass
        # "a, b, c" for in/not in
        if "," in stripped:
            return [part.strip() for part in stripped.split(",") if part.strip()]
    if isinstance(value, tuple):
        return list(value)
    return value


def _norm_operator(operator):
    """Map operator spellings models use onto Odoo's set."""
    if not isinstance(operator, str):
        return operator
    op = operator.strip()
    lowered = op.lower()
    if not op:
        # The model left the operator slot blank. With a field and a
        # value in place, "=" is the only sensible reading.
        return "="
    aliases = {
        "==": "=",
        "!==": "!=",
        "<>": "!=",
        "equals": "=",
        "eq": "=",
        "not equal": "!=",
        "contains": "ilike",
        "starts with": "=like",
        "ends with": "=ilike",
        "in list": "in",
        "not in list": "not in",
        "is": "=",
        "is not": "!=",
    }
    return aliases.get(lowered, op)


# Knowledge and flow tools live in their own module: the knowledge
# layer is a subsystem, not a helper of the record tools.
from odoo.addons.nova_ai.models.nova_ai_knowledge_tools import (  # noqa: E402
    tool_list_flows, tool_list_flow_runs, tool_read_knowledge,
    tool_run_flow, tool_search_knowledge, tool_write_knowledge)
# The write-preparation tools live in their own module because they need
# this one's helpers: importing them here and them there would be a
# cycle, so they fetch the helpers lazily instead.
from odoo.addons.nova_ai.models.nova_ai_write_tools import (  # noqa: E402
    tool_build_domain, tool_draft_flow, tool_update_flow, tool_export_flow,
    tool_import_flow, tool_list_models, tool_set_fields,
    tool_validate_write)


def _safe_domain(env, domain):
    """Normalize a model-supplied domain into Odoo's leaf format.

    Models write domains in several reasonable shapes: a flat
    ['name', 'ilike', 'Acme'], a dict leaf, a JSON string, an empty
    operator. Rejecting all of them produced the same dead-end error
    over and over ("Domain leaves must be [field, operator, value]"),
    because the model was never told what to send instead.

    So: repair what is unambiguous, keep the operator whitelist and
    the model ACL exactly as strict as before, and only fail on input
    we genuinely cannot read — with a message that shows the shape we
    wanted.
    """
    if domain is None:
        return []
    if isinstance(domain, str):
        # A whole domain handed over as a JSON string.
        try:
            domain = json.loads(domain)
        except (ValueError, TypeError):
            raise AccessError(env._(
                "Domain must be a list of [field, operator, value] "
                "triples, e.g. [[\"name\", \"ilike\", \"Acme\"]]."))
    if isinstance(domain, dict):
        # One leaf as an object: {field, operator, value}.
        domain = [[domain.get("field"), domain.get("operator", "="),
                   domain.get("value")]]
    if not isinstance(domain, (list, tuple)):
        raise AccessError(env._(
            "Domain must be a list of [field, operator, value] triples."))
    # A flat triple: ['name', 'ilike', 'Acme'] instead of a list of one.
    if (len(domain) == 3 and all(not isinstance(part, (list, tuple))
                                  for part in domain)
            and isinstance(domain[0], str)):
        domain = [list(domain)]
    clean = []
    dropped_placeholders = 0
    for leaf in domain:
        if leaf in ("&", "|", "!"):
            clean.append(leaf)
            continue
        if isinstance(leaf, dict):
            leaf = [leaf.get("field"), leaf.get("operator", "="),
                    leaf.get("value")]
        # A leaf wrapped one level too deep: [[["name", "ilike", "x"]]].
        while (isinstance(leaf, (list, tuple)) and len(leaf) == 1
               and isinstance(leaf[0], (list, tuple))):
            leaf = leaf[0]
        if not isinstance(leaf, (list, tuple)) or len(leaf) != 3:
            # Four elements: the model appended a stray extra. Keep the
            # first three (field, operator, value) and drop the rest.
            if isinstance(leaf, (list, tuple)) and len(leaf) > 3:
                leaf = list(leaf)[:3]
            else:
                raise AccessError(env._(
                    "Domain leaves must be [field, operator, value] "
                    "triples, e.g. [[\"name\", \"ilike\", \"Acme\"]]."))
        field, operator, value = leaf
        if field is None and value is None:
            # A placeholder leaf the model never filled: [None, '=', None].
            # Dropping one of several is harmless, but if NOTHING real
            # remains the domain would silently become match-all, so
            # refuse instead of returning the whole table.
            dropped_placeholders += 1
            continue
        if not field or not isinstance(field, str):
            raise AccessError(env._(
                "Domain leaves need a field name, e.g. "
                "[[\"name\", \"ilike\", \"Acme\"]]. Got %(leaf)s.") % {
                    "leaf": repr(list(leaf))[:120]})
        operator = _norm_operator(operator)
        if operator not in _DOMAIN_OPS:
            raise AccessError(env._(
                "Domain operator %(op)s is not allowed. Use one of: "
                "%(allowed)s.") % {
                    "op": operator or "(empty)",
                    "allowed": ", ".join(sorted(_DOMAIN_OPS))})
        clean.append([field, operator, _norm_domain_value(value, operator)])
    if not clean and dropped_placeholders and not any(
            part in ("&", "|", "!") for part in domain):
        raise AccessError(env._(
            "The domain was only placeholders - name a real field "
            "and value, e.g. [[\"name\", \"ilike\", \"Acme\"]]."))
    return clean


def _fmt_value(value):
    if isinstance(value, tuple) and len(value) == 2:
        return value[1]
    if hasattr(value, "display_name"):
        return value.display_name
    if isinstance(value, (list, tuple)):
        return [(_fmt_value(v) if not isinstance(v, (str, int, float, bool,
                                                    type(None))) else v)
                for v in value]
    return value


def _hr(text, limit=4000):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "",
                  flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


# ---------------------------------------------------------------- reads

def _page_args(env, model_name, args, default_limit=10):
    """Shared limit/offset/order parsing for read tools.

    Order is restricted to real fields with an explicit direction so a
    creative model cannot smuggle SQL into the ORDER BY clause (Odoo
    validates again downstream; this fails loud first, guiding the
    model instead of erroring mid-turn).
    """
    limit = max(1, min(int(args.get("limit") or default_limit), 25))
    offset = max(0, min(int(args.get("offset") or 0), 1000))
    order = ""
    raw = (args.get("order") or "").strip()
    if raw:
        parts = []
        model = env[model_name]
        for chunk in raw.split(","):
            bits = chunk.strip().split()
            if not bits:
                continue
            field = bits[0]
            direction = (bits[1].lower() if len(bits) > 1 else "asc")
            if field not in model._fields:
                raise AccessError(env._(
                    "Cannot order by %(field)s.") % {"field": field})
            if direction not in ("asc", "desc"):
                raise AccessError(env._(
                    "Order direction must be asc or desc."))
            parts.append("%s %s" % (field, direction))
        order = ", ".join(parts)
    return limit, offset, order


_SMART_FIELD_TYPES = (
    "char", "text", "html", "selection", "date", "datetime",
    "monetary", "float", "integer", "boolean", "many2one",
)
_SMART_FIELD_SKIP = (
    "one2many", "many2many", "many2one_reference", "binary",
    "json", "properties",
)

# The fields a human looks at first on any record: contact points,
# money, dates, state. Worth more than a bare type match, because a
# tie on type is otherwise decided alphabetically - and "email" would
# lose to "bank_account_count".
_HUMAN_KEYS = (
    "email", "phone", "mobile", "name", "title", "subject", "date",
    "amount", "total", "price", "state", "status", "partner",
    "product", "reference", "number", "code",
)


def _rank_fields(env, model_name, hint, domain_names, limit=8):
    """The fields worth reading, most relevant first.

    Relevance is ranked, not guessed: fields the query filters on win,
    then fields whose name or label matches a hint word, then the
    cheap-to-read identity fields. Heavy relations and blobs are
    never in the default - a default you have to trim is not a
    default. Binary and group-gated fields stay out exactly like the
    explicit path does.
    """
    model = env[model_name]
    try:
        terms = [term for term in re.split(r"[^a-z0-9_]+", str(
            hint or "").lower()) if term]
    except Exception:
        terms = []
    wanted = {str(name).lower() for name in (domain_names or [])}
    extra = _field_acl_denied(env, model_name)
    scored = []
    for name, field in model._fields.items():
        if name in ("id",):
            continue
        if field.type in _SMART_FIELD_SKIP:
            continue
        lowered = name.lower()
        if lowered in _SENSITIVE_FIELDS or name in extra:
            continue
        if field.type == "binary" or field.groups:
            continue
        label = str(field.string or name).lower()
        score = 0
        if name in ("name", "display_name", "title", "subject", "number"):
            score += 3
        if lowered in _HUMAN_KEYS:
            score += 2
        if lowered in wanted:
            score += 5
        for term in terms:
            if term and (term in lowered or term in label):
                score += 3
        if field.type in ("char", "monetary", "float", "integer"):
            score += 1
        if score > 0:
            scored.append((-score, name in ("name", "display_name"), name))
    scored.sort()
    return [name for _score, _first, name in scored[:max(1, limit)]]


def _domain_field_names(domain):
    """The field names a domain filters on - the relevance signal."""
    names = []
    for clause in domain or []:
        if isinstance(clause, (list, tuple)) and len(clause) >= 3 and \
                isinstance(clause[0], str):
            names.append(clause[0])
    return names


def _default_fields(env, model_name, args, domain):
    """The fields when the model named none.

    `fields` given: exactly those (filtered for safety) - unchanged.
    `fields` omitted: the fields relevant to what the user is looking
    at, ranked by _rank_fields. A caller that already knows what it
    wants is never second-guessed.
    """
    given = args.get("fields")
    if given:
        return _safe_fields(env, model_name, given)
    hint = args.get("about") or ""
    names = _rank_fields(env, model_name, hint,
                         _domain_field_names(domain))
    if names:
        return names
    return _safe_fields(env, model_name, ["name"])


def tool_search_records(env, args, ctx):
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    domain = _safe_domain(env, args.get("domain") or [])
    limit, offset, order = _page_args(env, model_name, args)
    # `about` lets the model say WHAT it is looking for ("overdue
    # invoices with amounts") when it does not yet know WHICH fields
    # carry it. Explicit `fields` always win.
    fields = _default_fields(env, model_name, args, domain)
    model = env[model_name]
    total = model.search_count(domain)
    rows = model.search_read(
        domain, fields + ["display_name"],
        offset=offset, limit=limit, order=order or None)
    return {
        "model": model_name, "total": total, "offset": offset,
        "limit": limit, "has_more": offset + len(rows) < total,
        # Cursor pattern for the model: pass offset + limit as the
        # next offset while has_more is true.
        "next_offset": offset + limit if offset + len(rows) < total
        else None,
        "rows": [
            {"id": r.pop("id"),
             **{k: _fmt_value(v) for k, v in r.items()}}
            for r in rows],
    }


def _batch_ids(env, args):
    """Shared res_id/res_ids parsing: one id stays one id, a list is
    capped at 10 (reads only — batch writes would dodge per-card
    review, so no write tool accepts this)."""
    ids = args.get("res_ids")
    if ids is None:
        single = int(args.get("res_id") or 0)
        return [single] if single else []
    if not isinstance(ids, (list, tuple)):
        raise AccessError(env._("res_ids must be a list of ids."))
    clean = []
    for raw in ids:
        try:
            num = int(raw or 0)
        except (TypeError, ValueError):
            continue
        if num and num not in clean:
            clean.append(num)
    if len(clean) > 10:
        raise AccessError(env._(
            "At most 10 records per batch call."))
    return clean


def _gone_or_denied(env, model_name, res_id):
    """One uniform answer: "Record not found."

    A sudo existence check could tell "deleted" apart from "no
    access", but that distinction is an existence oracle: an
    authenticated caller could enumerate ids hidden by record rules.
    Everyone gets the same sentence; admins read the audit log.
    """
    return env._("Record not found.")


def tool_read_record(env, args, ctx):
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    ids = _batch_ids(env, args)
    if not ids:
        raise AccessError(env._("Record not found."))
    fields = _default_fields(env, model_name, args, [])
    records = env[model_name].browse(ids).exists()
    found = []
    missing = []
    for res_id in ids:
        record = records.filtered(lambda r, i=res_id: r.id == i)
        if not record:
            missing.append(res_id)
            continue
        data = record.read(fields + ["display_name"])[0]
        data.pop("id", None)
        found.append({"res_id": res_id, **{
            k: _fmt_value(v) for k, v in data.items()}})
    if args.get("res_ids") is None:
        # Single-record shape unchanged: one dict, or not found.
        if not found:
            raise AccessError(_gone_or_denied(env, model_name, ids[0]))
        return found[0]
    return {"model": model_name, "records": found, "missing": missing}


def tool_count_records(env, args, ctx):
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    domain = _safe_domain(env, args.get("domain") or [])
    return {"model": model_name, "count": env[model_name].search_count(domain)}


def tool_open_view(env, args, ctx):
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    view_type = args.get("view_type") or "list"
    if view_type not in ("list", "kanban", "form", "pivot"):
        view_type = "list"
    domain = _safe_domain(env, args.get("domain") or [])
    action = {
        "type": "ir.actions.act_window",
        "name": env[model_name]._description or model_name,
        "res_model": model_name,
        "view_mode": view_type,
        "domain": domain,
        "target": "current",
    }
    return {"action": action}


def tool_summarize_thread(env, args, ctx):
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    record = env[model_name].browse(int(args.get("res_id") or 0)).exists()
    if not record:
        raise AccessError(_gone_or_denied(env, model_name, args.get("res_id")))
    if "message_ids" not in record._fields:
        return {"record": record.display_name, "messages": []}
    messages = []
    for message in record.message_ids.sorted("date")[-25:]:
        body = _hr(message.body or message.subject or "", 600)
        if not body:
            continue
        messages.append({
            "author": message.author_id.display_name or "System",
            "date": str(message.date),
            "body": body,
        })
    return {"record": record.display_name, "messages": messages}


def tool_next_steps(env, args, ctx):
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    record = env[model_name].browse(int(args.get("res_id") or 0)).exists()
    if not record:
        raise AccessError(_gone_or_denied(env, model_name, args.get("res_id")))
    out = {"record": record.display_name, "state": _fmt_value(
        getattr(record, "state", "")), "open_activities": [],
        "recent_notes": []}
    if "activity_ids" in record._fields:
        for activity in record.activity_ids[:5]:
            out["open_activities"].append({
                "summary": activity.summary, "date_deadline":
                str(activity.date_deadline)})
    if "message_ids" in record._fields:
        for message in record.message_ids.filtered(
                lambda m: m.message_type == "comment")[-3:]:
            out["recent_notes"].append(_hr(message.body or "", 300))
    if model_name == "res.partner":
        invoices = env["account.move"].search([
            ("partner_id", "=", record.id),
            ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")),
        ], limit=5) if "account.move" in env else []
        out["open_invoices"] = [{
            "name": inv.name, "amount_residual": inv.amount_residual,
            "invoice_date_due": str(inv.invoice_date_due)} for inv in invoices]
    return out


def tool_draft_reply(env, args, ctx):
    """Give the model the thread it is replying to; prose is its job."""
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    record = env[model_name].browse(int(args.get("res_id") or 0)).exists()
    if not record:
        raise AccessError(_gone_or_denied(env, model_name, args.get("res_id")))
    thread = []
    if "message_ids" in record._fields:
        for message in record.message_ids.sorted("date")[-10:]:
            body = _hr(message.body or "", 500)
            if body:
                thread.append({
                    "author": message.author_id.display_name,
                    "date": str(message.date), "body": body})
    return {"record": record.display_name, "tone": args.get("tone") or "",
            "thread": thread}


# ---------------------------------------------------------------- writes (confirm only)

# The only card payload keys a user edit may touch at approve time.
# Everything else (model/res_id/method/field/values/args) stays
# exactly as queued, so an approval can never be retargeted onto
# another record or method by a crafted RPC.
_EDITABLE_CARD_KEYS = ("body", "summary")

# Chain links: a card may declare it runs after another queued card
# (after {"kind", "model"}) and which of its payload keys get filled
# with that card's approved record id (bind {"res_id": "created"}).
# Only "created" targets exist: explicit ids always win, and only
# falsy slots are ever filled — nothing is overwritten blindly.
_BIND_TARGET = "created"
_CHAIN_KEY_RE = None


def _card_token(kind, payload, after=None, bind=None):
    """Deterministic card identity: same intent, same token. The
    chain link is part of the queued token so a bound card never
    dedupes against its unbound twin; the token then stays put
    through binding so Approve keeps finding the card."""
    base = {"payload": payload or {}}
    if after:
        base["after"] = after
    if bind:
        base["bind"] = bind
    return kind + ":" + json.dumps(base, sort_keys=True,
                                   default=str)[:160]


def _chain_args(args):
    """Pull a validated chain link out of tool args, or (None, None).

    after: {"kind": <tool code>, "model": <model>} — which earlier
    queued card this one waits for. bind: {payload_key: "created"} —
    which payload keys take that card's approved res_id. Anything
    malformed refuses loud so the model fixes the link instead of
    queuing a card that can never resolve.
    """
    import re
    global _CHAIN_KEY_RE
    if _CHAIN_KEY_RE is None:
        _CHAIN_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")
    raw_after = (args or {}).get("after")
    raw_bind = (args or {}).get("bind")
    after = None
    if raw_after is not None:
        if not isinstance(raw_after, dict):
            raise AccessError(_("Chain link 'after' must be "
                                "{kind, model}."))
        kind = (raw_after.get("kind") or "").strip()
        model = (raw_after.get("model") or "").strip()
        if not kind or kind not in REGISTRY:
            raise AccessError(_("Chain link waits for unknown tool "
                                "'%(kind)s'.") % {"kind": kind or "—"})
        if not model or not _CHAIN_KEY_RE.match(model):
            raise AccessError(_("Chain link needs a model name."))
        after = {"kind": kind, "model": model}
    bind = None
    if raw_bind is not None:
        if not isinstance(raw_bind, dict) or not raw_bind:
            raise AccessError(_("Chain link 'bind' must be "
                                "{field: 'created'}."))
        clean = {}
        for key, target in raw_bind.items():
            if not isinstance(key, str) or not _CHAIN_KEY_RE.match(key):
                raise AccessError(_("Chain bind key %(key)s is not "
                                    "a field name.") % {"key": key})
            if target != _BIND_TARGET:
                raise AccessError(_("Chain bind target must be "
                                    "'created'."))
            clean[key] = target
        bind = clean
    if after and not bind:
        bind = {"res_id": _BIND_TARGET}
    return after, bind


def _chain_target(env, model_name, res_id, args):
    """Browse a write card's target, or None when its id arrives later.

    A chained step names a record that does not exist yet (the draft
    created two cards up). res_id 0/missing is accepted only with a
    valid after-link; validation then moves to approve time, which
    already re-checks every state. Anything else is still refused
    now, loudly. Call only after _model_allowed (unknown models
    must keep raising _denied, not KeyError).
    """
    try:
        rid = int(res_id or 0)
    except (TypeError, ValueError):
        rid = 0
    record = env[model_name].browse(rid).exists() if (
        model_name and rid) else None
    if record:
        return record
    after, _bind = _chain_args(args or {})
    if after:
        return None
    raise AccessError(env._("Record not found."))


# Fields Odoo itself fills during create(), so demanding them up
# front only blocks valid intents. product.template variants are
# the classic case: required=True with no compute and no default,
# yet create() mints them automatically. Add entries here only with
# a verified auto-fill path — never as a shortcut around a real
# missing value.
_AUTO_CREATED_FIELDS = frozenset({
    ("product.template", "product_variant_ids"),
})


def _missing_required(env, model_name, provided):
    """Required fields the caller must still supply, or [].

    Skips computed fields (Odoo computes them), fields with
    defaults (Odoo fills them), already-provided values,
    ORM-auto-created relations, and _inherits parent links
    (delegate inheritance mints the parent row on create — e.g.
    the mail.alias behind a new project, the partner behind a
    new user — so demanding them blocks valid cards). Used
    identically at queue time (early warning) and approve time
    (the gate), so the two never disagree.
    """
    try:
        model = env[model_name]
    except KeyError:
        return []
    fields = model._fields
    try:
        inherits_links = set(model._inherits.values())
    except Exception:
        inherits_links = set()
    provided = provided or {}
    missing = []
    for fname, field in fields.items():
        try:
            required = bool(field.required)
        except Exception:
            continue
        if not required:
            continue
        # Storage is deliberately NOT a condition: what matters is
        # what create() needs, and non-stored required fields fail
        # just as loudly without values.
        if field.default is not None:
            continue
        if getattr(field, "compute", None):
            continue
        if fname in provided:
            continue
        if fname in inherits_links:
            continue
        if (model_name, fname) in _AUTO_CREATED_FIELDS:
            continue
        missing.append(fname)
    return sorted(missing)


def _confirm_card(kind, title, summary, payload, origin="",
                  after=None, bind=None):
    from datetime import datetime, timedelta, timezone
    return {
        # Deterministic on purpose: the same intent builds the same
        # token, which is what makes the repeat guard, card dedupe
        # and idempotent re-Approve work. Unpredictability is not
        # needed — a token only works inside its owner's session.
        "token": _card_token(kind, payload, after, bind),
        "kind": kind,
        "title": title,
        "summary": summary,
        "payload": payload,
        # A chain link: which earlier queued card this one waits for
        # and which payload keys take that card's approved id.
        # Absent on plain cards (backward compatible).
        "after": after,
        "bind": bind,
        # Cards die after a day: an approval must match a fresh
        # intent, not last week's. Old cards without this key stay
        # valid (backward compatible).
        "expires": (datetime.now(timezone.utc)
                    + timedelta(hours=24)).isoformat(),
        # Origin marks cards that must never auto-apply (digitized
        # bills): the T3 auto path checks this, the confirm route
        # ignores it.
        "origin": origin or kind,
    }


def _card_live(card):
    """True when a card may still be approved or dismissed: cards
    without an expiry predate the rule and stay valid."""
    from datetime import datetime, timezone
    try:
        expires = (card or {}).get("expires")
        if not expires:
            return True
        return datetime.fromisoformat(expires) >= datetime.now(
            timezone.utc)
    except (TypeError, ValueError):
        return True


def card_freshness(env, card):
    """Re-validate a pending card against today's record state.

    Returns "" when the card still makes sense, else a short reason
    the drawer shows before Approve. The server re-checks again at
    approve time regardless — this is the early warning, not the
    gate.
    """
    try:
        kind = (card or {}).get("kind")
        payload = (card or {}).get("payload") or {}
        model_name = payload.get("model", "")
        if kind == "create_draft":
            return "" if _model_allowed(env, model_name) else \
                "that model is no longer available"
        if not model_name:
            return ""
        record = env[model_name].browse(
            payload.get("res_id") or 0).exists()
        if not record:
            # A chained card waits for its id from an earlier
            # approval: not gone, just not born yet. The drawer
            # already says which step it waits for.
            if (card or {}).get("after"):
                return ""
            return "the record is gone"
        if kind == "set_field":
            field = payload.get("field")
            try:
                current = record[field]
            except Exception:
                return "the field is gone"
            shown = getattr(current, "display_name", current)
            if str(shown or "") == str(payload.get("value") or "") \
                    or (hasattr(current, "id") and current.id
                        == payload.get("value")):
                return "already set to that value"
            return ""
        if kind == "set_fields":
            # A card whose fields all already hold the wanted value is
            # stale: approving it would change nothing.
            values = payload.get("values") or {}
            if not isinstance(values, dict) or not values:
                return "the card carries no values"
            for name, value in values.items():
                try:
                    current = record[name]
                except Exception:
                    return "the field is gone"
                shown = getattr(current, "display_name", current)
                if str(shown or "") == str(value or "") \
                        or (hasattr(current, "id") and current.id == value):
                    continue
                return ""
            return "already set to that value"
        if kind == "confirm_sale_order":
            if record.state not in _APPROVABLE_ORDERS:
                return "the quotation is %s" % record.state
            return ""
        if kind == "validate_invoice":
            if record.state != "draft":
                return "the invoice is %s" % record.state
            return ""
        if kind == "set_field":
            return _move_frozen_refusal(record, "edit")
        if kind == "unlink_record":
            return _move_frozen_refusal(record, "delete")
        if kind in ("register_payment", "reconcile_moves",
                    "reverse_move"):
            if record.state != "posted":
                return "the invoice is %s" % record.state
            return _move_frozen_refusal(record, "edit") or ""
        if kind == "resolve_ticket":
            try:
                closed = bool(record.stage_id.is_close)
            except Exception:
                closed = False
            return "the ticket is already closed" if closed else ""
        if kind == "invoice_order":
            if record.state not in ("sale", "done"):
                return "the order is %s" % record.state
            if record.invoice_status not in ("to invoice", "upselling"):
                return "nothing left to invoice (%s)" % (
                    record.invoice_status)
            return ""
        if kind == "execute_method":
            if (payload.get("model") == "account.move"
                    and payload.get("method") in ("button_draft",
                                                  "button_cancel")):
                return _move_draft_refusal(
                    record, payload.get("method")) or ""
            return ""
        return ""
    except Exception:
        return ""


def _preview_value(env, model_name, field_name, value):
    """One 'Label: value' preview line for a write card. Many2one ids
    resolve to names so the approver reads meaning, not numbers."""
    try:
        field = env[model_name]._fields.get(field_name)
    except Exception:
        field = None
    label = (field.string if field and field.string
             else field_name)
    shown = value
    try:
        if field and field.type == "many2one" and isinstance(
                value, (int, float)) and float(value).is_integer():
            target = env[field.comodel_name].browse(
                int(value)).exists()
            if target:
                shown = target.display_name
        elif field and field.type == "selection" and isinstance(
                value, str):
            # Selection keys are meaningless on a card ("consu"):
            # show the human label the approver actually judges.
            shown = dict(_selection_options(field, None)).get(
                value, value)
        elif isinstance(value, (list, tuple)) and value and all(
                isinstance(v, (list, tuple)) and len(v) == 3
                for v in value):
            shown = "%d line(s)" % len(value)
    except Exception:
        shown = value
    text = shown if isinstance(shown, str) else str(shown)
    # Strip markup server-side: values come from the model or from
    # record names, both of which can carry injected tags, and the
    # drawer renders previews as text.
    return _hr("%s: %s" % (label, text[:60]), 120)


def _refuse_lead_dupe(env, values, ctx=None):
    """Voice/paste lead capture lands here from any channel: an
    existing lead with the same email or name turns the draft into a
    question (which keeper?) instead of a silent duplicate. Escapes:
    force=true in args, or a session where the user just affirmed the
    ask (the server bridges weak models that cannot pass force back).
    Exact match after normalization: ilike finds candidates, the
    filter decides."""
    if (ctx or {}).get("force_dupe"):
        return
    email = (values.get("email_from") or "").strip().lower()
    name = (values.get("name") or "").strip()
    domain = []
    if email:
        domain = [("email_from", "ilike", email)]
    elif name:
        domain = [("name", "ilike", name)]
    if not domain:
        return
    dupes = env["crm.lead"].search(domain, limit=3)
    if email:
        dupes = dupes.filtered(
            lambda lead: (lead.email_from or "").strip().lower()
            == email)
    else:
        lowered = name.lower()
        dupes = dupes.filtered(
            lambda lead: (lead.name or "").strip().lower() == lowered)
    if not dupes:
        return
    options = ["Create anyway"]
    for dupe in dupes:
        options.append("Open %s (%s)" % (
            dupe.display_name, dupe.stage_id.name or "?"))
    raise AskUser(
        env._("A matching lead already exists: %(who)s. Create "
              "another, or open the existing one?") % {
                  "who": ", ".join(
                      "%s (%s)" % (dupe.display_name,
                                   dupe.stage_id.name or "?")
                      for dupe in dupes)},
        options)


def _refuse_exact_dupe(env, model_name, values, ctx=None):
    """Silent duplicates stop here for partners and products.

    Same shape as the lead guard: an exact name/email/code match turns
    the draft into a question instead of a twin. Escapes: force=true
    in args, or force_dupe in ctx (the user just affirmed the ask).
    """
    if (ctx or {}).get("force_dupe"):
        return
    configs = {
        "res.partner": [("email", "email"),
                        ("name", "name")],
        "product.template": [("default_code", "default_code"),
                             ("name", "name")],
    }
    checks = configs.get(model_name) or []
    Model = env[model_name]
    for value_key, field_name in checks:
        needle = (values.get(value_key) or "").strip()
        if not needle:
            continue
        try:
            dupes = Model.search(
                [(field_name, "ilike", needle)], limit=3)
            dupes = dupes.filtered(
                lambda r, _f=field_name, _n=needle.lower(): (
                    (getattr(r, _f, "") or "").strip().lower() == _n))
        except Exception:
            continue
        if not dupes:
            continue
        options = ["Create anyway"]
        for dupe in dupes:
            options.append("Open %s" % dupe.display_name)
        raise AskUser(
            env._("A matching %(model)s already exists: %(who)s. "
                   "Create another, or open the existing one?") % {
                       "model": Model._description or model_name,
                       "who": ", ".join(
                           d.display_name for d in dupes)},
            options)


def _coerce_create_value(env, model_name, field, value):
    """Validate one create_draft value the way set_field does, so a
    queued card can never carry a value Odoo itself would refuse.
    Returns the normalized value. Raises AccessError naming the
    problem (the turn loop hands it straight back to the model so
    it re-queues correctly in the same turn instead of showing the
    user a card that can only fail on Approve)."""
    if value is None or value is False:
        # Explicitly unset: Odoo treats both as empty. Coercion
        # would stringify None into "None".
        return value
    if isinstance(value, float) and value.is_integer():
        # JSON has no int/float distinction: 1.0 means id 1.
        value = int(value)
    ftype = (field.type if field else "") or ""
    if ftype in ("one2many", "many2many") and isinstance(
            value, (list, tuple)):
        # Command triples pass through structurally checked, with
        # (0, 0, vals) / (1, id, vals) dicts coerced recursively.
        coerced = []
        for command in value:
            if not isinstance(command, (list, tuple)) or not command:
                raise AccessError(env._(
                    "Bad line command %(cmd)r on %(field)s: use "
                    "[0, 0, {values}], [1, id, {values}], "
                    "[2, id], [3, id], [4, id], [5] or "
                    "[6, 0, [ids]].") % {
                        "cmd": command, "field": field.string
                        if field and field.string else ""})
            op = command[0]
            if op not in (0, 1, 2, 3, 4, 5, 6):
                raise AccessError(env._(
                    "Bad line command %(cmd)r on %(field)s.") % {
                        "cmd": command, "field": field.string
                        if field and field.string else ""})
            if op in (0, 1) and len(command) == 3 and isinstance(
                    command[2], dict):
                sub = env[field.comodel_name]
                inner = {}
                for key, val in command[2].items():
                    sub_field = sub._fields.get(key)
                    if sub_field is None:
                        raise AccessError(env._(
                            "No field %(field)s on %(model)s.") % {
                                "field": key,
                                "model": field.comodel_name})
                    inner[key] = _coerce_create_value(
                        env, field.comodel_name, sub_field, val)
                coerced.append([op, command[1], inner])
            else:
                coerced.append(list(command))
        return coerced
    if ftype in ("char", "text", "html", "boolean", "integer",
                 "float", "date", "datetime", "selection",
                 "many2one"):
        _value, _display = _coerce_field_value(
            env, field, value, None)
        return _value
    # Exotic kinds (reference, binary, json, ...): keep today's
    # behavior — pass through, Odoo decides at Approve time.
    return value


def tool_create_draft(env, args, ctx):
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    values = args.get("values") or {}
    if not isinstance(values, dict):
        values = {}
    safe = {}
    for key, value in values.items():
        field = env[model_name]._fields.get(key)
        if field is None:
            continue
        # Validate + normalize now, not at Approve: a queued card
        # must never carry a value Odoo itself would refuse (wrong
        # selection key, bad date, unknown id...). Failures and
        # ambiguous names raise here, so the turn loop corrects or
        # asks BEFORE any card exists for the user to approve.
        safe[key] = _coerce_create_value(env, model_name, field, value)
    if model_name == "crm.lead" and not (args.get("force") is True):
        _refuse_lead_dupe(env, safe, ctx)
    if model_name in ("res.partner", "product.template") and not (
            args.get("force") is True):
        _refuse_exact_dupe(env, model_name, safe, ctx)
    if model_name == "product.product" and not safe.get(
            "product_tmpl_id"):
        # A variant cannot exist without its template — and a template
        # auto-creates its first variant. Refuse here (with the fix
        # spelled out) instead of queuing a card that can never be
        # approved: the turn loop hands this straight back to the
        # model so it re-queues correctly in the same turn.
        raise AccessError(env._(
            "Create a product.template instead (name, list_price, "
            "default_code...): Odoo auto-creates its variant. Only "
            "use product.product when adding a variant to an "
            "existing template, with its product_tmpl_id."))
    title = env[model_name]._description or model_name
    if model_name == "account.move":
        # _description is the generic "Journal Entry": approvers should
        # see what the draft really is (F4: T-806 cosmetic note).
        move_type = (safe.get("move_type") or "").strip()
        if move_type == "in_invoice":
            title = env._("Vendor Bill")
        elif move_type == "out_invoice":
            title = env._("Customer Invoice")
    card = _confirm_card(
        "create_draft",
        env._("Create %s") % title,
        env._("%d fields prepared") % len(safe),
        {"model": model_name, "values": safe},
    )
    preview = [_preview_value(env, model_name, key, value)
               for key, value in list(safe.items())[:8]]
    if len(safe) > 8:
        preview.append(env._("+ %d more field(s)") % (len(safe) - 8))
    dropped = [key for key in (values or {}) if key not in safe]
    if dropped:
        # The model sent fields that do not exist (internal_reference
        # instead of default_code, sales_price instead of list_price
        # ...): say so on the card, and the turn loop repeats it back
        # to the model so it can re-queue with looked-up names
        # instead of the user approving a half-empty record.
        preview.append(env._(
            "Ignored unknown fields: %s — check names with "
            "fields_get.") % ", ".join(dropped[:6]))
    try:
        missing = _missing_required(env, model_name, safe)
    except Exception:
        missing = []
    if missing:
        preview.append(env._(
            "Still missing required: %s") % ", ".join(missing[:6]))
    card["preview"] = preview
    if dropped:
        card["notice"] = env._(
            "Ignored unknown fields: %s — look up real field names "
            "with fields_get and re-queue.") % ", ".join(dropped[:6])
    return card


def tool_log_note(env, args, ctx):
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    record = _chain_target(env, model_name, args.get("res_id"), args)
    name = record.display_name if record else (
        "a new %s" % (env[model_name]._description or model_name))
    return _confirm_card(
        "log_note", env._("Log note on %s") % name,
        _hr(args.get("body") or "", 120),
        {"model": model_name,
         "res_id": record.id if record else 0,
         "body": args.get("body") or ""},
    )


def tool_schedule_activity(env, args, ctx):
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    record = _chain_target(env, model_name, args.get("res_id"), args)
    name = record.display_name if record else (
        "a new %s" % (env[model_name]._description or model_name))
    return _confirm_card(
        "schedule_activity",
        env._("Schedule activity on %s") % name,
        _hr(args.get("summary") or "", 120),
        {"model": model_name, "res_id": record.id if record else 0,
         "summary": _hr(args.get("summary") or "Follow up", 120),
         "date": args.get("date") or str(fields.Date.today())},
    )


_APPROVABLE_ORDERS = ("draft", "sent")


def tool_confirm_sale_order(env, args, ctx):
    """Queue confirming a quotation. The card states the total so the
    approver sees the money, not just the number."""
    if not _model_allowed(env, "sale.order"):
        raise _denied("sale.order", env)
    order = _chain_target(env, "sale.order", args.get("res_id"), args)
    if order and order.state not in _APPROVABLE_ORDERS:
        raise AccessError(env._(
            "Only draft or sent quotations can be confirmed "
            "(this one is %(state)s).") % {"state": order.state})
    name = order.display_name if order else "a new quotation"
    if order:
        summary = env._("%(partner)s — %(amount)s") % {
            "partner": order.partner_id.display_name,
            "amount": "%s %s" % (order.amount_total,
                                 order.currency_id.name)}
    else:
        summary = env._("Confirm when created — runs only on approval.")
    return _confirm_card(
        "confirm_sale_order",
        env._("Confirm quotation %s") % name,
        summary,
        {"model": "sale.order", "res_id": order.id if order else 0},
    )


def tool_validate_invoice(env, args, ctx):
    """Queue validating (posting) a draft invoice or bill."""
    if not _model_allowed(env, "account.move"):
        raise _denied("account.move", env)
    move = _chain_target(env, "account.move", args.get("res_id"), args)
    if move and move.state != "draft":
        raise AccessError(env._(
            "Only draft invoices can be validated "
            "(this one is %(state)s).") % {"state": move.state})
    name = move.display_name if move else "a new invoice"
    if move:
        summary = env._("%(partner)s — %(amount)s") % {
            "partner": move.partner_id.display_name,
            "amount": "%s %s" % (move.amount_total,
                                 move.currency_id.name)}
    else:
        summary = env._("Validate when created — runs only on approval.")
    return _confirm_card(
        "validate_invoice",
        env._("Validate %s") % name,
        summary,
        {"model": "account.move", "res_id": move.id if move else 0},
    )


# ------------------------------------------------- generic operations
# copy / lines / delete / methods / aggregates / introspection /
# attachments. Reads run inline; every write queues a confirm card and
# re-validates at execute time. Nothing here bypasses record rules:
# everything runs as the user.

_COPY_RESET_STATEFUL = ("draft", "cancel")

_DENIED_METHODS = frozenset({
    # Private or identity-shedding.
    "create", "write", "unlink", "copy",
    # Anything starting with _ is refused separately below.
})


def _check_method(env, model_name, method):
    """A business method the agent may offer on a card. Refuses
    private methods, identity-shedding helpers, and raw ORM writes —
    those have dedicated, audited tools."""
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    name = (method or "").strip()
    if not name or name.startswith("_"):
        raise AccessError(env._(
            "Only public business methods can run, not %s.") % {
                "name": name or "—"})
    lowered = name.lower()
    if lowered in _DENIED_METHODS or lowered.startswith(
            ("sudo", "with_user", "with_env", "with_context")):
        raise AccessError(env._(
            "Method %s cannot run through the agent.") % {"name": name})
    return name


def _move_draft_refusal(record, method="button_draft"):
    """Why account.move.button_draft/button_cancel must not run, or "".

    Deliberate policy, stricter than Odoo 20 core (which unreconciles
    and cancels paid entries itself): through the agent, a paid
    invoice goes the credit-note route, never an in-place reset or
    cancel. Defensive getattr: the guard degrades to "" on models
    without these fields and never crashes the turn.
    """
    try:
        if getattr(record, "_name", "account.move") != "account.move":
            return ""
        state = getattr(record, "state", "") or ""
        if getattr(record, "inalterable_hash", False):
            return "the entry is locked (hashed) and cannot be reset"
        payment = getattr(record, "payment_state", "not_paid") or "not_paid"
        paid = payment in ("paid", "in_payment", "partial")
        if method == "button_draft" and state not in ("posted", "cancel"):
            return ("only posted or cancelled entries can be reset "
                    "to draft (this one is %s)") % (state or "unknown")
        if paid and method in ("button_draft", "button_cancel"):
            verb = ("resetting it to draft" if method == "button_draft"
                    else "cancelling it")
            return ("it is %s — %s "
                    "would break its payment reconciliation"
                    ) % (payment.replace("_", " "), verb)
    except Exception:
        return ""
    return ""


def _move_frozen_refusal(record, action):
    """Why an account.move must not be edited/deleted here, or "".

    action is "edit" (set_field) or "delete" (unlink_record).
    Draft/cancelled entries stay writable; posted ones go through
    Cancel + duplicate instead of in-place writes or deletes.
    """
    try:
        if getattr(record, "_name", "account.move") != "account.move":
            return ""
        state = getattr(record, "state", "") or ""
        if state in ("draft", "cancel", ""):
            return ""
        if getattr(record, "inalterable_hash", False):
            return "the entry is locked (hashed)"
        payment = getattr(record, "payment_state", "not_paid") or "not_paid"
        gerund = "editing" if action == "edit" else "deleting"
        if payment in ("paid", "in_payment", "partial"):
            return ("it is %s — %s a paid entry would break "
                    "its payment reconciliation") % (payment.replace(
                        "_", " "), gerund)
        return ("it is posted — cancel it first instead of "
                "%s it in place") % gerund
    except Exception:
        return ""
    return ""


def _similar_methods(record, hint=""):
    """Up to 5 public runnable methods, hint-matches first.

    Fed back when the model invents a method name, so the next guess
    is grounded in what the model actually has instead of a second
    hallucination.
    """
    try:
        names = [n for n in dir(type(record))
                 if not n.startswith("_") and callable(
                     getattr(type(record), n, None))]
    except Exception:
        return []
    denied = {d.lower() for d in _DENIED_METHODS}
    names = [n for n in names
             if n.lower() not in denied
             and not n.lower().startswith(
                 ("sudo", "with_user", "with_env", "with_context"))]
    hint = (hint or "").lower()
    if hint:
        keys = [w for w in hint.replace("_", " ").split() if len(w) > 2]
        scored = sorted(
            names,
            key=lambda n: (0 if any(k in n.lower() for k in keys) else 1, n))
    else:
        scored = sorted(names)
    return scored[:5]


def tool_copy_record(env, args, ctx):
    """Queue duplicating a record. The copy starts as a draft where
    the model supports states; sequences and posting fields are never
    carried over."""
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    record = _chain_target(env, model_name, args.get("res_id"), args)
    lines = ""
    if record:
        try:
            for fname in ("order_line", "invoice_line_ids", "line_ids",
                          "move_ids", "move_line_ids"):
                if fname in record._fields:
                    try:
                        lines = " — %d line(s)" % len(record[fname])
                        break
                    except Exception:
                        continue
        except Exception:
            lines = ""
    name = record.display_name if record else "a new %s" % (
        env[model_name]._description or model_name)
    return _confirm_card(
        "copy_record",
        env._("Duplicate %s") % name,
        env._("%(model)s %(name)s%(lines)s - the copy starts over "
              "as a draft") % {
                  "model": model_name, "name": name,
                  "lines": lines},
        {"model": model_name, "res_id": record.id if record else 0},
    )


def tool_read_related(env, args, ctx):
    """Read one2many lines of a record (order lines, invoice lines,
    picking moves...). Capped; restricted/binary fields stay out.
    res_ids reads up to 10 records in one call."""
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    ids = _batch_ids(env, args)
    if not ids:
        raise AccessError(env._("Name at least one record (res_id)."))
    field_name = (args.get("field") or "").strip()
    try:
        field = env[model_name]._fields[field_name]
    except KeyError:
        raise AccessError(env._(
            "No relation %(field)s on %(model)s.") % {
                "field": field_name, "model": model_name})
    if field.type not in ("one2many", "many2many") or field.groups:
        raise AccessError(env._(
            "Relation %(field)s cannot be read by the agent.") % {
                "field": field_name})
    if field.comodel_name and not _model_allowed(env, field.comodel_name):
        raise _denied(field.comodel_name, env)
    # Line fields validate against the line model, not the parent:
    # product_uom_qty lives on sale.order.line, not sale.order.
    wanted = _safe_fields(
        env, field.comodel_name or model_name,
        args.get("fields") or ["display_name"])
    limit, offset, order = _page_args(
        env, field.comodel_name or model_name, args)
    records = env[model_name].browse(ids).exists()
    batches = []
    missing = []
    for res_id in ids:
        record = records.filtered(lambda r, i=res_id: r.id == i)
        if not record:
            missing.append(res_id)
            continue
        batches.append(_related_lines(
            env, record, field_name, wanted, limit, offset, order))
    if args.get("res_ids") is None:
        if not batches:
            raise AccessError(_gone_or_denied(
                env, model_name, (args.get("res_id") or 0)))
        return batches[0]
    return {"model": model_name, "field": field_name,
            "records": batches, "missing": missing}


def _related_lines(env, record, field_name, wanted, limit, offset,
                   order):
    """One record's related lines, paged and optionally sorted."""
    lines = record[field_name]
    if order:
        # Recordsets sort in Python: stable one-key passes from the
        # last key backwards keep multi-field orders correct, each
        # with its own direction. Missing values sort last.
        keys = []
        for chunk in order.split(","):
            bits = chunk.strip().rsplit(" ", 1)
            keys.append((bits[0], bits[1] if len(bits) > 1 else "asc"))
        try:
            for name, direction in reversed(keys):
                def _one(line, _name=name):
                    value = line[_name]
                    if value is None or value is False:
                        return (1, "")
                    if hasattr(value, "id"):
                        return (0, value.display_name or "")
                    return (0, value)
                lines = lines.sorted(
                    key=_one, reverse=(direction == "desc"))
        except Exception:
            raise AccessError(env._("Cannot order by %s.") % order)
    total = len(lines)
    rows = []
    for line in lines[offset:offset + limit]:
        rows.append({"id": line.id, **{
            name: _fmt_value(line[name]) for name in wanted
            if name in line._fields}})
    return {"model": record._name, "res_id": record.id,
            "field": field_name, "count": total, "offset": offset,
            "limit": limit, "has_more": offset + len(rows) < total,
            "next_offset": offset + limit
            if offset + len(rows) < total else None,
            "rows": rows}


_READ_TEXT_TYPES = (
    "text/", "application/json", "application/xml",
    "application/javascript", "application/rss+xml",
    "application/atom+xml", "application/xhtml+xml", "application/csv",
)

_READ_MAX_BYTES = 2_000_000
_READ_MAX_CHARS = 8000


def _maybe_unb64(raw):
    """Undo a double base64 encoding (bytes of base64 text): writing
    base64 bytes into `raw` stores them as-is, so reads hand back
    base64 instead of content. Only decodes when the round-trip is
    exact — real binaries never match and pass through untouched."""
    try:
        text = bytes(raw).decode("ascii").strip()
    except Exception:
        return raw
    if not text or len(text) % 4:
        return raw
    try:
        decoded = base64.b64decode(text, validate=True)
    except Exception:
        return raw
    try:
        if base64.b64encode(decoded).decode("ascii") == text:
            return decoded
    except Exception:
        pass
    return raw


def tool_read_attachment(env, args, ctx):
    """Read a file attached to a record into the turn: text files and
    PDFs (text layer) come back as text; images and other binaries
    come back as a metadata envelope (the turn pipeline is text —
    bills go through digitize_bill, which has the vision path)."""
    try:
        attachment_id = int((args or {}).get("attachment_id") or 0)
    except (TypeError, ValueError):
        attachment_id = 0
    attachment = env["ir.attachment"].browse(attachment_id).exists()
    if not attachment:
        raise AccessError(env._("Attachment not found."))
    model_name = attachment.res_model or ""
    if not model_name or not _model_allowed(env, model_name):
        raise _denied(model_name or "attachments", env)
    if attachment.res_id:
        try:
            env[model_name].browse(attachment.res_id).exists().check_access(
                "read")
        except Exception:
            raise _denied(model_name, env)
    raw = attachment.raw
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    if not isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.content or b""
        except Exception:
            raw = b""
    raw = _maybe_unb64(raw)
    if len(raw) > _READ_MAX_BYTES:
        raise AccessError(env._(
            "That file is too large to read (%d bytes).") % len(raw))
    base = {"name": attachment.name or "",
            "mimetype": attachment.mimetype or "",
            "size": len(raw)}
    mimetype = (attachment.mimetype or "").split(";")[0].strip().lower()
    if mimetype.startswith(_READ_TEXT_TYPES):
        text = bytes(raw).decode("utf-8", "replace")
        base.update({"kind": "text", "truncated": len(text) > _READ_MAX_CHARS,
                     "content": text[:_READ_MAX_CHARS]})
        return base
    if mimetype == "application/pdf":
        _stage(ctx, "reading document")
        try:
            import io as _io
            from pypdf import PdfReader as _PdfReader
            reader = _PdfReader(_io.BytesIO(bytes(raw)))
            if reader.is_encrypted:
                raise AccessError(env._(
                    "That PDF is encrypted and cannot be read."))
            pages = [(page.extract_text() or "")
                     for page in reader.pages]
        except AccessError:
            raise
        except Exception:
            raise AccessError(env._(
                "That PDF could not be read as text."))
        text = "\n".join(pages).strip()
        if not text:
            base.update({
                "kind": "image",
                "note": "No selectable text: a scanned PDF. "
                        "Vendor bills go through digitize_bill."})
            return base
        base.update({"kind": "text", "pages": len(pages),
                     "truncated": len(text) > _READ_MAX_CHARS,
                     "content": text[:_READ_MAX_CHARS]})
        return base
    if mimetype.startswith("image/"):
        base.update({
            "kind": "image",
            "note": "Image contents stay out of the text turn. "
                    "Vendor bills go through digitize_bill."})
        return base
    raise AccessError(env._(
        "That file type (%s) cannot be read into the turn.")
        % (mimetype or "unknown"))


def tool_unlink_record(env, args, ctx):
    """Queue deleting a record. Confirm-only, T3-gated at the tool
    level, re-checked at execute time. There is no silent delete."""
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    record = env[model_name].browse(
        int(args.get("res_id") or 0)).exists()
    if not record:
        raise AccessError(_gone_or_denied(
            env, model_name, args.get("res_id")))
    return _confirm_card(
        "unlink_record",
        env._("Delete %s") % record.display_name,
        env._("%(model)s %(name)s — deletion cannot be undone") % {
            "model": model_name, "name": record.display_name},
        {"model": model_name, "res_id": record.id},
    )


def tool_cancel_invoice(env, args, ctx):
    """Queue cancelling an invoice. Pre-checked: paid/locked entries
    refuse with the credit-note route instead of a dead card."""
    if not _model_allowed(env, "account.move"):
        raise _denied("account.move", env)
    move = _chain_target(env, "account.move", args.get("res_id"), args)
    if move:
        refusal = _move_draft_refusal(move, "button_cancel")
        if refusal:
            raise AccessError(env._(
                "Cannot cancel %(name)s: %(reason)s. Offer Duplicate "
                "or a credit note instead.") % {
                    "name": move.display_name, "reason": refusal})
        name = move.display_name
        detail = env._("%(model)s %(name)s is %(state)s%(unpaid)s — "
                       "runs only on approval") % {
            "model": "account.move", "name": move.display_name,
            "state": move.state,
            "unpaid": "" if (getattr(
                move, "payment_state", "") or "") == "not_paid"
            else " (%s)" % move.payment_state}
    else:
        name = "a new invoice"
        detail = env._("Cancel when created — runs only on approval.")
    return _confirm_card(
        "execute_method",
        env._("Cancel %s") % name,
        detail,
        {"model": "account.move", "res_id": move.id if move else 0,
         "method": "button_cancel", "args": []},
    )


def _sale_order_draft(env, res_id, args=None):
    order = env["sale.order"].browse(int(res_id or 0)).exists()
    if not order:
        # A chained step waits for an order that does not exist yet;
        # validation moves to approve time.
        after = ((args or {}).get("after") or {})
        if isinstance(after, dict) and after.get("kind") \
                and after.get("model"):
            return None
        raise AccessError(env._("Record not found."))
    if order.state not in ("draft", "sent"):
        raise AccessError(env._(
            "That order is already %(state)s.") % {"state": order.state})
    return order


def tool_cancel_sale_order(env, args, ctx):
    """Queue cancelling a draft/sent quotation."""
    if not _model_allowed(env, "sale.order"):
        raise _denied("sale.order", env)
    order = _sale_order_draft(env, args.get("res_id"), args)
    name = order.display_name if order else "a new quotation"
    return _confirm_card(
        "execute_method",
        env._("Cancel %s") % name,
        env._("sale.order %(name)s — runs only on approval") % {
            "name": name},
        {"model": "sale.order", "res_id": order.id if order else 0,
         "method": "action_cancel", "args": []},
    )


def tool_confirm_purchase_order(env, args, ctx):
    """Queue confirming a draft RFQ."""
    if not _model_allowed(env, "purchase.order"):
        raise _denied("purchase.order", env)
    order = _chain_target(
        env, "purchase.order", args.get("res_id"), args)
    if order and order.state not in ("draft", "sent"):
        raise AccessError(env._(
            "That purchase order is already %(state)s.") % {
                "state": order.state})
    name = order.display_name if order else "a new purchase order"
    return _confirm_card(
        "execute_method",
        env._("Confirm %s") % name,
        env._("purchase.order %(name)s — runs only on approval") % {
            "name": name},
        {"model": "purchase.order", "res_id": order.id if order else 0,
         "method": "button_confirm", "args": []},
    )


def tool_validate_delivery(env, args, ctx):
    """Queue validating a ready delivery. Only assigned pickings
    qualify — anything else names what is missing instead."""
    if not _model_allowed(env, "stock.picking"):
        raise _denied("stock.picking", env)
    picking = _chain_target(
        env, "stock.picking", args.get("res_id"), args)
    if picking and picking.state != "assigned":
        raise AccessError(env._(
            "That delivery is %(state)s, not ready: reserve stock "
            "first (it must reach Ready before validating).") % {
                "state": picking.state})
    name = picking.display_name if picking else "a new delivery"
    return _confirm_card(
        "execute_method",
        env._("Validate %s") % name,
        env._("stock.picking %(name)s — quantities must be set; "
              "runs only on approval") % {"name": name},
        {"model": "stock.picking", "res_id": picking.id if picking else 0,
         "method": "button_validate", "args": []},
    )


def tool_invoice_order(env, args, ctx):
    """Queue creating the invoice(s) for a confirmed order."""
    if not _model_allowed(env, "sale.order"):
        raise _denied("sale.order", env)
    order = _chain_target(env, "sale.order", args.get("res_id"), args)
    if order:
        if order.state not in ("sale", "done"):
            raise AccessError(env._(
                "That order is %(state)s: confirm it before invoicing."
            ) % {"state": order.state})
        if order.invoice_status not in ("to invoice", "upselling"):
            raise AccessError(env._(
                "Nothing to invoice on that order (%(status)s): deliver "
                "first, or switch the product invoicing policy to "
                "ordered quantities.") % {"status": order.invoice_status})
        name = order.display_name
    else:
        name = "a new order"
    return _confirm_card(
        "invoice_order",
        env._("Invoice %s") % name,
        env._("sale.order %(name)s — creates the draft invoice; "
              "runs only on approval") % {"name": name},
        {"model": "sale.order", "res_id": order.id if order else 0},
    )


def tool_add_order_line(env, args, ctx):
    """Queue adding one line to a draft/sent order."""
    if not _model_allowed(env, "sale.order"):
        raise _denied("sale.order", env)
    order = _sale_order_draft(env, args.get("res_id"), args)
    product = env["product.product"].browse(
        int(args.get("product_id") or 0)).exists()
    if not product:
        raise AccessError(env._("Product not found."))
    try:
        qty = float(args.get("quantity") or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        raise AccessError(env._("Quantity must be positive."))
    oname = order.display_name if order else "a new order"
    return _confirm_card(
        "create_draft",
        env._("Add %(qty)g x %(product)s to %(order)s") % {
            "qty": qty, "product": product.display_name,
            "order": oname},
        env._("sale.order %(name)s — runs only on approval") % {
            "name": oname},
        {"model": "sale.order.line",
         "values": {"order_id": order.id if order else 0,
                    "product_id": product.id,
                    "product_uom_qty": qty}},
    )


def tool_register_payment(env, args, ctx):
    """Queue registering a payment against a posted invoice."""
    if not _model_allowed(env, "account.move"):
        raise _denied("account.move", env)
    move = _chain_target(env, "account.move", args.get("res_id"), args)
    if move:
        if move.state != "posted":
            raise AccessError(env._(
                "That invoice is %(state)s: only posted invoices take "
                "payments.") % {"state": move.state})
        residual = float(getattr(move, "amount_residual", 0) or 0)
        if residual <= 0:
            raise AccessError(env._(
                "That invoice has nothing left to pay."))
    else:
        residual = 0.0
    try:
        amount = float(args.get("amount") or residual)
    except (TypeError, ValueError):
        amount = residual
    if move:
        if amount <= 0 or amount - residual > 0.005:
            raise AccessError(env._(
                "Amount must be between 0 and %(residual).2f.") % {
                    "residual": residual})
    elif amount <= 0:
        # Deferred target: the approver still names the amount now;
        # the residual cap re-checks at approve time.
        raise AccessError(env._("Name a positive amount to pay."))
    journal_id = 0
    try:
        journal_id = int(args.get("journal_id") or 0)
    except (TypeError, ValueError):
        journal_id = 0
    journal = env["account.journal"].browse(journal_id).exists() if (
        journal_id) else None
    if not journal:
        journal = env["account.journal"].search(
            [("type", "=", "bank")], limit=1) or env[
                "account.journal"].search([("type", "=", "cash")], limit=1)
    if not journal:
        raise AccessError(env._("No bank or cash journal found."))
    name = move.display_name if move else "a new invoice"
    return _confirm_card(
        "register_payment",
        env._("Pay %(amount).2f on %(name)s") % {
            "amount": amount, "name": name},
        env._("%(model)s %(name)s via %(journal)s — runs only on "
              "approval") % {"model": "account.move",
                             "name": name,
                             "journal": journal.display_name},
        {"model": "account.move", "res_id": move.id if move else 0,
         "amount": amount, "journal_id": journal.id},
    )


def tool_reconcile_invoice(env, args, ctx):
    """Queue reconciling an invoice with its payment move(s)."""
    if not _model_allowed(env, "account.move"):
        raise _denied("account.move", env)
    move = _chain_target(env, "account.move", args.get("res_id"), args)
    pay_ids = args.get("payment_ids") or []
    if not isinstance(pay_ids, (list, tuple)) or not pay_ids:
        raise AccessError(env._(
            "Name the payment move(s) to reconcile with."))
    payments = env["account.move"].browse(pay_ids).exists()
    if not payments:
        raise AccessError(env._("Payment move(s) not found."))
    name = move.display_name if move else "a new invoice"
    return _confirm_card(
        "reconcile_moves",
        env._("Reconcile %s") % name,
        env._("%(model)s %(name)s with %(count)d payment move(s) — "
              "runs only on approval") % {
                  "model": "account.move", "name": name,
                  "count": len(payments)},
        {"model": "account.move", "res_id": move.id if move else 0,
         "payment_ids": payments.ids},
    )


def tool_credit_note(env, args, ctx):
    """Queue a credit note (reversal) for a posted invoice."""
    if not _model_allowed(env, "account.move"):
        raise _denied("account.move", env)
    move = _chain_target(env, "account.move", args.get("res_id"), args)
    if move and move.state != "posted":
        raise AccessError(env._(
            "Only posted invoices can be reversed (this one is "
            "%(state)s).") % {"state": move.state})
    reason = str(args.get("reason") or "Reversal")[:200]
    name = move.display_name if move else "a new invoice"
    return _confirm_card(
        "reverse_move",
        env._("Credit note for %s") % name,
        env._("%(model)s %(name)s — %(reason)s; runs only on "
              "approval") % {"model": "account.move",
                             "name": name, "reason": reason},
        {"model": "account.move", "res_id": move.id if move else 0,
         "reason": reason},
    )


def tool_resolve_ticket(env, args, ctx):
    """Queue resolving a helpdesk ticket into its closing stage."""
    if not _model_allowed(env, "helpdesk.ticket"):
        raise _denied("helpdesk.ticket", env)
    ticket = _chain_target(
        env, "helpdesk.ticket", args.get("res_id"), args)
    stage = env["helpdesk.stage"].search(
        [("is_close", "=", True)], limit=1)
    if not stage:
        stage = env["helpdesk.stage"].search(
            ["|", ("name", "ilike", "done"),
             "|", ("name", "ilike", "closed"),
             ("name", "ilike", "resolved")], limit=1)
    if not stage:
        raise AccessError(env._("No closing stage found."))
    note = str(args.get("note") or "")[:500]
    name = ticket.display_name if ticket else "a new ticket"
    return _confirm_card(
        "resolve_ticket",
        env._("Resolve %s") % name,
        env._("helpdesk.ticket %(name)s → %(stage)s — runs only on "
              "approval") % {"name": name,
                             "stage": stage.display_name},
        {"model": "helpdesk.ticket", "res_id": ticket.id if ticket else 0,
         "stage_id": stage.id, "note": note},
    )


def tool_log_timesheet(env, args, ctx):
    """Queue logging hours on a project task."""
    if not _model_allowed(env, "account.analytic.line"):
        raise _denied("account.analytic.line", env)
    task = _chain_target(env, "project.task",
                           args.get("task_id"), args)
    try:
        hours = float(args.get("hours") or 0)
    except (TypeError, ValueError):
        hours = 0
    if hours <= 0 or hours > 24:
        raise AccessError(env._("Hours must be between 0 and 24."))
    if task:
        summary = str(args.get("summary") or task.display_name)[:200]
        # Analytic lines require a real date (never False) and at
        # least one plan account: take the task project's account,
        # else refuse with an ask instead of a crashing create.
        project = task.project_id
        account_id = project.account_id.id if project and getattr(
            project, "account_id", None) else False
        if not account_id:
            raise AccessError(env._(
                "That task's project has no analytic account: pick a "
                "project with one first."))
        day = args.get("date") or fields.Date.today()
        values = {"name": summary, "unit_amount": hours,
                  "task_id": task.id,
                  "project_id": project.id if project else False,
                  "account_id": account_id,
                  "date": day}
        label = task.display_name
    else:
        # Deferred task: the approver still names hours and words now;
        # project, account and today's date fill in at approve time.
        summary = str(args.get("summary") or "Worked hours")[:200]
        values = {"name": summary, "unit_amount": hours,
                  "task_id": 0,
                  "project_id": False,
                  "account_id": False,
                  "date": fields.Date.today()}
        label = "a new task"
    return _confirm_card(
        "create_draft",
        env._("Log %(hours)g h on %(task)s") % {
            "hours": hours, "task": label},
        env._("account.analytic.line — runs only on approval"),
        {"model": "account.analytic.line", "values": values},
    )


def tool_execute_method(env, args, ctx):
    """Queue calling a public business method (confirm, validate, pay,
    cancel...). Confirm-only and never auto-applied: the card names the
    method, the record and the arguments, and the user approves."""
    model_name = args.get("model", "")
    record = None
    if model_name:
        if not _model_allowed(env, model_name):
            raise _denied(model_name, env)
        record = _chain_target(env, model_name, args.get("res_id"), args)
    if not record and not ((args or {}).get("after") or {}).get("kind"):
        raise AccessError(env._(
            "Name the record to act on (res_id)."))
    name = _check_method(env, model_name, args.get("method"))
    if not _side_effect_allowed(env, model_name, name):
        raise AccessError(env._(
            "Method %(method)s is not on this deployment's approved "
            "list. Ask the administrator to allowlist it.") % {
                "method": "%s.%s" % (model_name, name)})
    if record is None:
        # Deferred target: existence, method and state checks all move
        # to approve time, which re-validates everything against the
        # then-real record.
        pass
    else:
        target = getattr(type(record), name, None)
        if not callable(target):
            hints = _similar_methods(record, name)
            raise AccessError(env._(
                "Method %(method)s does not exist on %(model)s."
                "%(hints)s Do not guess another name — use fields_get "
                "on %(model)s and offer one of the available methods."
            ) % {
                "method": name, "model": model_name,
                "hints": (" Available: %s." % ", ".join(hints))
                if hints else ""})
    if record is not None and model_name == "account.move" \
            and name in ("button_draft", "button_cancel"):
        refusal = _move_draft_refusal(record, name)
        if refusal:
            raise AccessError(env._(
                "Cannot run %(method)s on %(name)s: %(reason)s. Offer "
                "Duplicate (copy_record) or a credit note instead — "
                "queue one of those, not another reset method.") % {
                    "method": name, "name": record.display_name,
                    "reason": refusal})
    echoed = str(args.get("args") or "")[:200]
    label = record.display_name if record else "a new %s" % model_name
    raw_args = args.get("args")
    if raw_args is not None and not isinstance(raw_args, (list, tuple)):
        raise AccessError(env._(
            "Method arguments must be a list, e.g. [\"draft\"]."))
    raw_kwargs = args.get("kwargs") or {}
    if not isinstance(raw_kwargs, dict):
        raise AccessError(env._(
            "Method keyword arguments must be an object, "
            "e.g. {\"state\": \"draft\"}."))
    return _confirm_card(
        "execute_method",
        env._("%(method)s on %(record)s") % {
            "method": name, "record": label},
        env._("%(model)s %(name)s%(args)s — runs only on approval") % {
            "model": model_name, "name": label,
            "args": (" with %s" % echoed) if echoed else ""},
        {"model": model_name, "res_id": record.id if record else 0,
         "method": name,
         "args": list(raw_args) if raw_args else [],
         "kwargs": dict(raw_kwargs)},
    )


def tool_read_group(env, args, ctx):
    """Aggregate: counts and sums grouped by a field. Read-only.

    Odoo 20 read_group takes (domain, groupby, aggregates) and
    returns tuples, so this wrapper translates the old field list:
    '__count' -> id:count, 'amount_total' -> amount_total:sum for
    numeric fields, explicit 'field:agg' passes through. Rows come
    back as dicts keyed by the groupby/aggregate specs (__count
    kept for back-compat).
    """
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    domain = _safe_domain(env, args.get("domain") or [])
    wanted = [f for f in (args.get("fields") or ["__count"])
              if isinstance(f, str)][:5]
    groupby = [g for g in (args.get("groupby") or []) if isinstance(
        g, str)][:3]
    aggregates = []
    keys = []
    for spec in wanted:
        if spec == "__count":
            aggregates.append("id:count")
            keys.append("__count")
            continue
        if ":" in spec:
            aggregates.append(spec)
            keys.append(spec.replace(":", "_"))
            continue
        try:
            field = env[model_name]._fields[spec]
        except KeyError:
            raise AccessError(env._("No field %(field)s on %(model)s.")
                              % {"field": spec, "model": model_name})
        if field.type not in ("integer", "float", "monetary"):
            raise AccessError(env._(
                "Field %(field)s is not summable: use "
                "'%(field)s:agg' (e.g. %(field)s:array_agg).") % {
                    "field": spec})
        aggregates.append("%s:sum" % spec)
        keys.append("%s_sum" % spec)
    try:
        groups = env[model_name].read_group(
            list(domain), groupby, aggregates, limit=25)
    except Exception as err:
        raise AccessError(env._("Aggregation failed: %s")
                          % str(err)[:160])
    rows = []
    for group in groups[:25]:
        row = {}
        for pos, spec in enumerate(list(groupby) + keys):
            try:
                row[spec] = _fmt_value(group[pos])
            except IndexError:
                row[spec] = None
        rows.append(row)
    return {"model": model_name, "groupby": groupby, "rows": rows}


def tool_fields_get(env, args, ctx):
    """List a model's fields (name, type, label, required, readonly,
    selection options). Read-only introspection so the model stops
    inventing field names. Internals (groups/compute/inverse) stay out.
    'query' matches a substring against names AND labels (e.g.
    "date" finds every date-ish field without dumping all 150+);
    'fields' names exact fields. Filters run before the 120-row cap,
    so a focused query always sees the complete answer."""
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    query = (args.get("query") or "").strip().lower()
    wanted = args.get("fields") or []
    if isinstance(wanted, str):
        wanted = [wanted]
    wanted = {str(name).strip() for name in wanted if str(name).strip()}
    if wanted and query:
        raise AccessError(env._(
            "Pass query or fields, not both."))
    out = []
    for name, field in sorted(env[model_name]._fields.items()):
        if field.groups:
            continue
        if wanted and name not in wanted:
            continue
        label = field.string or name
        if query and query not in name.lower() \
                and query not in (label or "").lower():
            continue
        entry = {"name": name, "type": field.type,
                 "label": label or name,
                 "required": bool(field.required),
                 "readonly": bool(field.readonly),
                 "has_default": field.default is not None}
        if field.type == "selection":
            try:
                entry["options"] = [
                    key for key, _label in field.selection or []][:20]
            except Exception:
                pass
        if field.type in ("many2one", "one2many", "many2many"):
            entry["relation"] = field.comodel_name
        out.append(entry)
        if len(out) >= 120:
            break
    return {"model": model_name, "fields": out,
            "query": query or sorted(wanted) or None}


def _public_business_methods(env, model_name):
    """Curated method names for self-correction: public business
    buttons first (action_*/button_*), then other public callables.
    Private, identity-shedding and raw-ORM names never surface."""
    try:
        cls = type(env[model_name])
    except KeyError:
        return []
    denied = {d.lower() for d in _DENIED_METHODS}
    found = []
    try:
        names = [n for n in dir(cls) if not n.startswith("_")]
    except Exception:
        return []
    for name in names:
        try:
            target = getattr(cls, name, None)
        except Exception:
            continue
        if not callable(target):
            continue
        lowered = name.lower()
        if lowered in denied or lowered.startswith(
                ("sudo", "with_user", "with_env", "with_context")):
            continue
        found.append(name)
    found.sort(key=lambda n: (
        0 if n.startswith(("action_", "button_")) else 1, n))
    return found[:30]


def _method_signature(env, model_name, name):
    """(params, doc): parameter names with defaults for one public
    method, plus its docstring head. Read-only introspection —
    signatures are examined, never executed. No file paths or
    source code leave the server."""
    import inspect
    cls = type(env[model_name])
    target = getattr(cls, name, None)
    if not callable(target):
        return None, ""
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        return [], ""
    params = []
    for index, (pname, param) in enumerate(sig.parameters.items()):
        if index == 0 and pname in ("self", "cls", "records", "this"):
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            params.append("%s..." % pname)
        elif param.default is inspect.Parameter.empty:
            params.append(pname)
        else:
            try:
                shown = repr(param.default)
            except Exception:
                shown = "…"
            params.append("%s=%s" % (pname, shown[:40]))
    try:
        doc = (inspect.getdoc(target) or "").strip().split("\n")[0]
    except Exception:
        doc = ""
    return params, doc[:300]


def tool_diagnose_access(env, args, ctx):
    """Diagnose whether the user may touch a model or a record, and why.

    Read what the ORM itself would decide - model access per operation,
    the ir.access rows that apply to this user, the effective rule
    domain for each operation, and, for one record, whether THIS row
    may be read and written. Field names come back with their groups,
    so "the user cannot write this field" is answered before a card
    is queued for a write that will fail at Approve.
    """
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    model = env[model_name]
    ops = args.get("operations")
    if not isinstance(ops, (list, tuple)) or not ops:
        ops = ["read", "write", "create", "unlink"]
    ops = [str(op).strip().lower() for op in ops
           if str(op).strip().lower() in
           ("read", "write", "create", "unlink")]
    if not ops:
        raise AccessError(env._(
            "operations must be any of read, write, create, unlink."))
    out = {"model": model_name, "label": model._description or model_name}

    # 1) Model access, per operation, as the ORM decides it.
    can = {}
    for op in ops:
        try:
            can[op] = bool(model.has_access(op))
        except Exception:
            can[op] = False
    out["can"] = can

    # 2) The access rows that apply to THIS user. A row with no group
    # is a global restriction - it binds everyone, including admins
    # who are not the superuser.
    rows = []
    try:
        user_groups = {g.id for g in
                       (env.user.all_group_ids or env.user.group_ids)}
    except Exception:
        user_groups = set()
    try:
        rule_rows = env["ir.access"].search(
            [("model_id.model", "=", model_name)])
    except Exception:
        rule_rows = []
    for row in rule_rows:
        group = row.group_id
        applies = (not group) or (group.id in user_groups)
        rows.append({
            "name": row.name or "",
            "operation": row.operation or "",
            "group": group.display_name if group else "",
            "global_rule": not bool(group),
            "domain": row.domain or "",
            "read": bool(row.for_read),
            "write": bool(row.for_write),
            "create": bool(row.for_create),
            "unlink": bool(row.for_unlink),
            "applies_to_you": bool(applies),
        })
    out["rules"] = rows

    # 3) The user's own groups, so the model can see the set it is in.
    try:
        out["your_groups"] = sorted(
            {g.display_name for g in
             (env.user.all_group_ids or env.user.group_ids)})
    except Exception:
        out["your_groups"] = []

    # 4) The effective rule domain per operation: the rows Nova may
    # never see. Rendered, not evaluated - a domain is the truth.
    try:
        for op in ops:
            out.setdefault("domains", {})[op] = str(
                model._access_domain(op))
    except Exception:
        pass

    # 5) One record, judged per operation. A missing row and an
    # unreadable row look the same from the outside; here they do not.
    res_id = args.get("res_id")
    if res_id:
        try:
            number = int(res_id)
        except (TypeError, ValueError):
            raise AccessError(env._("res_id must be a record id."))
        record = model.browse(number).exists()
        if not record:
            out["record"] = {"res_id": number, "exists": False}
        else:
            judged = {"res_id": number, "exists": True,
                      "display_name": record.display_name}
            for op in ops:
                try:
                    judged[op] = bool(record.has_access(op))
                except Exception:
                    judged[op] = False
            out["record"] = judged

    # 6) Field-level groups, for the fields the model names. A field
    # behind a group the user lacks will fail at Approve; saying so
    # BEFORE the card is queued is the whole point.
    wanted = args.get("fields") or []
    if isinstance(wanted, str):
        wanted = [wanted]
    gated = []
    for name in [str(f).strip() for f in wanted if str(f).strip()]:
        field = model._fields.get(name)
        if field is None:
            gated.append({"field": name, "known": False})
            continue
        groups = [g.strip() for g in str(getattr(field, "groups", "")
                                        or "").split(",") if g.strip()]
        missing = [g for g in groups
                   if not env.user.has_group(g)]
        gated.append({
            "field": name,
            "known": True,
            "label": field.string or name,
            "type": field.type,
            "readonly": bool(field.readonly),
            "groups": groups,
            "blocked_by_group": missing,
            "writable": bool(field.readonly is False and not missing),
        })
    if gated:
        out["fields"] = gated
    detail = {"read": can.get("read", True)}
    if not can.get("read", True):
        out["note"] = ("You may not read this model: stop here and say "
                       "so, do not search it.")
    return out


def tool_scan_codebase(env, args, ctx):
    """Read the deployment's own code into knowledge-shaped answers.

    The agent knows Odoo's core from its seeds, but not YOUR models -
    your sale.order.line extension, your approval model, your computed
    fields. This scans the custom addons (AST only, never imported,
    never executed) and returns either the catalogue of models found
    or the full concept bodies for the models you name. Propose what
    you learn with write_knowledge; the human review gate still
    applies to everything you write.
    """
    from odoo.addons.nova_ai.models import nova_ai_scanner as scanner
    query = str(args.get("model") or args.get("query") or "").strip().lower()
    try:
        limit = max(1, min(int(args.get("limit") or 200), 200))
    except (TypeError, ValueError):
        limit = 200
    roots = args.get("paths")
    if isinstance(roots, str):
        roots = [roots]
    import os as _os
    try:
        import odoo.tools as _tools
        raw = _tools.config.get("addons_path") or []
        if isinstance(raw, str):
            raw = raw.split(",")
        default_roots = [str(p).strip() for p in raw
                         if "custom" in str(p).lower()]
    except Exception:
        default_roots = []
    if not roots:
        roots = default_roots
    # Confine explicit paths to the custom addons: a model-supplied
    # path must resolve inside one of the default roots, so the tool
    # can never walk /etc, the venv, or another database's filestore.
    allowed = [_os.path.realpath(p) for p in default_roots]
    roots = [r for r in roots
             if not allowed
             or any(_os.path.realpath(r) == a
                    or _os.path.realpath(r).startswith(a + _os.sep)
                    for a in allowed)]
    if not roots:
        raise AccessError(env._(
            "scan_codebase is confined to the custom addons."))
    found = scanner.scan_paths(roots, limit_models=limit)
    models = found.get("models", [])
    if query:
        models = [m for m in models
                  if query in (m.get("model") or "").lower()
                  or query in (m.get("doc") or "").lower()
                  or any(query in (f.get("name") or "").lower()
                         for f in m.get("fields", []))]
    if args.get("full"):
        # Full bodies for the named models: the agent reads these the
        # way it reads a skill.
        out = []
        for model in models[:10]:
            concept = scanner.concept_for_model(model)
            out.append({
                "code": concept["code"],
                "name": concept["name"],
                "description": concept["description"],
                "body": concept["body"],
                "source": (concept["sources"] or [{}])[0].get("resource", ""),
            })
        return {"models": out,
                "note": "Propose what matters with write_knowledge."}
    rows = [{
        "model": m.get("model") or "",
        "label": m.get("doc") or m.get("model") or "",
        "fields": len(m.get("fields") or []),
        "methods": len(m.get("methods") or []),
        "file": (m.get("file") or "").replace("\\", "/").split(
            "custom_addons/")[-1],
    } for m in models]
    return {
        "models": rows,
        "files": found.get("files", 0),
        "truncated": bool(found.get("truncated", False)),
        "note": ("Pass model=<technical name> with full=true for the "
                 "whole concept, then propose it with write_knowledge."),
    }


def tool_inspect_method(env, args, ctx):
    """Read a method's signature and docs — or list a model's
    business methods. Read-only self-repair kit: when a method call
    fails, inspect first, then re-queue with correct arguments
    instead of guessing again. Never executes anything; never
    reveals file paths or source code.
    """
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    name = (args.get("method") or "").strip()
    if not name:
        methods = _public_business_methods(env, model_name)
        if not methods:
            raise AccessError(env._(
                "No callable business methods on %(model)s.") % {
                    "model": model_name})
        return {"model": model_name, "methods": methods}
    name = _check_method(env, model_name, name)
    try:
        target = getattr(type(env[model_name]), name, None)
    except KeyError:
        raise AccessError(env._("Model not found."))
    if not callable(target):
        hints = _similar_methods(env[model_name], name)
        raise AccessError(env._(
            "Method %(method)s does not exist on %(model)s."
            "%(hints)s Do not guess another name — list the model's "
            "methods with inspect_method and pick one.") % {
                "method": name, "model": model_name,
                "hints": (" Available: %s." % ", ".join(hints))
                if hints else ""})
    params, doc = _method_signature(env, model_name, name)
    out = {"model": model_name, "method": name,
           "params": params or []}
    if doc:
        out["does"] = doc
    return out


def _ask_option_label(option):
    """One option's button label, whatever shape it was queued in."""
    if isinstance(option, dict):
        return str(option.get("label") or "").strip()
    return str(option or "").strip()


def tool_ask_user(env, args, ctx):
    """Ask the user a clarifying question with clickable options.
    Use it instead of guessing when the request is ambiguous ("which
    Acme?", "post or draft first?"). Ends the turn; the user's pick
    arrives as the next message with full history intact, so the
    turn resumes with the answer. Read-only: nothing is queued,
    nothing runs. Options accept plain strings or {label,
    description} objects; header is an optional short label."""
    question = (args.get("question") or "").strip()
    if not question:
        raise AccessError(env._(
            "Ask a real question: the 'question' text is required."))
    if len(question) > 300:
        question = question[:300]
    header = (args.get("header") or "").strip()[:30]
    raw_options = args.get("options") or []
    if not isinstance(raw_options, (list, tuple)):
        raw_options = [raw_options]
    options = []
    for option in raw_options:
        if isinstance(option, dict):
            label = str(option.get("label") or "").strip()
            if not label or label in [o.get("label") for o in options
                                      if isinstance(o, dict)] \
                    or label in options:
                continue
            item = {"label": label[:80]}
            description = str(
                option.get("description") or "").strip()[:120]
            if description:
                item["description"] = description
            options.append(item)
        else:
            text = str(option or "").strip()
            if text and text not in [_ask_option_label(o)
                                     for o in options]:
                options.append(text[:80])
        if len(options) >= 6:
            break
    if not options:
        raise AccessError(env._(
            "Give 1 to 6 concrete options, not an open question."))
    payload = {"question": question, "options": options}
    if header:
        payload["header"] = header
    return "ask", payload, None


def tool_list_attachments(env, args, ctx):
    """List attachments on a record (name, type, size) so scans can be
    found for digitizing. Read-only; contents are never returned here.
    ir.attachment is otherwise never allow-listed — this scoped read is
    the single documented exception, still under record rules."""
    model_name = args.get("model", "")
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    record = env[model_name].browse(
        int(args.get("res_id") or 0)).exists()
    if not record:
        raise AccessError(_gone_or_denied(
            env, model_name, args.get("res_id")))
    rows = []
    try:
        attachments = env["ir.attachment"].search([
            ("res_model", "=", model_name),
            ("res_id", "=", record.id),
        ], limit=25)
    except Exception:
        attachments = []
    for attachment in attachments:
        try:
            size = attachment.file_size or 0
        except Exception:
            size = 0
        if size > 25 * 1024 * 1024:
            continue
        rows.append({"id": attachment.id, "name": attachment.name,
                     "mimetype": attachment.mimetype or "",
                     "size": size})
    return {"model": model_name, "res_id": record.id,
            "attachments": rows}


def _selection_options(field, record=None):
    """Selection options as [(key, label)]: Odoo 20 selections may be
    a static list or a callable taking the record/env."""
    options = getattr(field, "selection", None)
    if callable(options):
        try:
            options = options(record) if record is not None else options()
        except Exception:
            return []
        if isinstance(options, dict):
            return list(options.items())
    try:
        return [(key, _label) for key, _label in options or []]
    except TypeError:
        return []


_SETTABLE_TYPES = ("char", "text", "html", "selection", "integer", "float",
                   "boolean", "date", "datetime", "many2one")


def _coerce_field_value(env, field, raw, record=None):
    """Raw model output -> (value, display). Raises AccessError with
    the allowed shape when the value does not fit the field. record
    feeds dynamic (callable) selections where the caller has one."""
    ftype = field.type
    if ftype in ("char", "text", "html"):
        # Html fields sanitize on write server-side; the cap keeps
        # dumps out of the turn either way.
        return str(raw or "")[:2000], str(raw or "")[:80]
    if ftype == "boolean":
        if isinstance(raw, bool):
            return raw, str(raw)
        text = str(raw or "").strip().lower()
        if text in ("true", "yes", "1"):
            return True, "True"
        if text in ("false", "no", "0"):
            return False, "False"
        raise AccessError(env._(
            "True or False expected, got %(raw)s.") % {"raw": raw})
    if ftype in ("integer", "float"):
        try:
            value = int(raw) if ftype == "integer" else float(raw)
        except (TypeError, ValueError):
            raise AccessError(env._(
                "A number expected, got %(raw)s.") % {"raw": raw})
        return value, str(value)
    if ftype in ("date", "datetime"):
        text = str(raw or "").strip().lower()
        # Relative days first: weak models say "tomorrow", not dates.
        if text in ("today", "tomorrow", "yesterday"):
            from datetime import date as _date, timedelta as _td
            base = _date.today()
            if text == "tomorrow":
                base = base + _td(days=1)
            elif text == "yesterday":
                base = base - _td(days=1)
            return base.isoformat(), base.isoformat()
        try:
            fields.Date.from_string(str(raw)[:10])
        except ValueError:
            raise AccessError(env._(
                "YYYY-MM-DD expected, got %(raw)s.") % {"raw": raw})
        return str(raw)[:10], str(raw)[:10]
    if ftype == "selection":
        options = _selection_options(field, record)
        keys = [key for key, _label in options]
        if str(raw) not in keys:
            raise AccessError(env._(
                "One of %(keys)s expected, got %(raw)s.") % {
                    "keys": ", ".join(keys[:12]), "raw": raw})
        label = dict(options).get(str(raw), str(raw))
        return str(raw), label
    if ftype == "many2one":
        try:
            target = env[field.comodel_name].browse(int(raw)).exists()
        except (TypeError, ValueError):
            target = None
        if not target and isinstance(raw, str) and raw.strip():
            # The model gave a name, not an id ("Acme" instead of 7):
            # resolve by name. Zero or several hits is not an error,
            # it is a question for the user — never guess the vendor.
            target = _resolve_many2one_name(
                env, field.comodel_name, raw.strip())
        if not target:
            raise AccessError(env._(
                "No %(model)s with id %(raw)s.") % {
                    "model": field.comodel_name, "raw": raw})
        return target.id, target.display_name
    raise AccessError(env._("Field cannot be set by the agent."))


class AskUser(Exception):
    """Ambiguous arguments: the tool needs a choice, not a failure.

    Handlers raise this instead of AccessError when the request is
    valid but under-specified ("which Acme?"). The loop turns it into
    a direct question in the reply; nothing is queued, nothing runs.
    """

    def __init__(self, question, options=None):
        super().__init__(question)
        self.question = question
        # Same 6-option cap as tool_ask_user and the loop's ask
        # path: the drawer renders at most 6 buttons, so longer
        # lists would silently drop options between raise and render.
        self.options = (options or [])[:6]


def _resolve_many2one_name(env, model_name, name):
    """Name -> single record, or AskUser when 0/2+ match (never guess).
    Returns the record, or None only when nothing matches at all."""
    found = env[model_name].name_search(name, limit=4)
    if len(found) == 1:
        return env[model_name].browse(found[0][0]).exists()
    if len(found) > 1:
        raise AskUser(
            env._("Which %(model)s do you mean by %(name)r?") % {
                "model": env[model_name]._description or model_name,
                "name": name},
            [label for _id, label in found])
    return None


def _check_settable(env, model_name, field_name):
    """Returns (record-less) field or raises an actionable refusal."""
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    try:
        field = env[model_name]._fields[field_name]
    except KeyError:
        raise AccessError(env._(
            "No field %(field)s on %(model)s.") % {
                "field": field_name, "model": model_name})
    if field.groups:
        raise AccessError(env._("Field cannot be set by the agent."))
    # Pay and identity fields stay human-only even where no group
    # protects them: group rules are the gate, this is the fence.
    # The deployer's field_acl deny list fences reads and writes alike.
    lowered = (field_name or "").lower()
    if lowered in _SENSITIVE_FIELDS or field_name in _field_acl_denied(
            env, model_name) or any(
                token in lowered for token in ("salary", "wage", "ssn",
                                               "passport", "bank_account")):
        raise AccessError(env._("Field cannot be set by the agent."))
    if field.readonly or not field.store \
            or field.type not in _SETTABLE_TYPES:
        raise AccessError(env._(
            "Field %(field)s cannot be set by the agent.") % {
                "field": field_name})
    return field


def tool_set_field(env, args, ctx):
    """Queue writing one simple field. The card shows old -> new; the
    write happens only on Approve, with the user's own rights."""
    model_name = args.get("model", "")
    # Model gate before browse: unknown models refuse as AccessError,
    # never as a raw KeyError from the registry.
    if not _model_allowed(env, model_name):
        raise _denied(model_name, env)
    record = _chain_target(env, model_name, args.get("res_id"), args)
    field = _check_settable(env, model_name, args.get("field") or "")
    value, display = _coerce_field_value(
        env, field, args.get("value"), record)
    if record is None:
        card = _confirm_card(
            "set_field",
            env._("Set %(field)s on a new %(model)s") % {
                "field": field.string or args["field"],
                "model": model_name},
            "%s" % (display or "—"),
            {"model": model_name, "res_id": 0,
             "field": args["field"], "value": value},
        )
        card["preview"] = [
            "%s: %s" % (field.string or args["field"], display or "—")]
        return card
    try:
        old = record[args["field"]]
        if not old:
            old_display = ""
        elif field.type == "selection" and isinstance(old, str):
            # Selection keys are meaningless on a card ("consu"):
            # show the human label the approver actually judges.
            old_display = str(dict(
                _selection_options(field, record)).get(old, old))[:80]
        else:
            shown = getattr(old, "display_name", old)
            old_display = str(shown or "")[:80]
    except Exception:
        old_display = ""
    card = _confirm_card(
        "set_field",
        env._("Set %(field)s on %(record)s") % {
            "field": field.string or args["field"],
            "record": record.display_name},
        "%s -> %s" % (old_display or "—", display or "—"),
        {"model": model_name, "res_id": record.id,
         "field": args["field"], "value": value},
    )
    card["preview"] = [
        "%s: %s -> %s" % (field.string or args["field"],
                          old_display or "—", display or "—")]
    return card


def set_field_old_state(env, model_name, res_id, field_name):
    """Current raw value of a settable field, JSON-safe, or None.

    Captured before Approve runs so Undo restores exactly what the
    write changed — not what the card claimed. Relational and binary
    fields are out of scope: only the simple kinds undo."""
    if not _model_allowed(env, model_name):
        return None
    try:
        record = env[model_name].browse(int(res_id or 0)).exists()
    except (TypeError, ValueError):
        return None
    if not record:
        return None
    try:
        field = env[model_name]._fields[field_name]
    except KeyError:
        return None
    if field.type in ("one2many", "many2many", "binary", "reference"):
        return None
    try:
        raw = record[field_name]
    except Exception:
        return None
    if field.type == "many2one":
        value = raw.id if raw else False
        display = (raw.display_name or "")[:80] if raw else ""
    elif field.type == "selection" and isinstance(raw, str):
        value = raw
        display = str(dict(
            _selection_options(field, record)).get(raw, raw))[:80]
    else:
        import datetime as _dt
        if isinstance(raw, (_dt.date, _dt.datetime)):
            value = raw.isoformat()
        elif isinstance(raw, (str, int, float, bool)) or raw is False \
                or raw is None:
            value = raw
        else:
            value = str(raw)[:200]
        display = str(raw or "")[:80]
    return {"model": model_name, "res_id": record.id,
            "field": field_name, "value": value, "display": display}


def apply_set_field_inverse(env, state, agent=None):
    """Apply the stored undo state as a direct write (via run_confirm,
    with the same guards as Approve) and return its result. The
    caller swaps the state so Undo toggles back and forth."""
    if not state or not isinstance(state, dict):
        raise AccessError(env._("Nothing to undo."))
    card = tool_set_field(env, {
        "model": state.get("model"), "res_id": state.get("res_id"),
        "field": state.get("field"), "value": state.get("value")}, {})
    return run_confirm(env, card, agent)


# ---------------------------------------------------------------- fetch

def _stage(ctx, text):
    """One progress stage for the drawer's live line. Never raises:
    progress is UI sugar, never load-bearing."""
    try:
        emit = (ctx or {}).get("progress")
        if callable(emit):
            emit(text)
    except Exception:
        pass


def _is_public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, ValueError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local \
                or ip.is_reserved or ip.is_multicast \
                or ip.is_unspecified:
            return False
    return True


def tool_fetch_url(env, args, ctx):
    url = _normalize_fetch_url(
        env, (args.get("url") or "").strip())
    tool = (ctx or {}).get("tool")
    timeout = _FETCH_TIMEOUT
    try:
        configured = int(getattr(tool, "timeout_s", 0) or 0)
    except (TypeError, ValueError):
        configured = 0
    if configured > 0:
        # The admin's per-tool timeout, bounded: never below 5s
        # (refusals by slowness) and never above 120s (the worker
        # must stay responsive for everyone else).
        timeout = max(5, min(configured, 120))
    response = _fetch_public_url(env, url, ctx, timeout=timeout)
    # Streaming does not auto-decode gzip; without this the index fills
    # with compressed bytes whenever a server sends them.
    response.raw.decode_content = True
    body = b""
    for chunk in response.iter_content(8192):
        body += chunk
        if len(body) > _FETCH_MAX_BYTES:
            break
    if b"\x00" in body[:512]:
        # The header claimed text, the bytes say binary: refuse rather
        # than filling the turn with decoded junk.
        response.close()
        raise AccessError(env._(
            "That link is a binary file, not a readable page."))
    raw_text = body.decode("utf-8", "replace")
    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", raw_text, re.I | re.S)
    if match:
        # Title comes from the raw markup: _hr strips tags, so this
        # must run first.
        title = _hr(match.group(1), 120)
    text = _hr(raw_text, 8000)
    return {"url": url, "title": title, "text": text,
            "links": _extract_outlinks(raw_text, url)}


def _extract_outlinks(html, base_url, limit=20):
    """Absolute http(s) links on the page, in order, deduplicated.

    This is what turns fetch_url from a reader into research: one
    fetch hands the model the next pages to open (docs indexes,
    table-of-contents pages, "see also" sections). Listing a URL
    never fetches it — each follow-up goes through the same SSRF
    gate — so no validation is needed here beyond scheme.
    """
    from urllib.parse import urljoin as _join
    found = []
    try:
        for match in re.finditer(
                r'<a\s[^>]*href\s*=\s*["\']([^"\']+)["\']',
                html or "", re.IGNORECASE):
            href = (match.group(1) or "").strip()
            if not href or href.startswith(("#", "javascript:",
                                            "mailto:", "tel:")):
                continue
            absolute = _join(base_url or "", href)
            try:
                parsed = urlparse(absolute)
            except Exception:
                continue
            if parsed.scheme not in ("http", "https"):
                continue
            if not parsed.hostname:
                continue
            if absolute not in found:
                found.append(absolute)
            if len(found) >= limit:
                break
    except Exception:
        return []
    return found


# Ports the fetcher may use: anything else (SSH, databases, internal
# dashboards on odd ports) is refused before any packet is sent.
_FETCH_PORTS = (80, 443)

# Redirects are followed by hand (at most this many), because requests'
# built-in follower never re-checks the target: one 302 to
# 169.254.169.254 would turn the tool into an SSRF door.
_FETCH_MAX_REDIRECTS = 3

# Only textual bodies are worth indexing; binaries are refused with a
# plain note instead of filling the turn with decoded junk.
_FETCH_TEXT_TYPES = (
    "text/", "application/json", "application/xml",
    "application/javascript", "application/rss+xml",
    "application/atom+xml", "application/xhtml+xml",
)


def _normalize_fetch_url(env, url):
    """Clean a user-supplied URL or raise AccessError.

    Strips any userinfo (so embedded credentials never leave the box),
    keeps only http(s) on ports 80/443, and refuses non-public hosts.
    Pure string work plus DNS: no packet is sent to the target.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AccessError(env._("Only http(s) URLs can be fetched."))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in _FETCH_PORTS:
        raise AccessError(env._(
            "Only the normal web ports can be fetched."))
    if not _is_public_host(parsed.hostname):
        # Keep the tool from becoming an SSRF door into the LAN.
        raise AccessError(env._("That address is not publicly reachable."))
    netloc = parsed.hostname
    if parsed.port:
        netloc += ":%d" % parsed.port
    return parsed._replace(netloc=netloc).geturl()


def _fetch_public_url(env, url, ctx=None, timeout=_FETCH_TIMEOUT):
    """GET a normalized URL, following redirects only onto URLs that
    pass _normalize_fetch_url again. Raises AccessError on redirect
    abuse, non-text bodies, and request failures."""
    current = url
    for _ in range(_FETCH_MAX_REDIRECTS + 1):
        _stage(ctx, "connecting")
        # Re-resolve at request time, not just at normalize time: DNS
        # can change between the first check and this packet (rebinding),
        # so the last word belongs to a fresh lookup.
        try:
            current = _normalize_fetch_url(env, current)
        except AccessError:
            raise
        except Exception:
            raise AccessError(
                env._("That address is not publicly reachable.")) from None
        try:
            response = requests.get(
                current, timeout=timeout, stream=True,
                allow_redirects=False,
                headers={"User-Agent": "nova-ai/1.0"})
        except requests.RequestException:
            # Never surface raw transport text (paths, resolvers) to
            # the turn; the log keeps the detail.
            raise AccessError(
                env._("That page could not be reached.")) from None
        if response.status_code not in (301, 302, 303, 307, 308):
            break
        target = (response.headers.get("location") or "").strip()
        response.close()
        if not target:
            raise AccessError(env._("That link leads nowhere."))
        # Re-validate the redirect target from scratch: scheme, port,
        # credentials, and host publicity.
        current = _normalize_fetch_url(
            env, urljoin(current, target))
    else:
        raise AccessError(env._("Too many redirects."))
    if response.status_code >= 400:
        response.close()
        raise AccessError(
            env._("That page answered %d.") % response.status_code)
    _stage(ctx, "reading")
    kind = (response.headers.get("content-type") or "").split(";")[0]
    kind = kind.strip().lower()
    if kind and not kind.startswith(_FETCH_TEXT_TYPES):
        response.close()
        raise AccessError(
            env._("That link is a %s file, not a readable page.")
            % (kind or "binary"))
    return response


def _validate_args(env, code, schema, args):
    """Weak-model guard: check tool arguments against the tool's own
    schema BEFORE anything runs, so a hallucinated call fails with an
    actionable message instead of executing halfway.

    Lenient on purpose: int-like strings coerce (weak models quote
    numbers), missing optionals default, unknown extras pass through
    (handlers ignore what they do not need). Strict only on required
    presence and real type mismatches. Returns the coerced args."""
    if not isinstance(schema, dict):
        return args
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        return args
    args = dict(args or {})
    missing = [key for key in (schema.get("required") or [])
               if args.get(key) in (None, "")]
    if missing:
        raise AccessError(env._(
            "%(tool)s needs %(missing)s.") % {
                "tool": code, "missing": ", ".join(missing)})
    for key, spec in props.items():
        if key not in args or args[key] is None:
            continue
        want = (spec or {}).get("type") if isinstance(spec, dict) \
            else None
        value = args[key]
        if want == "integer" and isinstance(value, str):
            try:
                args[key] = int(value.strip())
                continue
            except (TypeError, ValueError):
                pass
        if want == "number" and isinstance(value, str):
            try:
                args[key] = float(value.strip())
                continue
            except (TypeError, ValueError):
                pass
        ok = ((want == "string" and isinstance(value, str))
              or (want == "integer" and isinstance(value, int)
                  and not isinstance(value, bool))
              or (want == "number" and isinstance(value, (int, float))
                  and not isinstance(value, bool))
              or (want == "boolean" and isinstance(value, bool))
              or (want == "array" and isinstance(value, (list, tuple)))
              or (want == "object" and isinstance(value, dict)))
        if want and not ok:
            raise AccessError(env._(
                "%(tool)s: %(key)s must be %(want)s.") % {
                    "tool": code, "key": key, "want": want})
    return args


def _resolve_date_word(value):
    """today/tomorrow/yesterday -> ISO date, else the value as-is."""
    try:
        delta = {"today": 0, "tomorrow": 1, "yesterday": -1}[
            str(value).strip().lower()]
    except (AttributeError, KeyError, TypeError, ValueError):
        return value
    from datetime import date, timedelta
    return (date.today() + timedelta(days=delta)).isoformat()


_LISTISH_KEYS = ("domain", "fields", "groupby", "values", "ids",
                 "res_ids", "args", "values_list")


def _coerce_tool_args(args):
    """Repair weak-model arguments before schema validation.

    Stringified lists/dicts ("[('state','=','draft')]") are parsed
    back into structures (JSON first, literal_eval for tuple-style
    domains); date words become ISO dates. Anything unparseable
    passes through untouched so validation still reports it.
    """
    import ast
    out = dict(args or {})
    for key, value in out.items():
        if key in _LISTISH_KEYS and isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            parsed = None
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                try:
                    parsed = ast.literal_eval(text)
                except (SyntaxError, ValueError, TypeError,
                        MemoryError):
                    parsed = None
            if isinstance(parsed, (list, tuple, dict)):
                out[key] = parsed
        if (key == "date" or key.endswith("_date")
                or key in ("deadline", "date_deadline")):
            out[key] = _resolve_date_word(value)
    return out


# Per-process rolling counters, keyed by (user, tool). The audit
# log is written by the controller path only, so a counter that reads
# it would stay at zero for direct callers (tests, flows, cron, the
# Studio itself) and never refuse. In-memory is exact on a single
# worker and approximate otherwise; the refusal names the window so a
# model can retry instead of guessing.
_RATE_USE = {}


def _rate_count(env, tool_code, user_id, window_s=3600):
    """Calls in the trailing window, however the tool was invoked."""
    try:
        import time as _time
        now = _time.time()
    except Exception:
        return 0
    try:
        key = (int(user_id or 0), str(tool_code or ""))
    except (TypeError, ValueError):
        return 0
    cutoff = now - window_s
    recent = [when for when in _RATE_USE.get(key, []) if when >= cutoff]
    _RATE_USE[key] = recent
    return len(recent)


def _rate_record(tool_code, user_id):
    """One call happened. Recorded when the call is allowed, so a
    refused call does not extend its own window."""
    try:
        import time as _time
        key = (int(user_id or 0), str(tool_code or ""))
        _RATE_USE.setdefault(key, []).append(_time.time())
    except Exception:
        pass


def _rate_check(env, code, ctx):
    """Refuse an over-limit tool before it runs. Unlimited (0) tools
    cost nothing to check: the search runs only when a limit is set.

    Returns "" when the call may proceed, else the refusal text.
    """
    tool_rec = (ctx or {}).get("tool")
    limit = 0
    try:
        limit = int(getattr(tool_rec, "rate_limit_per_hour", 0) or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return ""
    try:
        user = (ctx or {}).get("user") or env.user
        user_id = user.id if user else 0
    except Exception:
        return ""
    if not user_id:
        return ""
    used = _rate_count(env, code, user_id)
    if used < limit:
        _rate_record(code, user_id)
        return ""
    return ("That tool may run %d times an hour and you have used all "
            "%d of them - try again within the hour, or ask the user "
            "to wait." % (limit, used))


def execute_tool(env, code, args, ctx):
    """Run one tool. Returns (kind, payload, card) where kind is
    ``result`` | ``action`` | ``confirm`` | ``ask``."""
    handler = REGISTRY.get(code)
    if handler is None:
        return "result", {"note": "tool handled by the model itself"}, None
    args = _coerce_tool_args(args if isinstance(args, dict) else {})
    # Schema check first (the loop passes the tool record in ctx);
    # direct callers without one run unvalidated, as before.
    tool_rec = (ctx or {}).get("tool")
    schema = getattr(tool_rec, "schema", None)
    if isinstance(schema, dict) and schema:
        args = _validate_args(env, code, schema, args)
    # Rate limit before anything runs: an over-limit tool refuses with
    # the reset window named, not a silent failure downstream.
    refused = _rate_check(env, code, ctx)
    if refused:
        raise AccessError(refused)
    # Agent model subset: queue-time enforcement travels in context.
    env = _scoped_env(env, (ctx or {}).get("agent"))
    try:
        payload = handler(env, args, ctx)
    except AskUser as ask:
        return "ask", {"question": ask.question,
                       "options": ask.options}, None
    if isinstance(payload, tuple) and len(payload) == 3:
        # The handler chose its own envelope (e.g. digitize returns a
        # card or a follow-up question depending on what the scan gave).
        return payload
    if code in CONFIRM_KINDS:
        # Chain link (multi-step plans): the model queues a later step
        # with after={kind, model} + bind={field: "created"} and the
        # approval of the earlier card fills the ids in. Validated
        # here, once, for every tool — no per-tool or seed changes.
        after, bind = _chain_args(args)
        if after or bind:
            card = dict(payload or {})
            card["after"] = after
            card["bind"] = bind
            card["token"] = _card_token(
                card.get("kind") or code, card.get("payload") or {},
                after, bind)
            return "confirm", None, card
        return "confirm", None, payload
    if code == "open_view":
        return "action", payload.get("action"), None
    return "result", payload, None


def find_dependency(pending, card):
    """The first still-pending card this one waits for, or None.

    Matching is FIFO on (kind, model): parallel chains of the same
    shape bind in queue order, so two draft orders queued together
    feed two invoice cards in the same order.
    """
    after = (card or {}).get("after") or {}
    if not after:
        return None
    mine = (card or {}).get("token")
    for other in pending or []:
        if not isinstance(other, dict):
            continue
        if other.get("token") == mine:
            continue
        if other.get("kind") != after.get("kind"):
            continue
        payload = other.get("payload") or {}
        if (payload.get("model") or "") != (after.get("model") or ""):
            continue
        return other
    return None


def _apply_bind(payload, bind, res_id):
    """Fill a card payload's bound slots with an approved res_id.
    Only falsy slots fill (explicit ids always win); dotted paths
    like values.order_id reach into nested dicts without clobbering
    non-dicts. Returns the (possibly new) payload dict."""
    payload = dict(payload or {})
    for key, target in (bind or {"res_id": _BIND_TARGET}).items():
        if target != _BIND_TARGET:
            continue
        if "." in key:
            head, tail = key.split(".", 1)
            sub = payload.get(head)
            if not isinstance(sub, dict) or sub.get(tail):
                continue
            sub = dict(sub)
            sub[tail] = res_id
            payload[head] = sub
            continue
        if payload.get(key):
            continue
        payload[key] = res_id
    return payload


def bind_from_applied(card, applied_records):
    """Bind a newly queued card against already-applied results.

    Late joiners — queued after their dependency already applied
    this turn — bind immediately instead of waiting for an approval
    that will never come. Returns the (possibly new) card.
    """
    after = (card or {}).get("after")
    if not after:
        return card
    for done in applied_records or []:
        if (done.get("_kind") or "") != after.get("kind"):
            continue
        if (done.get("_model") or "") != after.get("model"):
            continue
        payload = _apply_bind(card.get("payload"), card.get("bind"),
                              done.get("res_id"))
        # Token stays put on purpose: the drawer holds this token,
        # and Approve must still find the card after binding.
        return dict(card, payload=payload, after=None,
                    bound_to="auto")
    return card


def _bind_dependents(pending, card, result):
    """Core of chain resolution over a plain card list: every unbound
    dependent whose (kind, model) matches the approved card gets its
    bound payload keys filled with the approved res_id (falsy slots
    only), oldest first, one approval feeding one dependent.
    Mutates the list in place; returns the list of unbound indices.
    """
    kind = (card or {}).get("kind")
    model_name = ((card or {}).get("payload") or {}).get("model")
    res_id = (result or {}).get("res_id") or 0
    if not kind or not res_id:
        return []
    token = (card or {}).get("token")
    changed = []
    for index, other in enumerate(list(pending or [])):
        if not isinstance(other, dict):
            continue
        # One approval feeds exactly one dependent (FIFO): a second
        # approval of the same shape feeds the next in line.
        if changed:
            break
        after = other.get("after") or {}
        if not after:
            continue
        if other.get("token") == token:
            continue
        if after.get("kind") != kind:
            continue
        payload = dict(other.get("payload") or {})
        if (payload.get("model") or "") != (model_name or ""):
            continue
        bind = other.get("bind") or {"res_id": _BIND_TARGET}
        payload = _apply_bind(payload, bind, res_id)
        # Token stays put on purpose: the drawer holds this token,
        # and Approve must still find the card after binding.
        pending[index] = dict(other, payload=payload, after=None,
                              bound_to=token)
        changed.append(index)
    return changed


def _enrich_timesheet(env, payload):
    """Fill a chained timesheet's project/account/date from its task.

    The queue-time card cannot know them (the task does not exist
    yet); at resolve time the created task id is in, so derive the
    rest instead of failing the approve on missing fields.
    """
    try:
        values = payload.get("values") or {}
        if (payload.get("model") != "account.analytic.line"
                or not values.get("task_id")
                or values.get("account_id")):
            return payload
        task = env["project.task"].browse(
            values["task_id"]).exists()
        if not task:
            return payload
        project = task.project_id
        account_id = project.account_id.id if project and getattr(
            project, "account_id", None) else False
        if not account_id:
            return payload
        values = dict(values,
                      project_id=project.id if project else False,
                      account_id=account_id,
                      date=values.get("date") or fields.Date.today())
        return dict(payload, values=values)
    except Exception:
        return payload


def resolve_dependents(env, session, card, result):
    """Fill bound ids on pending cards waiting for this approval.

    Runs after a card is approved: every unbound dependent whose
    (kind, model) matches gets the approved res_id written into its
    bound payload keys (falsy slots only — explicit ids always win),
    oldest first, one approval feeding one dependent. Returns how
    many cards were unblocked.
    """
    del env
    pending = list((session and session.pending) or [])
    if not pending:
        return 0
    changed = _bind_dependents(pending, card, result)
    if changed:
        for index in changed:
            try:
                pending[index] = dict(
                    pending[index],
                    payload=_enrich_timesheet(
                        env, pending[index].get("payload") or {}))
            except Exception:
                pass
        try:
            session.write({"pending": pending})
        except Exception:
            return 0
    return len(changed)


def _default_state_value(env, model_name):
    """The state to assume for a new draft, if any. Only selection
    states that actually accept "draft" qualify — blindly injecting
    it (e.g. on project.task, whose states start at
    01_in_progress) turns an approvable card into a guaranteed
    Approve-time failure."""
    try:
        field = env[model_name]._fields.get("state")
    except Exception:
        return None
    if field is None or getattr(field, "type", "") != "selection":
        return None
    try:
        keys = [key for key, _label in _selection_options(field, None)]
    except Exception:
        return None
    return "draft" if "draft" in keys else None


def run_confirm(env, card, agent=None):
    """Execute an approved confirm card with the user's own rights.
    The agent's model subset is re-checked here (approve time), so a
    card queued yesterday cannot escape today's scope."""
    env = _scoped_env(env, agent)
    kind = card.get("kind")
    tool = env["nova.ai.tool"].search([("code", "=", kind)], limit=1)
    if tool and tool.group_ids and not (
            tool.group_ids & env.user.all_group_ids):
        raise AccessError(env._(
            "You may not approve %(tool)s.") % {
                "tool": tool.name or kind})
    payload = card.get("payload") or {}
    model_name = payload.get("model", "")
    # Business action tools: one generic approver for all 114 specs.
    if (payload.get("biz") or "") in _biz.specs():
        from odoo.addons.nova_ai.models.nova_ai_business_tools import (
            run_confirm_biz)
        return run_confirm_biz(env, card, agent)
    if kind == "update_flow":
        # A chat-drafted revision: apply the validated graph onto the
        # target flow instead of creating a twin.
        target_id = payload.get("update_flow_id") or 0
        target = env["nova.ai.flow"].browse(int(target_id or 0)).exists()
        if not target:
            raise AccessError(env._("That flow no longer exists."))
        values = dict(payload.get("update_values") or {})
        values.pop("name", None)
        if values:
            target.write(values)
        return {"model": "nova.ai.flow", "res_id": target.id,
                "display_name": target.display_name}
    if kind == "flow_email":
        # A flow-queued email: approved here, sent here, logged here.
        to = (payload.get("to") or "").strip()
        subject = (payload.get("subject") or "").strip()
        body = payload.get("body") or ""
        if not to or not subject:
            raise AccessError(env._("That email is missing its "
                                    "recipient or subject."))
        mail = env["mail.mail"].create({
            "subject": subject,
            "body_html": body,
            "email_to": to,
        })
        try:
            mail.send()
        except Exception as err:
            raise AccessError(env._("Email queued but not sent: %s")
                              % str(err)[:200])
        return {"model": "mail.mail", "res_id": mail.id,
                "display_name": subject}
    if kind in ("create_draft", "draft_flow"):
        if not _model_allowed(env, model_name):
            raise AccessError(env._("Model not allowed."))
        values = dict(payload.get("values") or {})
        default_state = _default_state_value(env, model_name)
        if default_state is not None:
            values.setdefault("state", default_state)
        missing = _missing_required(env, model_name, values)
        if missing:
            raise AccessError(env._(
                "Cannot create %(model)s: still needs %(fields)s. "
                "Ask for the missing details instead of approving.") % {
                    "model": model_name, "fields": ", ".join(missing)})
        # Some rules live outside field metadata (Odoo 20 partner
        # names are a DB CHECK, not required=True): run the create in
        # a savepoint so any refusal rolls back here and returns as
        # an actionable message instead of a 500.
        try:
            with env.cr.savepoint():
                record = env[model_name].create(values)
        except AccessError:
            raise
        except Exception as err:
            raise AccessError(env._(
                "Cannot create %(model)s: %(err)s") % {
                    "model": model_name,
                    "err": str(err).split("\n")[0][:200]})
        return {"model": model_name, "res_id": record.id,
                "display_name": record.display_name}
    if kind == "log_note":
        if not _model_allowed(env, model_name):
            raise AccessError(env._("Model not allowed."))
        record = env[model_name].browse(payload.get("res_id")).exists()
        if not record:
            raise AccessError(_gone_or_denied(
                env, model_name, payload.get("res_id")))
        record.message_post(body=payload.get("body") or "",
                            message_type="comment")
        return {"model": model_name, "res_id": record.id,
                "display_name": record.display_name}
    if kind == "schedule_activity":
        if not _model_allowed(env, model_name):
            raise AccessError(env._("Model not allowed."))
        record = env[model_name].browse(payload.get("res_id")).exists()
        if not record:
            raise AccessError(_gone_or_denied(
                env, model_name, payload.get("res_id")))
        activity_type = env["mail.activity.type"].search([], limit=1)
        if not activity_type:
            raise AccessError(env._("No activity type configured."))
        # Odoo 20 takes the type by xmlid-or-False plus act_values:
        # the record id goes in activity_type_id, never positionally.
        record.activity_schedule(
            False,
            summary=payload.get("summary") or "Follow up",
            date_deadline=payload.get("date"),
            activity_type_id=activity_type.id)
        return {"model": model_name, "res_id": record.id,
                "display_name": record.display_name}
    if kind == "confirm_sale_order":
        if not _model_allowed(env, "sale.order"):
            raise AccessError(env._("Model not allowed."))
        order = env["sale.order"].browse(
            payload.get("res_id")).exists()
        if not order:
            raise AccessError(_gone_or_denied(
                env, "sale.order", payload.get("res_id")))
        # Re-check: the quotation may have moved since the card queued.
        if order.state not in _APPROVABLE_ORDERS:
            raise AccessError(env._(
                "That quotation is already %(state)s.") % {
                    "state": order.state})
        order.action_confirm()
        return {"model": "sale.order", "res_id": order.id,
                "display_name": order.display_name}
    if kind == "validate_invoice":
        if not _model_allowed(env, "account.move"):
            raise AccessError(env._("Model not allowed."))
        move = env["account.move"].browse(
            payload.get("res_id")).exists()
        if not move:
            raise AccessError(_gone_or_denied(
                env, "account.move", payload.get("res_id")))
        if move.state != "draft":
            raise AccessError(env._(
                "That invoice is already %(state)s.") % {
                    "state": move.state})
        move.action_post()
        return {"model": "account.move", "res_id": move.id,
                "display_name": move.display_name}
    if kind == "set_field":
        field = _check_settable(env, model_name, payload.get("field"))
        record = env[model_name].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(_gone_or_denied(env, model_name, payload.get("res_id")))
        frozen = _move_frozen_refusal(record, "edit")
        if frozen:
            raise AccessError(env._(
                "Cannot edit %(name)s: %(reason)s.") % {
                    "name": record.display_name, "reason": frozen})
        value, _display = _coerce_field_value(
            env, field, payload.get("value"), record)
        record.write({payload["field"]: value})
        return {"model": model_name, "res_id": record.id,
                "display_name": record.display_name}
    if kind == "set_fields":
        # One card, several fields: every field is checked, coerced and
        # written in one go, so the record is never half-updated.
        values = payload.get("values") or {}
        if not isinstance(values, dict) or not values:
            raise AccessError(env._("The card carries no values."))
        record = env[model_name].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(_gone_or_denied(env, model_name, payload.get("res_id")))
        frozen = _move_frozen_refusal(record, "edit")
        if frozen:
            raise AccessError(env._(
                "Cannot edit %(name)s: %(reason)s.") % {
                    "name": record.display_name, "reason": frozen})
        clean = {}
        for name, value in values.items():
            field = _check_settable(env, model_name, name)
            coerced, _display = _coerce_field_value(
                env, field, value, record)
            clean[name] = coerced
        record.write(clean)
        return {"model": model_name, "res_id": record.id,
                "display_name": record.display_name}
    if kind == "copy_record":
        if not _model_allowed(env, model_name):
            raise AccessError(env._("Model not allowed."))
        record = env[model_name].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(_gone_or_denied(env, model_name, payload.get("res_id")))
        # Copies restart life as drafts; posting data, sequences and
        # references never carry over to the twin. The draft state is
        # only injected when the model accepts it (see create_draft),
        # so copies of tasks and pickings do not die at Approve time.
        default_state = _default_state_value(env, model_name)
        defaults = {"state": default_state} if default_state else None
        twin = record.copy(defaults)
        return {"model": model_name, "res_id": twin.id,
                "display_name": twin.display_name}
    if kind == "unlink_record":
        if not _model_allowed(env, model_name):
            raise AccessError(env._("Model not allowed."))
        record = env[model_name].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(_gone_or_denied(
                env, model_name, payload.get("res_id")))
        frozen = _move_frozen_refusal(record, "delete")
        if frozen:
            raise AccessError(env._(
                "Cannot delete %(name)s: %(reason)s.") % {
                    "name": record.display_name, "reason": frozen})
        name = record.display_name
        record.unlink()
        return {"model": model_name, "res_id": 0,
                "display_name": name}
    if kind == "invoice_order":
        if not _model_allowed(env, "sale.order"):
            raise AccessError(env._("Model not allowed."))
        order = env["sale.order"].browse(
            payload.get("res_id")).exists()
        if not order:
            raise AccessError(_gone_or_denied(
                env, "sale.order", payload.get("res_id")))
        if order.state not in ("sale", "done"):
            raise AccessError(env._(
                "That order is %(state)s: confirm it before "
                "invoicing.") % {"state": order.state})
        if order.invoice_status not in ("to invoice", "upselling"):
            raise AccessError(env._(
                "Nothing to invoice on that order "
                "(%(status)s).") % {"status": order.invoice_status})
        moves = order._create_invoices()
        move = moves[:1] if moves else order.invoice_ids[:1]
        if not move:
            raise AccessError(env._("No invoice was created."))
        return {"model": "account.move", "res_id": move.id,
                "display_name": move.display_name}
    if kind == "register_payment":
        if not _model_allowed(env, "account.move"):
            raise AccessError(env._("Model not allowed."))
        move = env["account.move"].browse(
            payload.get("res_id")).exists()
        if not move:
            raise AccessError(_gone_or_denied(
                env, "account.move", payload.get("res_id")))
        if move.state != "posted":
            raise AccessError(env._(
                "That invoice is %(state)s: only posted invoices "
                "take payments.") % {"state": move.state})
        residual = float(getattr(move, "amount_residual", 0) or 0)
        try:
            amount = float(payload.get("amount") or residual)
        except (TypeError, ValueError):
            amount = residual
        if amount <= 0 or amount - residual > 0.005:
            raise AccessError(env._(
                "Amount must be between 0 and %(residual).2f.") % {
                    "residual": residual})
        journal = env["account.journal"].browse(
            payload.get("journal_id")).exists()
        if not journal:
            raise AccessError(env._("Journal not found."))
        wizard = env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=[move.id]).create({
                "amount": amount, "journal_id": journal.id,
                "payment_date": fields.Date.today(),
            })
        wizard.action_create_payments()
        move.invalidate_recordset()
        return {"model": "account.move", "res_id": move.id,
                "display_name": move.display_name}
    if kind == "reconcile_moves":
        if not _model_allowed(env, "account.move"):
            raise AccessError(env._("Model not allowed."))
        move = env["account.move"].browse(
            payload.get("res_id")).exists()
        if not move:
            raise AccessError(_gone_or_denied(
                env, "account.move", payload.get("res_id")))
        payments = env["account.move"].browse(
            payload.get("payment_ids") or []).exists()
        if not payments:
            raise AccessError(env._("Payment move(s) not found."))
        lines = (move.line_ids + payments.mapped("line_ids")).filtered(
            lambda line: line.account_id.reconcile
            and not line.reconciled)
        if not lines:
            raise AccessError(env._(
                "Nothing reconcilable on those moves."))
        lines.reconcile()
        return {"model": "account.move", "res_id": move.id,
                "display_name": move.display_name}
    if kind == "reverse_move":
        if not _model_allowed(env, "account.move"):
            raise AccessError(env._("Model not allowed."))
        move = env["account.move"].browse(
            payload.get("res_id")).exists()
        if not move:
            raise AccessError(_gone_or_denied(
                env, "account.move", payload.get("res_id")))
        if move.state != "posted":
            raise AccessError(env._(
                "Only posted invoices can be reversed."))
        reversal = env["account.move.reversal"].with_context(
            active_model="account.move",
            active_ids=[move.id]).create({
                # No refund_method on Odoo 20 (nor 18/19): the wizard
                # only knows reason/journal. Extra keys crash create.
                "reason": payload.get("reason") or "Reversal",
            })
        reversal.reverse_moves()
        credit = reversal.new_move_ids[:1]
        if not credit:
            raise AccessError(env._("No credit note was created."))
        return {"model": "account.move", "res_id": credit.id,
                "display_name": credit.display_name}
    if kind == "resolve_ticket":
        if not _model_allowed(env, "helpdesk.ticket"):
            raise AccessError(env._("Model not allowed."))
        ticket = env["helpdesk.ticket"].browse(
            payload.get("res_id")).exists()
        if not ticket:
            raise AccessError(_gone_or_denied(
                env, "helpdesk.ticket", payload.get("res_id")))
        stage = env["helpdesk.stage"].browse(
            payload.get("stage_id")).exists()
        if not stage:
            raise AccessError(env._("Closing stage not found."))
        ticket.write({"stage_id": stage.id})
        if payload.get("note"):
            ticket.message_post(body=payload["note"],
                                message_type="comment")
        return {"model": "helpdesk.ticket", "res_id": ticket.id,
                "display_name": ticket.display_name}
    if kind == "execute_method":
        if not _model_allowed(env, model_name):
            raise AccessError(env._("Model not allowed."))
        record = env[model_name].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(_gone_or_denied(env, model_name, payload.get("res_id")))
        name = _check_method(env, model_name, payload.get("method"))
        if not _side_effect_allowed(env, model_name, name):
            raise AccessError(env._(
                "Method %(method)s is not on this deployment's "
                "approved list.") % {
                    "method": "%s.%s" % (model_name, name)})
        target = getattr(record, name, None)
        if not callable(target):
            raise AccessError(env._("Method no longer available."))
        if model_name == "account.move" and name in ("button_draft",
                                                        "button_cancel"):
            refusal = _move_draft_refusal(record, name)
            if refusal:
                raise AccessError(env._(
                    "Cannot run %(method)s on %(name)s: %(reason)s. "
                    "Cancel it or duplicate it into a fresh draft "
                    "instead.") % {
                        "method": name, "name": record.display_name,
                        "reason": refusal})
        method_args = payload.get("args") or []
        if not isinstance(method_args, (list, tuple)):
            raise AccessError(env._(
                "The queued arguments are not a list."))
        method_kwargs = payload.get("kwargs") or {}
        if not isinstance(method_kwargs, dict):
            raise AccessError(env._(
                "The queued keyword arguments are not an object."))
        # No truncation: the user approved exactly these arguments,
        # so running fewer would execute a different intent.
        outcome = target(*list(method_args), **dict(method_kwargs))
        result = {"model": model_name, "res_id": record.id,
                  "display_name": record.display_name}
        if isinstance(outcome, dict) and outcome.get("type"):
            # The method answered with a follow-up action (backorder
            # wizard, autoprint...) instead of finishing: dropping it
            # would report success while work waits. Carry it so the
            # drawer says what still needs doing.
            result["action"] = outcome
        return result
    raise AccessError(env._("Unknown confirmation."))


# ------------------------------------------------- digitize (OCR -> draft)

_OCR_MIN_CHARS = 40
_OCR_LOW_CONF = 0.55
_DIGITIZE_MIMES = ("image/png", "image/jpeg", "image/jpg", "image/webp",
                   "application/pdf")
_DIGITIZE_MAX_MB = 10
_DIGITIZE_MAX_BYTES = _DIGITIZE_MAX_MB * 1024 * 1024
_DIGITIZE_MAX_PAGES = 10


def _ocr_attachment_text_layer(env, attachment, raw):
    """Embedded PDF text, or None. Pure-Python (pypdf), no ML, no key."""
    del env
    is_pdf = (attachment.mimetype == "application/pdf"
              or bytes(raw)[:4] == b"%PDF")
    if not is_pdf:
        return None
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(bytes(raw)))
        pages = [(page.extract_text() or "").strip()
                 for page in reader.pages]
        pages = [text for text in pages if text]
    except Exception:
        return None
    if sum(len(text) for text in pages) < _OCR_MIN_CHARS:
        return None
    if len(pages) > 1:
        text = "".join(
            "\n\n--- page %d ---\n%s" % (n + 1, page)
            for n, page in enumerate(pages)).lstrip()
    else:
        text = pages[0]
    return {"text": text, "pages": len(pages)}


def _ocr_attachment(env, attachment):
    """ir.attachment -> {text, pages, mean_confidence, engine}.

    Only the embedded PDF text layer is read locally (pure Python, no
    ML, no key). Anything scanned goes to the model's own eyes
    (vision); without a vision-capable model the tool refuses
    honestly instead of guessing. Never invents text.
    """
    # Odoo 20 stores file bytes on attachment.raw (datas is gone).
    # Filestore-backed attachments come back as a lazy file wrapper.
    raw = attachment.raw or b""
    if not isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.content or b""
        except Exception:
            try:
                raw = bytes(raw)
            except Exception:
                raw = b""
    if not raw:
        raise AccessError(_("Attachment not found."))
    layered = _ocr_attachment_text_layer(env, attachment, raw)
    if layered:
        return {"text": layered["text"],
                "pages": layered["pages"], "mean_confidence": 1.0,
                "engine": "pdf-text"}
    return {"text": "", "pages": 0, "mean_confidence": 0.0,
            "engine": "none"}

_EXTRACT_SYSTEM = (
    "You extract vendor bill fields from OCR text. Reply with ONLY a "
    "JSON object, no prose, no fences: "
    '{"partner_name": str, "vat": str|null, "date": "YYYY-MM-DD"|null, '
    '"number": str|null, "currency": "XXX"|null, "lines": '
    '[{"description": str, "qty": number, "price": number}], '
    '"total": number|null, "low_confidence": [field names]}. '
    "Use null when a field is absent. Copy digits exactly; never invent."
)


def _parse_extraction(raw):
    """Strict bill-fields JSON or an honest AccessError. Shared by the
    OCR-text path and the vision path so both promise the same shape."""
    cleaned = re.sub(r"```(?:json)?|```", "", raw or "").strip()
    try:
        found = json.loads(cleaned)
    except (TypeError, ValueError):
        raise AccessError(_(
            "The scan was read but its fields came back unusable. "
            "Upload a clearer scan or type the bill manually."))
    if not isinstance(found, dict):
        raise AccessError(_(
            "The scan was read but its fields came back unusable. "
            "Upload a clearer scan or type the bill manually."))
    return found


def _extract_bill_fields(model, text):
    """Model proposes bill fields as JSON. Raises AccessError (honest,
    retryable message) when the output is unusable."""
    try:
        data = model._chat([
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": text[:6000]},
        ])
        raw = model._content_of(data) or ""
    except Exception as err:
        raise AccessError(_("Bill fields could not be extracted: %s")
                          % str(err)[:160])
    return _parse_extraction(raw)


_VISION_SYSTEM = (
    "You read vendor bill scans. Reply with ONLY a JSON object, no "
    "prose, no fences: "
    '{"partner_name": str, "vat": str|null, "date": "YYYY-MM-DD"|null, '
    '"number": str|null, "currency": "XXX"|null, "lines": '
    '[{"description": str, "qty": number, "price": number}], '
    '"total": number|null, "low_confidence": [field names]}. '
    "Use null when a field is absent. Copy digits exactly; never invent."
)

# Model ids that refused vision this worker lifetime: skip without
# another doomed call. Learned flags persist in supports_vision.
_NO_VISION = set()


class NovaAiVisionUnsupported(Exception):
    """This model cannot see images. Not an error: try the next
    candidate, then the OCR-text path."""


def _vision_chat(env, model, pngs):
    """One vision call. Returns the extraction dict.

    Raises NovaAiVisionUnsupported when the model cannot see (caller
    tries the next candidate); other failures propagate as plain
    errors so the caller can fall back to OCR text.
    """
    import base64
    provider = model.provider_id
    images = [base64.b64encode(png).decode("ascii") for png in pngs]
    if provider.kind == "ollama":
        messages = [{"role": "user", "content": _VISION_SYSTEM,
                     "images": images}]
    else:
        content = [{"type": "text", "text": _VISION_SYSTEM}]
        for image in images:
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + image}})
        messages = [{"role": "user", "content": content}]
    try:
        data = model._chat(messages)
    except requests.HTTPError as err:
        status = getattr(getattr(err, "response", None),
                         "status_code", 0)
        if status in (400, 404, 422):
            raise NovaAiVisionUnsupported(model.name)
        raise
    raw = model._content_of(data) or ""
    if not raw.strip():
        raise NovaAiVisionUnsupported(model.name + " (empty)")
    return _parse_extraction(raw)


def _vision_extract_fields(env, agent, pngs):
    """Bill fields straight from scan images. Tries models flagged
    supports_vision first, then the agent's own model; learns the flag
    on success. Returns the dict, or None when no candidate can see
    (caller falls back to OCR text). Never raises for vision issues.
    """
    if agent is not None:
        # The agent record may come from another environment (a queued
        # job, a test, a sudo caller): re-home it to the invoking env
        # FIRST, or model_id keeps the old env and every check and
        # write below runs with the wrong user's rights.
        try:
            agent = agent.with_env(env)
        except Exception:
            pass
    Model = env["nova.ai.model"]
    candidates = list(Model.search(
        [("supports_vision", "=", True),
         ("active", "=", True)])[:3])
    agent_model = agent.model_id if agent else None
    if agent_model and agent_model not in candidates:
        candidates.append(agent_model)
    for model in candidates:
        if not model.provider_id or model.id in _NO_VISION:
            continue
        try:
            found = _vision_chat(env, model, pngs[:3])
        except NovaAiVisionUnsupported as err:
            # Refusals blacklist; empty replies do not (transient or
            # terse model, retryable next turn).
            if "empty" not in str(err).lower():
                _NO_VISION.add(model.id)
            continue
        except Exception:
            # Transient (429/5xx/timeout): fall through to the OCR-text
            # tail below rather than failing the turn. The OCR result
            # (or its honest error) is what the user sees.
            continue
        if not model.supports_vision:
            try:
                # Only persist the capability when the caller may
                # actually change model config; a turn never elevates
                # a low-privilege user to reconfigure a shared model.
                if model.has_access("write"):
                    model.write({"supports_vision": True})
            except Exception:
                pass
        return found
    return None


def _scan_images(attachment, raw):
    """Scan -> PNG bytes (image as-is; PDF rasterized, first 3 pages).

    PyMuPDF only renders pixels here — no ML, no weight, works on any
    CPU. Raises AccessError when rasterizing is impossible.
    """
    is_pdf = (attachment.mimetype == "application/pdf"
              or raw[:4] == b"%PDF")
    if not is_pdf:
        return [bytes(raw)]
    try:
        import fitz
    except ImportError:
        raise AccessError(_(
            "The PDF engine is not installed on the server "
            "(pymupdf)."))
    try:
        doc = fitz.open(stream=bytes(raw), filetype="pdf")
    except Exception:
        raise AccessError(_("That PDF could not be read."))
    if len(doc) > _DIGITIZE_MAX_PAGES:
        raise AccessError(_(
            "That PDF has %(pages)d pages: split it under %(cap)d "
            "and retry.") % {"pages": len(doc),
                             "cap": _DIGITIZE_MAX_PAGES})
    out = []
    for page in doc:
        if len(out) >= 3:
            break
        try:
            out.append(page.get_pixmap(dpi=300).tobytes("png"))
        except Exception:
            continue
    if not out:
        raise AccessError(_("That PDF could not be read."))
    return out


def _resolve_bill_partner(env, extracted):
    """Exactly-one partner match wins; otherwise candidates for the user
    to pick. Never guesses between lookalikes."""
    Partner = env["res.partner"]
    vat = ((extracted.get("vat") or "") + "").strip().replace(" ", "")
    if vat:
        hit = Partner.search([("vat", "ilike", vat)], limit=2)
        if len(hit) == 1:
            return hit, []
        if len(hit) > 1:
            return None, [{"id": p.id, "name": p.display_name}
                          for p in hit]
    name = ((extracted.get("partner_name") or "") + "").strip()
    if name:
        hit = Partner.search([("name", "ilike", name)], limit=3)
        if len(hit) == 1:
            return hit, []
        if hit:
            return None, [{"id": p.id, "name": p.display_name}
                          for p in hit]
    return None, []


def _bill_values(env, extracted, partner):
    """Extraction JSON -> account.move draft values. Raises AccessError
    when something essential is missing (never half-builds)."""
    lines = extracted.get("lines") or []
    if not isinstance(lines, list) or not lines:
        raise AccessError(_(
            "No line items were found on the scan. Upload a clearer "
            "scan or create the bill manually."))
    account = env["account.account"].search(
        [("account_type", "=", "expense")], limit=1)
    if not account:
        raise AccessError(_(
            "No expense account exists to book the lines on."))
    move_lines = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        try:
            qty = float(line.get("qty") or 1)
            price = float(line.get("price") or 0)
        except (TypeError, ValueError):
            continue
        move_lines.append((0, 0, {
            "name": str(line.get("description") or "Item")[:200],
            "quantity": qty,
            "price_unit": price,
            "account_id": account.id,
        }))
    if not move_lines:
        raise AccessError(_(
            "The line amounts were unreadable. Upload a clearer scan "
            "or create the bill manually."))
    try:
        total = float(extracted.get("total")) \
            if extracted.get("total") is not None else None
    except (TypeError, ValueError):
        total = None
    if total is not None:
        line_sum = sum(q * p for (_, _, q, p) in
                       [(0, 0, l[2]["quantity"], l[2]["price_unit"])
                        for l in move_lines])
        if abs(line_sum - total) > 0.05:
            return None, {
                "line_sum": round(line_sum, 2),
                "ocr_total": round(total, 2),
            }
    date = (extracted.get("date") or "") or ""
    values = {"move_type": "in_invoice", "partner_id": partner.id}
    try:
        if date:
            fields.Date.from_string(date)
            values["invoice_date"] = date
    except ValueError:
        pass
    number = (extracted.get("number") or "") or ""
    if number:
        values["ref"] = number[:60]
    currency = ((extracted.get("currency") or "") + "").strip().upper()
    if re.fullmatch(r"[A-Z]{3}", currency or ""):
        journal = env["account.journal"].search(
            [("type", "=", "purchase"),
             ("currency_id.name", "=", currency)], limit=1)
        if journal:
            values["journal_id"] = journal.id
    values["invoice_line_ids"] = move_lines
    return values, None


def _expense_account_name(env, values):
    """Display name for the booked-expense flag. Never breaks the card:
    a restricted reader still gets an approvable summary."""
    try:
        lines = values.get("invoice_line_ids") or []
        account_id = (lines[0][2] or {}).get("account_id") if lines \
            else None
        account = env["account.account"].browse(account_id).exists()
        if account:
            return account.display_name
    except Exception:
        pass
    return env._("the expense account")


def _digitize_card(env, extracted, engine, pages, attachment_name,
                    ocr_text="", mean_confidence=1.0):
    """Extraction JSON -> confirm card or follow-up question.

    Shared tail for the vision path and the OCR-text path: partner
    resolution, totals reconciliation, then a DRAFT card. Ambiguity is
    returned for the user, never guessed.
    """
    partner, candidates = _resolve_bill_partner(env, extracted)
    if not partner:
        return {"engine": engine,
                "extracted": {k: extracted.get(k) for k in (
                    "partner_name", "vat", "date", "number",
                    "currency", "total")},
                "needs_partner": True,
                "candidates": candidates,
                "guidance": "Ask the user which vendor this bill is "
                            "from (name them if several match), then "
                            "call create_draft for an account.move "
                            "in_invoice with partner_id, ref, "
                            "invoice_date and invoice_line_ids.",
                "ocr_text": (ocr_text or "")[:2000]}
    values, mismatch = _bill_values(env, extracted, partner)
    if mismatch:
        return {"engine": engine,
                "partner": partner.display_name,
                "needs_total": True,
                "mismatch": mismatch,
                "guidance": "Ask the user for the correct bill total "
                            "(OCR total vs line sum differ), then call "
                            "create_draft with the confirmed figures.",
                "ocr_text": (ocr_text or "")[:2000]}
    low = extracted.get("low_confidence") or []
    flags = []
    if mean_confidence < _OCR_LOW_CONF:
        flags.append(env._("low scan confidence (%s)") % mean_confidence)
    if low:
        flags.append(env._("uncertain fields: %s") % ", ".join(
            str(f) for f in low[:5]))
    flags.append(env._("lines booked to %s — change before approving")
                 % _expense_account_name(env, values))
    flags.append(env._("taxes not extracted — verify on the draft"))
    summary = env._(
        "%(partner)s — total %(total)s | %(engine)s, %(pages)d page(s)"
        "%(flags)s") % {
            "partner": partner.display_name,
            "total": "%s %s" % (
                extracted.get("total"),
                (extracted.get("currency") or "")),
            "engine": engine,
            "pages": pages,
            "flags": (" | " + " | ".join(flags)) if flags else "",
        }
    return "confirm", None, _confirm_card(
        "create_draft",
        env._("Digitize bill %s") % (
            extracted.get("number") or attachment_name),
        summary,
        {"model": "account.move", "values": values},
        origin="digitize_bill",
    )


def tool_digitize_bill(env, args, ctx):
    """Scan/PDF of a vendor bill -> draft bill confirm card.

    Order of attempts, cheapest and most accurate first:
    1. embedded PDF text (perfect when present),
    2. the model's own eyes (vision-capable endpoint, learned flag).
    No local OCR engines exist by design: without selectable text
    and without vision, the tool refuses honestly.
    Never posts, never guesses the partner or the total: ambiguity is
    returned for the user to resolve, and the card creates a DRAFT the
    user approves and can still edit.
    """
    attachment = env["ir.attachment"].browse(
        int(args.get("attachment_id") or 0)).exists()
    if not attachment or not attachment.raw:
        raise AccessError(env._("Attachment not found."))
    mimetype = attachment.mimetype or ""
    if mimetype not in _DIGITIZE_MIMES and not mimetype.startswith(
            "image/"):
        raise AccessError(env._(
            "Only PDF or image scans can be digitized."))
    agent = (ctx or {}).get("agent")
    # The router decides who reads the scan: the agent's own model when
    # it can see, otherwise the cheapest vision model that can. A
    # capability the step needs is never traded away.
    model = None
    if agent:
        try:
            model = env["nova.ai.router"].route_for_tool(
                env, agent, "digitize_bill")
        except Exception:
            model = agent.model_id
    _stage(ctx, "reading scan")
    raw = attachment.raw
    if not isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.content or b""
        except Exception:
            try:
                raw = bytes(raw)
            except Exception:
                raw = b""
    if len(raw) > _DIGITIZE_MAX_BYTES:
        raise AccessError(env._(
            "That scan is too large (%d MB): split it under %d MB "
            "and retry.") % (len(raw) // 1048576, _DIGITIZE_MAX_MB))
    # 1. Embedded text layer: exact, instant, no model needed.
    _stage(ctx, "reading text layer")
    ocr = _ocr_attachment_text_layer(env, attachment, raw)
    if ocr:
        if not model:
            return {"engine": "pdf-text",
                    "text": ocr["text"][:4000], "needs_model": True}
        _stage(ctx, "extracting fields")
        extracted = _extract_bill_fields(model, ocr["text"])
        _stage(ctx, "checking vendor and totals")
        return _digitize_card(env, extracted, "pdf-text",
                              ocr["pages"], attachment.name,
                              ocr["text"])
    # 2 + 3 need the page images (image as-is, PDF rasterized). A
    # raster failure raises honestly here: the OCR tail would fail the
    # same way a moment later, only with a vaguer message.
    images = _scan_images(attachment, raw)
    if agent and images:
        _stage(ctx, "reading scan with vision")
        found = _vision_extract_fields(env, agent, images)
        if found:
            _stage(ctx, "checking vendor and totals")
            return _digitize_card(env, found, "vision",
                                  len(images), attachment.name)
    # 3. Only embedded text is readable locally (pure Python). A scan
    # without a text layer and without a vision-capable model is
    # refused honestly - there is no local OCR engine by design.
    _stage(ctx, "reading text layer")
    ocr = _ocr_attachment(env, attachment)
    if len(ocr["text"]) >= _OCR_MIN_CHARS:
        if not model:
            return {"engine": ocr["engine"],
                    "text": ocr["text"][:4000], "needs_model": True}
        _stage(ctx, "extracting fields")
        extracted = _extract_bill_fields(model, ocr["text"])
        _stage(ctx, "checking vendor and totals")
        return _digitize_card(env, extracted, ocr["engine"],
                              ocr["pages"], attachment.name,
                              ocr["text"], ocr["mean_confidence"])
    raise AccessError(env._(
        "This scan has no selectable text and the configured model "
        "cannot read images. Use a PDF with selectable text, or "
        "connect a vision-capable model."))


# Registry lives last: every tool above must exist before it is built.
# Business action tools (114 typed Odoo tools) join from their own
# module: it imports this file's helpers lazily, so no cycle.
from odoo.addons.nova_ai.models import (  # noqa: E402
    nova_ai_business_tools as _biz)
REGISTRY = {
    "search_records": tool_search_records,
    "read_record": tool_read_record,
    "count_records": tool_count_records,
    "open_view": tool_open_view,
    "summarize_thread": tool_summarize_thread,
    "next_steps": tool_next_steps,
    "draft_reply": tool_draft_reply,
    "translate_message": None,  # model-only: prose is the whole job
    "search_knowledge": tool_search_knowledge,
    "read_attachment": tool_read_attachment,
    "create_draft": tool_create_draft,
    "log_note": tool_log_note,
    "schedule_activity": tool_schedule_activity,
    "confirm_sale_order": tool_confirm_sale_order,
    "validate_invoice": tool_validate_invoice,
    "set_field": tool_set_field,
    "set_fields": tool_set_fields,
    "build_domain": tool_build_domain,
    "list_models": tool_list_models,
    "validate_write": tool_validate_write,
    "draft_flow": tool_draft_flow,
    "update_flow": tool_update_flow,
    "export_flow": tool_export_flow,
    "import_flow": tool_import_flow,
    "diagnose_access": tool_diagnose_access,
    "scan_codebase": tool_scan_codebase,
    "copy_record": tool_copy_record,
    "read_related": tool_read_related,
    "unlink_record": tool_unlink_record,
    "execute_method": tool_execute_method,
    "cancel_invoice": tool_cancel_invoice,
    "cancel_sale_order": tool_cancel_sale_order,
    "confirm_purchase_order": tool_confirm_purchase_order,
    "validate_delivery": tool_validate_delivery,
    "invoice_order": tool_invoice_order,
    "add_order_line": tool_add_order_line,
    "register_payment": tool_register_payment,
    "reconcile_moves": tool_reconcile_invoice,
    "reverse_move": tool_credit_note,
    "resolve_ticket": tool_resolve_ticket,
    "log_timesheet": tool_log_timesheet,
    "read_group": tool_read_group,
    "fields_get": tool_fields_get,
    "inspect_method": tool_inspect_method,
    "ask_user": tool_ask_user,
    "list_attachments": tool_list_attachments,
    "digitize_bill": tool_digitize_bill,
    "fetch_url": tool_fetch_url,
    # Knowledge: search, read (progressive disclosure), propose.
    "search_knowledge": tool_search_knowledge,
    "read_knowledge": tool_read_knowledge,
    "write_knowledge": tool_write_knowledge,
    # Flows: saved multi-step plans.
    "list_flows": tool_list_flows,
    "run_flow": tool_run_flow,
    "list_flow_runs": tool_list_flow_runs,
}
REGISTRY.update(_biz.registry())

CONFIRM_KINDS = {"create_draft", "log_note", "schedule_activity",
                 "confirm_sale_order", "validate_invoice", "set_field",
                 "set_fields", "draft_flow", "update_flow", "export_flow",
                 "import_flow", "flow_email", "flow_approval",
                 # Read ops return data, never cards: they must NOT be
                 # confirm kinds, or the loop would wait for approval.
                 *(code for code, spec in _biz.specs().items()
                   if spec.get("op") != "read"),
                 "copy_record", "unlink_record", "execute_method",
                 "invoice_order", "register_payment", "reconcile_moves",
                 "reverse_move", "resolve_ticket",
                 # Typed wrappers queue create_draft/execute_method
                 # cards under their own code: the code, not the card
                 # kind, decides the envelope in execute_tool.
                 "cancel_invoice", "cancel_sale_order",
                 "confirm_purchase_order", "validate_delivery",
                 "add_order_line", "log_timesheet"}
