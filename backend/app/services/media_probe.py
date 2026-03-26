from __future__ import annotations

import base64
import io
import math
import mimetypes
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.config import settings
from app.models import MediaKind

try:
    import magic  # type: ignore
except ImportError:
    magic = None


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
GIF_EXTENSIONS = {".gif"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz", ".tbz2", ".7z", ".rar"}


@dataclass
class MediaProbe:
    mime_type: str
    width: int | None
    height: int | None
    duration_seconds: float | None
    blur_score: float | None


@dataclass
class FramePayload:
    mime_type: str
    data_url: str
    caption: str


def detect_file_type(filename: str) -> str:
    name = filename.lower()
    if name.endswith((".tar.gz", ".tar.bz2")):
        return "archive"
    suffix = Path(name).suffix
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in GIF_EXTENSIONS:
        return "gif"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in ARCHIVE_EXTENSIONS:
        return "archive"
    return "unknown"


def detect_media_kind(filename: str) -> MediaKind | None:
    file_type = detect_file_type(filename)
    if file_type == "image":
        return MediaKind.image
    if file_type == "gif":
        return MediaKind.gif
    if file_type == "video":
        return MediaKind.video
    return None


def sniff_mime(path: Path) -> str:
    if magic is not None:
        return magic.from_file(str(path), mime=True)
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _blur_score_from_rgb_array(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _first_image_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        normalized = ImageOps.exif_transpose(image.convert("RGB"))
        return np.array(normalized)


def probe_media(path: Path, kind: MediaKind) -> MediaProbe:
    if kind in {MediaKind.image, MediaKind.gif}:
        with Image.open(path) as image:
            width, height = image.size
        blur = None
        if kind == MediaKind.image:
            blur = _blur_score_from_rgb_array(_first_image_rgb(path))
        return MediaProbe(sniff_mime(path), width, height, None, blur)

    capture = cv2.VideoCapture(str(path))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) or None
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) or None
    fps = capture.get(cv2.CAP_PROP_FPS) or 0
    frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = float(frame_count / fps) if fps > 0 else None
    blur_scores: list[float] = []
    for ratio in [0.1, 0.45, 0.8]:
        if frame_count <= 0:
            break
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_count * ratio))
        ok, frame = capture.read()
        if not ok:
            continue
        blur_scores.append(_blur_score_from_rgb_array(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
    capture.release()
    blur = round(sum(blur_scores) / len(blur_scores), 2) if blur_scores else None
    return MediaProbe(sniff_mime(path), width, height, duration, blur)


def _thumbnail_from_image(path: Path, output_path: Path) -> None:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image.convert("RGB"))
        image.thumbnail((settings.thumbnail_width, settings.thumbnail_width))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format="JPEG", quality=88)


def _thumbnail_from_video(path: Path, output_path: Path) -> None:
    capture = cv2.VideoCapture(str(path))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    capture.set(cv2.CAP_PROP_POS_FRAMES, max(frame_count // 3, 0))
    ok, frame = capture.read()
    capture.release()
    if not ok:
        return
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    image.thumbnail((settings.thumbnail_width, settings.thumbnail_width))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="JPEG", quality=88)


def create_thumbnail(path: Path, kind: MediaKind, output_path: Path) -> None:
    if kind == MediaKind.video:
        _thumbnail_from_video(path, output_path)
    else:
        _thumbnail_from_image(path, output_path)


def _image_to_data_url(image: Image.Image, caption: str) -> FramePayload:
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG", quality=92)
    encoded = base64.b64encode(buffered.getvalue()).decode("ascii")
    return FramePayload("image/jpeg", f"data:image/jpeg;base64,{encoded}", caption)


def extract_frames_for_model(path: Path, kind: MediaKind, max_frames: int = 8) -> list[FramePayload]:
    frames: list[FramePayload] = []
    if kind == MediaKind.image:
        with Image.open(path) as image:
            frame = ImageOps.exif_transpose(image.convert("RGB"))
            frame.thumbnail((1800, 1800))
            frames.append(_image_to_data_url(frame, "image"))
        return frames

    if kind == MediaKind.gif:
        with Image.open(path) as image:
            total_frames = getattr(image, "n_frames", 1)
            step = max(math.ceil(total_frames / max_frames), 1)
            for frame_index in range(0, total_frames, step):
                image.seek(frame_index)
                frame = ImageOps.exif_transpose(image.convert("RGB"))
                frame.thumbnail((1600, 1600))
                frames.append(_image_to_data_url(frame, f"gif_frame_{frame_index}"))
                if len(frames) >= max_frames:
                    break
        return frames

    capture = cv2.VideoCapture(str(path))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if frame_count <= 0:
        capture.release()
        return frames
    indexes = sorted({int(frame_count * ratio) for ratio in [0.05, 0.18, 0.31, 0.44, 0.57, 0.7, 0.83, 0.96]})
    for index in indexes[:max_frames]:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            continue
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        image.thumbnail((1600, 1600))
        frames.append(_image_to_data_url(image, f"video_frame_{index}"))
    capture.release()
    return frames


def technical_tags(kind: MediaKind, probe: MediaProbe) -> list[str]:
    tags = ["picture" if kind == MediaKind.image else kind.value]
    if kind == MediaKind.gif:
        tags.extend(["animated", "loopable"])
    if kind == MediaKind.video:
        tags.extend(["motion", "clip"])
    if probe.width and probe.height:
        if probe.width >= 1920 or probe.height >= 1920:
            tags.append("high_res")
        if probe.height > probe.width:
            tags.append("portrait")
        elif probe.width > probe.height:
            tags.append("landscape")
        else:
            tags.append("square")
    if probe.blur_score is not None:
        if probe.blur_score < 70:
            tags.extend(["blurred", "soft_focus"])
        elif probe.blur_score > 260:
            tags.append("sharp")
    return sorted(set(tags))
