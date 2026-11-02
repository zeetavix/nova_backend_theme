# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Backfill branding metadata for logos uploaded before 20.0.1.0.13.

Older installs stored the raw upload with no measured metadata, so the
login page could not tell a wordmark from a square mark and squashed wide
logos into a 54x54 box. Re-run the same normalisation the model now does
on upload so existing assets behave exactly like new ones.
"""

import logging

from odoo.addons.nova_backend_theme.models.nova_theme_settings import (
    _as_bytes, _normalise_logo, _square_icon,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = env["res.company"].with_context(active_test=False).search([])
    fixed = 0
    for company in companies:
        vals = {}
        try:
            logo_raw = _as_bytes(company.nova_brand_logo)
            if logo_raw and not company.nova_brand_logo_meta:
                data, meta = _normalise_logo(logo_raw)
                vals["nova_brand_logo"] = data
                vals["nova_brand_logo_meta"] = meta
        except Exception as error:  # noqa: BLE001 — one bad row must not stop the rest
            _logger.warning("Nova: could not read logo of company %s: %s",
                            company.id, error)
        try:
            icon_raw = _as_bytes(company.nova_brand_favicon)
            if icon_raw and not company.nova_brand_favicon_meta:
                data, meta = _square_icon(icon_raw)
                vals["nova_brand_favicon"] = data
                vals["nova_brand_favicon_meta"] = meta
        except Exception as error:  # noqa: BLE001
            _logger.warning("Nova: could not read favicon of company %s: %s",
                            company.id, error)
        if vals:
            company.write(vals)
            fixed += 1
    if fixed:
        _logger.info("Nova: normalised branding assets for %s company(ies).",
                     fixed)
