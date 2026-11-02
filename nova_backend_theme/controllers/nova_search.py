# Part of Nova. See LICENSE file for full copyright and licensing details.

import re
import time

from odoo import http
from odoo.http import request

_MODEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")
DEFAULT_MODELS = (
    "res.partner,sale.order,account.move,project.task,crm.lead,"
    "product.product,purchase.order,stock.picking"
)
# Models never searchable no matter the admin allowlist (config/secrets).
_DENIED_PREFIXES = ("ir.", "res.config")
_DENIED_EXACT = {"res.users"}
PER_MODEL_LIMIT = 5
TOTAL_LIMIT = 20
MIN_CHARS = 2
MAX_CHARS = 100
MIN_INTERVAL_MS = 200

_throttle = {}
_allow_cache = {}


def _like_escape(value):
    # LIKE metacharacters in user input must not broaden the match: a bare
    # "%%" would otherwise turn the prefix pass into match-all-readable.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _allowlisted_models():
    # NOTE: the cache below is intentionally global (dbname, raw) — the value
    # is deployment config plus a model-existence check, never per-user data.
    # A future per-user allowlist must key by uid/companies instead.
    try:
        # ir.config_parameter grants read to internal users; no sudo needed.
        raw = request.env["ir.config_parameter"].get_str("nova.search.models", DEFAULT_MODELS)
    except Exception:
        raw = DEFAULT_MODELS
    try:
        dbname = request.env.cr.dbname
    except Exception:
        dbname = ""
    cache_key = (dbname, raw)
    cached = _allow_cache.get(cache_key)
    if cached is not None:
        return list(cached)
    models = []
    for name in (raw or "").split(","):
        name = name.strip()
        if not name or not _MODEL_RE.match(name):
            continue
        if name in _DENIED_EXACT or name.startswith(_DENIED_PREFIXES):
            continue
        try:
            request.env[name]
        except KeyError:
            continue
        models.append(name)
    if len(_allow_cache) >= 32:
        _allow_cache.clear()
    _allow_cache[cache_key] = models
    return list(models)


class NovaSearchController(http.Controller):
    @http.route("/nova/search", type="jsonrpc", auth="user", methods=["POST"], readonly=True)
    def search(self, q="", limit=TOTAL_LIMIT):
        if not isinstance(q, str):
            q = ""
        query = (q or "").strip()[:MAX_CHARS]
        if len(query) < MIN_CHARS:
            return {"results": []}
        try:
            limit = max(1, min(int(limit or TOTAL_LIMIT), TOTAL_LIMIT))
        except (TypeError, ValueError):
            limit = TOTAL_LIMIT
        # Light per-user throttle (best-effort burst guard, fail-open).
        try:
            now = int(time.time() * 1000)
            peer = ""
            try:
                peer = (request.httprequest.remote_addr or "")
            except Exception:
                peer = ""
            try:
                dbname = request.env.cr.dbname
            except Exception:
                dbname = ""
            key = (dbname, request.session.uid or 0, peer)
            if now - _throttle.get(key, 0) < MIN_INTERVAL_MS:
                return {"results": [], "throttled": True}
            _throttle[key] = now
            if len(_throttle) > 2048:
                _throttle.clear()
        except Exception:
            pass
        per_model = []
        for model_name in _allowlisted_models():
            try:
                model = request.env[model_name]
                if not model.has_access("read"):
                    continue
                # Ranked passes: exact, then prefix, then token ilike.
                ranked = []
                seen = set()
                try:
                    for _id, name in model.name_search(query, [], "=", 2) or []:
                        if _id not in seen:
                            seen.add(_id)
                            ranked.append((_id, name))
                except Exception:
                    pass
                try:
                    for _id, name in model.name_search(_like_escape(query) + "%", [], "=like", 3) or []:
                        if _id not in seen:
                            seen.add(_id)
                            ranked.append((_id, name))
                except Exception:
                    pass
                try:
                    for token in re.split(r"[^a-zA-Z0-9]+", query):
                        if len(token) < 2 or len(ranked) >= PER_MODEL_LIMIT:
                            continue
                        for _id, name in model.name_search(token, [], "ilike", PER_MODEL_LIMIT) or []:
                            if _id not in seen:
                                seen.add(_id)
                                ranked.append((_id, name))
                            if len(ranked) >= PER_MODEL_LIMIT:
                                break
                except Exception:
                    pass
                if ranked:
                    per_model.append((model_name, model._description or model_name, ranked))
            except Exception:
                # Missing model/field or denied access: skip silently.
                continue
        # Round-robin merge so one model never starves the rest.
        results = []
        round_index = 0
        while len(results) < limit:
            progressed = False
            for model_name, model_label, ranked in per_model:
                if round_index < len(ranked):
                    _id, display_name = ranked[round_index]
                    results.append(
                        {
                            "model": model_name,
                            "model_label": model_label,
                            "res_id": _id,
                            "display_name": display_name,
                        }
                    )
                    progressed = True
                    if len(results) >= limit:
                        break
            if not progressed:
                break
            round_index += 1
        return {"results": results}
