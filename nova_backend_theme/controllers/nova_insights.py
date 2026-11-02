# Part of Nova. See LICENSE file for full copyright and licensing details.

import copy
import datetime
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_CACHE_TTL = 90
_CACHE_MAX = 512
_cache = {}


def _today():
    try:
        return datetime.date.today().isoformat()
    except Exception:
        return "2200-01-01"


def _now_utc():
    # Server-UTC day boundary for Datetime fields; user-tz edge documented.
    try:
        return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "2200-01-01 00:00:00"


def _providers():
    """Core insight providers: (key, label, model, domain, overdue_domain, hints).

    Counts only, fail-silent per tile. overdue_domain narrows the base domain
    to the urgent subset (compared server-side so cached tiles stay correct).
    """
    today = _today()
    return [
        (
            "open_customer_invoices",
            "Open customer invoices",
            "account.move",
            [
                ("move_type", "in", ["out_invoice", "out_refund", "out_receipt"]),
                ("state", "=", "posted"),
                ("payment_state", "not in", ["paid", "reversed"]),
            ],
            [("invoice_date_due", "<", today)],
            ["invoicing", "accounting"],
        ),
        (
            "draft_vendor_bills",
            "Draft vendor bills",
            "account.move",
            [
                ("move_type", "in", ["in_invoice", "in_refund", "in_receipt"]),
                ("state", "=", "draft"),
            ],
            [],
            ["purchase", "accounting", "invoicing"],
        ),
        (
            "open_quotations",
            "Quotations waiting",
            "sale.order",
            [("state", "in", ["draft", "sent"])],
            [("state", "in", ["draft", "sent"]), ("validity_date", "<", today)],
            ["sale"],
        ),
        (
            "active_tasks",
            "Active tasks",
            "project.task",
            [],
            [("date_deadline", "<", _now_utc())],
            ["project"],
        ),
        (
            "open_leads",
            "Open leads",
            "crm.lead",
            [],
            [],
            ["crm", "sales"],
        ),
    ]


def companies_key(env):
    try:
        return tuple(sorted(env.allowed_company_ids.ids))
    except Exception:
        return ()


def get_tiles(env, fresh=False):
    """Shared helper: insight tiles with 90s per-scope cache. Fail-open."""
    cache_key = (env.cr.dbname, env.uid, companies_key(env))
    now = time.time()
    if fresh:
        _cache.pop(cache_key, None)
    else:
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL:
            return copy.deepcopy(cached[1])
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    tiles = []
    for key, label, model_name, domain, overdue_domain, hints in _providers():
        if len(tiles) >= 5:
            break
        try:
            model = env[model_name]
        except KeyError:
            continue
        try:
            if not model.has_access("read"):
                continue
            count = model.search_count(domain or [])
            if not isinstance(count, int):
                continue
            overdue = 0
            if overdue_domain:
                try:
                    # Narrow the BASE domain: overdue means past-due AND open.
                    overdue = model.search_count(list(domain or []) + list(overdue_domain)) or 0
                except Exception:
                    overdue = 0
            tiles.append(
                {
                    "key": key,
                    "label": label,
                    "value": count,
                    "overdue": overdue if isinstance(overdue, int) else 0,
                    "res_model": model_name,
                    "domain": domain or [],
                    "app_hints": hints,
                }
            )
        except Exception as exc:  # noqa: BLE001 - per-tile fail-silent by design
            _logger.debug("Nova insight %s skipped: %s", key, exc)
            continue
    _cache[cache_key] = (now, tiles)
    return tiles


class NovaInsightsController(http.Controller):
    @http.route("/nova/insights", type="jsonrpc", auth="user", methods=["POST"], readonly=True)
    def insights(self):
        return {"tiles": get_tiles(request.env)}
