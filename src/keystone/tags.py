"""Marketplace listing tags.

The catalogue and seller form share this finite vocabulary. Keeping the
validation server-side prevents arbitrary strings from becoming unbounded
facets, while the public endpoint lets clients render the same labels without
duplicating policy.
"""

from __future__ import annotations


DOMAIN_TAGS: dict[str, str] = {
    "math": "Mathematics",
    "biology": "Biology",
    "coding": "Coding",
    "legal": "Legal",
    "medicine": "Medicine",
    "finance": "Finance",
    "science": "Science",
    "multilingual": "Multilingual",
    "writing": "Writing",
    "reasoning": "Reasoning",
}

MODEL_SIZE_TAGS: dict[str, str] = {
    "under-1b": "Under 1B",
    "1b-3b": "1B–3B",
    "3b-7b": "3B–7B",
    "7b-13b": "7B–13B",
    "13b-34b": "13B–34B",
    "34b-70b": "34B–70B",
    "70b-plus": "70B+",
}


def normalise_domain_tags(values: list[str] | None) -> list[str]:
    """Validate, de-duplicate, and preserve the system's canonical order."""
    selected = set(values or [])
    unknown = sorted(selected - DOMAIN_TAGS.keys())
    if unknown:
        raise ValueError(f"unsupported domain tags: {', '.join(unknown)}")
    return [tag for tag in DOMAIN_TAGS if tag in selected]


def normalise_size_tag(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if value not in MODEL_SIZE_TAGS:
        raise ValueError(f"unsupported model size tag: {value}")
    return value


def catalogue() -> dict[str, list[dict[str, str]]]:
    return {
        "domains": [{"id": key, "label": label} for key, label in DOMAIN_TAGS.items()],
        "model_sizes": [
            {"id": key, "label": label} for key, label in MODEL_SIZE_TAGS.items()
        ],
    }


__all__ = [
    "DOMAIN_TAGS",
    "MODEL_SIZE_TAGS",
    "catalogue",
    "normalise_domain_tags",
    "normalise_size_tag",
]
