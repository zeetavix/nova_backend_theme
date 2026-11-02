# Nova AI

Model-agnostic AI agents for the Nova backend theme

Nova AI
=======

A clean-room (OPL-1) AI agent layer for Odoo Community that ships with
the Nova backend theme. Connect any model — local (Ollama, LM Studio,
llama.cpp, vLLM) or hosted (OpenAI-compatible: OpenAI, OpenRouter,
Groq, Together, …) — and run agents inside the Nova design system.

Capability tiers make weak models useful instead of broken:
T1 BASIC (no tool calling) runs deterministic server-side micro-tools
with confirm cards; T2 ASSISTANT gets the full tool loop; T3 AGENT
(unlocked only with a large model + admin toggle) may auto-apply writes
behind a complete audit log.

No Odoo-hosted model, no IAP, no Enterprise code copied. Depends on the
Nova backend theme + mail (chatter actions) and degrades without them.

Voice works out of the box through the browser (mic and speakers,
zero server cost). For server-side hearing and speech with no
third-party key, open Settings → Nova AI, pick a voice, and press
"Prepare offline voice": faster-whisper and Piper run in-process —
no service to install, no keys to paste. Server voice is strictly
opt-in and refuses work when the server is busy.

| | |
|---|---|
| Odoo | 20.0 Community |
| Depends | `nova_backend_theme`, `mail` |
| License | OPL-1 (Odoo Proprietary License v1.0) |
| Price | **Free** |

No Enterprise code is copied or required.

## Installation

1. Copy the addon folder into your addons path.
2. Update the apps list (`-u base` or the Apps menu).
3. Install **Nova AI**.

## Support

support@zeetavix.com — https://zeetavix.com
