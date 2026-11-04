/** @odoo-module **/
//
// Nova form jump-bar — Ctrl+J cycles notebook pages (invalid first), Ctrl+.
// jumps to the next invalid field, activating its page first. Pure DOM on
// stock hooks (o_page_invalid, o_field_invalid); no RPC, no template changes.
//
// Scoping: callbacks receive { target } from the hotkey service, so the root
// form is derived from the event target — never document.querySelector — and
// isAvailable keeps both shortcuts silent outside a form.
//

import { patch } from "@web/core/utils/patch";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { _t } from "@web/core/l10n/translation";
import { FormController } from "@web/views/form/form_controller";
import { onMounted, onWillUnmount } from "@odoo/owl";

function formOf(target) {
    try {
        return target && target.closest ? target.closest(".o_form_view") : null;
    } catch {
        return null;
    }
}

function inForm(target) {
    if (formOf(target)) {
        return true;
    }
    // Focus may sit on body right after clicking a tab or button — fall back
    // to the focused element, still form-gated.
    try {
        return !!formOf(document.activeElement);
    } catch {
        return false;
    }
}

function formRoot(target) {
    return formOf(target) || formOf(document.activeElement);
}

function notebookPages(root) {
    try {
        return [...root.querySelectorAll(".o_notebook_headers .nav-link")];
    } catch {
        return [];
    }
}

// Rotation state for Ctrl+J: invalid pages first, then the rest, advancing
// one step per press so repeated presses cycle instead of sticking.
let novaJumpIndex = 0;

function jumpPage(root) {
    const pages = notebookPages(root);
    if (!pages.length) {
        return;
    }
    const invalid = pages.filter((page) => {
        try {
            return page.classList.contains("o_page_invalid");
        } catch {
            return false;
        }
    });
    const ordered = [...invalid, ...pages.filter((p) => !invalid.includes(p))];
    const next = ordered[novaJumpIndex % ordered.length];
    novaJumpIndex += 1;
    if (next) {
        try {
            next.click();
        } catch {
            // page stays put
        }
    }
}

function focusableChildren(widget) {
    try {
        return [...widget.querySelectorAll("input, select, textarea, button, [tabindex]")].filter(
            (el) => {
                try {
                    return !el.disabled && (el.offsetWidth || el.offsetHeight);
                } catch {
                    return false;
                }
            }
        );
    } catch {
        return [];
    }
}

function activateContainingPage(root, node) {
    // Inactive notebook panes are unmounted — click the tab first.
    let pane = null;
    try {
        pane = node.closest ? node.closest(".tab-pane") : null;
    } catch {
        pane = null;
    }
    if (!pane || !pane.id) {
        return;
    }
    let tab = null;
    try {
        tab =
            root.querySelector(`.o_notebook_headers .nav-link[aria-controls="${pane.id}"]`) ||
            root.querySelector(`.o_notebook_headers .nav-link[data-bs-target="#${pane.id}"]`);
    } catch {
        tab = null;
    }
    if (tab) {
        try {
            tab.click();
        } catch {
            // field may stay hidden
        }
    }
}

let novaFieldIndex = 0;

function jumpField(root) {
    let widgets = [];
    try {
        widgets = [...root.querySelectorAll(".o_field_invalid")];
    } catch {
        widgets = [];
    }
    if (!widgets.length) {
        return;
    }
    const widget = widgets[novaFieldIndex % widgets.length];
    novaFieldIndex += 1;
    if (!widget) {
        return;
    }
    activateContainingPage(root, widget);
    const focusables = focusableChildren(widget);
    const target = focusables[0] || widget;
    try {
        target.scrollIntoView({ block: "nearest" });
    } catch {
        // focus still attempted below
    }
    try {
        if (focusables[0]) {
            focusables[0].focus({ preventScroll: true });
        }
    } catch {
        // focus stays put
    }
}

try {
    patch(FormController.prototype, {
        setup() {
            super.setup();
            useHotkey("control+j", ({ target }) => jumpPage(formRoot(target)), {
                bypassEditableProtection: true,
                isAvailable: inForm,
            });
            useHotkey("control+.", ({ target }) => jumpField(formRoot(target)), {
                bypassEditableProtection: true,
                isAvailable: inForm,
            });
            // Invalid-count badge: template-free pill on the save button.
            // Observes class changes on this controller's root only. Clicks
            // are captured on the root (not the pill) because the pill often
            // sits on a *disabled* save button, which swallows its own events.
            let observer = null;
            let observedRoot = null;
            let badge = null;
            let lastCount = -1;
            const currentRoot = () => {
                try {
                    return typeof this.rootRef === "function" ? this.rootRef() : null;
                } catch {
                    return null;
                }
            };
            const ensureObserved = (root) => {
                if (!root || typeof MutationObserver === "undefined") {
                    return;
                }
                if (observedRoot === root && observer) {
                    return;
                }
                try {
                    if (observer) {
                        observer.disconnect();
                    }
                } catch {
                    // re-binding anyway
                }
                try {
                    observer = new MutationObserver(() => {
                        if (refreshQueued) {
                            return;
                        }
                        refreshQueued = true;
                        try {
                            (window.requestAnimationFrame || ((fn) => setTimeout(fn, 0)))(() => {
                                refreshQueued = false;
                                refreshBadge();
                            });
                        } catch {
                            refreshQueued = false;
                        }
                    });
                    observer.observe(root, {
                        subtree: true,
                        attributes: true,
                        attributeFilter: ["class"],
                    });
                    observedRoot = root;
                } catch {
                    observer = null;
                    observedRoot = null;
                }
            };
            const refreshBadge = () => {
                const root = currentRoot();
                if (!root) {
                    return;
                }
                ensureObserved(root);
                let save = null;
                try {
                    save = root.querySelector(".o_form_button_save");
                } catch {
                    save = null;
                }
                let count = 0;
                try {
                    count = root.querySelectorAll(".o_field_invalid").length;
                } catch {
                    count = 0;
                }
                if (count === lastCount && badge && badge.isConnected) {
                    return;
                }
                lastCount = count;
                if (!save) {
                    return;
                }
                if (!badge || !badge.isConnected) {
                    try {
                        badge = document.createElement("span");
                        badge.className = "nova-invalid-badge";
                        badge.setAttribute("role", "status");
                        save.appendChild(badge);
                    } catch {
                        badge = null;
                        return;
                    }
                }
                try {
                    badge.textContent = String(count);
                    badge.hidden = count <= 0;
                    badge.setAttribute(
                        "aria-label",
                        count > 0 ? _t("%s invalid fields — click to jump", count) : _t("No invalid fields")
                    );
                    badge.title = count > 0 ? _t("%s invalid — click to jump (Ctrl+.)", count) : "";
                } catch {
                    // pill stays stale
                }
            };
            let refreshQueued = false;
            const onRootClickCapture = (ev) => {
                let pill = null;
                try {
                    pill = ev.target && ev.target.closest ? ev.target.closest(".nova-invalid-badge") : null;
                } catch {
                    pill = null;
                }
                if (!pill || pill.hidden) {
                    return;
                }
                try {
                    ev.preventDefault();
                    ev.stopPropagation();
                } catch {
                    // jump proceeds
                }
                jumpField(formRoot(ev.target));
            };
            onMounted(() => {
                const root = currentRoot();
                if (!root) {
                    return;
                }
                refreshBadge();
                ensureObserved(root);
                try {
                    root.addEventListener("click", onRootClickCapture, true);
                } catch {
                    // pill stays indicator-only
                }
            });
            onWillUnmount(() => {
                try {
                    const root = observedRoot || currentRoot();
                    if (root) {
                        root.removeEventListener("click", onRootClickCapture, true);
                    }
                } catch {
                    // listener already gone
                }
                try {
                    if (observer) {
                        observer.disconnect();
                    }
                } catch {
                    // already gone
                }
                observer = null;
                observedRoot = null;
                try {
                    if (badge && badge.isConnected) {
                        badge.remove();
                    }
                } catch {
                    // pill already gone
                }
                badge = null;
            });
        },
    });
} catch (e) {
    // eslint-disable-next-line no-console
    console.error("[nova] form jump-bar patch failed:", e);
}
