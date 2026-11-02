# Part of Nova. See LICENSE file for full copyright and licensing details.

import copy
import datetime
import logging
import time

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

_CACHE_TTL = 60
_CACHE_MAX = 512
_cache = {}
_ROW_LIMIT = 10
_HIDDEN_LABEL = "Private record"


def _record_readable(env, res_model, res_id):
    """mail.activity's record rule grants read on your own activities even
    when the underlying document is off-limits, so the name must be checked
    against the document itself before it is ever returned."""
    if not res_model or not res_id:
        return False
    try:
        model = env[res_model]
    except KeyError:
        return False
    try:
        if not model.has_access("read"):
            return False
        record = model.browse(res_id)
        if not record.exists():
            return False
        record.check_access("read")
    except Exception:
        return False
    return True


def get_attention(env, fresh=False):
    """Shared helper: overdue/today activities with 60s per-scope cache."""
    try:
        companies = tuple(sorted(env.allowed_company_ids.ids))
    except Exception:
        companies = ()
    cache_key = (env.cr.dbname, env.uid, companies)
    now = time.time()
    if fresh:
        _cache.pop(cache_key, None)
    else:
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL:
            return copy.deepcopy(cached[1])
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    payload = {"overdue_count": 0, "today_count": 0, "items": []}
    try:
        model = env["mail.activity"]
    except KeyError:
        return payload
    try:
        if not model.has_access("read"):
            return payload
    except Exception:
        return payload
    try:
        # User timezone, not server date: avoids "TODAY · 1 DUE" showing
        # yesterday's deadline after midnight server-side.
        today = fields.Date.context_today(env.user).isoformat()
    except Exception:
        try:
            today = datetime.date.today().isoformat()
        except Exception:
            return payload
    try:
        base_domain = [("user_id", "=", env.uid), ("active", "=", True)]
        overdue_ids = model.search(
            base_domain + [("date_deadline", "<", today)],
            order="date_deadline ASC, id ASC",
            limit=_ROW_LIMIT,
        )
        today_ids = model.search(
            base_domain + [("date_deadline", "=", today)],
            order="id ASC",
            limit=_ROW_LIMIT,
        )
        payload["overdue_count"] = model.search_count(
            base_domain + [("date_deadline", "<", today)]
        )
        payload["today_count"] = model.search_count(
            base_domain + [("date_deadline", "=", today)]
        )
        seen = set()
        readable_cache = {}
        for rec in list(overdue_ids) + list(today_ids):
            if rec.id in seen:
                continue
            seen.add(rec.id)
            try:
                summary = rec.summary or (rec.activity_type_id.name if rec.activity_type_id else "")
                res_model = rec.res_model or ""
                res_id = rec.res_id or 0
                cache_token = (res_model, res_id)
                if cache_token not in readable_cache:
                    readable_cache[cache_token] = _record_readable(env, res_model, res_id)
                res_name = (
                    (rec.res_name or "")
                    if readable_cache[cache_token]
                    else _HIDDEN_LABEL
                )
                payload["items"].append(
                    {
                        "id": rec.id,
                        "summary": summary or "Activity",
                        "res_model": res_model,
                        "res_id": res_id,
                        "res_name": res_name,
                        "date_deadline": str(rec.date_deadline or ""),
                        "bucket": "overdue"
                        if str(rec.date_deadline or "") < today
                        else "today",
                    }
                )
            except Exception:
                continue
            if len(payload["items"]) >= _ROW_LIMIT:
                break
    except Exception as exc:  # noqa: BLE001 - fail-silent by design
        _logger.debug("Nova attention skipped: %s", exc)
        return payload
    _cache[cache_key] = (now, payload)
    return copy.deepcopy(payload)


class NovaAttentionController(http.Controller):
    @http.route("/nova/attention", type="jsonrpc", auth="user", methods=["POST"], readonly=True)
    def attention(self):
        return get_attention(request.env)
