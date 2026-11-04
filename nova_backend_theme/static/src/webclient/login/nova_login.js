/** @odoo-module **/
//
// Nova login scheme hook — the login page loads before any backend session
// exists, so the SCSS cannot ask the server which mode the user chose.
// Read the same nova_color_scheme cookie the backend writes and flag it on
// <html> as early as this module runs, so the dark login twin applies even
// when the OS itself is in light mode.
//

function readScheme() {
    try {
        const match = document.cookie.match(/(?:^|;\s*)nova_color_scheme=([^;]*)/);
        return match ? decodeURIComponent(match[1]).toLowerCase() : "";
    } catch {
        return "";
    }
}

try {
    if (readScheme() === "dark") {
        document.documentElement.setAttribute("data-nova-login", "dark");
    } else {
        document.documentElement.removeAttribute("data-nova-login");
    }
} catch {
    // light login stands
}
