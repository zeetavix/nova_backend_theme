# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Agents.

One agent = one role (system prompt + instructions) bound to one model
and a set of tools. Access is a group allow-list: an empty list means
"every internal user"; otherwise the agent is only offered to members
of the listed groups, and the service layer enforces the same list on
every request so a forged agent id cannot escape it.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_MAX_ROUNDS = {"t1": 0, "t2": 6, "t3": 12}


class NovaAiAgent(models.Model):
    _name = "nova.ai.agent"
    _description = "Nova AI Agent"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    initials = fields.Char(
        help="1-2 characters shown in the drawer avatar.")
    description = fields.Char(help="One line shown under the name.")
    model_id = fields.Many2one(
        "nova.ai.model", ondelete="restrict",
        help="Required before the agent can answer; the drawer shows a "
             "setup state until an admin connects a provider.")
    requires = fields.Char(
        help="Capabilities this agent cannot work without, comma "
             "separated: tools, vision, json, reasoning, embeddings. "
             "When its own model cannot do them, the router sends the "
             "turn to a model that can — a capability the job needs is "
             "never traded for a cheaper model.")

    def required_capabilities(self):
        """The agent's required capabilities, as a set."""
        raw = (self.requires or "").replace(";", ",")
        return {part.strip().lower() for part in raw.split(",")
                if part.strip()}

    def model_for_turn(self, env=None):
        """The model that should run this agent's turns.

        The agent's own model wins whenever it can do the job. When it
        cannot — the agent needs to read images and its model cannot —
        the router finds one that can. The returned record carries the
        reason, so the Studio can say why the turn ran elsewhere.
        """
        env = env or self.env
        requires = self.required_capabilities()
        own = self.model_id
        if not requires or not own:
            return own, ""
        try:
            caps = env["nova.ai.router"].capabilities_of(own)
        except Exception:
            return own, ""
        missing = sorted(requires - caps)
        if not missing:
            return own, ""
        try:
            chosen = env["nova.ai.router"].pick(
                env, requires=requires, tier=self.tier, prefer=own)
        except Exception:
            return own, ""
        if chosen and chosen.id != own.id:
            return chosen, "needs %s" % ", ".join(missing)
        return own, ""
    system_prompt = fields.Html(
        help="Mission statement. Sent before every conversation.")
    instructions = fields.Text(
        help="Skill-specific refinements appended to the system prompt.")
    temperature = fields.Float(default=0.2)
    tool_ids = fields.Many2many("nova.ai.tool", string="Tools")
    group_ids = fields.Many2many(
        "res.groups", string="Allowed groups",
        help="Empty = every internal user.")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company)
    auto_apply = fields.Boolean(
        help="T3 only: let confirm-grade tools run without a card. "
             "Every auto write is audited.")
    model_scope = fields.Char(
        help="Comma-separated Odoo models this agent may touch "
             "(e.g. sale.order,sale.order.line). Empty = everything "
             "the requesting user can read. Enforced when tools run "
             "AND when cards are approved.")
    active = fields.Boolean(default=True)

    tier = fields.Selection(related="model_id.tier", store=True)
    max_rounds = fields.Integer(compute="_compute_max_rounds")

    _name_uniq = models.Constraint(
        "UNIQUE(name)", "Agent names must be unique.")

    @api.constrains("active", "model_id")
    def _check_model_when_active(self):
        for agent in self:
            if agent.active and not agent.model_id:
                raise ValidationError(agent.env._(
                    "Agent %s needs a model before it can be activated.",
                    agent.name))

    @api.depends("tier")
    def _compute_max_rounds(self):
        for agent in self:
            agent.max_rounds = _MAX_ROUNDS.get(agent.tier, 0)

    @api.constrains("auto_apply", "tier")
    def _check_auto_apply_tier(self):
        for agent in self:
            if agent.auto_apply and agent.tier != "t3":
                raise ValidationError(_(
                    "Agent %s may only auto-apply writes on a T3 model.")
                    % agent.name)

    def _user_allowed(self, user=None):
        """Record-rule twin: the service layer re-checks this per call,
        including the active flag the read domain carries."""
        user = user or self.env.user
        for agent in self:
            if not agent.active:
                return False
            if agent.company_id and agent.company_id not in \
                    self.env.companies:
                return False
            if not agent.group_ids or agent.group_ids & user.all_group_ids:
                continue
            return False
        return True

    def tools_for_tier(self):
        """Tools this agent may offer, given the connected model's tier
        and the current user's groups. Ungranted tools vanish here, so
        discovery, the model prompt and the loop all hide the same
        set — there is no second list to drift."""
        self.ensure_one()
        if not self.model_id:
            return self.env["nova.ai.tool"]
        rank = {"t1": 1, "t2": 2, "t3": 3}
        model = self.model_id
        user_groups = self.env.user.all_group_ids
        return self.tool_ids.filtered(
            lambda tool: tool.active
            and rank[tool.tier_min] <= rank[model.tier]
            and (not tool.allowed_models or model.name in [
                m.strip() for m in tool.allowed_models.split(",") if m.strip()])
            and (not tool.group_ids or tool.group_ids & user_groups)
        )
