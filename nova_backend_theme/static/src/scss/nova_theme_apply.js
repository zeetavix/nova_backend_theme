/** @odoo-module **/
//
// Nova runtime theme bridge — applies per-user/company theme choices
// (accent, density, corners) from session info as CSS custom properties.
// Runs once at bundle load (session data is embedded in the page), then
// re-paints whenever the colour scheme flips so derived tones stay right.
// Everything degrades to the compiled Nova defaults when unset.
//

import { session } from "@web/session";

const HEX_RE = /^#[0-9a-fA-F]{6}$/;
const RADIUS_BASE = { sm: 8, base: 10, lg: 12, xl: 16 };
// Discrete on-scale sets per density (never multiplied: 0.5x/1.5x factors
// produced off-scale 4/5/6/15/18/24px values).
const RADIUS_SETS = {
    soft: { sm: 8, base: 8, lg: 10, xl: 12 },
    default: { sm: 8, base: 10, lg: 12, xl: 16 },
    round: { sm: 10, base: 12, lg: 16, xl: 16 },
};

function clampHex(value) {
    return typeof value === "string" && HEX_RE.test(value) ? value : null;
}

function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex(r, g, b) {
    const h = (v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
    return `#${h(r)}${h(g)}${h(b)}`;
}

function mix(first, second, weight) {
    const a = hexToRgb(first);
    const b = hexToRgb(second);
    return toHex(
        a[0] * weight + b[0] * (1 - weight),
        a[1] * weight + b[1] * (1 - weight),
        a[2] * weight + b[2] * (1 - weight)
    );
}

function rgba(hex, alpha) {
    const [r, g, b] = hexToRgb(hex);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function luminance(hex) {
    const [r, g, b] = hexToRgb(hex).map((v) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(first, second) {
    const a = luminance(first);
    const b = luminance(second);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function cacheKey() {
    try {
        const db = session.db || "db";
        const uid = session.uid || (session.user_id !== undefined ? session.user_id : "anon");
        return `nova_theme_cache:${db}:${uid}`;
    } catch {
        return "nova_theme_cache";
    }
}

function readCache() {
    try {
        const raw = window.localStorage.getItem(cacheKey());
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
}

function writeCache(theme) {
    try {
        window.localStorage.setItem(cacheKey(), JSON.stringify(theme));
    } catch {
        // cache optional
    }
}

function readCookie(name) {
    try {
        const match = document.cookie.match(
            new RegExp("(?:^|;\\s*)" + name + "=([^;]*)"));
        return match ? decodeURIComponent(match[1]).toLowerCase() : "";
    } catch {
        return "";
    }
}

function isDark() {
    try {
        const mode = document.documentElement.getAttribute("data-color-mode");
        if (mode) {
            return mode === "dark";
        }
        // Server truth first: the same nova_color_scheme cookie that
        // ir.http.color_scheme() reads. The OS is only a fallback.
        // (Reading the OS first painted light-mode accents — links near
        // 1.9:1 — onto charcoal whenever Nova dark met a light OS.)
        const cookie = readCookie("nova_color_scheme");
        if (cookie === "dark") {
            return true;
        }
        if (cookie === "light") {
            return false;
        }
        return (
            window.matchMedia &&
            window.matchMedia("(prefers-color-scheme: dark)").matches
        );
    } catch {
        return false;
    }
}

let currentTheme = null;

export function applyNovaTheme() {
    // Paint the cached theme first (pre-paint, no flash), then session truth.
    const cached = readCache();
    if (cached) {
        currentTheme = cached;
        paintTheme(cached);
    }
    let theme = null;
    try {
        theme = session.nova_theme || null;
    } catch {
        theme = null;
    }
    if (!theme) {
        return;
    }
    currentTheme = theme;
    paintTheme(theme);
    writeCache(theme);
}

function repaint() {
    if (currentTheme) {
        paintTheme(currentTheme);
    }
}

function watchScheme() {
    try {
        const root = document.documentElement;
        if (root && window.MutationObserver) {
            new MutationObserver(repaint).observe(root, {
                attributes: true,
                attributeFilter: ["data-color-mode"],
            });
        }
    } catch {
        // observer optional
    }
    try {
        const media = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
        if (media) {
            const handler = () => repaint();
            if (media.addEventListener) {
                media.addEventListener("change", handler);
            } else if (media.addListener) {
                media.addListener(handler);
            }
        }
    } catch {
        // listener optional
    }
}

function setVar(root, name, value) {
    try {
        root.style.setProperty(name, value);
    } catch {
        // a single token must never break the rest
    }
}

function paintTheme(theme) {
    let root = null;
    try {
        root = document.documentElement;
    } catch {
        return;
    }
    if (!root || !root.style || !root.style.setProperty) {
        return;
    }
    try {
        const accent = clampHex(theme.accent);
        const dark = isDark();
        if (accent) {
            // Dark surfaces need lightened signals; light needs darkened ones.
            const hover = dark ? mix("#FFFFFF", accent, 0.18) : mix("#000000", accent, 0.12);
            const active = dark ? mix("#FFFFFF", accent, 0.35) : mix("#000000", accent, 0.3);
            const soft = dark ? rgba(accent, 0.22) : rgba(accent, 0.12);
            // Readable text on top of the accent: white unless black wins.
            const fg = contrast(accent, "#FFFFFF") >= contrast(accent, "#101214")
                ? "#FFFFFF"
                : "#101214";
            const [r, g, b] = hexToRgb(accent);
            setVar(root, "--nova-accent", accent);
            setVar(root, "--nova-accent-hover", hover);
            setVar(root, "--nova-accent-active", active);
            setVar(root, "--nova-accent-soft", soft);
            setVar(root, "--nova-accent-fg", fg);
            // Bootstrap families Odoo reads at runtime: links, focus rings,
            // primary utilities and the portal accent all follow.
            setVar(root, "--bs-primary", accent);
            setVar(root, "--bs-primary-rgb", `${r}, ${g}, ${b}`);
            setVar(root, "--bs-link-color", active);
            setVar(root, "--bs-link-hover-color", active);
            setVar(root, "--bs-link-color-rgb", `${hexToRgb(active).join(", ")}`);
            setVar(root, "--bs-link-hover-color-rgb", `${hexToRgb(active).join(", ")}`);
            setVar(root, "--bs-focus-ring-color", rgbaf(accent, 0.25));
            setVar(root, "--portal-primary-color", accent);
            if (contrast(accent, "#FFFFFF") < 3) {
                try {
                    if (window.console && window.location.search.indexOf("debug") !== -1) {
                        window.console.warn(
                            `[nova] accent ${accent} is below 3:1 on white; button text flips to ${fg}.`
                        );
                    }
                } catch {
                    // silent in production
                }
            }
        }
        const set = RADIUS_SETS[theme.radius] || RADIUS_SETS.default;
        for (const name of Object.keys(RADIUS_BASE)) {
            setVar(root, `--nova-radius-${name}`, `${set[name]}px`);
        }
        if (theme.density === "compact" || theme.density === "comfortable") {
            root.setAttribute("data-nova-density", theme.density);
        }
        // Column spotlight is opt-in; missing key (stale cache) means off.
        try {
            if (theme.colspot === true) {
                root.setAttribute("data-nova-colspot", "1");
            } else {
                root.removeAttribute("data-nova-colspot");
            }
        } catch {
            // density still stands
        }
    } catch {
        // theme stays compiled-default
    }
}

function rgbaf(hex, alpha) {
    const [r, g, b] = hexToRgb(hex);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

applyNovaTheme();
watchScheme();
