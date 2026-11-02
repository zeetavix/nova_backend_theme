# Nova — free Odoo 20 Community addons by Zeetavix

Backend theme, 2D grid/Gantt/split views, per-app use-cases, and a
model-agnostic AI agent layer. Every addon in this repo is **free**
(price `0.0`, OPL-1).

| Addon | What it does |
|---|---|
| `nova_backend_theme` | Calm, accessible, white-label backend theme + dark mode + branded login |
| `nova_backend_theme_mail` | Free bridge: renames stock Discuss menus to Messages when `mail` is present |
| `nova_backend_theme_portal` | Free bridge: syncs the company accent to the customer portal |
| `nova_grid` | Generic `&lt;grid&gt;` view engine: rows, date/selection/many2one columns, inline editing, totals, heatmap, dark mode |
| `nova_gantt` | Generic `&lt;gantt&gt;` view engine: lanes, drag-to-move, edge-resize, today line, dialogs |
| `nova_split` | Split view: list left, form panel right, zero per-model work |
| `nova_attendance_grid` | My Attendance grid (`hr_attendance`) |
| `nova_leave_grid` | Leave grid (`hr_holidays`) |
| `nova_timesheet_grid` | Timesheet grid with KPI dashboard (`hr_timesheet`, `project`) |
| `nova_project_workload_grid` | Project workload grid (`project`) |
| `nova_event_gantt` | Events Gantt (`event`) |
| `nova_leave_gantt` | Who-is-out Gantt (`hr_holidays`) |
| `nova_project_gantt` | Project portfolio Gantt (`project`) |
| `nova_ai` | AI agents: tiers, Studio control room, 29 library flows with node canvas, knowledge, schedules, chat drawer, offline voice |

The use-case grid/Gantt modules depend on their engine, so installing one
pulls the engine in automatically. `nova_ai` needs the backend theme.

## Requirements

| | |
|---|---|
| Odoo | 20.0 Community |
| License | LGPL-3 (free and open source) |
| Price | Free — all modules, `0.0` USD |

No Enterprise code is copied or required.

## Installation

1. Copy the addon folders you need into your addons path.
2. Update the apps list (`-u base` or the Apps menu).
3. Install the addons you want (theme first if you use `nova_ai`).
4. Theme: Settings › Appearance. AI: Settings › Nova AI.

## Layout

```
nova_backend_theme*         theme + two free bridges
nova_grid / nova_gantt      base view engines
nova_split                  split view
nova_*_grid / nova_*_gantt  one use-case module per app
nova_ai                     the agent layer
```

## License

Odoo Proprietary License v1.0 (OPL-1) — see [LICENSE](LICENSE).
Free of charge; a license file ships with every addon.
Support: support@zeetavix.com — https://zeetavix.com
