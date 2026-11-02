# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Flow runs — audit-grade execution history.

Every run records who triggered it, with what inputs, what each node
returned (truncated), and how it ended. Nothing here stores business
row contents beyond what the engine already truncates; payloads are
capped so a run over 10k invoices cannot bloat the database.
"""

from odoo import fields, models


class NovaAiFlowRun(models.Model):
    _name = "nova.ai.flow.run"
    _description = "Nova AI Flow Run"
    _order = "id desc"

    flow_id = fields.Many2one(
        "nova.ai.flow", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", default=lambda self: self.env.user)
    trigger = fields.Char(default="manual", index=True)
    status = fields.Selection([
        ("ok", "Succeeded"),
        ("error", "Failed"),
        ("waiting_approval", "Waiting approval"),
    ], default="ok", index=True)
    inputs = fields.Json()
    outputs = fields.Json()
    node_log = fields.Json(
        help="Per-node results, truncated for storage.")
    # n8n-style execution inspector: per-node input + output payloads.
    node_inputs = fields.Json(
        help="Scope snapshot fed INTO each node, by node id.")
    node_outputs = fields.Json(
        help="Value each node produced, by node id.")
    error = fields.Text()
    duration_ms = fields.Integer()
    started_at = fields.Datetime(default=fields.Datetime.now, index=True)
    idempotency_key = fields.Char(index=True, copy=False,
        help="Exact-match dedupe key (replaces ilike-on-inputs).")
    resume_scope = fields.Json(help="Scope snapshot to resume a paused run.")
    resume_pending = fields.Json(help="Pending node ids to resume.")
    resume_visited = fields.Json(help="Visited node ids to resume.")
    paused_node = fields.Char(help="Node id that queued approval.")

    def _touch_node(self, node_id, node_type):
        """Heartbeat while a run walks the graph (Studio polling)."""
        return True
