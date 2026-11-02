/** @odoo-module **/
//
// Nova brand suffix: every document title reads "... — <brand>".
// The brand name is configurable (Settings > Appearance > Branding) and is
// pushed through the session; the static <title> fallback is handled by the
// web.layout inherit. This covers the dynamic TitlePlugin path.
//

import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { TitlePlugin } from "@web/core/browser/title_plugin";

const BRAND = (session.nova_brand && session.nova_brand.name) || "Zeetavix";

try {
    patch(TitlePlugin.prototype, {
        setup() {
            super.setup();
            try {
                this.setParts({ nova_brand: BRAND });
            } catch {
                // title stays stock
            }
        },
    });
} catch (e) {
    // eslint-disable-next-line no-console
    console.error("[nova] brand patch failed:", e);
}
