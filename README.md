# aicon

A cloud-based voice-to-AI call assistant built with Flask. It receives phone calls via Twilio, transcribes audio with Whisper, sends the transcript to the OpenAI API, generates a spoken reply, and can optionally trigger SMS, email, or remote SSH commands.

## Features
- **Voice Calls** via Twilio webhooks
- **Transcription** using OpenAI Whisper
- **Conversational AI** with the OpenAI Chat API
- **Text‑to‑Speech** for responses
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
utils/
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
4. Configure your Twilio voice webhook to point to `/voice` on your server.

## Environment Variables
The application uses the following variables:
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
- `OPENAI_API_KEY`
- `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`
- `TWILIO_TEST_NUMBER` (optional SMS demo)
- `TEST_EMAIL` (optional email demo)
- `SSH_HOST`, `SSH_USER`, `SSH_KEY_PATH`, `SSH_ALLOWED_HOSTS`

## Safety
SSH execution is disabled unless the host is present in `SSH_ALLOWED_HOSTS`. SMS messages are chunked to avoid exceeding Twilio limits.

## License
MIT
