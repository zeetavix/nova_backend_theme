# Design Review: Nova Gantt

Philosophy: calm, dense, professional timeline (Meta-clarity + Apple-HIG craft).
Date: 2026-10-26

## R1 — First release (engine 20.0.1.0 + 3 apps)
- Fixed table geometry analogue: CSS-grid timeline, 300px sticky lane column,
  36px-min day columns, sticky header + first column, sticky today line.
- Bars: accent pills with tabular date tooltips, edge handles with ew-resize
  cursor, hover brightening, focus ring; narrow bars (<8% of the window) hide
  their inline label but keep the tooltip.
- Weekend columns dim in the body; the header stays on the elevated band.
- Dark mode verified on the Nova theme (surface rgb(27,28,31), ink 13.88:1
  class tokens) — no white surfaces, no teal footer bug class.
- Verified: 22/22 shell checks (install, versions, arch validation, defaults,
  fetch, move/clamp/guard semantics, placeholder cleanup); mouse-drag moves,
  edge-resize, keyboard nudge — all server-persisted, zero JS console errors.
- Store shots: project portfolio, events, who's-out — light + dark each,
  seeded deterministically, tour overlays suppressed for captures only.
- Known honest limits: undated records never render (documented per app);
  per-employee leave lanes need no sections beyond the first level.
