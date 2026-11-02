# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Model-agnostic Gantt helpers installed on ``base``.

Every model automatically gains the Nova Gantt server API, so a new Gantt
needs only XML — no Python — unless custom date semantics are required:

* :meth:`nova_gantt_fetch` — one-RPC record fetch (search_read wrapper that
  normalises relational values to plain ids regardless of ORM version).
* :meth:`nova_gantt_update_dates` — move/resize a bar: writes the new start
  and stop dates, clamping stop at start.

Helper names are gantt-prefixed so this mixin coexists with the Nova Grid
mixin on the same ``base`` model without collisions.
"""
from __future__ import annotations

from odoo import api, fields, models
from odoo.exceptions import UserError


def _first(value):
    """Unwrap a ``(id, name)`` / ``[id, name]`` pair from the web client."""
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


class Base(models.AbstractModel):
    _inherit = "base"

    # ------------------------------------------------------------------
    # Normalisation helpers
    # ------------------------------------------------------------------
    @api.model
    def _nova_gantt_normalize_domain(self, domain):
        """Flatten ``(id, name)`` pair values so ``search`` matches.

        The Gantt client builds lane domains from formatted group values,
        which are ``[id, display_name]`` pairs for many2one fields. The ORM
        cannot compare those directly, so unwrap them to ids.
        """
        clean = []
        for leaf in domain or []:
            if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
                fname, op, value = leaf
                if op == "=":
                    value = _first(value)
                clean.append([fname, op, value])
            else:
                clean.append(leaf)
        return clean

    @api.model
    def _nova_gantt_normalize_vals(self, vals):
        """Unwrap relational ``(id, name)`` pairs to plain ids for write."""
        vals = dict(vals or {})
        for fname, value in list(vals.items()):
            if fname in self._fields and self._fields[fname].type == "many2one":
                vals[fname] = _first(value)
        return vals

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    @api.model
    def nova_gantt_fetch(self, domain, fields_list):
        """One-RPC record fetch for Gantt bars.

        Returns raw ``search_read`` records (many2one values stay as
        ``[id, display_name]`` pairs, like the grid engine expects, so lane
        titles keep their labels). Only the *domain* is normalised.
        """
        return self.search_read(
            self._nova_gantt_normalize_domain(domain or []), fields_list or ["display_name"]
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    @api.model
    def nova_gantt_update_dates(self, domain, date_start_field, date_stop_field, new_start, new_stop):
        """Move or resize a Gantt bar.

        Finds the records in ``domain`` (normally exactly one bar) and writes
        the new start/stop dates. Stop is clamped at start — dragging never
        produces an inverted bar; callers that need refusal semantics
        override this method.

        :returns: falsy on success (an action dict may be returned by
            overrides to open something client-side).
        """
        domain = self._nova_gantt_normalize_domain(domain or [])
        records = self.search(domain)
        if not records:
            return False
        if not new_start:
            raise UserError(self.env._("A bar needs a start date."))
        stop = new_stop or new_start
        if str(stop) < str(new_start):
            stop = new_start
        vals = {}
        if date_start_field:
            vals[date_start_field] = new_start
        if date_stop_field:
            vals[date_stop_field] = stop
        records.write(self._nova_gantt_normalize_vals(vals))
        return False
