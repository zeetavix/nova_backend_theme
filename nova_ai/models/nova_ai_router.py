# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Model routing — pick the right model for the job, not the first one.

Ported from Javis OS' capability router and AnythingLLM's dynamic model
routing, and reduced to the part that actually matters in a business
backend: some jobs need a capability other models do not have (a bill
scan needs vision), and when several models can do the job the cheap
and reliable one should win.

The rule the reference earns and we keep: **never switch model to save
money if the switch costs a capability the step needs.** Hard-filter
first, rank second.
"""

from odoo import _, api, fields, models

# Capability flags a model may declare. Kept small and honest: a flag we
# cannot verify is a flag we must not rely on.
CAPABILITIES = [
    ("tools", "Tool calling"),
    ("vision", "Reads images"),
    ("reasoning", "Long reasoning"),
    ("json", "Strict JSON output"),
    ("embeddings", "Embeddings"),
]


class NovaAiRouter(models.AbstractModel):
    _name = "nova.ai.router"
    _description = "Nova AI Model Router"

    @api.model
    def capabilities_of(self, model):
        """The capability set of one model, as a set of strings."""
        caps = set()
        for name, _label in CAPABILITIES:
            if name == "tools" and model.supports_tools:
                caps.add(name)
            elif name == "vision" and model.supports_vision:
                caps.add(name)
            elif name == "json" and model.supports_json:
                caps.add(name)
            elif name == "embeddings" and model.supports_embeddings:
                caps.add(name)
            elif name == "reasoning" and model.supports_reasoning:
                caps.add(name)
        return caps

    @api.model
    def pick(self, env, requires=None, tier=None, prefer=None,
             exclude_ids=None, data_class=None):
        """Choose a model for a step.

        ``requires`` is a set of capability names the step cannot work
        without. ``tier`` narrows to one capability tier. ``prefer`` is
        the agent's own model: it wins whenever it can do the job, so
        routing never silently changes the voice of an agent.

        Returns a model record, or an empty recordset.
        """
        requires = set(requires or ())
        exclude = set(exclude_ids or ())
        domain = [("active", "=", True)]
        if tier:
            domain.append(("tier", "=", tier))
        candidates = env["nova.ai.model"].search(domain)
        scored = []
        for candidate in candidates:
            if candidate.id in exclude:
                continue
            caps = self.capabilities_of(candidate)
            missing = sorted(requires - caps)
            if missing:
                # Hard filter: a capability the step needs is never
                # traded away for a cheaper model.
                continue
            if prefer and candidate.id == prefer.id:
                # The agent's own model wins whenever it can do the job.
                # It is a separate key, not a score: a cost score can be
                # negative, and a negative score would beat a plain 0.
                scored.append((0, 0.0, candidate.id, candidate))
                continue
            score = (
                # Reliability leads: a cheap model that fumbles the
                # call costs more than a dear one that lands it.
                (candidate.cost_in_per_mtok or 0.0) * 0.0001
                + (candidate.cost_out_per_mtok or 0.0) * 0.0003
                - (candidate.reliability or 0.5) * 0.01
                + (candidate.latency_hint_ms or 0) * 0.0000001
            )
            scored.append((1, score, candidate.id, candidate))
        if not scored:
            return env["nova.ai.model"].browse()
        # Preferred first, then the cheapest/most reliable, then by id
        # so the choice is stable.
        scored.sort(key=lambda item: (item[0], item[1], item[2]))
        return scored[0][3]

    @api.model
    def route_for_tool(self, env, agent, tool_code):
        """The model that should run a given tool for an agent.

        The agent's own model wins unless the tool needs a capability
        it lacks (a scan needs vision) — then the cheapest model with
        that capability takes the single call.
        """
        prefer = agent.model_id if agent else None
        requires = set()
        if tool_code in ("digitize_bill", "read_attachment"):
            requires.add("vision")
        if tool_code in ("search_records", "read_record", "create_draft",
                          "set_field", "fields_get", "inspect_method",
                          "read_group", "count_records",
                          "search_knowledge", "read_knowledge",
                          "fetch_url", "build_domain", "list_models",
                          "validate_write", "scan_codebase"):
            # Structured arguments: a model that cannot call tools
            # (T1) cannot run these, even though the text-call
            # fallback exists — it still needs the tool loop.
            requires.add("tools")
        chosen = self.pick(
            env, requires=requires, tier=agent.tier if agent else None,
            prefer=prefer)
        if chosen:
            return chosen
        return prefer or env["nova.ai.model"].browse()

    @api.model
    def explain(self, env, requires=None, tier=None):
        """Why a model was or was not chosen. Used by the settings UI."""
        requires = set(requires or ())
        rows = []
        domain = [("active", "=", True)]
        if tier:
            # Show the same candidate set pick() chooses from, so the
            # Studio table never lists an ineligible tier as eligible.
            domain.append(("tier", "=", tier))
        for candidate in env["nova.ai.model"].search(domain):
            caps = self.capabilities_of(candidate)
            missing = sorted(requires - caps)
            rows.append({
                "id": candidate.id,
                "name": candidate.name,
                "tier": candidate.tier,
                "capabilities": sorted(caps),
                "missing": missing,
                "eligible": not missing,
                "cost_in": candidate.cost_in_per_mtok,
                "cost_out": candidate.cost_out_per_mtok,
                "reliability": candidate.reliability,
            })
        return rows
