from __future__ import annotations

from flask import Blueprint, g, jsonify, request, send_file
from sqlalchemy.orm import joinedload

from app.db.session import SessionLocal
from app.models import MediaItem, ShareLink
from app.services.audit import audit
from app.services.guest_access import media_item_visible_to_user
from app.services.share_links import (
    burn_share_link,
    create_share_link,
    open_public_share,
    parse_share_constraints,
    serialize_share,
    share_asset_token_ttl_seconds,
    shares_query_for_user,
    user_can_manage_share,
    verify_share_asset_token,
)
from app.services.storage import absolute_media_path, absolute_thumbnail_path
from app.utils.auth import member_required


shares_bp = Blueprint("shares", __name__)


def _send_uncached_file(path, *, mimetype: str | None, download_name: str | None = None):
    response = send_file(
        path,
        mimetype=mimetype,
        download_name=download_name,
        conditional=True,
        max_age=0,
        etag=False,
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _base_url() -> str:
    return request.host_url.rstrip("/")


def _shares_query(session):
    return session.query(ShareLink).options(
        joinedload(ShareLink.media),
        joinedload(ShareLink.created_by),
    )


@shares_bp.get("/shares")
@member_required
def list_shares():
    session = SessionLocal()
    try:
        query = shares_query_for_user(_shares_query(session), g.current_user)
        media_id = (request.args.get("media_id") or "").strip()
        if media_id:
            query = query.filter(ShareLink.media_id == media_id)
        rows = query.order_by(ShareLink.created_at.desc()).limit(300).all()
        return jsonify({"items": [serialize_share(row, base_url=_base_url()) for row in rows]})
    finally:
        session.close()


@shares_bp.post("/media/<media_id>/shares")
@member_required
def create_share(media_id: str):
    payload = request.get_json(force=True) or {}
    session = SessionLocal()
    try:
        media = session.get(MediaItem, media_id)
        if media is None or not media_item_visible_to_user(media, g.current_user):
            return jsonify({"error": "Not found"}), 404
        expires_at, max_views = parse_share_constraints(payload)
        share = create_share_link(
            session,
            media=media,
            created_by_id=g.current_user.id,
            expires_at=expires_at,
            max_views=max_views,
        )
        session.commit()
        share = _shares_query(session).filter(ShareLink.id == share.id).first()
        audit(
            "share.created",
            f"Created share link for {media.original_filename}",
            actor_id=g.current_user.id,
            owner_id=media.owner_id,
            context={"share_id": share.id, "media_id": media.id, "max_views": max_views, "expires_at": share.expires_at.isoformat() if share.expires_at else None},
        )
        return jsonify({"share": serialize_share(share, base_url=_base_url())}), 201
    except ValueError as exc:
        session.rollback()
        return jsonify({"error": str(exc)}), 400
    finally:
        session.close()


@shares_bp.post("/shares/<share_id>/burn")
@member_required
def burn_share(share_id: str):
    session = SessionLocal()
    try:
        share = _shares_query(session).filter(ShareLink.id == share_id).first()
        if share is None or share.media is None or not user_can_manage_share(g.current_user, share):
            return jsonify({"error": "Not found"}), 404
        burn_share_link(share)
        session.commit()
        audit(
            "share.burned",
            f"Burned share link for {share.media.original_filename}",
            actor_id=g.current_user.id,
            owner_id=share.media.owner_id,
            context={"share_id": share.id, "media_id": share.media_id},
        )
        return jsonify({"share": serialize_share(share, base_url=_base_url())})
    finally:
        session.close()


@shares_bp.get("/shares/public/<share_id>")
def open_share_public(share_id: str):
    session = SessionLocal()
    try:
        share = _shares_query(session).filter(ShareLink.id == share_id).first()
        if share is None or share.media is None:
            return jsonify({"error": "Share not found"}), 404
        try:
            opened_share, asset_token = open_public_share(session, share)
        except ValueError as exc:
            return jsonify({"error": "Share unavailable", "status": str(exc)}), 410
        return jsonify(
            {
                "share": serialize_share(opened_share, base_url=_base_url(), public_asset_token=asset_token),
                "asset_token_ttl_seconds": share_asset_token_ttl_seconds(),
            }
        )
    finally:
        session.close()


@shares_bp.get("/shares/public/<share_id>/file")
def stream_shared_file(share_id: str):
    access_token = (request.args.get("access_token") or "").strip()
    if not access_token:
        return jsonify({"error": "Share access token is required"}), 403

    session = SessionLocal()
    try:
        share = _shares_query(session).filter(ShareLink.id == share_id).first()
        if share is None or share.media is None:
            return jsonify({"error": "Share not found"}), 404
        try:
            verify_share_asset_token(access_token, share_id)
        except PermissionError:
            return jsonify({"error": "Invalid share access token"}), 403
        return _send_uncached_file(
            absolute_media_path(share.media),
            mimetype=share.media.mime_type,
            download_name=share.media.original_filename,
        )
    finally:
        session.close()


@shares_bp.get("/shares/public/<share_id>/thumbnail")
def stream_shared_thumbnail(share_id: str):
    access_token = (request.args.get("access_token") or "").strip()
    if not access_token:
        return jsonify({"error": "Share access token is required"}), 403

    session = SessionLocal()
    try:
        share = _shares_query(session).filter(ShareLink.id == share_id).first()
        if share is None or share.media is None:
            return jsonify({"error": "Share not found"}), 404
        try:
            verify_share_asset_token(access_token, share_id)
        except PermissionError:
            return jsonify({"error": "Invalid share access token"}), 403
        thumbnail = absolute_thumbnail_path(share.media)
        if thumbnail is None or not thumbnail.exists():
            return jsonify({"error": "Thumbnail missing"}), 404
        return _send_uncached_file(thumbnail, mimetype="image/jpeg")
    finally:
        session.close()
