from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from time import perf_counter
from typing import Any

import httpx
from sqlalchemy import func

from app.config import _normalize_ai_proxy_base_url, settings
from app.db.session import new_session
from app.models import MediaItem, MediaTag, Tag, TagKind
from app.services.ai_limit_guard import is_ai_proxy_limit_status, trigger_ai_proxy_limit_sleep
from app.services.analysis_enrichment import enrich_analysis_tags, normalize_tag_name
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
- When a recognizable named character, mascot, avatar, VTuber, fursona, game character, anime/manga character, comic character, or meme character is visible, include the most specific character tag you can justify.
- When a recognizable franchise, game, series, anime, manga, comic, show, or universe can be inferred with reasonable confidence, include that franchise/source tag too.
- Prefer character + source pairs when justified. Examples:
  - Boykisser -> include boykisser
  - The Knight from Hollow Knight -> include the_knight and hollow_knight
  - Isabelle from Animal Crossing -> include isabelle and animal_crossing
- If you suspect a specific named character or franchise but confidence is weak, still mention that possibility in the descriptions, but do not emit the tag unless the evidence is reasonably strong.
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
- If recognizable, semantic_tags should include specific character tags and source/franchise tags, not just broad generic tags.
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


TAG_DESCRIPTION_PROMPT = """
You are building a high-quality tag catalog for a private multimedia archive. Each tag is used for search, discovery, moderation, and memory recall.

Your job is to explain one existing tag in detail and return strict JSON.

Rules:
- The canonical tag name is already decided. Do not rename it.
- Write descriptions in two languages:
  - description_ru: Russian
  - description_en: English
- Keep aliases, parent categories, and related tags in English only.
- Do not hallucinate niche facts. If a tag is ambiguous, explicitly say that in the descriptions and notes.
- Use the provided usage count and co-occurring tags as hints about how the tag is used in this archive.
- Explain what the tag usually means, what visual traits it implies, how it differs from nearby tags, and where it is likely to appear.
- For safety or moderation tags, mention moderation implications.
- For technical tags, explain rendering/format/quality implications.
- For fandom or character tags, mention recognizable traits, franchise/source, and common visual context when justified.

Output contract:
- description_ru: detailed Russian explanation of the tag.
- description_en: detailed English explanation of the tag.
- aliases: English aliases or close alternate names.
- parent_categories: broader parent tags or umbrellas.
- related_tags: nearby or often co-occurring tags.
- distinguishing_features: short English bullet-like phrases describing typical defining traits.
- common_contexts: short English bullet-like phrases describing where this tag appears.
- search_hints: short English search clues or phrase fragments that help rediscover the content.
- moderation_notes_ru: Russian moderation note.
- moderation_notes_en: English moderation note.
- ambiguity_note_ru: Russian note if the tag can be ambiguous, otherwise empty string.
- ambiguity_note_en: English note if the tag can be ambiguous, otherwise empty string.
- confidence: number from 0 to 1.

Return JSON only.
""".strip()


TAG_DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "description_ru": {"type": "string"},
        "description_en": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "parent_categories": {"type": "array", "items": {"type": "string"}},
        "related_tags": {"type": "array", "items": {"type": "string"}},
        "distinguishing_features": {"type": "array", "items": {"type": "string"}},
        "common_contexts": {"type": "array", "items": {"type": "string"}},
        "search_hints": {"type": "array", "items": {"type": "string"}},
        "moderation_notes_ru": {"type": "string"},
        "moderation_notes_en": {"type": "string"},
        "ambiguity_note_ru": {"type": "string"},
        "ambiguity_note_en": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "description_ru",
        "description_en",
        "aliases",
        "parent_categories",
        "related_tags",
        "distinguishing_features",
        "common_contexts",
        "search_hints",
        "moderation_notes_ru",
        "moderation_notes_en",
        "ambiguity_note_ru",
        "ambiguity_note_en",
        "confidence",
    ],
    "additionalProperties": False,
}


class AIProxyService:
    def __init__(self) -> None:
        self._concurrency_condition = threading.Condition()
        self._active_requests = 0

    @contextmanager
    def _acquire_request_slot(self, limit: int):
        safe_limit = max(1, int(limit))
        started_wait = perf_counter()
        with self._concurrency_condition:
            while self._active_requests >= safe_limit:
                self._concurrency_condition.wait(timeout=0.5)
            self._active_requests += 1
        wait_seconds = round(perf_counter() - started_wait, 3)
        try:
            yield wait_seconds
        finally:
            with self._concurrency_condition:
                self._active_requests = max(0, self._active_requests - 1)
                self._concurrency_condition.notify_all()

    def _existing_tags_for_owner(self, owner_id: int, limit: int) -> dict[str, list[str]]:
        session = new_session()
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

    def _build_http_error_detail(self, response: httpx.Response) -> str:
        text = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                text = json.dumps(payload, ensure_ascii=False)
            else:
                text = str(payload)
        except Exception:
            text = response.text
        return f"{response.request.method} {response.request.url} -> HTTP {response.status_code}: {' '.join(text.split())[:1200]}"

    def _request_structured_json(
        self,
        *,
        runtime_config: dict[str, Any],
        system_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], httpx.Response, float, float]:
        payload = {
            "model": runtime_config["ai_proxy_model"],
            "reasoning_effort": runtime_config["ai_proxy_reasoning_effort"],
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
        }

        verify: bool | str = bool(runtime_config["ai_proxy_verify_tls"])
        if runtime_config["ai_proxy_ca_bundle"]:
            verify = str(runtime_config["ai_proxy_ca_bundle"])

        with self._acquire_request_slot(int(runtime_config["ai_proxy_max_concurrency"])) as slot_wait_seconds:
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
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if is_ai_proxy_limit_status(status_code):
                state = trigger_ai_proxy_limit_sleep(status_code, self._build_http_error_detail(exc.response))
                raise AIProxyLimitCooldownError(
                    status_code=status_code,
                    sleep_until=state["sleep_until"],
                    detail=state.get("last_error") or "",
                ) from exc
            raise

        body = response.json()
        content_text = body["choices"][0]["message"]["content"]
        parsed = json.loads(content_text)
        return parsed, body, response, elapsed, slot_wait_seconds

    def _normalize_tag_list(self, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_name in values:
            name = normalize_tag_name(raw_name)
            if not name or name in seen:
                continue
            seen.add(name)
            normalized.append(name)
        return normalized

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

        parsed, body, response, elapsed, slot_wait_seconds = self._request_structured_json(
            runtime_config=runtime_config,
            system_prompt=ANALYSIS_PROMPT,
            schema_name="media_analysis",
            schema=ANALYSIS_SCHEMA,
            messages=[{"role": "user", "content": content}],
        )
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
            "slot_wait_seconds": slot_wait_seconds,
            "ai_max_concurrency": int(runtime_config["ai_proxy_max_concurrency"]),
            "frame_count": len(frames),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        }
        return parsed

    def describe_tag(
        self,
        *,
        tag_name: str,
        tag_kind: TagKind,
        usage_count: int,
        cooccurring_tags: list[str],
    ) -> dict[str, Any]:
        runtime_config = get_runtime_config_map()
        parsed, body, response, elapsed, slot_wait_seconds = self._request_structured_json(
            runtime_config=runtime_config,
            system_prompt=TAG_DESCRIPTION_PROMPT,
            schema_name="tag_description",
            schema=TAG_DESCRIPTION_SCHEMA,
            messages=[
                {
                    "role": "user",
                    "content": "\n".join(
                        [
                            f"tag_name: {tag_name}",
                            f"tag_kind: {tag_kind.value}",
                            f"usage_count_in_archive: {usage_count}",
                            f"top_cooccurring_tags: {', '.join(cooccurring_tags)}",
                            "Describe the canonical tag in detail without changing its identity.",
                        ]
                    ),
                }
            ],
        )
        usage = body.get("usage") or {}
        parsed["description_ru"] = str(parsed.get("description_ru", "")).strip()
        parsed["description_en"] = str(parsed.get("description_en", "")).strip()
        parsed["aliases"] = self._normalize_tag_list(list(parsed.get("aliases", [])))
        parsed["parent_categories"] = self._normalize_tag_list(list(parsed.get("parent_categories", [])))
        parsed["related_tags"] = self._normalize_tag_list(list(parsed.get("related_tags", [])))
        parsed["x_request_id"] = response.headers.get("x-request-id")
        parsed["x_metrics"] = {
            "model": body.get("model") or runtime_config["ai_proxy_model"],
            "reasoning_effort": runtime_config["ai_proxy_reasoning_effort"],
            "ai_seconds": elapsed,
            "slot_wait_seconds": slot_wait_seconds,
            "ai_max_concurrency": int(runtime_config["ai_proxy_max_concurrency"]),
            "usage_count": usage_count,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        }
        return parsed


class AIProxyLimitCooldownError(RuntimeError):
    def __init__(self, *, status_code: int, sleep_until: str | None, detail: str) -> None:
        self.status_code = status_code
        self.sleep_until = sleep_until
        self.detail = detail
        super().__init__(f"AI proxy cooldown active after HTTP {status_code}; sleep until {sleep_until or 'unknown'}")


ai_proxy_service = AIProxyService()
