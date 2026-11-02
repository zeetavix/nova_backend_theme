# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Nova knowledge — durable, provenance-tracked knowledge the agent can use.

Synthesised from three references:

* **OKF (Open Knowledge Format, Google/Apache-2.0)** — the frontmatter
  contract: one concept per record, ``type`` required, provenance in
  ``generated``/``verified``, lifecycle in ``status``/``stale_after``,
  citations joined to sources by id. We keep the *shape* (one row, one
  concept, join keys) and drop the file format: a database row is a
  better concept file in Odoo, and it inherits the ACL, the undo log and
  the record rules for free.
* **Javis OS** — the layering (sources the user owns, wiki the agent
  writes, memory it recalls) and the rule that the agent never writes
  a file directly: Python owns every write. Same here — the agent can
  *propose* knowledge, a human approves it.
* **AnythingLLM** — the citation shape a UI can render, and the
  ``odoo://model/res_id`` scheme so an answer deep-links into the
  record it came from.

Trust tiers are derived, never stored: no ``verified`` means
unverified, a machine verifier means machine-confirmed, a ``human:``
actor means human-reviewed. Advisory signals, not access control.
"""

import re

from odoo import _, api, fields, models

# OKF §7 actor convention: <producer>/<version>, human:<id>, process:<id>
_ACTOR_RE = re.compile(r"^\S+/\S+$|^human:\S+$|^process:\S+$")

# OKF §5.4 lifecycle vocabulary.
_STATUS = [("draft", "Draft"), ("stable", "Stable"),
           ("deprecated", "Deprecated")]

# Javis' layer split, plus a skill (a playbook the agent loads on demand).
_KIND = [("skill", "Skill"), ("memory", "Memory"), ("wiki", "Wiki"),
         ("reference", "Reference"), ("document", "Document")]

# The description is the router's trigger text: it decides whether a
# skill fires, so it is capped and must not be boilerplate.
_DESC_MAX = 150
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _slugify(text):
    """Path-safe concept id (OKF derives identity from the path)."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", (text or "").strip().lower())
    slug = slug.strip("-._")
    return slug[:64]


class NovaAiKnowledge(models.Model):
    _name = "nova.ai.knowledge"
    _description = "Nova AI Knowledge"
    _order = "kind, code"
    _log_access = False
    # Same chatter contract as flows: the form's chatter needs the
    # Discuss thread API or the record 500s in mail's store.
    _inherit = ["mail.thread", "mail.activity.mixin"]

    # --- identity -----------------------------------------------------
    code = fields.Char(
        required=True, index=True,
        help="Stable concept id, kebab-case. Derived from the title "
             "when left empty. Doubles as the skill name the agent "
             "loads.")
    name = fields.Char(required=True, help="Display title.")
    kind = fields.Selection(
        _KIND, required=True, default="wiki",
        help="memory: durable fact about the user or business. "
             "wiki: a concept the agent maintains. skill: a playbook the "
             "agent loads before acting. reference: docs. document: "
             "ingested source material.")
    description = fields.Char(
        help="One line the agent matches against. This is the trigger "
             "text for a skill — keep it under %d characters."
             % _DESC_MAX)
    body = fields.Text(
        help="The concept itself, in markdown. For a skill this is the "
             "procedure to follow.")

    # --- provenance (OKF §5) -----------------------------------------
    generated_by = fields.Char(
        help="Who produced this: <producer>/<version>, human:<id> or "
             "process:<id>. The human: prefix is what marks a concept "
             "human-reviewed.")
    generated_at = fields.Datetime(
        help="When the content last changed meaningfully.")
    verified_by = fields.Char(
        help="Who checked it. human:<id> means human-reviewed.")
    verified_at = fields.Datetime()
    status = fields.Selection(
        _STATUS, default="stable",
        help="Draft, stable, or deprecated. Absent means stable.")
    stale_after = fields.Date(
        help="Past this date the knowledge is stale and the agent is "
             "told to re-check it.")
    sources = fields.Json(
        help="List of {resource, id, title, author}. The id is the "
             "join key a citation footnote points at.")
    tags = fields.Json(help="List of tags.")

    # --- access -------------------------------------------------------
    owner_user_id = fields.Many2one(
        "res.users", ondelete="cascade",
        help="Empty = shared with every user of the agent. Set = only "
             "this user (and admins) may read it.")
    active = fields.Boolean(default=True)

    # --- self-learning (Javis' learn gate) ----------------------------
    proposed_by = fields.Char(
        help="Who proposed this concept, when the agent proposed it "
             "rather than an admin. A proposal stays a draft until a "
             "human reviews it — the agent never writes knowledge "
             "straight into the corpus.")
    reviewed_by = fields.Char(
        help="Who reviewed the proposal. human:<id> marks it "
             "human-reviewed.")
    review_note = fields.Text(
        help="Why the proposal was accepted or rejected.")
    is_proposed = fields.Boolean(
        compute="_compute_is_proposed", store=True,
        search="_search_is_proposed",
        help="True while the concept waits for a human to review it.")

    @api.depends("status", "proposed_by", "verified_by")
    def _compute_is_proposed(self):
        for record in self:
            record.is_proposed = bool(
                record.status == "draft" and record.proposed_by
                and not record.verified_by)

    def _search_is_proposed(self, operator, value):
        if operator in ("in", "not in"):
            want = any(bool(v) for v in (value or []))
        elif operator in ("=", "==", "!=", "<>"):
            want = bool(value)
        else:
            raise ValueError("Unsupported operator %r" % operator)
        domain = [("status", "=", "draft"), ("proposed_by", "!=", False),
                  ("verified_by", "=", False)]
        if not want:
            domain = ["!"] + domain
        return domain

    # --- usage (Javis: usage-aware ordering) -------------------------
    use_count = fields.Integer(default=0, readonly=True)
    last_used_at = fields.Datetime(readonly=True)

    _code_uniq = models.Constraint(
        "UNIQUE(code)", "One knowledge concept per code.")

    # ---------------------------------------------------------------
    # derived
    # ---------------------------------------------------------------
    @api.depends("verified_by")
    def _compute_trust(self):
        # OKF §5.3 — derived, never stored.
        for record in self:
            actor = (record.verified_by or "").strip()
            if not actor:
                record.trust_tier = "unverified"
            elif actor.lower().startswith("human:"):
                record.trust_tier = "human-reviewed"
            else:
                record.trust_tier = "machine-confirmed"

    trust_tier = fields.Selection(
        [("unverified", "Unverified"),
         ("machine-confirmed", "Machine confirmed"),
         ("human-reviewed", "Human reviewed")],
        compute="_compute_trust", search="_search_trust_tier",
        help="Derived from who verified this. Advisory only — it is not "
             "access control.")

    @api.depends("stale_after", "generated_at")
    def _compute_freshness(self):
        today = fields.Date.context_today(self)
        for record in self:
            record.is_stale = bool(
                record.stale_after and today >= record.stale_after)

    is_stale = fields.Boolean(
        compute="_compute_freshness", search="_search_is_stale",
        help="True when stale_after has passed. The agent is told to "
             "re-check rather than trust it.")

    def _search_is_stale(self, operator, value):
        """Staleness is a date comparison, so it can be searched.

        Odoo normalises a boolean comparison to ``in`` in several
        places, so both spellings are answered.
        """
        today = fields.Date.context_today(self)
        # Stale means: a stale_after is set AND it is not in the future.
        stale = [("stale_after", "!=", False), ("stale_after", "<=", today)]
        fresh = ["!"] + stale
        if operator in ("=", "=="):
            return stale if value else fresh
        if operator in ("!=", "<>"):
            return fresh if value else stale
        if operator == "in":
            return stale if any(bool(v) for v in (value or [])) else fresh
        if operator == "not in":
            return fresh if any(bool(v) for v in (value or [])) else stale
        raise ValueError("Unsupported operator %r" % operator)

    def _search_trust_tier(self, operator, value):
        """The trust tier is derived from who verified, so it searches
        the actor field, not the tier."""
        if operator in ("in", "not in"):
            wanted = list(value or [])
        elif operator in ("=", "==", "!=", "<>"):
            wanted = [value]
        else:
            raise ValueError("Unsupported operator %r" % operator)
        clauses = []
        for tier in wanted:
            if tier == "human-reviewed":
                clauses.append([("verified_by", "ilike", "human:%")])
            elif tier == "machine-confirmed":
                clauses.append([
                    ("verified_by", "!=", False),
                    ("verified_by", "not ilike", "human:%"),
                ])
            else:
                clauses.append([("verified_by", "=", False)])
        if not clauses:
            return [("id", "=", 0)]
        domain = clauses[0]
        for clause in clauses[1:]:
            domain = ["|"] + domain + clause
        if operator in ("!=", "<>", "not in"):
            domain = ["!"] + domain
        return domain

    def _compute_effective_status(self):
        # Absent status means stable (OKF §5.4).
        for record in self:
            record.effective_status = record.status or "stable"

    effective_status = fields.Selection(
        _STATUS, compute="_compute_effective_status")

    # ---------------------------------------------------------------
    # lifecycle
    # ---------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("code"):
                vals["code"] = self._unique_code(vals)
            if not vals.get("generated_at"):
                vals["generated_at"] = fields.Datetime.now()
            if not vals.get("generated_by"):
                vals["generated_by"] = "nova_ai/%s" % (
                    self.env["nova.ai.addon"].get_version("nova_ai")
                    if "nova.ai.addon" in self.env else "1.0")
        return super().create(vals_list)

    @api.model
    def _unique_code(self, vals):
        base = _slugify(vals.get("name") or vals.get("title") or "concept")
        if not base:
            base = "concept"
        code = base
        n = 2
        while self.with_context(active_test=False).search_count(
                [("code", "=", code)]):
            code = "%s-%d" % (base, n)
            n += 1
        return code

    def action_verify(self):
        """Mark as checked by a human (trust tier becomes human-reviewed)."""
        for record in self:
            record.write({
                "verified_by": "human:%s" % self.env.user.id,
                "verified_at": fields.Datetime.now(),
            })
        return True

    def action_accept(self):
        """Accept a proposed concept: it becomes stable and reviewed."""
        for record in self:
            record.write({
                "status": "stable",
                "verified_by": "human:%s" % self.env.user.id,
                "verified_at": fields.Datetime.now(),
                "reviewed_by": "human:%s" % self.env.user.id,
                "review_note": "Accepted.",
            })
        return True

    def action_reject(self):
        """Reject a proposal: it is archived, never silently kept."""
        for record in self:
            record.write({
                "status": "deprecated",
                "reviewed_by": "human:%s" % self.env.user.id,
                "review_note": "Rejected.",
            })
        return True

    def action_touch(self):
        """Usage counter — the router orders skills by how often they help."""
        for record in self:
            record.write({
                "use_count": (record.use_count or 0) + 1,
                "last_used_at": fields.Datetime.now(),
            })
        return True

    # ---------------------------------------------------------------
    # validation — "drift is a failing build" (OKF)
    # ---------------------------------------------------------------
    def validate(self):
        """Return a list of problems. Empty means conformant.

        The rules are deliberately few and mechanical (OKF §11): a
        concept needs an id, a type and a body. Everything advisory
        lives in :meth:`lint`.
        """
        problems = []
        for record in self:
            if not record.code:
                problems.append((record.id, "code is required"))
            if not _SLUG_RE.match(record.code or ""):
                problems.append((
                    record.id,
                    "code %r must be kebab-case, start with a letter or "
                    "digit, and be at most 64 characters."
                    % record.code))
            if not record.kind:
                problems.append((record.id, "kind is required"))
            if not (record.body or "").strip():
                problems.append((record.id, "body is empty"))
            if record.description and len(record.description) > _DESC_MAX:
                problems.append((
                    record.id,
                    "description is %d characters; the router cuts it at "
                    "%d, so the tail is silently lost."
                    % (len(record.description), _DESC_MAX)))
            for field in ("generated_by", "verified_by"):
                actor = (record[field] or "").strip()
                if actor and not _ACTOR_RE.match(actor):
                    problems.append((
                        record.id,
                        "%s %r is not a valid actor: use "
                        "<producer>/<version>, human:<id> or process:<id>."
                        % (field, actor)))
        return problems

    def lint(self):
        """Advisory findings. Never blocks anything."""
        findings = []
        today = fields.Date.context_today(self)
        for record in self:
            if record.is_stale:
                findings.append((record.id, "warn", "stale"))
            if record.effective_status == "draft":
                findings.append((record.id, "info", "draft"))
            if not record.description:
                findings.append((record.id, "info", "no description"))
            if not record.generated_by:
                findings.append((record.id, "info", "no provenance"))
            if record.stale_after and record.stale_after < today:
                findings.append((record.id, "warn", "past stale_after"))
        return findings
