# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Chat sessions and their messages.

A session is a conversation between one user and one agent, optionally
pinned to a record (the drawer's context chip). `pending` holds confirm
cards for write tools: queued, never executed, until the user approves
the card through /nova/ai/confirm. Messages keep the agent's steps and
tool calls so the drawer can replay "what the agent actually did".
"""

from odoo import api, fields, models
from odoo.exceptions import AccessError


class NovaAiSession(models.Model):
    _name = "nova.ai.session"
    _description = "Nova AI Session"
    _order = "last_write desc"

    agent_id = fields.Many2one(
        "nova.ai.agent", required=True, index=True, ondelete="cascade")
    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.user)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company)
    context_model = fields.Char(index=True)
    context_res_id = fields.Integer(index=True)
    context_domain = fields.Json(
        help="Pinned list filter, when the conversation is about a view "
             "rather than a single record.")
    title = fields.Char(compute="_compute_title", store=True)
    title_manual = fields.Char(
        help="History rename: shown instead of the computed title.")
    pending = fields.Json(help="Confirm cards awaiting user approval.")
    applied_keys = fields.Json(
        help="Idempotency keys of approved cards (capped): re-Approve "
             "with the same key is a no-op instead of an error.")
    progress_stage = fields.Char(
        help="Current long-tool stage for the live progress poll; "
             "empty when no turn is running.")
    cancel_flag = fields.Boolean(
        help="Set by /nova/ai/cancel: the running turn stops at the "
             "next round boundary instead of running to completion.")
    tone = fields.Char(
        help="Reply register for this session: concise, friendly or "
             "formal; empty means neutral.")
    undo_state = fields.Json(
        help="Inverse of the last applied set_field write, so Undo "
             "restores exactly what Approve changed.")
    is_view = fields.Boolean(
        help="Saved view: a named pin (agent + record or list filter) "
             "reopened from history instead of a past conversation.")
    last_write = fields.Datetime(default=fields.Datetime.now, index=True)
    message_ids = fields.One2many("nova.ai.message", "session_id")
    active = fields.Boolean(default=True)

    def rewind_last_turn(self):
        """Drop the last turn (the user prompt and its trailing assistant
        reply, if any) and return the user text to re-run, so a rerun
        stores the question exactly once. Returns "" when there is
        nothing to regenerate.

        Pending cards go with it: a card is only meaningful with the
        turn that produced it, and the rerun will queue fresh ones.
        Leaving the old cards approvable would execute writes whose
        question no longer exists.
        """
        self.ensure_one()
        if self.user_id != self.env.user:
            raise AccessError("Unknown session.")
        ordered = list(self.message_ids.sorted("id"))
        if ordered and ordered[-1].role == "assistant":
            ordered[-1].unlink()
            ordered = ordered[:-1]
        if not ordered or ordered[-1].role != "user":
            return ""
        import re
        text = re.sub(r"<[^>]+>", "", ordered[-1].content or "").strip()
        if not text:
            return ""
        ordered[-1].unlink()
        if self.pending:
            self.write({"pending": False})
        return text

    @api.depends("message_ids.content", "agent_id.name",
                 "title_manual")
    def _compute_title(self):
        import re
        for session in self:
            if session.title_manual:
                session.title = session.title_manual[:60]
                continue
            first = session.message_ids[:1]
            text = ""
            if first:
                # content is Html: strip tags before using it as a label.
                text = re.sub(r"<[^>]+>", " ", first.content or "")
                text = re.sub(r"\s+", " ", text).strip()[:60]
            session.title = text or session.agent_id.name

    def touch(self):
        self.write({"last_write": fields.Datetime.now()})

    def pop_card(self, token):
        """Take one pending card off the queue and return it, or None.

        Popping BEFORE execute is what makes double-Approve safe: the
        first request owns the card, the second finds nothing and
        stops instead of running the write twice. The row lock
        serializes two concurrent Approves so both cannot pop the
        same card in overlapping transactions. Flush first: ORM
        writes defer their SQL, and the raw SELECT below would
        otherwise read pre-write state in the same cursor.
        """
        self.ensure_one()
        try:
            self.flush_recordset()
        except Exception:
            pass
        self.env.cr.execute(
            "SELECT pending FROM nova_ai_session WHERE id = %s "
            "FOR UPDATE", (self.id,))
        row = self.env.cr.dictfetchone() or {}
        raw = row.get("pending", self.pending)
        if isinstance(raw, str):
            import json as _json
            try:
                raw = _json.loads(raw)
            except ValueError:
                raw = []
        pending = list(raw or [])
        card = next(
            (c for c in pending if c.get("token") == token), None)
        if not card:
            return None
        self.write({"pending": [
            c for c in pending if c.get("token") != token]})
        return card

    def restore_card(self, card):
        """Put a popped card back (a failed approve stays retryable),
        without ever duplicating it."""
        self.ensure_one()
        if not card:
            return
        try:
            self.flush_recordset()
        except Exception:
            pass
        self.env.cr.execute(
            "SELECT pending FROM nova_ai_session WHERE id = %s "
            "FOR UPDATE", (self.id,))
        row = self.env.cr.dictfetchone() or {}
        raw = row.get("pending", self.pending)
        if isinstance(raw, str):
            import json as _json
            try:
                raw = _json.loads(raw)
            except ValueError:
                raw = []
        current = list(raw or [])
        if any(c.get("token") == card.get("token") for c in current):
            return
        self.write({"pending": current + [card]})

    def already_applied(self, key):
        """True when this key approved a card before: re-Approve is a
        no-op, not an error and never a second write."""
        return bool(key) and key in (self.applied_keys or [])

    def mark_applied(self, key):
        """Remember one approved key, capped so the list cannot grow
        without bound."""
        self.ensure_one()
        if not key:
            return
        keys = [k for k in (self.applied_keys or []) if k != key]
        keys.append(key)
        self.write({"applied_keys": keys[-50:]})

    def set_progress(self, stage):
        """Publish one stage of a running turn for the progress poll.
        Committed at once so the poll (a separate request) sees it;
        stages are ephemeral UI, never load-bearing state."""
        self.ensure_one()
        self.write({"progress_stage": stage or ""})
        try:
            self.env.cr.commit()
        except Exception:
            pass


class NovaAiMessage(models.Model):
    _name = "nova.ai.message"
    _description = "Nova AI Message"
    _order = "id asc"
    _log_access = False

    session_id = fields.Many2one(
        "nova.ai.session", required=True, index=True, ondelete="cascade")
    role = fields.Selection(
        [("user", "User"), ("assistant", "Assistant"), ("tool", "Tool")],
        required=True)
    content = fields.Html()
    steps = fields.Json()
    tool_calls = fields.Json()
    created_at = fields.Datetime(default=fields.Datetime.now, index=True)

    @api.model
    def add(self, session, role, content="", steps=None, tool_calls=None):
        return self.create({
            "session_id": session.id,
            "role": role,
            "content": content or "",
            "steps": steps or [],
            "tool_calls": tool_calls or [],
            "created_at": fields.Datetime.now(),
        })
