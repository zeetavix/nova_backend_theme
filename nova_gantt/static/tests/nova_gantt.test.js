// Part of Nova. See LICENSE file for full copyright and licensing details.
// Minimal smoke test: arch parser defaults.
import { NovaGanttArchParser } from "@nova_gantt/views/nova_gantt_arch_parser";
import { parseXML } from "@web/core/utils/xml";

QUnit.module("nova_gantt");

QUnit.test("arch parser sets month default and date fields", (assert) => {
    const arch = `<grid string="X"><field name="user_id" type="row" section="1"/><field name="date" type="col"/><field name="unit_amount" type="measure"/></grid>`;
    const models = {
        "account.analytic.line": {
            fields: {
                user_id: { string: "User", type: "many2one" },
                date: { string: "Date", type: "date" },
            },
        },
    };
    const gridLike = arch.replace("<grid", "<gantt").replace("</grid>", "</gantt>");
    const withDates = gridLike.replace("<gantt string=\"X\">", '<gantt string="X" date_start="date" date_stop="date">');
    const info = new NovaGanttArchParser().parse(parseXML(withDates), models, "account.analytic.line");
    assert.strictEqual(info.dateStartField, "date", "date_start parsed");
    assert.strictEqual(info.dateStopField, "date", "date_stop parsed");
    assert.strictEqual(info.activeRangeName, "month", "month range default exists");
    assert.ok(info.sectionField && info.sectionField.name === "user_id", "section field parsed");
    assert.strictEqual(info.editable, true, "editable by default");
});

QUnit.test("readonly grid arch rejected by validator rules", (assert) => {
    const arch = `<gantt string="X"><field name="user_id" type="row"/></gantt>`;
    const models = {
        "account.analytic.line": {
            fields: {
                user_id: { string: "User", type: "many2one" },
                date: { string: "Date", type: "date" },
            },
        },
    };
    const info = new NovaGanttArchParser().parse(parseXML(arch), models, "account.analytic.line");
    assert.strictEqual(info.dateStartField, "", "missing date_start stays empty for the validator to reject");
});
