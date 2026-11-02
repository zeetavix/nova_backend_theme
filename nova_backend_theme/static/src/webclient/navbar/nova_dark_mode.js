/** @odoo-module **/
//
// Nova dark mode — a user-menu switch that actually works.
//
// Community 20 ships the dark bundle but hardcodes color_scheme to
// "light", so it can never load. The switch writes a browser-local
// cookie; Nova's ir.http.color_scheme() reads it server-side and the
// whole dark system (bundle + every .dark.scss twin) comes online.
//
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

const COOKIE = "nova_color_scheme";

function currentScheme() {
    try {
        const match = document.cookie.match(
            new RegExp("(?:^|;\\s*)" + COOKIE + "=([^;]*)"));
        const value = match ? decodeURIComponent(match[1]) : "";
        return value === "dark" ? "dark" : "light";
    } catch {
        return "light";
    }
}

function setScheme(value) {
    try {
        // One year, whole site, SameSite=Lax.
        document.cookie = `${COOKIE}=${value};path=/;max-age=31536000;SameSite=Lax`;
        // Core JS (graph Chart.js ticks/grid/legend, spreadsheet monokai,
        // palette helpers) reads the stock `color_scheme` cookie directly,
        // which Community never sets - mirror the choice there too so
        // canvas-rendered text follows the theme.
        document.cookie = `color_scheme=${value};path=/;max-age=31536000;SameSite=Lax`;
    } catch {
        // the reload below keeps the previous scheme
    }
    try {
        window.location.reload();
    } catch {
        // user reloads manually if needed
    }
}

registry.category("user_menuitems").add("nova_dark_mode", () => ({
    type: "switch",
    id: "nova_dark_mode",
    description: _t("Dark mode"),
    isChecked: currentScheme() === "dark",
    callback: () => setScheme(currentScheme() === "dark" ? "light" : "dark"),
    sequence: 45,
}));
