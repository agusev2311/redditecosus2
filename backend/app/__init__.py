import os

from flask import Flask
from flask_cors import CORS

from app.api import register_blueprints
from app.config import settings
from app.db.session import init_db
from app.services.audit import configure_logging
from app.services.processing import get_processing_coordinator
from app.services.storage import ensure_storage_layout


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["MAX_CONTENT_LENGTH"] = None
    app.config["JSON_SORT_KEYS"] = False
    CORS(app, origins=[settings.frontend_url], supports_credentials=True)

    ensure_storage_layout()
    configure_logging()
    init_db()
    register_blueprints(app)
    should_boot_workers = settings.env != "development" or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if should_boot_workers:
        get_processing_coordinator().boot()
    return app
