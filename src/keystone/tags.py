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


def model_size_tag(parameter_count: int | None) -> str | None:
    """Map a system-derived parameter count to the marketplace size facet."""
    if parameter_count is None or parameter_count < 0:
        return None
    if parameter_count < 1_000_000_000:
        return "under-1b"
    boundaries = (
        (3_000_000_000, "1b-3b"),
        (7_000_000_000, "3b-7b"),
        (13_000_000_000, "7b-13b"),
        (34_000_000_000, "13b-34b"),
        (70_000_000_000, "34b-70b"),
    )
    for upper_bound, tag in boundaries:
        if parameter_count <= upper_bound:
            return tag
    return "70b-plus"


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
    "model_size_tag",
    "normalise_domain_tags",
    "normalise_size_tag",
]
