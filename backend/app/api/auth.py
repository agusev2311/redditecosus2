from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import OperationalError

from app.db.session import SessionLocal, ensure_database_schema, is_missing_table_error
from app.models import User, UserRole
from app.services.audit import audit
from app.services.guest_access import serialize_guest_access
from app.utils.auth import hash_password, issue_token, login_required, verify_password


auth_bp = Blueprint("auth", __name__)


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role.value,
        "telegram_username": user.telegram_username,
        "guest_access": serialize_guest_access(user),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@auth_bp.get("/health")
def health():
    return jsonify({"status": "ok"})


def _scalar_with_schema_retry(query_factory):
    session = SessionLocal()
    try:
        try:
            return query_factory(session)
        except OperationalError as exc:
            if not is_missing_table_error(exc):
                raise
    finally:
        session.close()

    ensure_database_schema()

    session = SessionLocal()
    try:
        return query_factory(session)
    finally:
        session.close()


@auth_bp.get("/auth/bootstrap-status")
def bootstrap_status():
    return jsonify({"needs_bootstrap": _scalar_with_schema_retry(lambda session: session.query(User).count()) == 0})


@auth_bp.post("/auth/bootstrap")
def bootstrap():
    payload = request.get_json(force=True)
    for attempt in range(2):
        session = SessionLocal()
        try:
            if session.query(User).count() > 0:
                return jsonify({"error": "Bootstrap already completed"}), 409
            user = User(
                username=payload["username"].strip(),
                password_hash=hash_password(payload["password"]),
                role=UserRole.admin,
                telegram_username=(payload.get("telegram_username") or "").strip().lstrip("@") or None,
            )
            session.add(user)
            session.commit()
            token = issue_token(user)
            audit("auth.bootstrap", f"Bootstrapped admin {user.username}", actor_id=user.id, owner_id=user.id)
            return jsonify({"token": token, "user": _serialize_user(user)})
        except OperationalError as exc:
            if attempt == 1 or not is_missing_table_error(exc):
                raise
            ensure_database_schema()
        finally:
            session.close()

    raise RuntimeError("Unreachable bootstrap retry state")


@auth_bp.post("/auth/login")
def login():
    payload = request.get_json(force=True)
    username = payload["username"].strip()
    for attempt in range(2):
        session = SessionLocal()
        try:
            user = session.query(User).filter(User.username == username).first()
            if user is None or not verify_password(user.password_hash, payload["password"]):
                return jsonify({"error": "Invalid credentials"}), 401
            token = issue_token(user)
            audit("auth.login", f"Logged in {user.username}", actor_id=user.id, owner_id=user.id)
            return jsonify({"token": token, "user": _serialize_user(user)})
        except OperationalError as exc:
            if attempt == 1 or not is_missing_table_error(exc):
                raise
            ensure_database_schema()
        finally:
            session.close()

    raise RuntimeError("Unreachable login retry state")


@auth_bp.get("/auth/me")
@login_required
def me():
    from flask import g

    return jsonify({"user": _serialize_user(g.current_user)})
