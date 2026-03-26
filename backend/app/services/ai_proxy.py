from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings
from app.models import MediaItem
from app.services.media_probe import extract_frames_for_model, probe_media, technical_tags
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
- Distinguish between semantic tags, technical tags, and safety tags.
- Safety rating must be one of: sfw, questionable, nsfw.
- Infer whether the media is blurred or soft, even if the local blur score is already supplied.
- Preserve recurring visual concepts: characters, clothing, body parts, actions, objects, camera angle, lighting, color palette, environment, motion cues, meme format, overlays, text presence.
- Capture useful retrieval phrases in plain language, not only single-word tags.
- Technical tags must include the media type and quality hints such as picture, gif, video, animated, high_res, portrait, landscape, blurred, sharp, text_overlay, screenshot, illustration, photography, cg, meme, screen_recording when applicable.
- For adult content, be precise and conservative. Use questionable when there is suggestive or revealing content without clear explicit nudity. Use nsfw only when explicit sexual content or explicit nudity is clearly visible. Use sfw otherwise.
- If multiple frames are provided for a video or GIF, reason over the sequence, not just one frame.
- The description should be detailed enough that a human can recognize the file from memory.

Output contract:
- title: a short human-friendly label.
- description: a detailed paragraph describing the scene or sequence.
- semantic_tags: 12 to 40 tags, lower_snake_case where practical.
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
        "description": {"type": "string"},
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
        "description",
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
    def __init__(self) -> None:
        verify: bool | str = settings.ai_proxy_verify_tls
        if settings.ai_proxy_ca_bundle:
            verify = settings.ai_proxy_ca_bundle
        self.client = httpx.Client(timeout=settings.ai_proxy_timeout_seconds, verify=verify)

    def analyze_media(self, media: MediaItem) -> dict[str, Any]:
        path = absolute_media_path(media)
        probe = probe_media(path, media.kind)
        local_technical_tags = technical_tags(media.kind, probe)
        frames = extract_frames_for_model(path, media.kind)
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
            "model": settings.ai_proxy_model,
            "reasoning_effort": settings.ai_proxy_reasoning_effort,
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
        response = self.client.post(
            f"{settings.ai_proxy_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.ai_proxy_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        content_text = body["choices"][0]["message"]["content"]
        parsed = json.loads(content_text)
        parsed["x_request_id"] = response.headers.get("x-request-id")
        parsed["local_technical_tags"] = local_technical_tags
        return parsed


ai_proxy_service = AIProxyService()
