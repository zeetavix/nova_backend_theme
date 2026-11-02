# Nova Split View

Productivity split view: list on the left, form on the right

Nova Split View, clean-room productivity view for Odoo Community (OPL-1).

Any window action that already offers both a list and a form view
automatically gains a third view-switcher entry, Split. The list stays
on the left; clicking a row opens the record in a form panel docked on
the right, without leaving the list. Creating, saving, deleting and
paging all work in place, and the panel is resizable.

* Zero per-model work: eligibility is computed when the action loads,
  so every existing list+form screen (Sales, Contacts, Projects, ...)
  offers Split with no XML or code changes.
* First-class view type: ``split`` is registered on ``ir.ui.view`` and
  ``ir.actions.act_window.view`` exactly like ``grid`` in Nova Grid, so
  Studio and developers may also add it to ``view_mode`` explicitly.
* A per-model ``<split>`` view record stays optional; without one the
  server serves a minimal arch and the client reuses the action's own
  list and form views.
* Opt out per action with ``nova_split_disable`` in the action context,
  or globally in Settings.

No Enterprise code is copied. All Python + OWL is original.

| | |
|---|---|
| Odoo | 20.0 Community |
| Depends | `web` |
| License | OPL-1 (Odoo Proprietary License v1.0) |
| Price | **Free** |

No Enterprise code is copied or required.

## Installation

1. Copy the addon folder into your addons path.
2. Update the apps list (`-u base` or the Apps menu).
3. Install **Nova Split View**.

## Support

support@zeetavix.com — https://zeetavix.com
