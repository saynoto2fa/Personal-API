import re


def normalize_tag(tag: str) -> str:
    """'Gluten-Free ' -> 'gluten_free'."""
    return re.sub(r"[\s\-]+", "_", tag.strip().lower())


def normalize_tags(tags: list[str]) -> list[str]:
    return sorted({t for t in (normalize_tag(x) for x in tags) if t})


def strip_required(v: str | None, field: str) -> str | None:
    """Trim whitespace and reject blank strings. None passes through (PATCH bodies)."""
    if v is None:
        return v
    v = v.strip()
    if not v:
        raise ValueError(f"{field} must not be blank")
    return v
