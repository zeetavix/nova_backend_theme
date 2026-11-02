# Part of Nova. See LICENSE file for full copyright and licensing details.

import datetime

from odoo import http
from odoo.http import request

_STALE_WEEK_DAYS = 7
_STALE_MONTH_DAYS = 30
_MAX_ROWS = 30


def _staleness(write_date):
    if not write_date:
        return "unknown", ""
    try:
        if hasattr(write_date, "date"):
            day = write_date.date()
        else:
            day = datetime.date.fromisoformat(str(write_date)[:10])
        age = (datetime.date.today() - day).days
    except Exception:
        return "unknown", ""
    if age <= 0:
        return "fresh", "Updated today"
    if age == 1:
        return "fresh", "Updated yesterday"
    if age <= _STALE_WEEK_DAYS:
        return "fresh", f"Updated {age} days ago"
    if age <= _STALE_MONTH_DAYS:
        return "week", f"Updated {age} days ago"
    return "month", "Updated over a month ago"


def get_dock(env, state):
    """Shared helper: pinned-record dock rows with staleness. `state` is one
    nova.bookmark._state() call shared with the bookmarks section — callers
    must not re-query bookmarks. Fail-open: [] on any error."""
    try:
        records = (state or {}).get("records", [])[:_MAX_ROWS]
    except Exception:
        return []
    by_model = {}
    for rec in records:
        try:
            by_model.setdefault(rec["res_model"], []).append(rec["res_id"])
        except Exception:
            continue
    known = {}
    for model_name, ids in by_model.items():
        try:
            model = env[model_name]
        except KeyError:
            continue
        try:
            if not model.has_access("read"):
                continue
            for row in model.search_read(
                [["id", "in", list(set(ids))]], ["display_name", "write_date"]
            ):
                known[(model_name, row["id"])] = row
        except Exception:
            continue
    dock = []
    for rec in records:
        row = known.get((rec["res_model"], rec["res_id"]))
        if not row:
            continue
        display_name = row.get("display_name") or ""
        if not display_name:
            # Never echo the caller-supplied bookmark label as a record name.
            continue
        stale, stale_label = _staleness(row.get("write_date"))
        dock.append(
            {
                "res_model": rec["res_model"],
                "res_id": rec["res_id"],
                "display_name": display_name,
                "write_date": str(row.get("write_date") or ""),
                "stale": stale,
                "stale_label": stale_label,
            }
        )
    return dock


class NovaDockController(http.Controller):
    @http.route("/nova/dock", type="jsonrpc", auth="user", methods=["POST"], readonly=True)
    def dock(self):
        try:
            state = request.env["nova.bookmark"]._state(prune=False)
        except Exception:
            return {"dock": []}
        return {"dock": get_dock(request.env, state)}
