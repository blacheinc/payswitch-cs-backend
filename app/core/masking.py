"""PII masking helpers (VAPT finding 9).

Applied on the way *out*, in response schemas. Storage and scoring keep the full
value — masking there would silently corrupt the data the models depend on.
"""

from __future__ import annotations

MASK_CHAR = "•"
DEFAULT_VISIBLE = 4


def mask_tail(value: str | None, visible: int = DEFAULT_VISIBLE) -> str | None:
    """Mask all but the last ``visible`` characters.

    Separators are masked along with the digits: leaving them in place leaks the
    identifier's format, which narrows the search space for anyone trying to
    reconstruct it.

    A value short enough that ``visible`` would expose most of it is masked
    entirely — the alternative is a "masked" field that reveals nearly all of a
    short identifier.
    """
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return stripped

    if len(stripped) <= visible + 2:
        return MASK_CHAR * len(stripped)

    return MASK_CHAR * (len(stripped) - visible) + stripped[-visible:]


def mask_phone(value: str | None) -> str | None:
    """Mask a phone number, keeping the last 3 digits.

    Phone numbers are shorter and more structured than national IDs, so a
    4-character tail on a 10-digit local number gives away more than intended.
    """
    return mask_tail(value, visible=3)


def mask_national_id(value: str | None) -> str | None:
    """Mask a national ID, keeping the last 4 characters for reconciliation."""
    return mask_tail(value, visible=DEFAULT_VISIBLE)
