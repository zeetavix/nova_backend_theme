# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova Backend Theme",
    "summary": "Calm, accessible backend theme for Odoo Community",
    "description": """
Nova Backend Theme
==================

An original, clean-room backend theme for Odoo Community (OPL-1).

Design thesis: if Meta designed an ERP, it would feel instant, calm and
human — one action blue, soft surfaces, Apple-HIG craft, zero clutter.
Nova brings that to Community: light + dark semantic tokens, blurred
navbar, pill search, card views, friendly 10px radii, honest motion.

No Enterprise code is copied. Only public Community classes
(.o_main_navbar, .o_list_table, .o_form_sheet, …) are restyled with
original SCSS, plus additive JS that patches (never forks) core
components: Nova launcher home, command-palette providers, form jump-bar
with invalid-count badge, toast-history bell with DND + quiet hours,
board pinning, and per-user theme settings (accent, density, corners,
column spotlight). Template inherits are additive; remove the module and
stock behavior returns.

Depends only on web on purpose: portal/mail/board integrations are
guarded and degrade silently when those apps are absent.

Inspired by Apple HIG (accessibility, typography, layout, dark mode),
colour/typography/spacing systems and Meta's clarity language.
""",
    "version": "20.0.1.1.5",
    "category": "Theme/Backend",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "price": 10.0,
    "currency": "USD",
    "license": "OPL-1",
    "depends": ["web"],
    "images": [
        "static/description/screens/03-home.png",
        "static/description/screens/04-sales-list.png",
        "static/description/screens/05-sale-form.png",
        "static/description/screens/11-sales-dark.png",
        "static/description/screens/12-form-dark.png",
        "static/description/screens/01-login-light.png",
        "static/description/screens/02-login-dark.png",
        "static/description/screens/10-settings.png",
        "static/description/screens/06-crm-pipeline.png",
        "static/description/screens/07-project-tasks.png",
        "static/description/screens/08-calendar.png",
        "static/description/screens/09-inventory-list.png",
    ],
    "data": [
        "security/ir.access.csv",
        "views/nova_home_views.xml",
        "views/nova_theme_settings_views.xml",
        "views/nova_login_templates.xml",
        "views/nova_layout_templates.xml",
    ],
    "assets": {
        "web._assets_primary_variables": [
            (
                "before",
                "web/static/src/scss/primary_variables.scss",
                "nova_backend_theme/static/src/scss/nova_primary_variables.scss",
            ),
        ],
        "web._assets_secondary_variables": [
            (
                "before",
                "web/static/src/scss/secondary_variables.scss",
                "nova_backend_theme/static/src/scss/nova_secondary_variables.scss",
            ),
        ],
        "web._assets_backend_helpers": [
            (
                "before",
                "web/static/src/scss/bootstrap_overridden.scss",
                "nova_backend_theme/static/src/scss/nova_bootstrap_overridden.scss",
            ),
        ],
        "web.assets_backend": [
            "nova_backend_theme/static/src/scss/nova_fonts.scss",
            "nova_backend_theme/static/src/scss/nova_base.scss",
            "nova_backend_theme/static/src/webclient/navbar/nova_navbar.scss",
            "nova_backend_theme/static/src/webclient/navbar/nova_burger.scss",
            "nova_backend_theme/static/src/search/control_panel/nova_control_panel.scss",
            "nova_backend_theme/static/src/views/list/nova_list.scss",
            "nova_backend_theme/static/src/views/list/nova_list.js",
            "nova_backend_theme/static/src/views/form/nova_form.scss",
            "nova_backend_theme/static/src/views/kanban/nova_kanban.scss",
            "nova_backend_theme/static/src/views/calendar/nova_calendar.scss",
            "nova_backend_theme/static/src/views/activity/nova_activity.scss",
            "nova_backend_theme/static/src/views/board/nova_board.scss",
            "nova_backend_theme/static/src/views/spreadsheet/nova_spreadsheet.scss",
            "nova_backend_theme/static/src/views/onboarding/nova_onboarding.scss",
            "nova_backend_theme/static/src/views/discuss/nova_discuss.scss",
            "nova_backend_theme/static/src/core/buttons/nova_buttons.scss",
            "nova_backend_theme/static/src/core/nova_components.scss",
            "nova_backend_theme/static/src/core/notifications/nova_toast_history.scss",
            "nova_backend_theme/static/src/core/nova_feedback.scss",
            "nova_backend_theme/static/src/core/commands/nova_command_palette.scss",
            "nova_backend_theme/static/src/core/notifications/nova_toast_history.js",
            "nova_backend_theme/static/src/core/notifications/nova_toast_history.xml",
            "nova_backend_theme/static/src/views/chatter/nova_chatter.scss",
            "nova_backend_theme/static/src/views/fields/nova_fields.scss",
            "nova_backend_theme/static/src/views/form/nova_form_jump.js",
            "nova_backend_theme/static/src/webclient/settings/nova_settings.scss",
            "nova_backend_theme/static/src/home/nova_home.js",
            "nova_backend_theme/static/src/home/nova_home.xml",
            "nova_backend_theme/static/src/home/nova_launcher.scss",
            "nova_backend_theme/static/src/home/nova_bookmark_store.js",
            "nova_backend_theme/static/src/core/commands/nova_record_provider.js",
            "nova_backend_theme/static/src/core/commands/nova_bookmark_provider.js",
            "nova_backend_theme/static/src/core/commands/nova_board_context.js",
            "nova_backend_theme/static/src/core/commands/nova_board_pin.js",
            "nova_backend_theme/static/src/webclient/navbar/nova_navbar_patch.js",
            "nova_backend_theme/static/src/webclient/navbar/nova_brand.js",
            "nova_backend_theme/static/src/webclient/navbar/nova_dark_mode.js",
            "nova_backend_theme/static/src/scss/nova_theme_apply.js",
            "nova_backend_theme/static/src/scss/nova_bridge.scss",
        ],
        "web.assets_backend_lazy": [
            "nova_backend_theme/static/src/views/pivot/nova_pivot.scss",
            "nova_backend_theme/static/src/scss/nova_bridge.scss",
        ],
        "web.assets_web_dark": [
            (
                "before",
                "nova_backend_theme/static/src/scss/nova_primary_variables.scss",
                "nova_backend_theme/static/src/scss/nova_primary_variables.dark.scss",
            ),
            (
                "before",
                "nova_backend_theme/static/src/scss/nova_secondary_variables.scss",
                "nova_backend_theme/static/src/scss/nova_secondary_variables.dark.scss",
            ),
            (
                "before",
                "nova_backend_theme/static/src/scss/nova_bootstrap_overridden.scss",
                "nova_backend_theme/static/src/scss/nova_bootstrap_overridden.dark.scss",
            ),
            "nova_backend_theme/static/src/scss/nova_base.dark.scss",
            "nova_backend_theme/static/src/webclient/navbar/nova_navbar.dark.scss",
            "nova_backend_theme/static/src/webclient/navbar/nova_burger.dark.scss",
            "nova_backend_theme/static/src/search/control_panel/nova_control_panel.dark.scss",
            "nova_backend_theme/static/src/views/list/nova_list.dark.scss",
            "nova_backend_theme/static/src/views/form/nova_form.dark.scss",
            "nova_backend_theme/static/src/views/kanban/nova_kanban.dark.scss",
            "nova_backend_theme/static/src/views/calendar/nova_calendar.dark.scss",
            "nova_backend_theme/static/src/views/activity/nova_activity.dark.scss",
            "nova_backend_theme/static/src/views/board/nova_board.dark.scss",
            "nova_backend_theme/static/src/views/spreadsheet/nova_spreadsheet.dark.scss",
            "nova_backend_theme/static/src/views/onboarding/nova_onboarding.dark.scss",
            "nova_backend_theme/static/src/views/discuss/nova_discuss.dark.scss",
            "nova_backend_theme/static/src/views/chatter/nova_chatter.dark.scss",
            "nova_backend_theme/static/src/views/fields/nova_fields.dark.scss",
            "nova_backend_theme/static/src/views/pivot/nova_pivot.dark.scss",
            "nova_backend_theme/static/src/webclient/settings/nova_settings.dark.scss",
            "nova_backend_theme/static/src/core/buttons/nova_buttons.dark.scss",
            "nova_backend_theme/static/src/core/nova_components.dark.scss",
            "nova_backend_theme/static/src/core/notifications/nova_toast_history.dark.scss",
            "nova_backend_theme/static/src/core/nova_feedback.dark.scss",
            "nova_backend_theme/static/src/core/commands/nova_command_palette.dark.scss",
            "nova_backend_theme/static/src/home/nova_launcher.dark.scss",
        ],
        "web.assets_frontend": [
            "nova_backend_theme/static/src/scss/nova_fonts.scss",
            "nova_backend_theme/static/src/webclient/login/nova_login.scss",
            "nova_backend_theme/static/src/portal/nova_portal.scss",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
