import os
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from handlers.call_handler import init_app
from handlers import admin as admin_handlers
from handlers import billing as billing_handlers
from handlers import web as web_handlers
from handlers import metrics as metrics_handlers
from utils.db import init_db
from handlers import tts as tts_handlers
from utils.logger import configure_logging


def create_app():
    # Load environment variables from .env early
    load_dotenv()
    app = Flask(__name__)
    # Secret key for sessions (set FLASK_SECRET_KEY in env for production)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret")
    # Ensure correct scheme/host behind reverse proxy (Caddy)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    configure_logging()
    # Initialize database tables
    init_db()
    init_app(app)
    admin_handlers.init_app(app)
    billing_handlers.init_app(app)
    app.register_blueprint(web_handlers.bp)
    app.register_blueprint(metrics_handlers.bp)
    # Pre-warm greeting audio (non-fatal if it fails)
    greeting = os.environ.get("GREETING_TEXT")
    if greeting:
        try:
            tts_handlers.generate_sparkles_voice(greeting)
        except Exception:
            pass
    # Pre-warm onboarding greeting (either custom or default onboarding prompt)
    onboarding_greeting = os.environ.get("ONBOARDING_GREETING_TEXT")
    try:
        if onboarding_greeting:
            tts_handlers.generate_sparkles_voice(onboarding_greeting)
        else:
            from handlers import onboarding as onboarding_handlers
            tts_handlers.generate_sparkles_voice(onboarding_handlers.voice_prompt("ask_has_account"))
    except Exception:
        pass
    # Root is served by web blueprint (landing page)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5050)
