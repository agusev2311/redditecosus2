from __future__ import annotations

from functools import wraps
from typing import Any, Callable

import jwt
from flask import g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from app.config import settings
from app.db.session import SessionLocal
from app.models import User, UserRole


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    return check_password_hash(password_hash, password)


def issue_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role.value,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def _load_user_from_request() -> User | None:
    header = request.headers.get("Authorization", "")
    token = ""
    if header.startswith("Bearer "):
        token = header.removeprefix("Bearer ").strip()
    elif request.args.get("token"):
        token = request.args["token"]

    if not token:
        return None

    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        return None

    session = SessionLocal()
    try:
        user = session.get(User, int(payload.get("sub")))
        if user is None:
            return None
        session.expunge(user)
        return user
    finally:
        session.close()


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _load_user_from_request()
        if user is None:
            return jsonify({"error": "Authentication required"}), 401
        g.current_user = user
        return view(*args, **kwargs)

    return wrapped


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        user: User = g.current_user
        if user.role != UserRole.admin:
            return jsonify({"error": "Admin access required"}), 403
        return view(*args, **kwargs)

    return wrapped

