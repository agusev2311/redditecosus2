import os

from flask import Flask
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from app.api import register_blueprints
from app.config import settings
from app.db.session import init_db
from app.services.archive import cleanup_archive_staging
from app.services.audit import configure_logging
from app.services.processing import get_processing_coordinator
from app.services.storage import ensure_storage_layout
from app.services.tag_catalog import get_tag_description_coordinator


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["MAX_CONTENT_LENGTH"] = None
    app.config["JSON_SORT_KEYS"] = False
    if settings.trust_reverse_proxy:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)  # type: ignore[assignment]
    CORS(app, origins=list(settings.frontend_origins), supports_credentials=True)

    ensure_storage_layout()
    configure_logging()
    init_db()
    cleanup_archive_staging()
    register_blueprints(app)
    should_boot_workers = settings.env != "development" or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if settings.enable_processing and should_boot_workers:
        get_processing_coordinator().boot()
        get_tag_description_coordinator().boot()
    return app
