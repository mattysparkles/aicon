import os
from flask import Flask
from handlers.call_handler import init_app
from utils.logger import configure_logging


def create_app():
    app = Flask(__name__)
    configure_logging()
    init_app(app)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
