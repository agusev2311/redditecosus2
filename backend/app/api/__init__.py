from flask import Flask

from app.api.admin import admin_bp
from app.api.auth import auth_bp
from app.api.backups import backups_bp
from app.api.dashboard import dashboard_bp
from app.api.media import media_bp
from app.api.tags import tags_bp


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(media_bp, url_prefix="/api")
    app.register_blueprint(tags_bp, url_prefix="/api")
    app.register_blueprint(dashboard_bp, url_prefix="/api")
    app.register_blueprint(backups_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api")
