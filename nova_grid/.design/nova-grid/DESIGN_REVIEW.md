# Design Review: Nova Grid

Philosophy: calm, dense, professional timesheet grid (Meta-clarity + Apple-HIG craft).
Date: 2026-10-19

## Screenshots Captured (per round, in `screenshots/`)

| Screenshot | Breakpoint | Description |
| ---------- | ---------- | ----------- |
| `r3-dark-week-1500.png` (+ `r3-dark-failed.png`) | Desktop 1500 | Dark-mode attempt (see R3) |
| `r7-tablet-768-week.png` | Tablet 768 | Week grid, fits without h-scroll |
| `r7-mobile-390-*.png` | Mobile 390 | Kanban fallback (see R7) |
| `r6-keyboard-edit.png` | Desktop 1500 | Keyboard-only edit committed |
| store shots in `../static/description/img/` | Desktop 1500 | Week, inline-edit, month, sections |

## R1 — Visual hierarchy + typography
Value chips 15px/600 (hero), section names 14px/700, rows 14px/600, muted labels. Verified in week/all screenshots.

## R2 — Type ramp
Ramp in use: 10 (eyebrow) / 11 (avatar, compact) / 12 (rowhead label, compact) / 13 (period) / 14 (values, totals) / 15 (headers, grand). Tabular numerals everywhere. No stray sizes.

## R3 — Color system + dark mode
Accent token `--o-nova-accent` follows `--bs-primary` (matches any backend theme), heat via `color-mix`. Dark bundle compiles clean; **visual dark check = needs validation** — headless `prefers-color-scheme: dark` and `localStorage color_scheme` do not flip Odoo 20's toggle (it needs the in-app preference). Owner to glance once with dark mode on.

## R4 — Composition / spacing
8px-base rhythm: gaps 8, cell height 38/34, section pad 6, chip margins 4, radius 4/8/12/22. Sticky head + first col + total col with correct z-stack.

## R5 — Affordances + feedback
Hover `+` on editable empties, focus-visible ring, saving spinner, today top-accent, sticky totals. Verified week/month/all.

## R6 — Accessibility + keyboard
Cells are native buttons (tabbable, verified programmatically); full keyboard path proven by test (focus → Enter → type 3:00 → Enter commits, no mouse, no errors). Reduced-motion media query disables the mount animation.

## R7 — Responsive
768px: fits, toolbar wraps to two tidy rows. 390px: Odoo serves kanban cards (standard small-screen fallback for views without a mobile template); grid stays one tap away in the view dropdown. No h-scroll breakage anywhere.

## R8 — Loading / empty / error
Loading is instant locally with a 140ms settle fade; empty state uses the standard ActionHelper with custom copy; write failures surface Odoo's validation dialog with the server message (observed live once during testing) — never a silent hang.

## R9 — Motion
Single 140ms mount fade, instant collapse toggle, hover-only outlines. Reduced-motion disables animation.

## R10 — Holistic
Compared against brief: calm, dense, professional — holds. No stray Enterprise copying (clean-room). Full regression green (see run logs).

## R11 — Next-level product layer (world-class pass)
- **KPI strip** above the grid: Total / Today / Active days / Avg-per-day + 40h capacity bar. Verified live (19h, 48% bar renders 91px, zero console errors).
- **Overtime dots**: >8h day cells get a warm marker (timesheets only, never money/count grids).
- **Section totals**: bands read `Name (n · 32.5 Hours)` — group contribution at a glance.
- **Scroll depth**: sticky head/col/total gain shadows only while scrolled (passive listener, cleaned up on unmount).
- **Print stylesheet**: toolbar + KPIs hidden, table unconstrained for clean printouts.
- Accent token lives on all three Nova roots (wrap/toolbar/KPIs) after finding `color-mix` computed transparent outside the wrap — verified computed fill afterwards.

## R12 — Dark-mode release pass (20.0.2.1.0)
- Fixed asset loading so dark CSS ships via `web.assets_web_dark` (lazy bundles are chosen by a JS cookie the Nova theme never sets, so lazy CSS silently stayed light under dark).
- Removed Bootstrap `table-active` from the footer row (it pulled the theme's teal `$table-active-bg`); footer is now the neutral elevated band, and the "Total" label is a sticky row-head again.
- Weekend columns recede on dark (`rgba(0,0,0,.22)`); weekend headers stay on the elevated band.
- Fixed table geometry: project column 300px, day columns 92px, Total column 76px — week balances instead of a 528px gutter, month shows ~12 days, pinned Total still covers scrolled content (verified pixel-opaque).
- Removed conflicting Bootstrap `w-100` from value buttons so heat chips keep their margins; section chevrons 12→14px.
- Verification: `fin_verify` 14/14 (dark surface `rgb(27,28,31)`, ink 13.88:1, labels 6.37:1, footer elevated, today tinted, 31 columns, no JS errors); live 8020 dark with the theme's `#0E7C8A` accent, verified on real data.
- Store shots regenerated from seeded 8021/8022 with the stuck tour pointer suppressed (persistent `.o-overlay-container{display:none}` for captures only); dark week added as `05_my_week_dark.png`; engine shots refreshed from the same captures.

## R13 — Grid family: attendance, leave, workload (20.0.1.0.0 × 3, engine 20.0.2.3.0)
- New engine arch attrs so every grid ships its own small dashboard without JS: `kpi_capacity_target`, `kpi_capacity_label`, `kpi_unit` (capacity ring), plus `overtime_at` (opt-in overload dots) and `force_editable` (readonly measures with a custom write path, e.g. attendance writes check-out).
- Fixed rows to always follow the arch: stock actions with default Group-By search filters (attendance, leave) used to replace grid rows with ad-hoc group fields, breaking titles, domains and writes.
- Datetime columns group by day like dates (`date_deadline`), with day-boundary domains and datetime group-key normalisation — verified pixel-opaque against the sticky Total column.
- `nova_attendance_grid`: correction-only writes (adjust check-out, never fabricate check-ins), 24h guard, officer/manager ownership guard, 40h ring.
- `nova_leave_grid`: read-only by design (edits refused server-side too), month-first, day-unit KPIs.
- `nova_project_workload_grid`: project × stage × deadline-week planning with placeholder-task creation; attaches to the standalone All Tasks action (the per-project action needs an active_id context).
- Verified: 28/28 new-module checks (install, versions, arch validation, defaults, write semantics, security fail-closed); attendance/leave/workload render light + dark with zero JS errors.
