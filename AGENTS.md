AICon — Agent Guide and Project Overview

Purpose
- Ensure AI code agents make safe, consistent, and non-disruptive changes.
- Keep Voice and SMS feature parity unless explicitly stated otherwise.
- Provide project context, prerequisites, and a lightweight changelog.

Core Principles
- Parity: Any change to Voice flows must be mirrored for SMS, and vice versa, unless the task explicitly scopes otherwise.
- Minimal surface area: Touch only the modules related to the task. Do not refactor unrelated code.
- Backward compatibility: Maintain existing routes, env vars, and DB models unless the change is coordinated.
- No secrets in code: Use env vars and existing helpers. Never hardcode keys.
- Safe fallbacks: Never 500 to Twilio. Prefer gentle text/voice fallbacks instead of errors.

Key Components
- Entry: `app.py` (Flask app factory, WSGI setup, pre-warming TTS)
- Voice/SMS Webhooks: `handlers/call_handler.py`
  - Routes: `/voice`, `/play`, `/voice/idle_check`, `/twilio` (unified), `/onboard` (alias)
  - Behavior: Voice greeting, onboarding detection, security phrase flow, payments, memory, pause, SMS command handling
- Onboarding Flow: `handlers/onboarding.py`
  - Conversation state machine for both Voice and SMS
  - Flow key: `FLOW = "onboard"`, steps like `ask_has_account`, `ask_name`, `ask_prison_id`, `ask_affiliate`, `ask_support`
- TTS: `handlers/tts.py` (ElevenLabs playback, optional <Say> fallback)
- GPT Agent: `handlers/gpt_agent.py` (LLM responses with memory)
- DB Session & Models: `utils/db.py`, `utils/models.py`
- State & Utilities: `utils/call_state.py`, `utils/job_store.py`, `utils/transcript_logger.py`

Voice/SMS Parity Rules
- If a prompt or decision is added to Voice, add the same to SMS.
- If Voice accepts synonyms/DTMF, SMS should accept text variants (and vice versa).
- Keep onboarding state logic identical in spirit (same step names and transitions) for both channels.
- When Voice uses environment-configured greetings or pauses, ensure SMS uses consistent text prompts/greetings.

Onboarding Line Behavior
- If the call/text is to the onboarding number (`ONBOARDING_PHONE_NUMBER`), tag state data with `{"line": "onboarding"}` and use onboarding prompts.
- If a caller answers “yes” to “Do you already have an account?” on the onboarding line, respond:
  - Voice: advise to call their individual assigned number; then ask if they need help retrieving that number or other support.
  - SMS: same content; proceed to `ask_support` step.
- `ask_support` step options:
  - number: Explain how to retrieve their assigned number (SMS keyword `number`, or similar guidance in Voice).
  - support: Explain to text `help` for a human follow-up (existing help menu applies).
  - no: End the flow politely.

Call Handling Expectations
- Facility pre-announcements: Use a short pre-prompt pause on the onboarding line so prompts aren’t missed.
  - Env var: `PRE_PROMPT_PAUSE_SECONDS` (default 3 seconds)
- Always gather speech and DTMF where appropriate. Treat DTMF as input while in onboarding.
- Use `_play_elabs` to prefer ElevenLabs MP3 playback with optional <Say> fallback controlled by `ALLOW_TWILIO_SAY_FALLBACK`.

SMS Command Expectations
- `help` returns a help primer. Keep concise and accurate.
- `number` returns the caller’s assigned number if available; otherwise provide guidance.
- Security phrase: `set pass <phrase>` and `verify pass <phrase>` already exist.
- Billing: `pay [plan] [crypto]` produces a link via Stripe.

Environment Variables
- Twilio: `TWILIO_*`, optional separate `ONBOARDING_PHONE_NUMBER`, optional `MASTER_PHONE_NUMBER`.
- TTS: `ELEVENLABS_*`, `ALLOW_TWILIO_SAY_FALLBACK`.
- Voice behavior: `GATHER_TIMEOUT`, `PRE_PROMPT_PAUSE_SECONDS`.
- DB and server: `DATABASE_URL`, `PORT`, `HOST`, `FLASK_SECRET_KEY`.

Coding Guidelines
- Match existing style and minimal diffs.
- Log sparingly; avoid noisy logs in high-frequency routes.
- Don’t introduce new external dependencies without approval.
- Keep responses resilient: catch exceptions in webhook handlers and provide fallback responses.

Testing/Validation
- Manual: simulate Twilio POSTs (where possible) and verify TwiML shape, or exercise branches by unit-testing pure functions.
- When adding prompts, ensure both Voice and SMS paths are updated.
- Keep changes behind env flags when behavior could be environment-sensitive.

Changelog (human-maintained summary)
- 2025-09-19: Onboarding yes/no robustness; accept variants and DTMF 1/2. Voice now forwards DTMF into onboarding when applicable.
- 2025-09-19: Onboarding line flow updated: “yes” prompts advisory to use assigned number and offers `ask_support` options (number/support/no). SMS mirrored.
- 2025-09-19: Added `PRE_PROMPT_PAUSE_SECONDS` and initial pause on onboarding line to avoid facility pre-announcement collisions.
- 2025-09-19: Added SMS command `number` to return assigned number when available.

Do/Don’t Checklist
- Do update both Voice and SMS for flow changes.
- Do keep Twilio responses fast and never return 500s.
- Don’t hardcode phone numbers, voice IDs, or keys.
- Don’t refactor across modules unless required for the task.

