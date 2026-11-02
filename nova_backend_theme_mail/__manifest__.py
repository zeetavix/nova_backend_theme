# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Backend Theme (Discuss vocabulary)",
    "summary": "Renames Discuss menus to Messages when mail is installed",
    "description": """
Auto-installed bridge between Nova Backend Theme and Discuss. Renames the
stock "Discuss" menus to "Messages" so the product vocabulary stays
non-Odoo. Installs automatically only when both parents are present;
the core theme never depends on mail.
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
    "depends": ["nova_backend_theme", "mail"],
    "auto_install": True,
    "data": [
        "views/nova_menu_names.xml",
    ],
    "installable": True,
    "application": False,
}
