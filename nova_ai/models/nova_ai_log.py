# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Audit log for every provider call, probe and tool call.

Writes happen with sudo() from the service layer only; users never
create log rows by hand. Payload is kept small and must never carry
API keys or full message bodies — the service layer redacts before
calling :meth:`log`.
"""

import json

from odoo import api, models, fields


class NovaAiLog(models.Model):
    _name = "nova.ai.log"
    _description = "Nova AI Call Log"
    _order = "id desc"
    _log_access = False

    created_at = fields.Datetime(default=fields.Datetime.now, index=True)
    agent_id = fields.Many2one("nova.ai.agent", index=True, ondelete="set null")
    user_id = fields.Many2one("res.users", index=True, ondelete="set null")
    model_id = fields.Many2one("nova.ai.model", index=True, ondelete="set null")
    kind = fields.Selection(
        [
            ("chat", "Chat"),
            ("tool", "Tool call"),
            ("probe", "Capability probe"),
            ("error", "Error"),
            ("feedback", "User feedback"),
        ],
        required=True,
        index=True,
    )
    ok = fields.Boolean(default=True)
    error = fields.Text()
    latency_ms = fields.Integer()
    tokens_in = fields.Integer()
    tokens_out = fields.Integer()
    rounds = fields.Integer(help="Tool rounds consumed (agentic loops).")
    detail = fields.Json()

    @api.model
    def log(self, kind, agent=None, user=None, model=None, ok=True,
            error="", latency_ms=0, tokens_in=0, tokens_out=0, rounds=0,
            detail=None):
        """Append one audit row. `detail` must already be redacted."""
        try:
            payload = json.loads(json.dumps(detail or {}, default=str))
        except (TypeError, ValueError):
            payload = {"raw": str(detail)[:500]}
        return self.sudo().create(
            {
                "agent_id": agent.id if hasattr(agent, "id") else agent,
                "user_id": (user or self.env.user).id,
                "model_id": model.id if hasattr(model, "id") else model,
                "kind": kind,
                "ok": ok,
                "error": (error or "")[:2000],
                "latency_ms": int(latency_ms or 0),
                "tokens_in": int(tokens_in or 0),
                "tokens_out": int(tokens_out or 0),
                "rounds": int(rounds or 0),
                "detail": payload,
                "created_at": fields.Datetime.now(),
            }
        )
