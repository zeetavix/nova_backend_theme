# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Backend Theme (Portal accent)",
    "summary": "Syncs the Nova accent to the customer portal when installed",
    "description": """
Auto-installed bridge between Nova Backend Theme and Portal. Exposes the
company accent as --nova-accent on the portal frontend. Installs
automatically only when both parents are present; the core theme never
depends on portal.
""",
    "version": "20.0.1.0.0",
    "category": "Theme/Backend",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "price": 0.0,
    "currency": "USD",
    "license": "LGPL-3",
    "depends": ["nova_backend_theme", "portal"],
    "auto_install": True,
    "data": [
        "portal/nova_portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
