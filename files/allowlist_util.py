"""Plate normalisation helpers for allowlist matching."""

# Maps visually confusable OCR characters to a canonical form.
# Applied to both the candidate plate and every allowlist entry before comparison
# so that e.g. "AK750CM" and "AK75OCM" resolve to the same key.
_CONFUSABLES = str.maketrans("OI1S5B8Z2", "011553882")


def normalise_plate(plate: str) -> str:
    """Upper-case and fold common OCR confusable characters."""
    return plate.upper().translate(_CONFUSABLES)
