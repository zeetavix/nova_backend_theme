# Part of Nova. See LICENSE file for full copyright and licensing details.
#
# One-trip launcher bootstrap: insights + attention + dock + bookmarks in a
# single round-trip. Legacy routes stay as thin wrappers over the same
# helpers. Every section is fail-open — one bad section never breaks the rest.

import logging

from odoo import http
from odoo.http import request

from .nova_attention import get_attention
from .nova_dock import get_dock
from .nova_insights import get_tiles

_logger = logging.getLogger(__name__)

_SECTIONS = ("insights", "attention", "dock", "bookmarks", "create")

# One-click creation shortcuts. Only models the user may create on are
# returned (KeyError/missing create rights skip silently), so the home
# never offers a dead button. Labels are server-side for translation.
_CREATE_ACTIONS = [
    ("new_quotation", "New quotation", "sale.order", {}),
    ("new_invoice", "New invoice", "account.move", {"default_move_type": "out_invoice"}),
    ("new_bill", "New bill", "account.move", {"default_move_type": "in_invoice"}),
    ("new_task", "New task", "project.task", {}),
    ("new_lead", "New lead", "crm.lead", {}),
    ("new_contact", "New contact", "res.partner", {}),
]
_CREATE_MAX = 6


def _clean_sections(sections):
    if not sections:
        return list(_SECTIONS)
    try:
        wanted = [str(s) for s in sections]
    except Exception:
        return list(_SECTIONS)
    picked = [s for s in wanted if s in _SECTIONS]
    return picked or list(_SECTIONS)


def get_create(env):
    """One-click creation shortcuts for models the user may create on."""
    actions = []
    for key, label, model_name, context in _CREATE_ACTIONS:
        if len(actions) >= _CREATE_MAX:
            break
        try:
            model = env[model_name]
        except KeyError:
            continue
        try:
            if not model.has_access("create"):
                continue
        except Exception:
            continue
        actions.append(
            {
                "key": key,
                "label": label,
                "res_model": model_name,
                "context": dict(context or {}),
            }
        )
    return actions


class NovaHomeController(http.Controller):
    @http.route("/nova/home", type="jsonrpc", auth="user", methods=["POST"], readonly=True)
    def home(self, sections=None, fresh=False):
        env = request.env
        wanted = _clean_sections(sections)
        try:
            fresh = bool(fresh)
        except Exception:
            fresh = False
        result = {}
        errors = {}
        state = None
        if "dock" in wanted or "bookmarks" in wanted:
            try:
                # One _state() call feeds both sections. prune=False: this
                # route is readonly, so unlink() would roll back anyway;
                # dead pins are skipped visibly by get_dock regardless.
                state = env["nova.bookmark"]._state(prune=False)
            except Exception as exc:
                _logger.debug("Nova home bookmarks skipped: %s", exc)
                state = None
                errors["bookmarks"] = "unavailable"
        if "insights" in wanted:
            try:
                result["tiles"] = get_tiles(env, fresh=fresh)
            except Exception as exc:
                _logger.debug("Nova home insights skipped: %s", exc)
                result["tiles"] = []
                errors["insights"] = "unavailable"
        if "attention" in wanted:
            try:
                result["attention"] = get_attention(env, fresh=fresh)
            except Exception as exc:
                _logger.debug("Nova home attention skipped: %s", exc)
                result["attention"] = {"overdue_count": 0, "today_count": 0, "items": []}
                errors["attention"] = "unavailable"
        if "dock" in wanted:
            try:
                result["dock"] = get_dock(env, state) if state is not None else []
            except Exception as exc:
                _logger.debug("Nova home dock skipped: %s", exc)
                result["dock"] = []
                errors["dock"] = "unavailable"
        if "bookmarks" in wanted:
            try:
                result["bookmarks"] = dict(state) if state is not None else {"menus": [], "records": []}
            except Exception as exc:
                _logger.debug("Nova home bookmarks skipped: %s", exc)
                result["bookmarks"] = {"menus": [], "records": []}
                errors["bookmarks"] = "unavailable"
        if "create" in wanted:
            try:
                result["create"] = get_create(env)
            except Exception as exc:
                _logger.debug("Nova home create skipped: %s", exc)
                result["create"] = []
                errors["create"] = "unavailable"
        if errors:
            result["errors"] = errors
        return result
