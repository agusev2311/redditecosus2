from __future__ import annotations

import re
from typing import Any

from app.models import MediaItem


_NON_WORD_RE = re.compile(r"[^a-z0-9]+")

_KEYWORD_MAP: dict[str, tuple[str, ...]] = {
    "furry": (
        "furry",
        "anthro",
        "anthropomorphic",
        "anthropomorphic_animal",
        "anthro_character",
        "anthro_animal",
        "fursona",
        "fursuit",
        "kemono",
        "animal_humanoid",
        "humanoid_animal",
    ),
    "protogen": ("protogen", "robotic_furry", "synthetic_anthro"),
    "sergal": ("sergal",),
    "avali": ("avali",),
    "kemonomimi": ("kemonomimi",),
}

_FURRY_SPECIES_MAP: dict[str, tuple[str, ...]] = {
    "wolf": ("wolf", "wolf_character", "canine_wolf"),
    "fox": ("fox", "fox_character", "vulpine"),
    "canine": ("canine", "doglike", "dog_like"),
    "feline": ("feline", "catlike", "cat_like"),
    "dragon": ("dragon", "draconic"),
    "rabbit": ("rabbit", "bunny", "hare"),
    "deer": ("deer", "stag"),
    "hyena": ("hyena",),
    "shark": ("shark",),
    "avian": ("avian", "birdlike", "bird_like"),
    "pony": ("pony", "equine"),
    "kobold": ("kobold",),
}

_IMPLIES_MAP: dict[str, tuple[str, ...]] = {
    "protogen": ("furry", "anthro"),
    "sergal": ("furry", "anthro"),
    "avali": ("furry", "anthro"),
    "anthro": ("furry",),
    "fursuit": ("furry",),
    "fursona": ("furry",),
}

_FURRY_TRIGGER_TAGS = {"furry", "anthro", "protogen", "sergal", "avali", "fursuit", "fursona"}
_PROTOGEN_VISOR_WORDS = ("visor", "screen_face", "digital_visor", "led_face", "face_screen")
_PROTOGEN_SYNTHETIC_WORDS = ("robotic", "cybernetic", "synthetic", "mechanical", "android", "metallic")


def normalize_tag_name(raw_name: str) -> str:
    lowered = raw_name.strip().lower()
    if not lowered:
        return ""
    return _NON_WORD_RE.sub("_", lowered).strip("_")


def _build_normalized_corpus(parts: list[str]) -> str:
    normalized_parts = [normalize_tag_name(part) for part in parts if part]
    normalized_parts = [part for part in normalized_parts if part]
    if not normalized_parts:
        return "__"
    return f"_{'_'.join(normalized_parts)}_"


def _contains_keyword(normalized_corpus: str, keyword: str) -> bool:
    needle = normalize_tag_name(keyword)
    if not needle:
        return False
    return f"_{needle}_" in normalized_corpus


def _derive_protogen(normalized_corpus: str, stable_tags: set[str]) -> bool:
    if "protogen" in stable_tags:
        return True
    if not (stable_tags & _FURRY_TRIGGER_TAGS):
        return False
    has_visor = any(_contains_keyword(normalized_corpus, keyword) for keyword in _PROTOGEN_VISOR_WORDS)
    has_synthetic = any(_contains_keyword(normalized_corpus, keyword) for keyword in _PROTOGEN_SYNTHETIC_WORDS)
    return has_visor and has_synthetic


def enrich_analysis_tags(analysis: dict[str, Any], media: MediaItem) -> dict[str, Any]:
    semantic_tags = [normalize_tag_name(name) for name in analysis.get("semantic_tags", [])]
    semantic_tags = [name for name in semantic_tags if name]

    corpus_parts = [
        analysis.get("title", ""),
        analysis.get("description", ""),
        analysis.get("text_in_media", ""),
        media.original_filename,
        media.source_path or "",
        " ".join(semantic_tags),
    ]
    normalized_corpus = _build_normalized_corpus(corpus_parts)

    derived: set[str] = set()
    for canonical, keywords in _KEYWORD_MAP.items():
        if any(_contains_keyword(normalized_corpus, keyword) for keyword in keywords):
            derived.add(canonical)

    stable_tags = set(semantic_tags) | derived
    if _derive_protogen(normalized_corpus, stable_tags):
        stable_tags.add("protogen")

    for tag in list(stable_tags):
        stable_tags.update(_IMPLIES_MAP.get(tag, ()))

    if stable_tags & _FURRY_TRIGGER_TAGS:
        for canonical, keywords in _FURRY_SPECIES_MAP.items():
            if any(_contains_keyword(normalized_corpus, keyword) for keyword in keywords):
                stable_tags.add(canonical)

    ordered_tags: list[str] = []
    seen: set[str] = set()
    for raw_name in [*semantic_tags, *sorted(stable_tags)]:
        name = normalize_tag_name(raw_name)
        if not name or name in seen:
            continue
        seen.add(name)
        ordered_tags.append(name)

    analysis["semantic_tags"] = ordered_tags
    return analysis
