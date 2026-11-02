// Part of Nova. See LICENSE file for full copyright and licensing details.
// Clean-room grid arch parser. Compatible with Enterprise-style <grid> XML
// but implemented independently, with Nova extras.
import { _t } from "@web/core/l10n/translation";
import { exprToBoolean } from "@web/core/utils/strings";
import { visitXML } from "@web/core/utils/xml";
import { getActiveActions } from "@web/views/utils";

export class NovaGridArchParser {
    parse(xmlDoc, models, modelName) {
        const archInfo = {
            activeActions: getActiveActions(xmlDoc),
            hideLineTotal: false,
            hideColumnTotal: false,
            hasBarChartTotal: false,
            createInline: true,
            displayEmpty: false,
            showHeatmap: true,
            showProgress: false,
            heatmapScale: 8.0,
            editable: true,
            formViewId: false,
            sectionField: null,
            rowFields: [],
            columnFieldName: "",
            columnFieldType: "",
            measureField: {
                name: "__count",
                aggregator: "sum",
                readonly: true,
                string: _t("Count"),
            },
            readonlyField: null,
            widgetPerFieldName: {},
            ranges: {},
            activeRangeName: "",
            // Per-module KPI dashboard: capacity ring target + unit suffix.
            // Timesheets keep the legacy behaviour (unit_amount -> 40h)
            // when these are absent.
            kpiCapacityTarget: 0,
            kpiCapacityLabel: "",
            kpiUnit: "",
            overtimeAt: 0,
            // Escape hatch for models whose write path targets a different
            // field than the displayed measure (e.g. attendance corrections
            // write check_out while displaying worked_hours). The arch
            // author takes responsibility for nova_grid_update_cell.
            forceEditable: false,
        };

        visitXML(xmlDoc, (node) => {
            if (node.tagName === "grid") {
                for (const [attr, key] of [
                    ["hide_line_total", "hideLineTotal"],
                    ["hide_column_total", "hideColumnTotal"],
                    ["barchart_total", "hasBarChartTotal"],
                    ["create_inline", "createInline"],
                    ["display_empty", "displayEmpty"],
                    ["show_heatmap", "showHeatmap"],
                    ["show_progress", "showProgress"],
                ]) {
                    if (node.hasAttribute(attr)) {
                        archInfo[key] = exprToBoolean(node.getAttribute(attr));
                    }
                }
                if (node.hasAttribute("heatmap_scale")) {
                    archInfo.heatmapScale = parseFloat(node.getAttribute("heatmap_scale")) || 8.0;
                }
                if (node.hasAttribute("kpi_capacity_target")) {
                    archInfo.kpiCapacityTarget = parseFloat(node.getAttribute("kpi_capacity_target")) || 0;
                }
                if (node.hasAttribute("kpi_capacity_label")) {
                    archInfo.kpiCapacityLabel = node.getAttribute("kpi_capacity_label") || "";
                }
                if (node.hasAttribute("kpi_unit")) {
                    archInfo.kpiUnit = node.getAttribute("kpi_unit") || "";
                }
                if (node.hasAttribute("overtime_at")) {
                    archInfo.overtimeAt = parseFloat(node.getAttribute("overtime_at")) || 0;
                }
                if (node.hasAttribute("force_editable")) {
                    archInfo.forceEditable = exprToBoolean(node.getAttribute("force_editable"));
                }
                if (node.hasAttribute("editable")) {
                    archInfo.editable = exprToBoolean(node.getAttribute("editable"));
                }
                if (node.hasAttribute("form_view_id")) {
                    archInfo.formViewId = parseInt(node.getAttribute("form_view_id"), 10);
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
                    if (node.hasAttribute("widget")) {
                        archInfo.widgetPerFieldName[fieldName] = node.getAttribute("widget");
                    }
                    if (
                        node.hasAttribute("section") &&
                        exprToBoolean(node.getAttribute("section")) &&
                        !archInfo.sectionField
                    ) {
                        archInfo.sectionField = { name: fieldName, invisible };
                    } else {
                        archInfo.rowFields.push({ name: fieldName, invisible });
                    }
                } else if (ftype === "col") {
                    archInfo.columnFieldName = fieldName;
                    // The parser resolves the field from relational models
                    // here, so keep its type: the runtime fieldsInfo is not
                    // guaranteed to carry every col field (and datetime
                    // columns need the same date treatment server-side).
                    archInfo.columnFieldType = fieldInfo.type || "";
                    const { ranges, activeRangeName } = this._extractRanges(node);
                    archInfo.ranges = ranges;
                    archInfo.activeRangeName = activeRangeName;
                } else if (ftype === "measure") {
                    if (node.hasAttribute("widget")) {
                        archInfo.widgetPerFieldName[fieldName] = node.getAttribute("widget");
                    }
                    archInfo.measureField = {
                        name: fieldName,
                        aggregator: node.getAttribute("operator") || fieldInfo.aggregator || "sum",
                        string,
                        readonly:
                            exprToBoolean(node.getAttribute("readonly") || "0") ||
                            Boolean(fieldInfo.readonly),
                    };
                } else if (ftype === "readonly") {
                    archInfo.readonlyField = {
                        name: fieldName,
                        aggregator: node.getAttribute("operator") || fieldInfo.aggregator,
                        string,
                    };
                }
            }
        });

        // archInfo.editable still holds the raw editable="..." attr here.
        const rawEditable = archInfo.editable;
        archInfo.editable =
            (rawEditable || archInfo.forceEditable) &&
            archInfo.measureField &&
            (!archInfo.measureField.readonly || archInfo.forceEditable) &&
            archInfo.measureField.aggregator === "sum";
        return archInfo;
    }

    _extractRanges(colNode) {
        const ranges = {};
        let activeRangeName = "";
        let firstRangeName = "";
        for (const rangeNode of colNode.children) {
            if (rangeNode.tagName !== "range") {
                continue;
            }
            const rangeName = rangeNode.getAttribute("name");
            if (!rangeName) {
                continue;
            }
            if (!firstRangeName) {
                firstRangeName = rangeName;
            }
            ranges[rangeName] = {
                name: rangeName,
                description: rangeNode.getAttribute("string") || rangeName,
                span: rangeNode.getAttribute("span") || "week",
                step: rangeNode.getAttribute("step") || "day",
                default: exprToBoolean(rangeNode.getAttribute("default") || "0"),
            };
            if (ranges[rangeName].default) {
                activeRangeName = rangeName;
            }
        }
        // Sensible defaults when the author omits <range> children.
        if (!Object.keys(ranges).length) {
            ranges.week = { name: "week", description: "Week", span: "week", step: "day", default: true };
            ranges.month = { name: "month", description: "Month", span: "month", step: "day", default: false };
        }
        return { ranges, activeRangeName: activeRangeName || firstRangeName || "week" };
    }
}
