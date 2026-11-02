# Nova Flow — 110-phase from-scratch build in Odoo Community

Direction (locked): a non-developer builds ANY flow they imagine with
the available tools, end to end inside Odoo — guided builders, field
pickers, domain rows, validation that teaches. No code, no JSON
hand-writing required. n8n is the UX/semantics reference
(studied from source at n8n-ref: resource/operation nodes, connections
by output index, error ports, credentials vault, webhook response modes,
cron/interval triggers); the build itself is clean-room Odoo.

Rule: each phase lands code + test in this module and checks its box
only when the acceptance check passes. Wave order is build order.

## Wave A — Spec & contracts (P001–P010) — DONE
Built: DAG nodes/edges/triggers, engine walk, n8n converter, run model,
webhook route, chat tools, Studio routes, canvas v2, seeds, tests.
- [x] P001 DAG spec v2 (nodes/edges/triggers) — `nova_ai_flow_spec.py`
- [x] P002 Engine walks DAG, branch ports — `nova_ai_flow_engine.py`
- [x] P003 n8n converter import/export — `nova_ai_flow_n8n.py`
- [x] P004 Run history model — `nova_ai_flow_run.py`
- [x] P005 Webhook entry — `nova_ai_flow_hook.py`
- [x] P006 Chat tools draft/update/export/import — write_tools + registry
- [x] P007 Studio routes (runs/detail/import/export/copilot)
- [x] P008 Canvas v2 + execution IO + copilot drawer
- [x] P009 Graph seed data + tool grants + migration
- [x] P010 Graph test suite — `test_nova_ai_flow_graph.py`
- [x] P011 Spec v3: per-node `retry`, `timeoutMs`, `onError`, `executeOnce`, `alwaysOutput`
- [x] P012 Spec v3: `credentials` refs (named vault entries, inline-secret guard)
- [x] P013 Spec v3: trigger schema validation (webhook path/HMAC, schedule cron/interval, record model/event)
- [x] P014 Spec v3: settings block (executionOrder, timezone, errorWorkflow, retentionDays)
- [ ] P015 Spec v3: sticky-note/pin schema (pins feed test runs like n8n)
- [ ] P016 Forward-ref check across subflow boundaries
- [ ] P017 Max graph limits config (nodes/items/bytes via ir.config_parameter)
- [ ] P018 Spec docs + JSON schema file for canvas validation
- [ ] P019 Legacy steps → graph migration coverage for all 5 library files
- [ ] P020 Spec property tests (cycle, unreachable, duplicate id, unknown type)

## Wave B — Expression engine (P021–P030)
- [ ] P021 `={{...}}` + `$json/$input/$node/$workflow/$execution/$now/$item/$index`
- [ ] P022 String/number/date helpers (n8n subset: split, join, toInt, toFloat, isEmpty)
- [ ] P023 Date/time with timezone (user tz → UTC, cron-aware)
- [ ] P024 JMESPath-lite for nested pick (`rows[?amount>100].id`)
- [ ] P025 Expression sandbox: no attribute escape, capped eval time
- [ ] P026 Expression error messages name the node + snippet
- [ ] P027 `$prevNode` / `$input.first()` / `$input.all()` aliases
- [ ] P028 Per-item vs whole-list modes documented + tested
- [ ] P029 Expression caching for loop bodies (perf)
- [ ] P030 Expression fuzz tests (200 cases)

## Wave C — Engine runtime (P031–P050)
- [x] P031 Retry with backoff per node (retry honored inline; timeoutMs accepted)
- [x] P032 onError=continue routes to the error port (+ $error in both paths, empty-target fallthrough); stop aborts with node context
- [x] P033 executeOnce honored; alwaysOutputData accepted
- [x] P034 Disabled skips all ports; pins short-circuit leaf nodes on interactive triggers
- [ ] P035 Loop item streaming (no full-list materialization over cap)
- [ ] P036 Merge modes: append / mergeByKey / chooseBranch
- [ ] P037 Switch rules: equals/contains/regex/empty + fallback port
- [ ] P038 IF rich operators (exists, in, between, startsWith)
- [x] P039 Code node: threaded timeout (timeoutMs, cap 120s), 8000-char input cap, 50KB output cap
- [x] P040 Code node: $input {first,last,all} per loop item + `result` contract + oversize refusal
- [ ] P041 HTTP: methods POST/PUT/PATCH/DELETE allowlist per admin setting
- [ ] P042 HTTP: headers/auth from vault only, response pagination helper
- [ ] P043 HTTP: binary download guard (size cap, type allowlist)
- [ ] P044 Subflow namespaced outputs + depth guard (max 5)
- [ ] P045 Subflow error propagation with parent node context
- [ ] P046 Human approval: resume token, expiry, delegation
- [ ] P047 Approval expiry cron (stale cards auto-expire with note)
- [ ] P048 Run cancellation (user stop → status cancelled)
- [ ] P049 Partial execution rerun from failed node (n8n "retry from here")
- [ ] P050 Engine benchmarks (100-node graph < 5s overhead without IO)

## Wave D — Odoo nodes (P051–P070) — 114 biz tools landed in
`models/nova_ai_business_tools.py` (one declarative SPEC each, one
generic queue + one generic approve path, schemas auto-generated,
tool rows + tier grants via post_init + migration 20.0.1.21.0,
tests in `tests/test_nova_ai_biz_tools.py`)
- [ ] P051 odoo.search: streaming pages, order validation, field allowlist
- [ ] P052 odoo.read: batch ids, missing/gone distinction, binary exclusion
- [x] P053 odoo.create: required-field precheck, dupe guards, m2m commands (24 create tools)
- [x] P054 odoo.write: set/readonly/computed guards, confirm-card payload (31 write tools + m2m/property paths)
- [ ] P055 odoo.unlink: always card, never inline, archived-vs-deleted
- [x] P056 odoo.method: side-effect allowlist enforced, args validation (17 method tools, state guards)
- [x] P057 odoo.note/email/activity: channel post, notify followers, lead call/meeting, reminders (12 comms + email send-on-approve)
- [x] P058 read_group node odoo.group + odoo.count + odoo.email (send-on-approve)
- [ ] P059 fields_get/inspect helpers as canvas node pickers (no guessing)
- [ ] P060 Per-model test matrix: sale, purchase, stock, account, project, hr
- [ ] P061 Record-rule conformance tests (multi-company, multi-user)
- [ ] P062 Large-data tests (10k rows stream without OOM)
- [ ] P063 Onchange simulation before create/write cards
- [ ] P064 Attachment node (upload caps, type allowlist, no secrets)
- [ ] P065 Domain builder node (structured conditions → safe domain)
- [ ] P066 Chatter thread node (summarize/next-steps as graph nodes)
- [ ] P067 Mail activity done/close node
- [ ] P068 Multi-company env switching per node
- [ ] P069 Translatable content handling (subject/body langs)
- [ ] P070 Odoo node docs in canvas (per-node help + examples)

## Wave E — Triggers & infra (P071–P090)
- [x] P071 Webhook HMAC verification (X-Nova-Signature, vault ref)
- [ ] P072 Webhook per-flow rate limit + payload size cap + timestamp window
- [x] P073 Schedule trigger cron runner (interval + cron subset, overlap skip)
- [ ] P074 Record trigger via base.automation bridge (create/write/unlink/domain)
- [ ] P075 Record trigger without base.automation (model hook fallback)
- [x] P076 Secrets vault model (admin-only, audit reads; at-rest note)
- [ ] P077 Credential picker in canvas (never inline secret in JSON)
- [x] P078 Idempotency keys (run reuse on key; header passthrough)
- [ ] P079 Wait-node resume via cron (persisted wake-ups, no busy sleep)
- [x] P080 Run retention cron (trim IO first, cap rows)
- [ ] P081 Async worker (queue_job when present, thread fallback otherwise)
- [ ] P082 Overlap guard (one active execution per flow+key)
- [ ] P083 Run log rotation (node_log capped, IO truncated, index on flow_id)
- [ ] P084 Health endpoint (queue depth, last cron tick, worker RSS)
- [ ] P085 Kill-switch honors (nova_ai.disabled stops triggers + chat turns)
- [ ] P086 Trigger test-fire from canvas (sample payload → dry run)
- [ ] P087 Webhook signature rotation UI
- [ ] P088 Schedule preview (next 5 fire times for a cron)
- [ ] P089 Record trigger simulator (pick a record → show match/no-match + why)
- [ ] P090 Infra load test (100 webhooks/min, 10 schedules, overlap correct)

## Wave F — Canvas to n8n level (P091–P100)
- [ ] P091 Drag-from-port wiring (pointer events on output dots)
- [ ] P092 Port labels on wires (true/false/index/error) + color coding
- [ ] P093 Minimap + zoom-to-fit + canvas search (jump to node)
- [ ] P094 Undo/redo stack (50 steps, survives tab switch via localStorage)
- [ ] P095 Node docs panel (per-type params, examples, expression help)
- [ ] P096 Test-step: run single node with pinned/sample input
- [ ] P097 Pins: pin sample output per node, feed downstream in tests
- [ ] P098 Version snapshots + diff view (what changed between vN/vN+1)
- [ ] P099 Template gallery (10 Odoo recipes: chase, reorder, triage, onboard…)
- [ ] P100 Keyboard: delete/duplicate/align/distribute, copy/paste nodes

## Wave G — Copilot & chat (P101–P105)
- [ ] P101 Copilot streams plan before applying (no silent rewrites)
- [ ] P102 Copilot diff preview (added/removed/rewired) with Accept/Reject
- [ ] P103 One-click Apply of approved update card onto canvas
- [ ] P104 Chat "show me the flow" renders graph summary + open-canvas link
- [ ] P105 Copilot regression tests (10 utterances → expected graph shapes)

## Wave H — Hardening & release (P106–P110)
- [ ] P106 Full Odoo suite green on staging DB (all nova_ai tests)
- [ ] P107 Security pass: sandbox escape, SSRF, webhook auth, vault ACL
- [ ] P108 Perf pass: canvas 200-node render, engine 10k-item loop
- [ ] P109 Upgrade path test (1.19→1.20→next, noupdate seeds intact)
- [ ] P110 Release notes + admin runbook + version bump

## Wave I — No-code completeness (P111–P120, priority right after A-tail)
- [x] P111 Canvas describe/preview endpoints (models+fields+methods, dry-run)
- [x] P112 Guided inspector forms (trigger/odoo/if/switch/set/loop/http/ai/output)
- [ ] P113 Domain-row builder validation hints (field type-aware operators)
- [ ] P114 Values-row field picker with selection options + m2o search
- [ ] P115 Trigger forms: schedule cron helper, record model/event picker
- [ ] P116 Node templates: per-type starter configs (one click, then edit)
- [ ] P117 Inline validation messages naming node + fix (no raw tracebacks)
- [ ] P118 Copilot diff preview + one-click Apply in canvas
- [ ] P119 Template gallery: 15 Odoo flows installable from Studio
- [ ] P120 First-run wizard: goal → copilot drafts → guided refine → test → activate

Progress (loops 1-9): Wave A done; A-tail P011-P020 done; engine P031-P040 done (retry/backoff-cap/onError+routing/executeOnce/pins/IF-rich/switch-rules+fallback/merge-modes/code-timeout+$input/n8n-canonical validation); Wave D biz pack done (113 tools, send-on-approve email, multilevel BOM, vault-backed HMAC); triggers P071 HMAC (tested, fail-closed)/P073 scheduler + cron-match test/P078 idempotency (tested)/P080 retention (tested); P032 error ports tested; P034 pins tested + canvas copy; no-code P111-P112 + vault UI + describe scoping; security pass (webhook runs as creator, studio gates, XSS t-esc, uniform gone/denied, fail-closed import). Open: P041-P044 file/net nodes, P042 vault HTTP auth, P043 binary guard, P072 rate limit, P079 wait-resume, P089 record trigger, P091-P100 canvas polish, Wave G copilot diff, Wave H staging suite.
