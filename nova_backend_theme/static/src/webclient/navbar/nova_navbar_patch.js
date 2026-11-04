/** @odoo-module **/
//
// Nova navbar patch — the grid button opens the Nova Launcher (like the
// Enterprise home button) instead of the stock app-list dropdown, and fresh
// logins land on the launcher. Clean-room; only public APIs are used.
//
// Mechanism note: duplicate t-name template overrides do NOT win in this
// Odoo version (first registration sticks), so the stock dropdown template
// stays untouched. Instead, a capture-phase click interceptor preempts the
// dropdown toggle deterministically via its long-stable CSS hook.
//

import { onMounted, onWillDestroy } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { NavBar } from "@web/webclient/navbar/navbar";
import { WebClient } from "@web/webclient/webclient";

const NOVA_HOME_ACTION = { type: "ir.actions.client", tag: "nova_home" };
const APPS_TOGGLE_SELECTOR = ".o_navbar_apps_menu button";

function closeStrayAppsMenu(component) {
    // If the stock dropdown somehow opened (e.g. keyboard BEFORE our
    // interceptor attached), toggle it shut through its own handler so
    // internal open-state never desyncs. Reentrancy-guarded below.
    try {
        const toggle = document.querySelector(
            ".o_navbar_apps_menu button[aria-expanded='true']"
        );
        if (toggle && component && component._novaClosing !== true) {
            component._novaClosing = true;
            try {
                toggle.click();
            } finally {
                component._novaClosing = false;
            }
        }
    } catch {
        // menu stays as-is; navigation still proceeds
    }
}

let novaNavbarPatched = false;
if (!novaNavbarPatched) {
    novaNavbarPatched = true;
    try {
        patch(NavBar.prototype, {
            setup() {
                super.setup();
                const onGridClick = (ev) => {
                    if (this._novaClosing === true) {
                        return;
                    }
                    const target = ev.target instanceof HTMLElement ? ev.target : null;
                    const toggle = target ? target.closest(APPS_TOGGLE_SELECTOR) : null;
                    if (!toggle) {
                        return;
                    }
                    let inside = false;
                    try {
                        const root = this.root();
                        inside = !!root && root.contains(toggle);
                    } catch {
                        inside = false;
                    }
                    if (!inside) {
                        return;
                    }
                    ev.preventDefault();
                    ev.stopPropagation();
                    this.openNovaHome();
                };
                onMounted(() => window.addEventListener("click", onGridClick, true));
                onWillDestroy(() => window.removeEventListener("click", onGridClick, true));
                // The toggle only exists after mount: look it up here, not in
                // setup(), or the "Home" label is never applied.
                onMounted(() => {
                    try {
                        const toggle = document.querySelector(APPS_TOGGLE_SELECTOR);
                        if (toggle) {
                            toggle.setAttribute("aria-label", _t("Home"));
                            toggle.removeAttribute("aria-expanded");
                            toggle.removeAttribute("aria-haspopup");
                        }
                    } catch {
                        // toggle keeps stock semantics
                    }
                });
            },
            openNovaHome() {
                closeStrayAppsMenu(this);
                return this.actionService.doAction(NOVA_HOME_ACTION, {
                    clearBreadcrumbs: true,
                });
            },
        });

        patch(WebClient.prototype, {
            _loadDefaultApp() {
                try {
                    return this.env.services.action.doAction(NOVA_HOME_ACTION, {
                        clearBreadcrumbs: true,
                    });
                } catch {
                    return super._loadDefaultApp();
                }
            },
        });
    } catch (e) {
        // Loud by design: without these patches the grid button falls back to
        // the stock dropdown and fresh logins skip the launcher.
        // eslint-disable-next-line no-console
        console.error("[nova] navbar patch failed to apply:", e);
    }
}
