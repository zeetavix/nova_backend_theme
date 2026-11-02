# n8n ↔ Nova Flow coverage (source: n8n-io/n8n, packages/nodes-base)

Legend: DONE (shipped) · PARTIAL (works, gaps noted) · PLANNED (phase).
Node names/types are the exact n8n identifiers so workflows paste 1:1.

## Triggers
| n8n node | n8n type | Nova | Status |
|---|---|---|---|
| Manual Trigger | n8n-nodes-base.manualTrigger | trigger | DONE |
| Start | n8n-nodes-base.start | start/trigger | DONE |
| Webhook | n8n-nodes-base.webhook | trigger + /hook/ route | DONE (HMAC verify, POST-only, webhook-only triggers; P072 rate-limit pending) |
| Schedule Trigger | n8n-nodes-base.scheduleTrigger | trigger | DONE (interval+cron runner, overlap skip) |
| Execute Workflow Trigger | n8n-nodes-base.executeWorkflowTrigger | trigger | DONE (subflow entry) |
| Chat Trigger | n8n-nodes-base.chatTrigger | trigger=chat | PARTIAL (drawer chat exists, trigger node planned P089) |
| Form Trigger/Form Ending | n8n-nodes-base.formTrigger | — | PLANNED (P091 portal form) |
| Error Trigger | n8n-nodes-base.errorTrigger | — | PLANNED (P032 errorWorkflow) |
| n8n Trigger / Active workflows | n8n-nodes-base.n8nTrigger | — | PLANNED (bridge to n8n cloud, P092) |
| App triggers (Gmail/Slack/Stripe/…) | various | — | PLANNED (generic webhook-mapped subset, P093) |

## Flow logic (core)
| n8n node | Nova | Status |
|---|---|---|
| IF (conditions, AND/OR, data types) | if | DONE (P038 rich ops) |
| Switch (rules mode, rename output) | switch | DONE (P037 rules+rename+fallback) |
| Merge (append/combine/chooseBranch/SQL/multiplex, 3+ inputs) | merge | DONE (P036 append/chooseBranch/first) |
| Filter | filter | DONE (branch true/false, tested) |
| Loop Over Items (batching) | loop.foreach | PARTIAL (P035 streaming pending) |
| Split In Batches (legacy) | loop.foreach | DONE (alias) |
| Limit / Sort / Aggregate / Remove Duplicates / Compare Datasets | — | PLANNED (P041 set) |
| Date & Time | — | PLANNED (P023 helpers) |
| Wait (time/interval/webhook-call) | wait | PARTIAL (P079 resume pending) |
| Stop and Error | output (error) | DONE (alias) |
| NoOp / Sticky Note | note | DONE |

## Data transform
| n8n node | Nova | Status |
|---|---|---|
| Set / Edit Fields (assignments, dot-notation, toggleables) | set | DONE (assignments; dot-notation via paths) |
| Code (JS/Python, $input/$json, cont. mode) | code | DONE (P039/P040 sandbox+timeout+input; Python only) |
| Crypto (hash/HMAC/sign) | — | PLANNED (P043, vault-backed) |
| HTML / Markdown / XML / JSON / Spreadsheet File / Compression / Extract from File / Convert to File | — | PLANNED (P044 file nodes, size-capped) |
| Rename Keys / Item Lists ops | set | PLANNED (P041) |

## Network & files
| n8n node | Nova | Status |
|---|---|---|
| HTTP Request (all methods, auth, pagination, streaming) | http | PARTIAL (GET-only policy + SSRF guard; methods/vault-auth planned) |
| Respond to Webhook (all/last/first/json/stream) | respond | PARTIAL (P094 modes) |
| GraphQL / FTP / SSH / MQTT / Kafka / AMQP / Redis / WebSocket | — | PLANNED (P095 infra nodes, allowlisted) |

## Sub-workflows & human
| n8n node | Nova | Status |
|---|---|---|
| Execute Workflow | subflow | DONE (depth cap + self-call refuse + cards/needs_approval propagation; errorWorkflow P055 planned) |
| Human in the Loop (approve/text/form) | human.approval | PARTIAL (P046 resume/expiry/delegation) |

## AI (LangChain-era nodes)
| n8n node | Nova | Status |
|---|---|---|
| AI Agent (tools/memory/fallback) | ai.agent | PARTIAL (agent turn exists; tools/memory mapping P051) |
| Basic LLM Chain / Summarization / QA / Classifier / Sentiment | ai.prompt | PARTIAL (P052 presets) |
| Vector Store / Embeddings / Retriever / MCP | — | PLANNED (P053 RAG bridge to nova knowledge) |

## Execution semantics
| n8n behavior | Nova | Status |
|---|---|---|
| Per-item execution, $input.first/last/all, paired items | DONE (aliases + $input contract) |
| Retry per node + backoff | DONE (P031 inline retry+cap; P054 UI pending) |
| onError continue/stop + Error Trigger workflow | DONE (P032 continue+routing; errorWorkflow P055) |
| executeOnce / alwaysOutputData / disabled / pins | DONE (P033/P034 engine; pins UI P097) |
| Test step / partial execution / debug in editor | PLANNED (P096) |
| Active/inactive, versioned saves, templates, evaluations | PLANNED (P098–P100, settings P014 done-spec) |

## Odoo-native (no n8n equivalent — our edge)
CRUD/search/read_group/method/activity/note/email, record triggers,
approval cards, audit log, chat copilot — DONE/PARTIAL per Wave D.
