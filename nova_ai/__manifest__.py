# Part of Nova. See LICENSE file for full copyright and licensing details.
{
    "name": "Nova AI",
    "summary": "Model-agnostic AI agents for the Nova backend theme",
    "description": """
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
""",
    "version": "20.0.1.22.0",
    "category": "Productivity",
    "author": "Zeetavix",
    "maintainer": "Zeetavix",
    "support": "support@zeetavix.com",
    "website": "https://zeetavix.com",
    "license": "OPL-1",
    "price": 0.0,
    "currency": "USD",
    "images": [
        "static/description/img/01_studio.png",
        "static/description/img/02_agent_config.png",
        "static/description/img/03_chatter_ai.png",
        "static/description/img/04_thread_reader.png",
        "static/description/img/05_nova_reply.png",
        "static/description/img/06_editor.png",
        "static/description/img/07_flow.png",
        "static/description/img/08_tools.png",
        "static/description/img/09_models.png",
    ],
    "depends": ["nova_backend_theme", "mail"],
    "data": [
        "security/nova_ai_security.xml",
        "security/ir.access.csv",
        "data/nova_ai_tool_data.xml",
        "data/nova_ai_agent_data.xml",
        "data/nova_ai_prompt_data.xml",
        "data/nova_ai_knowledge_data.xml",
        "data/nova_ai_knowledge_odoo_data.xml",
        "data/nova_ai_flow_data.xml",
        "data/nova_ai_flow_library_data.xml",
        "data/nova_ai_flow_library2_data.xml",
        "data/nova_ai_flow_library3_data.xml",
        "data/nova_ai_flow_graph_data.xml",
        "data/nova_ai_flow_more_data.xml",
        "data/nova_ai_flow_cron_data.xml",
        "data/nova_ai_schedule_data.xml",
        "views/nova_ai_provider_views.xml",
        "views/nova_ai_model_views.xml",
        "views/nova_ai_agent_views.xml",
        "views/nova_ai_tool_views.xml",
        "views/nova_ai_session_views.xml",
        "views/nova_ai_schedule_views.xml",
        "views/nova_ai_prompt_views.xml",
        "views/nova_ai_source_views.xml",
        "views/nova_ai_knowledge_views.xml",
        "views/nova_ai_flow_views.xml",
        "views/nova_ai_flow_run_views.xml",
        "views/nova_ai_credential_views.xml",
        "views/nova_ai_menus.xml",
        "views/nova_ai_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "nova_ai/static/src/fields/nova_tier_badge.js",            "nova_ai/static/src/fields/nova_tier_badge.xml",
            "nova_ai/static/src/fields/nova_tier_badge.scss",
            "nova_ai/static/src/scss/nova_ai_nocontent.scss",
            "nova_ai/static/src/chat/nova_ai_drawer.js",
            "nova_ai/static/src/chat/nova_ai_drawer.xml",
            "nova_ai/static/src/chat/nova_ai_drawer.scss",
            "nova_ai/static/src/chat/nova_ai_command_provider.js",
            "nova_ai/static/src/chatter/nova_ai_chatter_patch.js",
            "nova_ai/static/src/chatter/nova_ai_chatter.xml",
            "nova_ai/static/src/chatter/nova_ai_chatter.scss",
            "nova_ai/static/src/chatter/nova_ai_text.js",
            "nova_ai/static/src/chatter/nova_ai_message_actions.js",
            "nova_ai/static/src/editor/nova_ai_powerbox_plugin.js",
            "nova_ai/static/src/editor/nova_ai_prompt_dialog.js",
            "nova_ai/static/src/editor/nova_ai_prompt_dialog.scss",
            "nova_ai/static/src/home/nova_ai_digest.js",
            "nova_ai/static/src/home/nova_ai_digest.xml",
            "nova_ai/static/src/home/nova_ai_digest.scss",
            "nova_ai/static/src/studio/nova_ai_studio.js",
            "nova_ai/static/src/studio/nova_ai_studio.xml",
            "nova_ai/static/src/studio/nova_ai_studio.scss",
            "nova_ai/static/src/flow/nova_flow_canvas.js",
            "nova_ai/static/src/flow/nova_flow_canvas.xml",
            "nova_ai/static/src/flow/nova_flow_canvas.scss",
            "nova_ai/static/src/views/nova_ai_list_actions.js",
            "nova_ai/static/src/voice/nova_speech_gate.js",
            "nova_ai/static/src/voice/nova_wake_phrase.js",
            "nova_ai/static/src/voice/nova_write_intent.js",
            "nova_ai/static/src/voice/nova_voice_recorder.js",
            "nova_ai/static/src/voice/nova_voice_audio.js",
            # nova_pcm16_processor.js is intentionally NOT bundled: the
            # AudioContext fetches it by URL, and bundling would break
            # registerProcessor (worklet scope only). Plain static serve
            # is all it needs.
        ],
        "web.assets_web_dark": [
            "nova_ai/static/src/fields/nova_tier_badge.dark.scss",
            "nova_ai/static/src/scss/nova_ai_nocontent.dark.scss",
            "nova_ai/static/src/chat/nova_ai_drawer.dark.scss",
            "nova_ai/static/src/chatter/nova_ai_chatter.dark.scss",
            "nova_ai/static/src/editor/nova_ai_prompt_dialog.dark.scss",
            "nova_ai/static/src/home/nova_ai_digest.dark.scss",
            "nova_ai/static/src/studio/nova_ai_studio.dark.scss",
            "nova_ai/static/src/flow/nova_flow_canvas.dark.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
}
