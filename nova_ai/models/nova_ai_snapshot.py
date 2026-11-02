# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Named session snapshots (T-295).

A snapshot freezes a session's restorable state — title, context pin
and the pending approval queue — under a name, so a user can rewind
an experiment (approve something else, regenerate, wander off) and
come back to exactly what was waiting. Messages are history, not
state: the snapshot stores the queue, not the transcript.
"""

from odoo import api, fields, models
from odoo.exceptions import AccessError


class NovaAiSnapshot(models.Model):
    _name = "nova.ai.snapshot"
    _description = "Nova AI Session Snapshot"
    _order = "id desc"

    name = fields.Char(required=True)
    session_id = fields.Many2one(
        "nova.ai.session", required=True, index=True, ondelete="cascade")
    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.user)
    state = fields.Json(
        required=True,
        help="Frozen session state: title, context pin and pending "
             "cards at save time.")
    created_at = fields.Datetime(default=fields.Datetime.now, index=True)

    @api.model
    def save_for(self, session, name):
        """Freeze the session's restorable state under `name`."""
        if not session or session.user_id != self.env.user:
            raise AccessError("Unknown session.")
        state = {
            "title": session.title_manual or session.title or "",
            "context_model": session.context_model or "",
            "context_res_id": session.context_res_id or 0,
            "context_domain": session.context_domain,
            "pending": session.pending or [],
        }
        return self.create({
            "name": (name or "").strip()[:60] or session.title[:60],
            "session_id": session.id,
            "state": state,
        })

    def restore_to(self, session):
        """Write the frozen state back onto its session."""
        self.ensure_one()
        if self.user_id != self.env.user or not session \
                or session.user_id != self.env.user:
            raise AccessError("Unknown snapshot.")
        if session.id != self.session_id.id:
            raise AccessError("That snapshot belongs to another chat.")
        state = self.state or {}
        session.write({
            "title_manual": (state.get("title") or "")[:60] or False,
            "context_model": state.get("context_model") or "",
            "context_res_id": int(state.get("context_res_id") or 0) or 0,
            "context_domain": state.get("context_domain"),
            "pending": state.get("pending") or [],
        })
        return True
