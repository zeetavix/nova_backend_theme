/** @odoo-module **/
//
// Shared board context: the palette has no env.searchModel, so WithSearch
// stashes its live {searchModel, displayName} here on setup and clears it on
// teardown (only its own stash). Plain module state, no services.
//

let current = null;

export function setBoardContext(ctx) {
    current = ctx || null;
}

export function getBoardContext() {
    return current;
}
