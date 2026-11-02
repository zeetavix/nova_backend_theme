// Part of Nova. See LICENSE file for full copyright and licensing details.
// Clean-room Gantt arch parser. Nova dialect: lanes come from <field>
// rows (with optional section="1"), the time axis from <range> spans,
// and bars stretch between date_start / date_stop attributes.
import { _t } from "@web/core/l10n/translation";
import { exprToBoolean } from "@web/core/utils/strings";
import { visitXML } from "@web/core/utils/xml";
import { getActiveActions } from "@web/views/utils";

export class NovaGanttArchParser {
    parse(xmlDoc, models, modelName) {
        const archInfo = {
            activeActions: getActiveActions(xmlDoc),
            editable: true,
            createInline: true,
            dateStartField: "",
            dateStopField: "",
            columnFieldType: "",
            kpiUnit: "",
            sectionField: null,
            rowFields: [],
            ranges: {},
            activeRangeName: "",
        };

        visitXML(xmlDoc, (node) => {
            if (node.tagName === "gantt") {
                for (const [attr, key] of [
                    ["editable", "editable"],
                    ["create_inline", "createInline"],
                ]) {
                    if (node.hasAttribute(attr)) {
                        archInfo[key] = exprToBoolean(node.getAttribute(attr));
                    }
                }
                for (const [attr, key] of [
                    ["date_start", "dateStartField"],
                    ["date_stop", "dateStopField"],
                    ["kpi_unit", "kpiUnit"],
                ]) {
                    if (node.hasAttribute(attr)) {
                        archInfo[key] = node.getAttribute(attr) || "";
                    }
                }
            } else if (node.tagName === "field") {
                const fieldName = node.getAttribute("name");
                const fieldInfo = models[modelName].fields[fieldName];
                if (!fieldInfo) {
                    return;
                }
                const ftype = node.getAttribute("type") || "row";
                const string = node.getAttribute("string") || fieldInfo.string;
                const invisible = node.getAttribute("invisible") || "False";
                if (ftype === "row") {
                    if (
                        node.hasAttribute("section") &&
                        exprToBoolean(node.getAttribute("section")) &&
                        !archInfo.sectionField
                    ) {
                        archInfo.sectionField = { name: fieldName, string, invisible };
                    } else {
                        archInfo.rowFields.push({ name: fieldName, string, invisible });
                    }
                }
            } else if (node.tagName === "range") {
                const rangeName = node.getAttribute("name");
                if (!rangeName) {
                    return;
                }
                archInfo.ranges[rangeName] = {
                    name: rangeName,
                    description: node.getAttribute("string") || rangeName,
                    span: node.getAttribute("span") || "month",
                    step: node.getAttribute("step") || "day",
                    default: exprToBoolean(node.getAttribute("default") || "0"),
                };
                if (archInfo.ranges[rangeName].default) {
                    archInfo.activeRangeName = rangeName;
                }
            }
        });

        if (!Object.keys(archInfo.ranges).length) {
            archInfo.ranges = {
                month: { name: "month", description: "Month", span: "month", step: "day", default: true },
                quarter: { name: "quarter", description: "Quarter", span: "quarter", step: "day", default: false },
            };
            archInfo.activeRangeName = "month";
        }
        if (!archInfo.activeRangeName) {
            archInfo.activeRangeName = Object.keys(archInfo.ranges)[0];
        }
        // The column fields come from date_start/date_stop attributes (not
        // <field> nodes), so record their type here: the client needs it to
        // decide date-vs-datetime handling (granularity suffix, domains).
        const startInfo = (models[modelName].fields || {})[archInfo.dateStartField] || {};
        archInfo.columnFieldType = startInfo.type || "";
        return archInfo;
    }
}
