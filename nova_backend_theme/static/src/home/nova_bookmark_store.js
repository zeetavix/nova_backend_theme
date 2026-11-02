/** @odoo-module **/
//
// Shared bookmark state: the launcher writes it, the palette reads it.
// Plain signals, no services here (services live in components).
//

import { signal } from "@odoo/owl";

export const bookmarkState = {
    menus: signal([]),
    records: signal([]),
};

export function setBookmarks(menus, records) {
    try {
        bookmarkState.menus.set(Array.isArray(menus) ? menus : []);
    } catch {
        // state stays put
    }
    try {
        bookmarkState.records.set(Array.isArray(records) ? records : []);
    } catch {
        // state stays put
    }
}
