# aicon

A cloud-based voice-to-AI call assistant built with Flask. It receives phone calls via Twilio, transcribes audio with Whisper, sends the transcript to the OpenAI API, generates a spoken reply, and can optionally trigger SMS, email, or remote SSH commands.

## Features
- **Voice Calls** via Twilio webhooks
- **Transcription** using OpenAI Whisper
- **Conversational AI** with the OpenAI Chat API
- **Text‑to‑Speech** with ElevenLabs (custom voice) for greeting and replies, with Twilio Polly fallback
- **SMS and Email** helpers for outbound messages
- **Optional SSH** command execution with safety checks

## Project Layout
```
app.py                # Flask entrypoint
handlers/
  call_handler.py     # Twilio voice routes and conversation flow
  transcription.py    # Whisper transcription
  gpt_agent.py        # OpenAI chat helper
  tts.py              # Text-to-speech
  sms.py              # Twilio SMS helper
  email.py            # SendGrid email helper
  ssh.py              # Optional SSH tool
  voices.py           # Voice mapping + per-user preference
utils/
  db.py               # SQLAlchemy engine + session helpers
  models.py           # Interaction + UserPreference models
  logger.py           # Logging configuration
templates/
  call_flow.xml       # Example TwiML template
requirements.txt
.env.example          # Environment variable template
```

## Quick Start
1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in credentials.
3. Run the app:
   ```bash
   python app.py
   ```
4. Configure your Twilio Voice and Messaging webhooks to point to `/twilio` on your server (unified handler for calls and SMS). `/voice` remains for backward compatibility.

## Environment Variables
The application uses the following variables:
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
- `OPENAI_API_KEY`
- `ELEVENLABS_API_KEY` (for custom voice TTS)
- `ELEVENLABS_VOICE_ID` (your ElevenLabs voice ID)
- `GREETING_TEXT` (optional custom greeting text)
- `DATABASE_URL` (optional; Postgres connection string; defaults to local SQLite `aicon.db`)
- `VOICE_MAP` (optional; JSON mapping of voice keyword to ElevenLabs voice ID)
- `GATHER_TIMEOUT` (optional; seconds to wait for voice input; default 8)
- `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`
- `TWILIO_TEST_NUMBER` (optional SMS demo)
- `TEST_EMAIL` (optional email demo)
- `SSH_HOST`, `SSH_USER`, `SSH_KEY_PATH`, `SSH_ALLOWED_HOSTS`

Notes:
- If `ELEVENLABS_API_KEY` and a voice are set (either `ELEVENLABS_VOICE_ID` or via `VOICE_MAP`/SMS keyword), the app generates and plays TTS using ElevenLabs. Otherwise, it falls back to Twilio Polly.
- Set `GREETING_TEXT` to override the first rotating greeting option.

## Unified Webhook, Dynamic Greeting, Logging, and Voice Management

- Use `/twilio` for both Voice and Messaging webhooks. The app auto-detects and returns the correct TwiML.
- Calls start with a rotating greeting like “Hey, it's Sparkles — what's on your mind today?” and gather speech for up to `GATHER_TIMEOUT` seconds.
- If the caller is silent, we play: “Still with me? Just say something or hang tight!” and continue the gather.
- Every SMS and voice turn logs to the database with user id (phone), input type, transcript, response, model, and voice id (for voice calls).
- SMS commands for voice selection:
  - `voice list` — show available voice keywords from `VOICE_MAP`
  - `voice <keyword>` — set voice preference (e.g., `voice sparkles`)
  - `upgrade voice <keyword>` — alias to set voice preference

## Billing & Subscriptions (Phase 2)

- Account via SMS/voice: Text or say `signup` to start onboarding. Prompts collect name, prison ID, and affiliate code, then store a user profile.
- Voice credit card capture: Say `pay` during a call to activate Twilio `<Pay>` to securely capture card details.
- SMS payment link: Text `pay [plan] [crypto]` (e.g., `pay pro crypto`) to receive a Stripe Checkout link. Crypto option applies a 25% discount via a Stripe coupon.
- Plans and discounts: Configure Stripe price IDs for `basic` and `pro` plans and an optional `STRIPE_COUPON_CRYPTO` for the crypto discount.
- Affiliate tracking: Provide users with codes in the `affiliates` table; when new signups include a code, referral entries are created and commissions computed.

Environment additions:
- `STRIPE_API_KEY`, `STRIPE_PRICE_BASIC`, `STRIPE_PRICE_PRO`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`, `STRIPE_COUPON_CRYPTO`
- `TWILIO_PAY_CONNECTOR` (for `<Pay>`)

## Admin Endpoints (Optional)

- `/admin/interactions?limit=50` — recent interactions as JSON.
- `/admin/preferences` — user preferences as JSON.
- If `ADMIN_TOKEN` is set, append `?token=<token>` to access.

## Safety
SSH execution is disabled unless the host is present in `SSH_ALLOWED_HOSTS`. SMS messages are chunked to avoid exceeding Twilio limits.

## License
MIT
