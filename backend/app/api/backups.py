from __future__ import annotations

import shutil

from flask import Blueprint, g, jsonify, request, send_file

from app.db.session import SessionLocal
from app.models import BackupScope, BackupSnapshot, UserRole
from app.services.backup import backup_service, list_visible_backups
from app.services.backup_restore import backup_restore_service
from app.services.resumable_uploads import (
    discard_upload_session,
    finalize_upload_session,
    prepare_upload_session,
    serialize_upload_session,
    write_upload_chunk,
)
from app.utils.auth import member_required


backups_bp = Blueprint("backups", __name__)


def _send_download(path, *, download_name: str | None, mimetype: str | None):
    response = send_file(
        path,
        as_attachment=True,
        download_name=download_name,
        mimetype=mimetype,
        conditional=True,
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@backups_bp.get("/backups")
@member_required
def list_backups():
    return jsonify({"items": list_visible_backups(g.current_user)})


@backups_bp.post("/backups")
@member_required
def create_backup():
    payload = request.get_json(force=True) or {}
    owner_id = payload.get("owner_id")
    if g.current_user.role != UserRole.admin:
        owner_id = g.current_user.id

    if "delivery" in payload:
        delivery = str(payload.get("delivery") or "").strip().lower()
    else:
        delivery = "telegram" if bool(payload.get("send_to_telegram", True)) else "download"

    try:
        snapshot_id = backup_service.create_snapshot(
            requester_id=g.current_user.id,
            scope=BackupScope(payload.get("scope", "metadata")),
            owner_id=owner_id,
            delivery=delivery,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"backup_id": snapshot_id}), 202


@backups_bp.get("/backups/<backup_id>/download")
@member_required
def download_backup(backup_id: str):
    try:
        access = backup_service.backup_access_for_user(backup_id, g.current_user)
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404
    if not access.allowed:
        return jsonify({"error": "Not found"}), 404

    artifact = backup_service.download_artifact_path(access.snapshot)
    if artifact is None:
        return jsonify({"error": "Download is not available"}), 404

    path, metadata = artifact
    return _send_download(
        path,
        download_name=str(metadata.get("file_name") or path.name),
        mimetype=str(metadata.get("content_type") or "application/gzip"),
    )


@backups_bp.delete("/backups/<backup_id>")
@member_required
def delete_backup(backup_id: str):
    try:
        access = backup_service.backup_access_for_user(backup_id, g.current_user)
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404
    if not access.allowed:
        return jsonify({"error": "Not found"}), 404

    try:
        result = backup_service.delete_snapshot(backup_id, actor_id=g.current_user.id)
    except FileNotFoundError:
        return jsonify({"error": "Not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify(result)


@backups_bp.post("/backups/import")
@member_required
def import_backup():
    files = request.files.getlist("files")
    confirmation = str(request.form.get("confirmation") or "")
    if not files:
        return jsonify({"error": "No files uploaded", "confirmation_phrase": backup_restore_service.confirmation_phrase}), 400

    stage_root = None
    try:
        staged_paths, stage_root = backup_restore_service.stage_uploaded_part_files(files)
        if len(staged_paths) == 1:
            result = backup_restore_service.import_backup_archive(
                staged_paths[0],
                requested_by_id=g.current_user.id,
                original_filename=files[0].filename or staged_paths[0].name,
                confirmation=confirmation,
            )
        else:
            result = backup_restore_service.import_backup_parts(
                staged_paths,
                requested_by_id=g.current_user.id,
                confirmation=confirmation,
            )
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc), "confirmation_phrase": backup_restore_service.confirmation_phrase}), 400
    finally:
        if stage_root is not None:
            shutil.rmtree(stage_root, ignore_errors=True)


@backups_bp.post("/backups/import/uploads/init")
@member_required
def init_backup_import_upload():
    payload = request.get_json(force=True) or {}
    file_name = str(payload.get("file_name") or "").strip()
    if not file_name:
        return jsonify({"error": "file_name is required"}), 400

    try:
        file_size = int(payload.get("file_size") or 0)
        last_modified_raw = payload.get("last_modified")
        last_modified = int(last_modified_raw) if last_modified_raw not in {None, ""} else None
        chunk_size_raw = payload.get("chunk_size")
        chunk_size = int(chunk_size_raw) if chunk_size_raw not in {None, ""} else None
        state = prepare_upload_session(
            owner_id=g.current_user.id,
            file_name=file_name,
            file_size=file_size,
            last_modified=last_modified,
            content_type=str(payload.get("content_type") or "").strip() or None,
            desired_chunk_size=chunk_size,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"upload": serialize_upload_session(state)})


@backups_bp.put("/backups/import/uploads/<upload_id>/parts/<int:part_index>")
@member_required
def upload_backup_import_chunk(upload_id: str, part_index: int):
    try:
        state = write_upload_chunk(upload_id, g.current_user.id, part_index, request.stream)
    except FileNotFoundError:
        return jsonify({"error": "Upload session not found"}), 404
    except PermissionError:
        return jsonify({"error": "Upload session not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"upload": serialize_upload_session(state)})


@backups_bp.post("/backups/import/uploads/<upload_id>/complete")
@member_required
def complete_backup_import_upload(upload_id: str):
    payload = request.get_json(force=True) or {}
    confirmation = str(payload.get("confirmation") or "")
    try:
        state, staged_path = finalize_upload_session(upload_id, g.current_user.id)
    except FileNotFoundError:
        return jsonify({"error": "Upload session not found"}), 404
    except PermissionError:
        return jsonify({"error": "Upload session not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        result = backup_restore_service.import_backup_archive(
            staged_path,
            requested_by_id=g.current_user.id,
            original_filename=state.file_name,
            confirmation=confirmation,
        )
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc), "confirmation_phrase": backup_restore_service.confirmation_phrase}), 400
    finally:
        discard_upload_session(upload_id)
