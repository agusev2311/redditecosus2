from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.db.session import SessionLocal
from app.models import User, UserRole
from app.services.audit import audit
from app.utils.auth import admin_required, hash_password


admin_bp = Blueprint("admin", __name__)


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role.value,
        "telegram_username": user.telegram_username,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@admin_bp.get("/users")
@admin_required
def list_users():
    session = SessionLocal()
    try:
        users = session.query(User).order_by(User.created_at.asc()).all()
        return jsonify({"items": [_serialize_user(user) for user in users]})
    finally:
        session.close()


@admin_bp.post("/users")
@admin_required
def create_user():
    from flask import g

    payload = request.get_json(force=True)
    session = SessionLocal()
    try:
        user = User(
            username=payload["username"].strip(),
            password_hash=hash_password(payload["password"]),
            role=UserRole(payload.get("role", "member")),
            telegram_username=(payload.get("telegram_username") or "").strip().lstrip("@") or None,
        )
        session.add(user)
        session.commit()
        audit("admin.user_created", f"Created user {user.username}", actor_id=g.current_user.id, owner_id=user.id)
        return jsonify({"user": _serialize_user(user)}), 201
    finally:
        session.close()


@admin_bp.patch("/users/<int:user_id>")
@admin_required
def update_user(user_id: int):
    from flask import g

    payload = request.get_json(force=True)
    session = SessionLocal()
    try:
        user = session.get(User, user_id)
        if user is None:
            return jsonify({"error": "Not found"}), 404
        if "role" in payload:
            user.role = UserRole(payload["role"])
        if "telegram_username" in payload:
            user.telegram_username = (payload.get("telegram_username") or "").strip().lstrip("@") or None
        if "password" in payload and payload["password"]:
            user.password_hash = hash_password(payload["password"])
        session.commit()
        audit("admin.user_updated", f"Updated user {user.username}", actor_id=g.current_user.id, owner_id=user.id)
        return jsonify({"user": _serialize_user(user)})
    finally:
        session.close()

