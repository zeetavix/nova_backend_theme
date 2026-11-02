# Part of Nova. See LICENSE file for full copyright and licensing details.

import base64
import io

from PIL import Image, ImageDraw

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nova_backend_theme.models.nova_theme_settings import (
    _as_bytes,
    _is_cleared,
    _normalise_logo,
    _square_icon,
    _svg_meta,
    accent_tones,
)


def _png_base64(width, height, colour=(14, 124, 138, 255), transparent=False):
    background = (255, 255, 255, 0) if transparent else colour
    image = Image.new("RGBA", (width, height), background)
    if transparent:
        draw = ImageDraw.Draw(image)
        draw.rectangle(
            [0, 0, max(1, width // 2), max(1, height // 2)], fill=colour)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@tagged("post_install", "-at_install")
class TestNovaBranding(TransactionCase):

    def setUp(self):
        super().setUp()
        # A scratch company: these tests must never write to the live one.
        self.company = self.env["res.company"].create({
            "name": "Nova Branding Test Co",
        })

    # ---------------------------------------------------------- helpers
    def test_accent_tones_dark_colour_gets_white_text(self):
        tones = accent_tones("#0E7C8A")
        self.assertEqual(tones["accent"], "#0E7C8A")
        self.assertEqual(tones["fg"], "#FFFFFF")
        self.assertTrue(tones["readable"])

    def test_accent_tones_light_colour_gets_dark_text(self):
        tones = accent_tones("#FFD400")
        self.assertEqual(tones["fg"], "#101214")
        self.assertFalse(tones["readable"])

    def test_accent_tones_fall_back_on_garbage(self):
        self.assertEqual(accent_tones("not-a-colour")["accent"], "#0E7C8A")
        self.assertEqual(accent_tones(None)["accent"], "#0E7C8A")

    def test_clear_vs_unreadable(self):
        self.assertTrue(_is_cleared(False))
        self.assertTrue(_is_cleared(None))
        self.assertTrue(_is_cleared(""))
        self.assertTrue(_is_cleared(b""))
        # A byte count (the bin_size shape) is not a clear request.
        self.assertFalse(_is_cleared(1200))
        self.assertFalse(_is_cleared({"size": 1200}))
        self.assertIsNone(_as_bytes(1200))

    # ------------------------------------------------------------ logos
    def test_wide_logo_is_a_wordmark(self):
        data, meta = _normalise_logo(
            base64.b64decode(_png_base64(900, 180)))
        self.assertTrue(meta["wordmark"])
        self.assertAlmostEqual(meta["ratio"], 5.0, places=1)
        self.assertIn("v", meta)
        self.assertTrue(data)

    def test_square_logo_is_not_a_wordmark(self):
        _data, meta = _normalise_logo(
            base64.b64decode(_png_base64(256, 256)))
        self.assertFalse(meta["wordmark"])
        self.assertAlmostEqual(meta["ratio"], 1.0, places=1)

    def test_oversized_logo_is_downscaled(self):
        _data, meta = _normalise_logo(
            base64.b64decode(_png_base64(3200, 1400)))
        self.assertLessEqual(max(meta["w"], meta["h"]), 1024)

    def test_light_logo_is_detected(self):
        _data, meta = _normalise_logo(base64.b64decode(
            _png_base64(200, 200, colour=(255, 255, 255, 255))))
        self.assertTrue(meta["light"])

    def test_dark_logo_is_detected(self):
        _data, meta = _normalise_logo(base64.b64decode(
            _png_base64(200, 200, colour=(20, 20, 20, 255))))
        self.assertFalse(meta["light"])

    def test_svg_logo_keeps_vector_and_reads_geometry(self):
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="371" height="74" '
            b'viewBox="0 0 371 74"><path fill="#1A1A1A" d="M0 0h10v10H0z"/></svg>'
        )
        _data, meta = _normalise_logo(svg)
        self.assertTrue(meta["svg"])
        self.assertTrue(meta["wordmark"])
        self.assertAlmostEqual(meta["ratio"], 5.014, places=2)
        self.assertFalse(meta["light"])

    def test_svg_meta_reads_light_fills(self):
        ratio, light = _svg_meta(
            b'<svg viewBox="0 0 200 100"><path fill="#FFFFFF"/></svg>')
        self.assertAlmostEqual(ratio, 2.0, places=2)
        self.assertTrue(light)

    def test_favicon_is_squared(self):
        data, meta = _square_icon(base64.b64decode(_png_base64(900, 180)))
        self.assertEqual(meta["size"], 256)
        image = Image.open(io.BytesIO(base64.b64decode(data)))
        self.assertEqual(image.size, (256, 256))

    def test_favicon_keeps_svg(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"/>'
        _data, meta = _square_icon(svg)
        self.assertTrue(meta.get("svg"))

    # -------------------------------------------------------- validation
    def test_bad_accent_hex_is_refused(self):
        company = self.company
        with self.assertRaises(ValidationError):
            company.write({"nova_accent_hex": "teal"})

    def test_bad_email_is_refused(self):
        company = self.company
        with self.assertRaises(ValidationError):
            company.write({"nova_brand_email": "not-an-email"})

    def test_unreadable_binary_is_refused_not_cleared(self):
        """A byte count must never silently wipe the company logo."""
        company = self.company
        with self.assertRaises(ValidationError):
            company.write({"nova_brand_logo": 123456})

    # ----------------------------------------------------------- payload
    def test_branding_payload_shape(self):
        company = self.company
        company.write({
            "nova_brand_name": "Acme",
            "nova_brand_email": "hi@acme.test",
            "nova_accent_hex": "#B42323",
        })
        payload = company._nova_branding()
        for key in ("name", "email", "has_logo", "has_favicon",
                    "logo_url", "favicon_url", "logo_wordmark",
                    "logo_light", "accent"):
            self.assertIn(key, payload)
        self.assertEqual(payload["name"], "Acme")
        self.assertEqual(payload["email"], "hi@acme.test")
        self.assertEqual(payload["accent"]["accent"], "#B42323")
        self.assertTrue(payload["logo_url"].startswith(
            "/nova_backend_theme/brand/logo"))

    def test_branding_email_falls_back_to_company_email(self):
        company = self.company
        company.write({"nova_brand_email": False})
        company.partner_id.email = "company@acme.test"
        self.assertEqual(
            company._nova_branding()["email"], "company@acme.test")

    def test_branding_name_falls_back_to_default(self):
        company = self.company
        company.write({"nova_brand_name": False})
        self.assertEqual(company._nova_branding()["name"], "Zeetavix")

    def test_logo_upload_sets_metadata_and_cache_buster(self):
        company = self.company
        company.write({"nova_brand_logo": _png_base64(900, 180)})
        meta = company.nova_brand_logo_meta
        self.assertTrue(meta.get("wordmark"))
        self.assertTrue(meta.get("v"))
        self.assertIn("?v=%s" % meta["v"], company._nova_branding()["logo_url"])

    def test_saving_settings_again_does_not_churn_the_digest(self):
        company = self.company
        company.write({"nova_brand_logo": _png_base64(600, 120)})
        first = company.nova_brand_logo_meta["v"]
        # Settings forms re-send the stored base64 on every save.
        again = _as_bytes(company.nova_brand_logo)
        company.write({"nova_brand_logo": base64.b64encode(again).decode()})
        self.assertEqual(company.nova_brand_logo_meta["v"], first)
