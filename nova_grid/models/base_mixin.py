# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Model-agnostic grid helpers installed on ``base``.

Every model automatically gains the Nova Grid server API, so a new grid
needs only XML — no Python — unless custom write semantics are required:

* :meth:`nova_grid_fetch` — one-RPC matrix fetch (read_group wrapper that
  returns name-keyed dicts regardless of ORM version).
* :meth:`nova_grid_update_cell` — apply an inline cell delta.
* :meth:`nova_grid_unavailability` — hook for greyed-out columns.
* :meth:`nova_grid_copy_previous` — previous-period row skeletons (pure,
  no writes — the client renders them as addable rows).
* ``grid_update_cell`` / ``grid_unavailability`` aliases keep
  Enterprise-authored views working unchanged.
"""
from __future__ import annotations

from odoo import api, models


def _first(value):
    """Unwrap a ``(id, name)`` / ``[id, name]`` pair coming from the web client."""
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


class Base(models.AbstractModel):
    _inherit = "base"

    # ------------------------------------------------------------------
    # Normalisation helpers (Odoo 20: read_group -> tuples; client sends pairs)
    # ------------------------------------------------------------------
    @api.model
    def _nova_normalize_groups(self, groups, groupby, aggregates):
        """Map positional ``read_group`` tuples to name-keyed dicts."""
        names = list(groupby) + list(aggregates)
        out = []
        for group in groups:
            if isinstance(group, dict):
                out.append(group)
            else:
                out.append(dict(zip(names, list(group))))
        return out

    @api.model
    def _nova_normalize_domain(self, domain):
        """Flatten ``(id, name)`` pair values so ``search`` matches.

        The grid client builds row domains from formatted group values,
        which are ``[id, display_name]`` pairs for many2one fields. The
        ORM cannot compare those directly, so unwrap them to ids.
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
    def _nova_normalize_vals(self, vals):
        """Unwrap relational ``(id, name)`` pairs to plain ids for create/write."""
        vals = dict(vals or {})
        for fname, value in list(vals.items()):
            if fname in self._fields and self._fields[fname].type == "many2one":
                vals[fname] = _first(value)
        return vals

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    @api.model
    def nova_grid_fetch(self, domain, groupby, aggregates):
        """One-RPC matrix fetch (read_group wrapper).

        :returns: list of dicts keyed by groupby/aggregate spec.
        """
        groups = self.read_group(self._nova_normalize_domain(domain), groupby, aggregates)
        return self._nova_normalize_groups(groups, groupby, aggregates)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    @api.model
    def nova_grid_update_cell(self, domain, measure_field_name, delta, default_values=None):
        """Apply an inline grid edit.

        Generic fallback semantics (models override for exact behaviour):

        * ``delta == 0`` → no-op.
        * existing records in ``domain`` → add ``delta`` to the first
          record's measure (clamped at 0; placeholder lines carrying no
          description are removed when they reach 0).
        * no records and ``delta > 0`` → create one record from
          ``default_values`` + domain defaults with ``measure = delta``.

        :returns: falsy (an action dict may be returned by overrides to
            open something client-side, mirroring Enterprise behaviour).
        """
        try:
            delta = float(delta or 0.0)
        except (TypeError, ValueError):
            return False
        if not delta:
            return False
        domain = self._nova_normalize_domain(domain)
        default_values = self._nova_normalize_vals(default_values)
        records = self.search(domain or [])
        if records:
            first = records[0]
            new_value = max(0.0, float(first[measure_field_name] or 0.0) + float(delta))
            first.write({measure_field_name: new_value})
            if new_value == 0 and "name" in first._fields and first.name in ("/", False, None):
                first.unlink()
            return False
        if delta <= 0:
            return False
        vals = {}
        for leaf in domain:
            if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
                fname, op, value = leaf
                if op == "=" and fname not in vals:
                    vals[fname] = value
        vals.update(default_values)
        vals[measure_field_name] = float(delta)
        vals = self._nova_normalize_vals(vals)
        self.create(vals)
        return False

    # Backwards / Enterprise-compatible alias.
    @api.model
    def grid_update_cell(self, domain, measure_field_name, value):
        return self.nova_grid_update_cell(domain, measure_field_name, value)

    # ------------------------------------------------------------------
    # Unavailability (greyed columns / days off)
    # ------------------------------------------------------------------
    @api.model
    def nova_grid_unavailability(self, start_date, end_date, groupby="", res_ids=None):
        """Return ``{res_id: [dates]}`` of unavailable columns.

        Base implementation reports nothing unavailable; HR-flavoured
        modules override this (weekends, leaves, ...).
        """
        return {}

    @api.model
    def grid_unavailability(self, start_date, end_date, groupby="", res_ids=()):
        return self.nova_grid_unavailability(start_date, end_date, groupby, list(res_ids or []))

    # ------------------------------------------------------------------
    # Copy previous period (pure: no writes)
    # ------------------------------------------------------------------
    @api.model
    def nova_grid_copy_previous(self, domain, date_field, period_start, period_end, prev_start, prev_end, row_fields):
        """Return previous-period row skeletons for the current period.

        Pure function — creates nothing. The client renders the returned
        rows as addable (zero) rows; saving happens through
        :meth:`nova_grid_update_cell` when the user types a value.

        :returns: ``{"rows": [{"values": {...}, "domain": [...]}], ...}``.
        """
        domain = self._nova_normalize_domain(domain)
        raw = self.read_group(
            domain + [(date_field, ">=", prev_start), (date_field, "<", prev_end)],
            row_fields,
            ["id:array_agg"],
        )
        groups = self._nova_normalize_groups(raw, row_fields, ["id:array_agg"])
        rows = []
        seen = set()
        for group in groups:
            values = {f: group.get(f) for f in row_fields if f in group}
            sig = tuple(_first(v) for v in values.values())
            if sig in seen:
                continue
            seen.add(sig)
            row_domain = [[f, "=", _first(v)] for f, v in values.items()]
            rows.append({"values": values, "domain": row_domain})
        return {"rows": rows, "period_start": period_start, "period_end": period_end}
