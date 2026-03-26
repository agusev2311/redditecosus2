from __future__ import annotations

import json
from time import perf_counter
from typing import Any

import httpx
from sqlalchemy import func

from app.config import _normalize_ai_proxy_base_url, settings
from app.db.session import SessionLocal
from app.models import MediaItem, MediaTag, Tag, TagKind
from app.services.analysis_enrichment import enrich_analysis_tags
from app.services.media_probe import extract_frames_for_model, probe_media, technical_tags
from app.services.runtime_config import get_runtime_config_map
from app.services.storage import absolute_media_path


ANALYSIS_PROMPT = """
You are the indexing brain of a private multimedia archive used to search, filter, moderate, and rediscover Reddit images, GIFs, and videos.

Your job is to analyze a single media item with maximum descriptive fidelity and return strict JSON.

Goals:
1. Produce highly searchable tags, not generic filler.
2. Write a rich, factual, visual description of what is visible in the media.
3. Include technical tags and moderation-oriented tags.
4. Respect uncertainty. If something is ambiguous, say so.
5. Never invent metadata that is not inferable from the media or supplied probe data.

Required behavior:
- Return descriptions in two languages:
  - description_ru: Russian
  - description_en: English
- Tags must stay in English only. Never output Russian tags.
- Distinguish between semantic tags, technical tags, and safety tags.
- Safety rating must be one of: sfw, questionable, nsfw.
- Infer whether the media is blurred or soft, even if the local blur score is already supplied.
- Preserve recurring visual concepts: characters, clothing, body parts, actions, objects, camera angle, lighting, color palette, environment, motion cues, meme format, overlays, text presence.
- Capture useful retrieval phrases in plain language, not only single-word tags.
- Prefer both umbrella tags and subtype tags when a more specific category is visible. Do not stop at a broad parent label if a narrower label is justified.
- If the media is furry / anthropomorphic / kemono / fursuit related, include furry plus the most specific justified subtype or species tags. Example: a protogen should produce protogen and furry, not only furry.
- If a sci-fi furry subtype such as protogen, sergal, or avali is clearly visible, emit that exact tag in addition to general furry/anthro tags.
- Technical tags must include the media type and quality hints such as picture, gif, video, animated, high_res, portrait, landscape, blurred, sharp, text_overlay, screenshot, illustration, photography, cg, meme, screen_recording when applicable.
- For adult content, be precise and conservative. Use questionable when there is suggestive or revealing content without clear explicit nudity. Use nsfw only when explicit sexual content or explicit nudity is clearly visible. Use sfw otherwise.
- If multiple frames are provided for a video or GIF, reason over the sequence, not just one frame.
- The description should be detailed enough that a human can recognize the file from memory.
- If preferred existing tags are provided, reuse those exact tags whenever they fit. Prefer an existing tag over inventing a near-duplicate. Create a new tag only when no existing tag matches well enough.

Output contract:
- title: a short human-friendly label.
- description_ru: a detailed Russian paragraph describing the scene or sequence.
- description_en: a detailed English paragraph describing the scene or sequence.
- semantic_tags: 16 to 48 tags, lower_snake_case where practical, mixing broad categories and precise subtypes/species/fandom labels.
- technical_tags: lower_snake_case tags for media/quality/rendering.
- safety_tags: must include one of sfw/questionable/nsfw and may include suggestive, nudity, censor, etc.
- blur_assessment: short sentence about sharpness or blur.
- text_in_media: visible text summary, empty string if none.
- people_count_estimate: integer >= 0.
- confidence: 0 to 1 overall confidence.

Return JSON only.
""".strip()


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description_ru": {"type": "string"},
        "description_en": {"type": "string"},
        "semantic_tags": {"type": "array", "items": {"type": "string"}, "minItems": 4},
        "technical_tags": {"type": "array", "items": {"type": "string"}, "minItems": 2},
        "safety_tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "safety_rating": {"type": "string", "enum": ["sfw", "questionable", "nsfw"]},
        "blur_assessment": {"type": "string"},
        "text_in_media": {"type": "string"},
        "people_count_estimate": {"type": "integer", "minimum": 0},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "title",
        "description_ru",
        "description_en",
        "semantic_tags",
        "technical_tags",
        "safety_tags",
        "safety_rating",
        "blur_assessment",
        "text_in_media",
        "people_count_estimate",
        "confidence",
    ],
    "additionalProperties": False,
}


class AIProxyService:
    def _existing_tags_for_owner(self, owner_id: int, limit: int) -> dict[str, list[str]]:
        session = SessionLocal()
        try:
            rows = (
                session.query(Tag.name, Tag.kind, func.count(MediaTag.id).label("usage_count"))
                .outerjoin(MediaTag, MediaTag.tag_id == Tag.id)
                .filter(Tag.owner_id == owner_id)
                .group_by(Tag.id)
                .order_by(func.count(MediaTag.id).desc(), Tag.name.asc())
                .all()
            )
        finally:
            session.close()

        tags_by_kind: dict[str, list[str]] = {"semantic": [], "technical": [], "safety": []}
        per_kind_limit = max(limit, 1)
        for name, kind, _usage in rows:
            bucket = tags_by_kind[kind.value]
            if len(bucket) >= per_kind_limit:
                continue
            bucket.append(name)
        return tags_by_kind

    def analyze_media(self, media: MediaItem) -> dict[str, Any]:
        runtime_config = get_runtime_config_map()
        path = absolute_media_path(media)
        probe = probe_media(path, media.kind)
        local_technical_tags = technical_tags(media.kind, probe)
        frames = extract_frames_for_model(path, media.kind)
        existing_tags = self._existing_tags_for_owner(media.owner_id, int(runtime_config["analysis_existing_tag_limit"]))
        content = [
            {
                "type": "text",
                "text": "\n".join(
                    [
                        f"original_filename: {media.original_filename}",
                        f"normalized_timestamp_utc: {media.normalized_timestamp.isoformat() if media.normalized_timestamp else 'unknown'}",
                        f"mime_type: {probe.mime_type}",
                        f"media_kind: {media.kind.value}",
                        f"width: {probe.width}",
                        f"height: {probe.height}",
                        f"duration_seconds: {probe.duration_seconds}",
                        f"local_blur_score: {probe.blur_score}",
                        f"local_technical_tags: {', '.join(local_technical_tags)}",
                        f"preferred_existing_semantic_tags: {', '.join(existing_tags['semantic'])}",
                        f"preferred_existing_technical_tags: {', '.join(existing_tags['technical'])}",
                        f"preferred_existing_safety_tags: {', '.join(existing_tags['safety'])}",
                        f"source_path: {media.source_path or ''}",
                        "Analyze the media and produce the schema exactly.",
                    ]
                ),
            }
        ]
        for frame in frames:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": frame.data_url,
                        "detail": "high",
                    },
                }
            )

        payload = {
            "model": runtime_config["ai_proxy_model"],
            "reasoning_effort": runtime_config["ai_proxy_reasoning_effort"],
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "media_analysis",
                    "strict": True,
                    "schema": ANALYSIS_SCHEMA,
                },
            },
            "messages": [
                {"role": "system", "content": ANALYSIS_PROMPT},
                {"role": "user", "content": content},
            ],
        }

        verify: bool | str = bool(runtime_config["ai_proxy_verify_tls"])
        if runtime_config["ai_proxy_ca_bundle"]:
            verify = str(runtime_config["ai_proxy_ca_bundle"])

        started = perf_counter()
        with httpx.Client(timeout=int(runtime_config["ai_proxy_timeout_seconds"]), verify=verify) as client:
            response = client.post(
                f"{_normalize_ai_proxy_base_url(str(runtime_config['ai_proxy_base_url'])).rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.ai_proxy_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        elapsed = round(perf_counter() - started, 3)
        response.raise_for_status()
        body = response.json()
        content_text = body["choices"][0]["message"]["content"]
        parsed = json.loads(content_text)
        usage = body.get("usage") or {}
        description_ru = str(parsed.get("description_ru", "")).strip()
        description_en = str(parsed.get("description_en", "")).strip()
        parsed["description_ru"] = description_ru
        parsed["description_en"] = description_en
        parsed["description"] = "\n\n".join(part for part in [description_ru, f"EN: {description_en}" if description_en else ""] if part)
        parsed = enrich_analysis_tags(parsed, media, existing_tags_by_kind=existing_tags)
        parsed["x_request_id"] = response.headers.get("x-request-id")
        parsed["local_technical_tags"] = local_technical_tags
        parsed["preferred_existing_tags"] = existing_tags
        parsed["x_metrics"] = {
            "model": body.get("model") or runtime_config["ai_proxy_model"],
            "reasoning_effort": runtime_config["ai_proxy_reasoning_effort"],
            "ai_seconds": elapsed,
            "frame_count": len(frames),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        }
        return parsed


ai_proxy_service = AIProxyService()
