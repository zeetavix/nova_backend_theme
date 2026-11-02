# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Serve the company's own branding assets.

The login page must show the client's identity, not the theme vendor's, so
these routes are public. When a company has not uploaded an asset we serve
the bundled default bytes directly instead of redirecting: a redirect would
add a round-trip to every anonymous page view and could not be cached.
"""
import base64
import binascii
import os

from odoo import http
from odoo.http import request

_DEFAULT_LOGO = "/nova_backend_theme/static/src/webclient/icons/nova_favicon.svg"
_DEFAULT_FAVICON = "/nova_backend_theme/static/src/webclient/icons/nova_favicon.svg"


def _sniff_mimetype(raw):
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if raw.startswith(b"GIF8"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if b"<svg" in raw[:512].lower():
        return "image/svg+xml"
    return "image/png"


def _default_bytes(url):
    """Read a bundled static file off disk (the public fallback asset)."""
    path = url.lstrip("/").split("?", 1)[0]
    try:
        module_path = os.path.dirname(os.path.dirname(__file__))
        relative = path.split("/", 1)[1] if "/" in path else path
        with open(os.path.join(module_path, relative), "rb") as handle:
            return handle.read()
    except OSError:
        return None


class NovaBranding(http.Controller):

    def _serve(self, field, fallback_url):
        company = request.env.company.sudo()
        data = company[field] if field in company._fields else False
        raw = None
        if data:
            # Odoo 20 binary fields are a lazy BinaryValue (``.content`` is
            # the decoded bytes), but legacy rows can still be base64 text.
            content = getattr(data, "content", None)
            if isinstance(content, (bytes, bytearray)):
                raw = bytes(content)
            elif isinstance(data, (bytes, bytearray)):
                raw = bytes(data)
            elif isinstance(data, str):
                try:
                    raw = base64.b64decode(data)
                except (binascii.Error, ValueError):
                    raw = None
        if not raw:
            raw = _default_bytes(fallback_url)
            mimetype = "image/svg+xml"
        else:
            mimetype = getattr(data, "mimetype", None) or _sniff_mimetype(raw)
        if not raw:
            # Nothing on disk either: fail honestly instead of an empty 200.
            return request.not_found()

        headers = [
            ("Content-Type", mimetype),
            ("Content-Length", str(len(raw))),
            # URLs are versioned with an upload digest, so the payload for a
            # given URL never changes.
            ("Cache-Control", "public, max-age=31536000, immutable"),
            ("X-Content-Type-Options", "nosniff"),
        ]
        if mimetype == "image/svg+xml":
            # An SVG opened directly is a document, not just an image.
            headers.append((
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            ))
        return request.make_response(raw, headers=headers)

    @http.route(
        "/nova_backend_theme/brand/logo", type="http", auth="public", cors="*",
    )
    def brand_logo(self, **kwargs):
        return self._serve("nova_brand_logo", _DEFAULT_LOGO)

    @http.route(
        "/nova_backend_theme/brand/favicon", type="http", auth="public", cors="*",
    )
    def brand_favicon(self, **kwargs):
        return self._serve("nova_brand_favicon", _DEFAULT_FAVICON)
