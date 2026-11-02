// Part of Nova. See LICENSE file for full copyright and licensing details.
// Minimal smoke test: arch parser defaults.
import { NovaGridArchParser } from "@nova_grid/views/nova_grid_arch_parser";
import { parseXML } from "@web/core/utils/xml";

QUnit.module("nova_grid");

QUnit.test("arch parser sets week/month defaults", (assert) => {
    const arch = `<grid editable="1"><field name="project_id" type="row"/><field name="date" type="col"/><field name="unit_amount" type="measure" operator="sum"/></grid>`;
    const models = {
        "account.analytic.line": {
            fields: {
                project_id: { string: "Project", type: "many2one" },
                date: { string: "Date", type: "date" },
                unit_amount: { string: "Hours", type: "float", aggregator: "sum" },
            },
        },
    };
    const info = new NovaGridArchParser().parse(parseXML(arch), models, "account.analytic.line");
    assert.strictEqual(info.columnFieldName, "date", "column field parsed");
    assert.strictEqual(info.columnFieldType, "date", "column field type kept for date detection");
    assert.ok(info.ranges.week, "week range default exists");
    assert.ok(info.editable === false || info.editable === true, "editable computed");
});

QUnit.test("arch parser reads per-module KPI dashboard attrs", (assert) => {
    const arch = `<grid editable="1" kpi_capacity_target="40" kpi_capacity_label="of 40h week" kpi_unit="h" overtime_at="8"><field name="employee_id" type="row"/><field name="date" type="col"/><field name="worked_hours" type="measure" operator="sum"/></grid>`;
    const models = {
        "hr.attendance": {
            fields: {
                employee_id: { string: "Employee", type: "many2one" },
                date: { string: "Date", type: "date" },
                worked_hours: { string: "Worked Hours", type: "float", aggregator: "sum" },
            },
        },
    };
    const info = new NovaGridArchParser().parse(parseXML(arch), models, "hr.attendance");
    assert.strictEqual(info.kpiCapacityTarget, 40, "capacity target parsed");
    assert.strictEqual(info.kpiCapacityLabel, "of 40h week", "capacity label parsed");
    assert.strictEqual(info.kpiUnit, "h", "unit suffix parsed");
    assert.strictEqual(info.overtimeAt, 8, "overtime threshold parsed");
});

QUnit.test("force_editable unlocks readonly measures with custom writers", (assert) => {
    const models = {
        "hr.attendance": {
            fields: {
                employee_id: { string: "Employee", type: "many2one" },
                date: { string: "Date", type: "date" },
                worked_hours: { string: "Worked Hours", type: "float", aggregator: "sum", readonly: true },
            },
        },
    };
    const plain = `<grid editable="1"><field name="employee_id" type="row"/><field name="date" type="col"/><field name="worked_hours" type="measure" operator="sum"/></grid>`;
    const forced = `<grid editable="1" force_editable="1"><field name="employee_id" type="row"/><field name="date" type="col"/><field name="worked_hours" type="measure" operator="sum"/></grid>`;
    assert.notOk(
        new NovaGridArchParser().parse(parseXML(plain), models, "hr.attendance").editable,
        "readonly measure stays locked by default"
    );
    assert.ok(
        new NovaGridArchParser().parse(parseXML(forced), models, "hr.attendance").editable,
        "force_editable unlocks the grid"
    );
});
