# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Company appearance + white-label branding.

Everything here is designed for real uploads, not ideal ones: a client
drops in a wide wordmark, a 3000px PNG, a JPEG on white, or a favicon
that is not square. The model normalises the asset, records what it
learned about it (aspect ratio, lightness), and the login page uses that
metadata to lay the logo out without ever squashing it.
"""

import base64
import binascii
import hashlib
import io
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Pillow resampling filters (named once so the module imports cleanly even
# on Pillow builds that moved the constants).
try:
    from PIL.Image import LANCZOS as _LANCZOS
    from PIL.Image import NEAREST as _NEAREST
except ImportError:  # pragma: no cover - very old Pillow
    _LANCZOS = _NEAREST = 1

# Default identity; every branding field falls back to it when unset.
NOVA_DEFAULT_BRAND = "Zeetavix"

# Upload guards. Anything bigger is refused with a readable message
# instead of silently bloating every page load.
NOVA_MAX_UPLOAD_BYTES = 4 * 1024 * 1024
NOVA_MAX_LOGO_EDGE = 1024
NOVA_ICON_SIZE = 256
NOVA_ICON_MAX_BYTES = 512 * 1024

# A logo wider than this is a wordmark: it replaces the brand text
# instead of sitting next to it.
NOVA_WORDMARK_RATIO = 2.0

NOVA_ACCENTS = [
    ("#0866FF", "Nova Blue"),
    ("#7C3AED", "Violet"),
    ("#0E7C8A", "Teal"),
    ("#0A7D2C", "Green"),
    ("#B42323", "Red"),
    ("#8A5700", "Amber"),
]

NOVA_DENSITIES = [
    ("comfortable", "Comfortable"),
    ("compact", "Compact"),
]

NOVA_RADII = [
    ("soft", "Soft"),
    ("default", "Default"),
    ("round", "Round"),
]

NOVA_RADIUS_FACTORS = {"soft": 0.5, "default": 1.0, "round": 1.5}

NOVA_SWITCHES = [
    ("inherit", "Inherit"),
    ("on", "On"),
    ("off", "Off"),
]


# ---------------------------------------------------------------------------
# colour + text helpers
# ---------------------------------------------------------------------------
def _clean_switch(value, company_value):
    if value == "on":
        return True
    if value == "off":
        return False
    return bool(company_value)


def _clean_accent(value):
    if isinstance(value, str) and HEX_RE.match(value):
        allowed = {key for key, _label in NOVA_ACCENTS}
        if value in allowed:
            return value
    return "#0E7C8A"


def _clean_choice(value, choices, fallback):
    allowed = {key for key, _label in choices}
    return value if value in allowed else fallback


def _hex_to_rgb(value):
    n = int(value[1:], 16)
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def _rgb_to_hex(r, g, b):
    return "#%02X%02X%02X" % (
        max(0, min(255, int(round(r)))),
        max(0, min(255, int(round(g)))),
        max(0, min(255, int(round(b)))),
    )


def _mix(first, second, weight):
    """Blend ``first`` over ``second`` (weight = share of ``first``)."""
    a, b = _hex_to_rgb(first), _hex_to_rgb(second)
    return _rgb_to_hex(
        a[0] * weight + b[0] * (1 - weight),
        a[1] * weight + b[1] * (1 - weight),
        a[2] * weight + b[2] * (1 - weight),
    )


def _channel_luminance(v):
    s = v / 255.0
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def _luminance(value):
    r, g, b = _hex_to_rgb(value)
    return (
        0.2126 * _channel_luminance(r)
        + 0.7152 * _channel_luminance(g)
        + 0.0722 * _channel_luminance(b)
    )


def _contrast(first, second):
    hi = max(_luminance(first), _luminance(second))
    lo = min(_luminance(first), _luminance(second))
    return round((hi + 0.05) / (lo + 0.05), 2)


def accent_tones(value):
    """Every tone the UI needs for one accent, computed once.

    The login page, the portal and the backend all read this, so a chosen
    colour can never look like three different colours in three places.
    ``fg`` is the readable text colour on top of the accent (WCAG AA when
    the accent allows it, black or white otherwise).
    """
    accent = value if isinstance(value, str) and HEX_RE.match(value) else "#0E7C8A"
    white_contrast = _contrast(accent, "#FFFFFF")
    black_contrast = _contrast(accent, "#000000")
    fg = "#FFFFFF" if white_contrast >= black_contrast else "#101214"
    return {
        "accent": accent,
        "hover": _mix("#FFFFFF", accent, 0.16),
        "active": _mix("#000000", accent, 0.22),
        "soft": _mix("#FFFFFF", accent, 0.12),
        "fg": fg,
        "contrast_on_white": white_contrast,
        "readable": white_contrast >= 3.0,
    }


# ---------------------------------------------------------------------------
# image helpers
# ---------------------------------------------------------------------------
def _as_bytes(value):
    """BinaryValue / base64 str / raw bytes -> bytes (None when unreadable)."""
    if not value:
        return None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    if isinstance(value, str):
        try:
            return base64.b64decode(value)
        except (binascii.Error, ValueError):
            return None
    return None


def _is_cleared(value):
    """True only for an explicit clear, never for a value we cannot read.

    The distinction matters: a client sending a byte-count (``bin_size``) or
    some other non-binary payload must fail loudly, not silently wipe the
    company's logo.
    """
    if value is False or value is None:
        return True
    if isinstance(value, (bytes, bytearray)):
        return len(value) == 0
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _encode_png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _open_image(raw):
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
        return image.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError(
            "That file is not an image Nova can read. "
            "Use a PNG, JPEG, WEBP, GIF, ICO or SVG file."
        ) from None


def _is_svg(raw):
    return b"<svg" in raw[:2048].lower()


def _svg_meta(raw):
    """Aspect ratio + lightness for a vector mark, without rasterising.

    Designers hand over SVG, and we cannot render it server-side without an
    extra dependency, so read the geometry from the root element and average
    the explicit fills to decide whether the mark needs a light plate.
    """
    text = raw[:400000].decode("utf-8", "ignore")
    width = height = 0.0
    match = re.search(
        r'viewBox\s*=\s*["\']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)',
        text,
    )
    if match:
        width, height = float(match.group(1)), float(match.group(2))
    else:
        w_match = re.search(r'\bwidth\s*=\s*["\']([\d.]+)', text)
        h_match = re.search(r'\bheight\s*=\s*["\']([\d.]+)', text)
        if w_match and h_match:
            width, height = float(w_match.group(1)), float(h_match.group(1))
    ratio = round(width / height, 3) if width and height else 1.0
    colours = re.findall(
        r'(?:fill|stroke)\s*[:=]\s*["\']?#([0-9A-Fa-f]{6})', text)
    light = False
    if colours:
        average = sum(
            _luminance("#" + value.upper()) for value in colours
        ) / len(colours)
        light = average >= 0.5
    return ratio, light


def _describe_logo(image):
    """Aspect ratio + whether the mark is light enough for a dark panel."""
    width, height = image.size
    ratio = round(width / float(height), 3) if height else 1.0
    sample = image.copy()
    sample.thumbnail((64, 64))
    pixels = [
        px for px in sample.getdata()
        if len(px) < 4 or px[3] > 24
    ]
    if not pixels:
        return ratio, False, False
    # Average perceived brightness over the visible pixels only.
    total = sum(
        0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2] for px in pixels
    )
    average = total / (len(pixels) * 255.0)
    return ratio, average >= 0.62, any(
        len(px) >= 4 and px[3] < 250 for px in pixels)


def _normalise_logo(raw):
    """Downscale an oversized logo, keep transparency, describe it."""
    if _is_svg(raw):
        ratio, light = _svg_meta(raw)
        data = base64.b64encode(raw).decode("ascii")
        return data, {
            "w": 0,
            "h": 0,
            "ratio": ratio,
            "light": light,
            "alpha": True,
            "wordmark": ratio >= NOVA_WORDMARK_RATIO,
            "svg": True,
            "v": hashlib.md5(data.encode("ascii")).hexdigest()[:12],
        }
    image = _open_image(raw)
    width, height = image.size
    if max(width, height) > NOVA_MAX_LOGO_EDGE:
        image.thumbnail((NOVA_MAX_LOGO_EDGE, NOVA_MAX_LOGO_EDGE))
        width, height = image.size
    ratio, light, has_alpha = _describe_logo(image)
    data = _encode_png(image)
    digest = hashlib.md5(data.encode("ascii")).hexdigest()[:12]
    return data, {
        "w": width,
        "h": height,
        "ratio": ratio,
        "light": light,
        "alpha": has_alpha,
        "wordmark": ratio >= NOVA_WORDMARK_RATIO,
        "v": digest,
    }


def _square_icon(raw):
    """Any image -> a centred square PNG, so tabs never squash it."""
    if _is_svg(raw):
        # Vectors stay vectors: a browser scales an SVG icon perfectly.
        data = base64.b64encode(raw).decode("ascii")
        return data, {
            "size": 0,
            "svg": True,
            "v": hashlib.md5(data.encode("ascii")).hexdigest()[:12],
        }
    image = _open_image(raw)
    # Scale to fill the square in both directions: a 16x16 .ico must grow
    # (otherwise it sits in a 16px speck inside a 256px transparent canvas
    # and the tab looks empty).
    widest = max(image.size) or 1
    scale = NOVA_ICON_SIZE / float(widest)
    if scale < 1.0:
        image.thumbnail((NOVA_ICON_SIZE, NOVA_ICON_SIZE), _LANCZOS)
    elif scale > 1.0:
        image = image.resize(
            (max(1, int(round(image.size[0] * scale))),
             max(1, int(round(image.size[1] * scale)))),
            _NEAREST,
        )
    from PIL import Image as _Image

    canvas = _Image.new("RGBA", (NOVA_ICON_SIZE, NOVA_ICON_SIZE), (0, 0, 0, 0))
    canvas.paste(
        image,
        ((NOVA_ICON_SIZE - image.size[0]) // 2,
         (NOVA_ICON_SIZE - image.size[1]) // 2),
        image,
    )
    data = _encode_png(canvas)
    raw_size = len(base64.b64decode(data))
    if raw_size > NOVA_ICON_MAX_BYTES:
        raise ValidationError(
            "That favicon is too heavy once converted (%d KB). "
            "Use a simpler image under %d KB."
            % (raw_size // 1024, NOVA_ICON_MAX_BYTES // 1024)
        )
    digest = hashlib.md5(data.encode("ascii")).hexdigest()[:12]
    return data, {"size": NOVA_ICON_SIZE, "v": digest}


class ResCompany(models.Model):
    _inherit = "res.company"

    nova_accent = fields.Selection(
        NOVA_ACCENTS, string="Nova Accent", default="#0E7C8A", required=True,
    )
    nova_accent_hex = fields.Char(
        string="Custom accent", size=7,
        help="Pick any brand colour (#RRGGBB). When set, it wins over the "
             "preset above.",
    )
    nova_density = fields.Selection(
        NOVA_DENSITIES, string="Nova Density", default="comfortable", required=True,
    )
    nova_radius = fields.Selection(
        NOVA_RADII, string="Nova Corners", default="default", required=True,
    )
    nova_colspot = fields.Boolean(
        string="Nova Column Spotlight", default=False,
    )
    # -- White-label branding -------------------------------------------
    # The theme ships with Zeetavix as the default identity, but a client
    # must be able to put their own name, logo and favicon on the login
    # page, the browser tab and the document title. Empty values fall back
    # to those defaults, so nothing breaks when only one asset is set.
    nova_brand_name = fields.Char(
        string="Brand name", default=NOVA_DEFAULT_BRAND,
        help="Shown on the login page, in the browser tab and in the "
             "document title suffix. Leave empty to use the default.",
    )
    nova_brand_email = fields.Char(
        string="Contact email",
        help="Shown on the login page and used by 'contact support' links. "
             "Leave empty to use the company email.",
    )
    nova_brand_logo = fields.Binary(
        string="Brand logo", attachment=True,
        help="Login logo. PNG, JPEG, WEBP or SVG. Wide wordmarks are placed "
             "as-is (no squashing); square marks sit next to the brand name.",
    )
    nova_brand_logo_meta = fields.Json(
        string="Logo metadata", readonly=True, copy=False,
        help="Measured on upload: size, aspect ratio, lightness. Drives the "
             "layout so any logo stays legible.",
    )
    nova_brand_favicon = fields.Binary(
        string="Favicon", attachment=True,
        help="Browser tab icon. Any image is accepted and squared "
             "automatically; 256x256 is generated for you.",
    )
    nova_brand_favicon_meta = fields.Json(
        string="Favicon metadata", readonly=True, copy=False,
    )

    # -------------------------------------------------------------- hooks
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._nova_prepare_assets(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._nova_prepare_assets(vals, records=self)
        return super().write(vals)

    @api.model
    def _nova_prepare_assets(self, vals, records=None):
        """Normalise uploaded branding assets before they are stored.

        Settings forms re-send every field on save, so an unchanged binary
        must not be re-encoded (that would churn the cache-busting digest
        and burn CPU on every unrelated settings save).
        """
        current = None
        if records is not None and len(records) == 1:
            current = records
        if "nova_brand_logo" in vals:
            value = vals.get("nova_brand_logo")
            if _is_cleared(value):
                vals["nova_brand_logo"] = False
                vals["nova_brand_logo_meta"] = False
            else:
                raw = _as_bytes(value)
                if raw is None:
                    raise ValidationError(
                        "Nova could not read the logo you uploaded. "
                        "Please retry with a PNG, JPEG, WEBP, GIF, ICO or SVG."
                    )
                if current is not None and _as_bytes(
                        current.nova_brand_logo) == raw:
                    vals.pop("nova_brand_logo", None)
                else:
                    if len(raw) > NOVA_MAX_UPLOAD_BYTES:
                        raise ValidationError(
                            "That logo is %d MB. Please upload something under "
                            "%d MB." % (
                                len(raw) // (1024 * 1024),
                                NOVA_MAX_UPLOAD_BYTES // (1024 * 1024))
                        )
                    data, meta = _normalise_logo(raw)
                    vals["nova_brand_logo"] = data
                    vals["nova_brand_logo_meta"] = meta
        if "nova_brand_favicon" in vals:
            value = vals.get("nova_brand_favicon")
            if _is_cleared(value):
                vals["nova_brand_favicon"] = False
                vals["nova_brand_favicon_meta"] = False
            else:
                raw = _as_bytes(value)
                if raw is None:
                    raise ValidationError(
                        "Nova could not read the favicon you uploaded. "
                        "Please retry with a PNG, JPEG, WEBP, GIF, ICO or SVG."
                    )
                if current is not None and _as_bytes(
                        current.nova_brand_favicon) == raw:
                    vals.pop("nova_brand_favicon", None)
                else:
                    if len(raw) > NOVA_MAX_UPLOAD_BYTES:
                        raise ValidationError(
                            "That favicon is %d MB. Please upload something "
                            "under %d MB." % (
                                len(raw) // (1024 * 1024),
                                NOVA_MAX_UPLOAD_BYTES // (1024 * 1024))
                        )
                    data, meta = _square_icon(raw)
                    vals["nova_brand_favicon"] = data
                    vals["nova_brand_favicon_meta"] = meta

    @api.constrains("nova_accent_hex")
    def _check_accent_hex(self):
        for company in self:
            value = (company.nova_accent_hex or "").strip()
            if value and not HEX_RE.match(value):
                raise ValidationError(
                    "Custom accent must be a hex colour like #0E7C8A."
                )

    @api.constrains("nova_brand_email")
    def _check_brand_email(self):
        for company in self:
            value = (company.nova_brand_email or "").strip()
            if value and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
                raise ValidationError(
                    "Contact email is not a valid address: %s" % value
                )

    # ------------------------------------------------------------ payload
    def _nova_accent(self):
        """The effective accent: custom hex first, then the preset."""
        self.ensure_one()
        custom = (self.nova_accent_hex or "").strip()
        if custom and HEX_RE.match(custom):
            return custom.upper()
        return _clean_accent(self.nova_accent)

    def _nova_branding(self):
        """Brand identity for templates and the web client (public-safe).

        Read as sudo because the login page renders for the public user,
        which must still see the company's own branding.
        """
        company = self.sudo()[:1]
        if not company:
            return {
                "name": NOVA_DEFAULT_BRAND,
                "email": "",
                "has_logo": False,
                "has_favicon": False,
                "logo_url": "/nova_backend_theme/brand/logo",
                "favicon_url": "/nova_backend_theme/brand/favicon",
                "logo_wordmark": False,
                "logo_light": False,
                "logo_ratio": 1.0,
                "accent": accent_tones("#0E7C8A"),
            }
        name = (company.nova_brand_name or "").strip() or NOVA_DEFAULT_BRAND
        email = (company.nova_brand_email or "").strip()
        if not email:
            try:
                email = (company.email or "").strip()
            except Exception:
                email = ""
        logo_meta = company.nova_brand_logo_meta or {}
        favicon_meta = company.nova_brand_favicon_meta or {}
        has_logo = bool(company.nova_brand_logo)
        has_favicon = bool(company.nova_brand_favicon)
        # Cache-busting: the assets are cached hard, so a changed logo must
        # change the URL. The digest is stored at upload time, so rendering
        # a page never has to read the binary just to build a URL.
        logo_v = logo_meta.get("v") or "0"
        favicon_v = favicon_meta.get("v") or "0"
        try:
            logo_ratio = float(logo_meta.get("ratio") or 1.0)
        except (TypeError, ValueError):
            logo_ratio = 1.0
        return {
            "name": name,
            "email": email,
            "has_logo": has_logo,
            "has_favicon": has_favicon,
            "logo_url": "/nova_backend_theme/brand/logo?v=%s" % logo_v,
            "favicon_url": "/nova_backend_theme/brand/favicon?v=%s" % favicon_v,
            "logo_wordmark": bool(logo_meta.get("wordmark")) if logo_meta
            else logo_ratio >= NOVA_WORDMARK_RATIO,
            "logo_light": bool(logo_meta.get("light")),
            "logo_ratio": logo_ratio,
            "accent": accent_tones(company._nova_accent()),
        }


class ResUsers(models.Model):
    _inherit = "res.users"
    # Empty = inherit the company default (clearing the field rolls back).
    nova_accent = fields.Selection(NOVA_ACCENTS, string="Nova Accent")
    nova_density = fields.Selection(NOVA_DENSITIES, string="Nova Density")
    nova_radius = fields.Selection(NOVA_RADII, string="Nova Corners")
    nova_colspot = fields.Selection(
        NOVA_SWITCHES, string="Nova Column Spotlight", default="inherit",
    )
    nova_dnd_sticky_only = fields.Boolean(
        string="Nova Do Not Disturb (sticky only)", default=False,
    )
    nova_dnd_bypass_danger = fields.Boolean(
        string="Nova DND Bypass (danger)", default=True,
    )
    nova_dnd_bypass_warning = fields.Boolean(
        string="Nova DND Bypass (warning)", default=False,
    )
    nova_quiet_start = fields.Float(
        string="Nova Quiet From (hour)", default=22.0,
    )
    nova_quiet_end = fields.Float(
        string="Nova Quiet Until (hour)", default=7.0,
    )

    def _nova_effective_theme(self):
        self.ensure_one()
        try:
            company = self.env.company
        except Exception:
            company = self.company_id
        accent = (self.nova_accent
                  or (company._nova_accent() if company else None))
        # accent_tones validates the value itself; passing it through
        # _clean_accent (which only knows the six presets) would silently
        # drop a custom brand hex back to teal.
        tones = accent_tones(accent)
        return {
            "accent": tones["accent"],
            "accent_hover": tones["hover"],
            "accent_active": tones["active"],
            "accent_soft": tones["soft"],
            "accent_fg": tones["fg"],
            "density": _clean_choice(
                self.nova_density or (company.nova_density if company else None),
                NOVA_DENSITIES,
                "comfortable",
            ),
            "radius": _clean_choice(
                self.nova_radius or (company.nova_radius if company else None),
                NOVA_RADII,
                "default",
            ),
            "colspot": _clean_switch(
                self.nova_colspot,
                company.nova_colspot if company else False,
            ),
        }


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nova_accent = fields.Selection(
        string="Accent color", related="company_id.nova_accent", readonly=False,
    )
    nova_accent_hex = fields.Char(
        string="Custom accent", related="company_id.nova_accent_hex",
        readonly=False,
    )
    nova_density = fields.Selection(
        string="Density", related="company_id.nova_density", readonly=False,
    )
    nova_radius = fields.Selection(
        string="Corners", related="company_id.nova_radius", readonly=False,
    )
    nova_colspot = fields.Boolean(
        string="Column spotlight", related="company_id.nova_colspot", readonly=False,
    )
    nova_brand_name = fields.Char(
        string="Brand name", related="company_id.nova_brand_name", readonly=False,
    )
    nova_brand_email = fields.Char(
        string="Contact email", related="company_id.nova_brand_email",
        readonly=False,
    )
    # Plain binaries with get_values/set_values, not related: Odoo's settings
    # form does not fetch related binary content, so the image widget would
    # show an empty box for a logo that is actually live.
    nova_brand_logo = fields.Binary(string="Brand logo")
    nova_brand_favicon = fields.Binary(string="Favicon")

    @api.model
    def get_values(self):
        res = super().get_values()
        company = self.env.company.sudo()
        res.update({
            "nova_brand_logo": company.nova_brand_logo or False,
            "nova_brand_favicon": company.nova_brand_favicon or False,
        })
        return res

    def set_values(self):
        res = super().set_values()
        company = self.env.company
        vals = {}
        try:
            for field in ("nova_brand_logo", "nova_brand_favicon"):
                value = self[field] if field in self._fields else False
                # Only ever WRITE an asset, never clear one from here: any
                # settings save that does not carry the binaries (a different
                # app section, an RPC call, a module's execute()) would
                # otherwise wipe the company's logo. Clearing is done on the
                # company record itself.
                if value:
                    vals[field] = value
        except Exception:
            vals = {}
        if vals:
            company.write(vals)
        return res
