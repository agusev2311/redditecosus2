from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.db.session import SessionLocal
from app.services.tag_catalog import build_tag_catalog_payload, count_pending_tag_descriptions, get_tag_description_coordinator
from app.utils.auth import member_required


tags_bp = Blueprint("tags", __name__)


@tags_bp.get("/tags")
@member_required
def list_tags():
    session = SessionLocal()
    try:
        payload = build_tag_catalog_payload(
            session,
            g.current_user,
            q=(request.args.get("q") or "").strip(),
            kind=request.args.get("kind") or None,
            described=request.args.get("described") or None,
            limit=int(request.args.get("limit") or 250),
            owner_id=int(request.args["owner_id"]) if request.args.get("owner_id") else None,
        )
        return jsonify(payload)
    finally:
        session.close()


@tags_bp.post("/tags/backfill-missing")
@member_required
def backfill_missing_tags():
    pending = count_pending_tag_descriptions(None if g.current_user.role.value == "admin" else g.current_user.id)
    get_tag_description_coordinator().notify_backfill_needed()
    return jsonify(
        {
            "pending_tags": pending,
            "started": pending > 0,
            "message": "Backfill запущен для тегов без AI-описания." if pending else "Все теги уже описаны.",
        }
    )
